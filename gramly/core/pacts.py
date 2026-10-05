"""The pact: creation, signing, sealing, state machine and timeline.

A pact has exactly two sides. Each side carries its own obligations, its own
deadline-relevant channel bindings and its own guarantor. The lifecycle is:

    draft ──sign(first)──> half ──sign(second)──> signed ──activate──> active
      │                      │                       │                  │
      └──cancel──> cancelled │                  dispute             done / broken
                             └──────hold / hold─release──────┘
"""

from __future__ import annotations

import hashlib
import json

from . import format as fmt
from . import rating, terms, users
from .store import Store, new_id, now

MAX_OBLIGATIONS = 12
CONDITIONS_CAP = 600


# ----------------------------------------------------------------------- keys

def key(s: Store, pid: str) -> str:
    return s.k("pact", pid)


def tl_key(s: Store, pid: str) -> str:
    return s.k("pact", pid, "tl")


def events_key(s: Store, pid: str) -> str:
    return s.k("pact", pid, "ev")


def counter_key(s: Store) -> str:
    return s.k("pact", "seq")


# --------------------------------------------------------------------- reads

def get(s: Store, pid: str) -> dict:
    return s.hgetall(key(s, pid))


def exists(s: Store, pid: str) -> bool:
    return s.exists(key(s, pid))


def timeline(s: Store, pid: str, limit: int = 100) -> list:
    return s.lrange(tl_key(s, pid), 0, limit - 1)


def events(s: Store, pid: str, limit: int = 200) -> list:
    return s.lrange(events_key(s, pid), -limit, -1)


def counterparty_of(pact: dict, uid: int) -> int:
    a = int(pact.get("side_a") or 0)
    return int(pact.get("side_b") or 0) if a == int(uid) else a


def is_party(pact: dict, uid: int) -> bool:
    return int(uid) in (int(pact.get("side_a") or 0), int(pact.get("side_b") or 0))


def sides(pact: dict) -> list:
    return [pact.get("side_a_meta") or {}, pact.get("side_b_meta") or {}]


def side_meta(pact: dict, uid: int) -> dict:
    for meta in sides(pact):
        if int(meta.get("uid") or 0) == int(uid):
            return meta
    return {}


def signatures(pact: dict) -> list:
    return pact.get("signatures") or []


def has_signed(pact: dict, uid: int) -> bool:
    return any(int(x.get("uid", 0)) == int(uid) for x in signatures(pact))


def signature_of(pact: dict, uid: int) -> dict | None:
    for x in signatures(pact):
        if int(x.get("uid", 0)) == int(uid):
            return x
    return None


def guarantor_of(pact: dict, uid: int | None = None) -> int:
    if uid is None:
        return int(pact.get("guarantor") or 0)
    return int(side_meta(pact, uid).get("guarantor") or 0)


def both_signed(pact: dict) -> bool:
    return len({int(x.get("uid", 0)) for x in signatures(pact)}) >= 2


def term_of(pact: dict) -> tuple[str, str]:
    start = pact.get("term_start") or pact.get("created_at")
    end = pact.get("term_end") or ""
    return fmt.fmt_date(start), fmt.fmt_date(end) if end else "бессрочно"


# -------------------------------------------------------------------- writes

def next_number(s: Store) -> int:
    return s.incr(counter_key(s))


def log(s: Store, pid: str, kind: str, text: str, uid: int = 0, **extra) -> dict:
    entry = {"t": now(), "kind": kind, "text": text, "uid": int(uid or 0)}
    entry.update(extra)
    s.rpush(tl_key(s, pid), entry, cap=300)
    return entry


def set_status(s: Store, pid: str, status: str, reason: str = "", uid: int = 0) -> dict:
    pact = get(s, pid)
    if not pact:
        return {}
    pact["status"] = status
    pact["status_reason"] = reason
    pact["status_at"] = now()
    if status != "active":
        pact.pop("active_at", None)
    if status in ("done", "broken", "cancelled"):
        pact["closed_at"] = now()
    pact["hash"] = seal(pact)
    s.hset(key(s, pid), pact)
    log(s, pid, f"status:{status}", reason or terms.STATUSES.get(status, status), uid)
    _mirror(s, pid)
    return pact


def seal(pact: dict) -> str:
    """Content hash over the meaningful fields, so edits are detectable."""
    body = {
        k: pact.get(k)
        for k in (
            "id", "num", "kind", "title", "side_a", "side_b",
            "side_a_meta", "side_b_meta", "term_start", "term_end",
            "signatures", "guarantor", "created_at",
        )
    }
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify(s: Store, pid: str) -> tuple[bool, str]:
    pact = get(s, pid)
    if not pact:
        return False, "пакт не найден"
    if seal(pact) == pact.get("hash"):
        return True, "целостность подтверждена"
    return False, "целостность нарушена: содержимое менялось после подписания"


def _mirror(s: Store, pid: str) -> None:
    """Denormalised list for fast profile views."""
    pact = get(s, pid)
    if not pact:
        return
    for uid in (int(pact.get("side_a") or 0), int(pact.get("side_b") or 0)):
        if uid:
            users.add_pact(s, uid, pid)
    s.hset(s.k("pub", pid), {
        "num": pact.get("num"),
        "kind": pact.get("kind"),
        "title": pact.get("title"),
        "status": pact.get("status"),
        "a": pact.get("side_a_name"),
        "b": pact.get("side_b_name"),
        "created_at": pact.get("created_at"),
        "hash": pact.get("hash"),
    })


def public(s: Store, pid: str) -> dict:
    return s.hgetall(s.k("pub", pid))


def list_for(s: Store, uid: int, limit: int = 40) -> list:
    out = []
    for pid in users.pact_ids(s, uid, limit):
        pact = get(s, pid)
        if pact:
            out.append(pact)
    return out


def list_recent(s: Store, limit: int = 30, status: str = "") -> list:
    out = []
    for pid in s.lrange(s.k("pact", "all"), -limit * 3, -1):
        pact = get(s, pid)
        if not pact:
            continue
        if status and pact.get("status") != status:
            continue
        out.append(pact)
        if len(out) >= limit:
            break
    return list(reversed(out))


# ------------------------------------------------------------------- creation

def create(
    s: Store,
    *,
    author: int,
    counterparty: int,
    kind: str,
    title: str,
    a_conditions: list,
    b_conditions: list,
    term_start: int,
    term_end: int = 0,
    guarantor: int = 0,
    a_meta_extra: dict | None = None,
    b_meta_extra: dict | None = None,
) -> dict:
    kind = kind if kind in terms.PACT_KINDS else terms.DEFAULT_KIND
    pid = new_id("p")
    author = int(author)
    counterparty = int(counterparty)
    if author == counterparty:
        counterparty = 0

    a_rec = users.get(s, author)
    b_rec = users.get(s, counterparty) if counterparty else {}

    a_meta = {
        "uid": author,
        "name": users.display(a_rec),
        "username": a_rec.get("username", ""),
        "conditions": _clean_conditions(a_conditions),
        "channels": (a_meta_extra or {}).get("channels", []),
        "guarantor": int((a_meta_extra or {}).get("guarantor", 0) or 0),
        "offers_stars": bool((a_meta_extra or {}).get("offers_stars", False)),
    }
    b_meta = {
        "uid": counterparty,
        "name": users.display(b_rec) if b_rec else "",
        "username": b_rec.get("username", "") if b_rec else "",
        "conditions": _clean_conditions(b_conditions),
        "channels": (b_meta_extra or {}).get("channels", []),
        "guarantor": int((b_meta_extra or {}).get("guarantor", 0) or 0),
        "offers_stars": bool((b_meta_extra or {}).get("offers_stars", False)),
    }

    pact = {
        "id": pid,
        "num": next_number(s),
        "kind": kind,
        "title": (title or "").strip()[:120] or default_title(s, kind, a_rec, b_rec),
        "side_a": author,
        "side_b": counterparty,
        "side_a_name": users.display(a_rec),
        "side_b_name": users.display(b_rec),
        "side_a_meta": a_meta,
        "side_b_meta": b_meta,
        "term_start": int(term_start or now()),
        "term_end": int(term_end or 0),
        "guarantor": int(guarantor or 0),
        "signatures": [],
        "status": "draft",
        "created_at": now(),
        "created_by": author,
        "invited": counterparty,
        "price_stars": int((a_meta_extra or {}).get("price_stars", 0) or 0),
    }
    pact["hash"] = seal(pact)
    s.hset(key(s, pid), pact)
    s.rpush(s.k("pact", "all"), pid, cap=20000)
    users.add_pact(s, author, pid)
    if counterparty:
        users.add_pact(s, counterparty, pid)
    users.update(s, author, pacts_opened=int(users.get(s, author).get("pacts_opened", 0) or 0) + 1)
    users.sync_username_index(s, a_rec)
    log(s, pid, "created", f"{terms.ENTITY} создан", author)
    _mirror(s, pid)
    return pact


def _clean_conditions(items) -> list:
    out = []
    for item in items or []:
        text = " ".join(str(item).split())
        if not text:
            continue
        out.append(text[:CONDITIONS_CAP])
    return out[:MAX_OBLIGATIONS]


def default_title(s: Store, kind: str, a_rec: dict, b_rec: dict) -> str:
    spec = terms.PACT_KINDS.get(kind, terms.PACT_KINDS[terms.DEFAULT_KIND])
    a = users.handle(a_rec)
    b = users.handle(b_rec) if b_rec else "контрагент"
    return f"{spec['label']}: {a} × {b}"


def set_conditions(s: Store, pid: str, uid: int, items: list) -> dict:
    pact = get(s, pid)
    meta = side_meta(pact, uid)
    if not meta:
        return pact
    meta["conditions"] = _clean_conditions(items)
    field = "side_a_meta" if int(pact.get("side_a") or 0) == int(uid) else "side_b_meta"
    pact[field] = meta
    pact["hash"] = seal(pact)
    s.hset(key(s, pid), pact)
    log(s, pid, "terms", "обновлены обязательства", uid)
    _mirror(s, pid)
    return pact


def set_term(s: Store, pid: str, start: int, end: int = 0) -> dict:
    pact = get(s, pid)
    pact["term_start"] = int(start)
    pact["term_end"] = int(end or 0)
    pact["hash"] = seal(pact)
    s.hset(key(s, pid), pact)
    log(s, pid, "term", "обновлён срок", pact.get("created_by", 0))
    _mirror(s, pid)
    return pact


def set_channels(s: Store, pid: str, uid: int, chat_ids) -> dict:
    pact = get(s, pid)
    meta = side_meta(pact, uid)
    if not meta:
        return pact
    ids = []
    for cid in chat_ids or []:
        try:
            cid = int(cid)
        except (TypeError, ValueError):
            continue
        rec = users.channel(s, cid)
        users.assign_owner(s, cid, uid)
        ids.append({
            "id": cid,
            "title": rec.get("title", ""),
            "username": rec.get("username", ""),
        })
    meta["channels"] = ids
    field = "side_a_meta" if int(pact.get("side_a") or 0) == int(uid) else "side_b_meta"
    pact[field] = meta
    pact["hash"] = seal(pact)
    s.hset(key(s, pid), pact)
    log(s, pid, "channels", f"привязано каналов: {len(ids)}", uid)
    _mirror(s, pid)
    return pact


def set_kind(s: Store, pid: str, kind: str) -> dict:
    pact = get(s, pid)
    if kind not in terms.PACT_KINDS:
        return pact
    pact["kind"] = kind
    pact["hash"] = seal(pact)
    s.hset(key(s, pid), pact)
    log(s, pid, "kind", f"тип: {terms.PACT_KINDS[kind]['label']}", pact.get("created_by", 0))
    _mirror(s, pid)
    return pact


def set_title(s: Store, pid: str, title: str) -> dict:
    pact = get(s, pid)
    pact["title"] = (title or "").strip()[:120] or pact.get("title", "")
    pact["hash"] = seal(pact)
    s.hset(key(s, pid), pact)
    _mirror(s, pid)
    return pact


# -------------------------------------------------------------------- signing

def sign(s: Store, pid: str, uid: int, text: str = "") -> tuple[dict, bool, str]:
    """Add a signature. Returns (pact, changed, message)."""
    pact = get(s, pid)
    if not pact:
        return {}, False, "пакт не найден"
    if not is_party(pact, uid):
        return pact, False, "вы не сторона этого пакта"
    if pact.get("status") in ("done", "broken", "cancelled"):
        return pact, False, f"{terms.ENTITY} уже {terms.STATUSES.get(pact['status'], 'закрыт')}"
    if has_signed(pact, uid):
        return pact, False, "вы уже подписали"

    rec = users.get(s, uid)
    entry = {
        "uid": int(uid),
        "name": users.display(rec),
        "username": rec.get("username", ""),
        "ts": now(),
        "text": (text or "Подтверждаю условия").strip()[:160],
        "ip_hint": "",
    }
    pact["signatures"] = signatures(pact) + [entry]
    both = len({int(x["uid"]) for x in pact["signatures"]}) >= 2
    pact["status"] = "signed" if both else "half"
    pact["signed_at"] = now() if both else pact.get("signed_at", 0)
    pact["hash"] = seal(pact)
    s.hset(key(s, pid), pact)
    log(s, pid, "sign", f"подпись: {entry['name']}", uid)
    users.update(s, uid, pacts_signed=int(users.get(s, uid).get("pacts_signed", 0) or 0) + 1)
    _mirror(s, pid)
    return pact, True, "подпись принята"


def activate(s: Store, pid: str, uid: int = 0) -> dict:
    pact = get(s, pid)
    if not both_signed(pact):
        return pact
    pact["status"] = "active"
    pact["active_at"] = now()
    pact["hash"] = seal(pact)
    s.hset(key(s, pid), pact)
    log(s, pid, "status:active", terms.ACTIVE, uid)
    _mirror(s, pid)
    return pact


def close(s: Store, pid: str, uid: int, verdict: str, note: str = "") -> tuple[dict, list]:
    """Verdict: completed | broken. Applies reputation both ways."""
    pact = get(s, pid)
    a, b = int(pact.get("side_a") or 0), int(pact.get("side_b") or 0)
    guilty, innocent = (uid, counterparty_of(pact, uid)) if uid in (a, b) else (0, 0)
    applied: list = []

    if verdict == "completed":
        pact = set_status(s, pid, "done", note or "исполнен по условиям", uid)
        if a and b:
            for x, y in ((a, b), (b, a)):
                if s.get_int(s.k("graph", x), 0) or s.smembers(s.k("graph", x)):
                    delta, why = rating.reward_completed(s, x, y, pid, note)
                    applied.append((x, delta, why))
        for x in (a, b):
            if x:
                users.update(s, x, pacts_closed=int(users.get(s, x).get("pacts_closed", 0) or 0) + 1)
                rating.note_pair(s, x, counterparty_of(pact, x))
    else:
        pact = set_status(s, pid, "broken", note or "нарушены условия", uid)
        if guilty:
            rating.penalise(s, guilty, "broken", rating.GAIN_BROKEN, note or "нарушил условия пакта", pid)
            users.update(s, guilty, pacts_broken=int(users.get(s, guilty).get("pacts_broken", 0) or 0) + 1)
            applied.append((guilty, rating.GAIN_BROKEN, "нарушение условий"))
        if innocent:
            delta, why = rating.reward_completed(s, innocent, guilty or 0, pid, "пострадавшая сторона")
            applied.append((innocent, delta, why))
    return pact, applied


def cancel(s: Store, pid: str, uid: int, note: str = "") -> tuple[dict, list]:
    pact = get(s, pid)
    other = counterparty_of(pact, uid)
    pact = set_status(s, pid, "cancelled", note or "расторгнут по инициативе стороны", uid)
    applied = []
    if both_signed(pact) and other:
        rating.penalise(s, uid, "cancel", rating.GAIN_CANCELLED_BY_YOU, note or "расторг подписанного пакта", pid)
        rating.penalise(s, other, "cancel", rating.GAIN_CANCELLED_BY_OTHER, note or "контрагент расторг", pid)
        applied = [(uid, rating.GAIN_CANCELLED_BY_YOU, ""), (other, rating.GAIN_CANCELLED_BY_OTHER, "")]
    return pact, applied


def attach_guarantor(s: Store, pid: str, uid: int, guarantor: int) -> tuple[dict, bool, str]:
    pact = get(s, pid)
    if not is_party(pact, uid):
        return pact, False, "только сторона может назначить гаранта"
    if users.guarantor_tier(s, guarantor) < 1:
        return pact, False, "у этого человека нет статуса гаранта"
    meta = side_meta(pact, uid)
    meta["guarantor"] = int(guarantor)
    field = "side_a_meta" if int(pact.get("side_a") or 0) == int(uid) else "side_b_meta"
    pact[field] = meta
    if not int(pact.get("guarantor") or 0):
        pact["guarantor"] = int(guarantor)
    pact["hash"] = seal(pact)
    s.hset(key(s, pid), pact)
    log(s, pid, "guarantor", f"гарант: {users.display(users.get(s, guarantor))}", uid)
    _mirror(s, pid)
    return pact, True, "гарант назначен"


# -------------------------------------------------------------------- reports

def raise_report(s: Store, *, reporter: int, target: int, pid: str = "", reason: str = "", text: str = "", photos=None) -> dict:
    rid = new_id("r")
    rec = {
        "id": rid,
        "reporter": int(reporter),
        "target": int(target),
        "pact": pid or "",
        "reason": reason or "другое",
        "text": (text or "").strip()[:1200],
        "photos": list(photos or [])[:4],
        "status": "open",
        "created_at": now(),
        "pact_title": (get(s, pid).get("title", "") if pid else ""),
        "target_name": users.display(users.get(s, target)),
        "reporter_name": users.display(users.get(s, reporter)),
    }
    s.hset(s.k("report", rid), rec)
    s.rpush(s.k("reports", "open"), rid, cap=2000)
    s.rpush(s.k("u", target, "against"), rid, cap=200)
    s.rpush(s.k("u", reporter, "filed"), rid, cap=200)
    users.count_report(s, target, 1)
    if pid:
        log(s, pid, "report", f"открыта жалоба: {rec['reason']}", reporter)
    return rec


def get_report(s: Store, rid: str) -> dict:
    return s.hgetall(s.k("report", rid))


def reports_for(s: Store, uid: int, limit: int = 30) -> list:
    return [get_report(s, r) for r in s.lrange(s.k("u", uid, "against"), 0, limit - 1)]


def open_reports(s: Store, limit: int = 60) -> list:
    out = []
    for rid in s.lrange(s.k("reports", "open"), 0, limit - 1):
        rec = get_report(s, rid)
        if rec:
            out.append(rec)
    return out


def resolve_report(s: Store, rid: str, upheld: bool, note: str = "", arbiter: int = 0) -> dict:
    rec = get_report(s, rid)
    if not rec:
        return {}
    rec["status"] = "upheld" if upheld else "dismissed"
    rec["verdict_at"] = now()
    rec["verdict_note"] = note[:600]
    rec["arbiter"] = int(arbiter or 0)
    s.hset(s.k("report", rid), rec)
    s.lrem(s.k("reports", "open"), rid)
    s.rpush(s.k("reports", "closed"), rid, cap=4000)
    target = int(rec.get("target") or 0)
    if upheld and target:
        rating.penalise(s, target, "report", rating.GAIN_REPORT_UPHELD, note or rec.get("reason", ""), rec.get("pact", ""))
        users.update(s, target, reports_upheld=int(users.get(s, target).get("reports_upheld", 0) or 0) + 1)
    elif target:
        rating.penalise(s, target, "report", rating.GAIN_REPORT_DISMISSED, "жалоба не подтвердилась", rec.get("pact", ""))
    if rec.get("reporter"):
        rating.penalise(s, int(rec["reporter"]), "report", -1.0, "жалоба рассмотрена", rec.get("pact", ""))
    return rec


# ------------------------------------------------------------------ monitoring

def watched_chats(pact: dict) -> list:
    out = []
    for meta in sides(pact):
        for ch in meta.get("channels") or []:
            cid = ch.get("id") if isinstance(ch, dict) else ch
            if cid:
                out.append(int(cid))
    return out


def guard_channels(pact: dict) -> int:
    """How many bound channels the bot is still alive in."""
    return len(watched_chats(pact))


def health(s: Store, pid: str) -> dict:
    pact = get(s, pid)
    chats = watched_chats(pact)
    alive = sum(1 for cid in chats if users.is_bot_admin(s, cid))
    lost = [cid for cid in chats if not users.is_bot_admin(s, cid)]
    return {
        "total": len(chats),
        "alive": alive,
        "lost": lost,
        "ok": not lost,
    }


def raise_alert(s: Store, pid: str, code: str, text: str) -> dict:
    entry = log(s, pid, f"alert:{code}", text)
    s.rpush(events_key(s, pid), {
        "t": now(),
        "code": code,
        "text": text,
        "level": "critical" if code in ("bot_removed", "channel_deleted") else "warn",
    }, cap=200)
    return entry


def alerts(pact: dict) -> list:
    return pact.get("alerts") or []


def stats(s: Store) -> dict:
    total = s.llen(s.k("pact", "all"))
    active = 0
    signed = 0
    for pid in s.lrange(s.k("pact", "all"), -200, -1):
        st = s.hget(key(s, pid), "status", "")
        if st == "active":
            active += 1
        elif st in ("signed", "half"):
            signed += 1
    return {
        "total": total,
        "active": active,
        "awaiting": signed,
        "users": len(list(s.scan_keys(s.k("u", "*")))),
        "channels": len(list(s.scan_keys(s.k("ch", "*")))),
        "reports_open": s.llen(s.k("reports", "open")),
        "ledger": s.llen(s.k("ledger", "chain")),
    }