"""Users, channels and the public profile cards."""

from __future__ import annotations

from . import format as fmt
from . import rating, terms
from .store import Store, new_id, now


def key(s: Store, uid: int) -> str:
    return s.k("u", uid)


def get(s: Store, uid: int) -> dict:
    data = s.hgetall(key(s, uid))
    if not data:
        return default(uid)
    return data


def default(uid: int) -> dict:
    return {
        "id": int(uid),
        "first_name": "",
        "last_name": "",
        "username": "",
        "bio": "",
        "avatar": "",
        "photo_url": "",
        "avatar_color": "",
        "emoji_status": "",
        "premium": False,
        "joined_at": now(),
        "last_seen": now(),
        "status": "",
        "open_to_pacts": True,
        "allow_reports": True,
        "star_balance": 0,
        "guarantor_tier": 0,
        "guarantor_cases": 0,
        "guarantor_settled": 0,
        "guarantor_disputes": 0,
        "pacts_opened": 0,
        "pacts_signed": 0,
        "pacts_closed": 0,
        "pacts_broken": 0,
        "reports_against": 0,
        "reports_upheld": 0,
    }


def touch(s: Store, user) -> dict:
    """Register or refresh a profile from a Telegram User object or dict."""
    uid, first, last, uname, photo_url = _extract(user)
    if not uid:
        return {}
    rec = get(s, uid)
    rec.update({
        "id": uid,
        "first_name": first or rec.get("first_name", ""),
        "last_name": last or rec.get("last_name", ""),
        "username": uname or rec.get("username", ""),
        "photo_url": photo_url or rec.get("photo_url", ""),
        "premium": bool(getattr(user, "is_premium", False)) or rec.get("premium", False),
        "last_seen": now(),
    })
    if not rec.get("joined_at"):
        rec["joined_at"] = now()
    s.hset(key(s, uid), rec)
    sync_username_index(s, rec)
    rating.settle(s, uid)
    return rec


def _extract(user) -> tuple:
    if user is None:
        return None, "", "", "", ""
    if isinstance(user, dict):
        uid = user.get("id")
        return (
            uid,
            user.get("first_name") or "",
            user.get("last_name") or "",
            user.get("username") or "",
            user.get("photo_url") or "",
        )
    return (
        getattr(user, "id", None),
        getattr(user, "first_name", "") or "",
        getattr(user, "last_name", "") or "",
        getattr(user, "username", "") or "",
        getattr(user, "photo_url", "") or "",
    )


def update(s: Store, uid: int, **fields) -> dict:
    rec = get(s, uid)
    rec.update(fields)
    rec["id"] = int(uid)
    s.hset(key(s, uid), rec)
    if rec.get("username"):
        sync_username_index(s, rec)
    return rec


def set_avatar(s: Store, uid: int, file_id: str) -> None:
    if file_id:
        s.hset(key(s, uid), {"avatar": file_id})


def avatar(s: Store, uid: int) -> str:
    return s.hget(key(s, uid), "avatar", "") or ""


def by_username(s: Store, username: str) -> dict | None:
    if not username:
        return None
    reg = s.k("username", username.lower().lstrip("@"))
    uid = s.get_str(reg)
    if not uid.isdigit():
        return None
    return get(s, int(uid))


def display(rec: dict) -> str:
    if not rec:
        return "неизвестно"
    name = fmt.full_name(rec.get("first_name"), rec.get("last_name"))
    return name or (f"@{rec['username']}" if rec.get("username") else str(rec.get("id")))


def handle(rec: dict) -> str:
    if rec.get("username"):
        return f"@{rec['username']}"
    return fmt.full_name(rec.get("first_name"), rec.get("last_name")) or str(rec.get("id"))


def pact_ids(s: Store, uid: int, limit: int = 200) -> list:
    return s.lrange(s.k("u", uid, "pacts"), 0, limit - 1)


def add_pact(s: Store, uid: int, pid: str) -> None:
    s.rpush(s.k("u", uid, "pacts"), pid, cap=400)


def remove_pact(s: Store, uid: int, pid: str) -> None:
    s.lrem(s.k("u", uid, "pacts"), pid)


def reports_against(s: Store, uid: int, limit: int = 50) -> list:
    return s.lrange(s.k("u", uid, "against"), 0, limit - 1)


def count_report(s: Store, uid: int, delta: int = 1) -> None:
    s.hincr(key(s, uid), "reports_against", delta)


def channels(s: Store, limit: int = 100) -> list:
    out = []
    for k in s.scan_keys(s.k("ch", "*")):
        rec = s.hgetall(k)
        if rec:
            out.append(rec)
    out.sort(key=lambda r: r.get("joined_at", 0), reverse=True)
    return out[:limit]


def save_channel(s: Store, chat) -> dict:
    cid = getattr(chat, "id", None) or (chat.get("id") if isinstance(chat, dict) else None)
    if not cid:
        return {}
    ck = s.k("ch", cid)
    rec = s.hgetall(ck) or {
        "id": int(cid),
        "title": "",
        "username": "",
        "type": "channel",
        "joined_at": now(),
        "owner": 0,
        "status": "unknown",
    }
    if isinstance(chat, dict):
        rec["title"] = chat.get("title") or rec.get("title", "")
        rec["username"] = chat.get("username") or rec.get("username", "")
        rec["type"] = chat.get("type") or rec.get("type", "channel")
    else:
        rec["title"] = getattr(chat, "title", "") or rec.get("title", "")
        rec["username"] = getattr(chat, "username", "") or rec.get("username", "")
        rec["type"] = getattr(chat, "type", "") or rec.get("type", "channel")
    s.hset(ck, rec)
    return rec


def channel(s: Store, cid: int) -> dict:
    return s.hgetall(s.k("ch", cid)) or {}


def is_bot_admin(s: Store, cid: int) -> bool:
    return s.hget(s.k("ch", cid), "bot_admin", False) in (True, "true", 1, "1")


def mark_bot_admin(s: Store, cid: int, value: bool, status: str = "") -> None:
    fields = {"bot_admin": bool(value)}
    if status:
        fields["bot_status"] = status
    fields["checked_at"] = now()
    s.hset(s.k("ch", cid), fields)


def link_channels(s: Store, owner: int, chat_ids) -> list:
    """Attach channels to a user, so a pact can watch them."""
    out = []
    for cid in chat_ids or []:
        try:
            cid = int(cid)
        except (TypeError, ValueError):
            continue
        s.sadd(s.k("u", owner, "chats"), str(cid))
        out.append(cid)
    return out


def user_chats(s: Store, owner: int) -> list:
    out = []
    for raw in s.smembers(s.k("u", owner, "chats")):
        if raw.isdigit():
            out.append(int(raw))
    return out


def owner_of(s: Store, cid: int) -> int:
    return s.hget(s.k("ch", cid), "owner", 0) or 0


def assign_owner(s: Store, cid: int, owner: int) -> None:
    s.hset(s.k("ch", cid), {"owner": int(owner)})


def invite_link(s: Store, uid: int) -> str:
    link = s.get_str(s.k("u", uid, "invite"))
    if not link:
        link = new_id("g")[:10]
        s.set_str(s.k("u", uid, "invite"), link)
    return link


# ------------------------------------------------------------------ star wallet

STAR_INCOME = 10


def stars(s: Store, uid: int) -> int:
    return int(s.hget(key(s, uid), "star_balance", 0) or 0)


def add_stars(s: Store, uid: int, amount: int, reason: str = "") -> int:
    value = stars(s, uid) + int(amount)
    s.hset(key(s, uid), {"star_balance": value})
    if reason:
        s.rpush(s.k("u", uid, "stars"), {"delta": int(amount), "reason": reason, "ts": now()}, cap=200)
    return value


def star_log(s: Store, uid: int, limit: int = 20) -> list:
    return s.lrange(s.k("u", uid, "stars"), 0, limit - 1)


def guarantor_tier(s: Store, uid: int) -> int:
    return int(s.hget(key(s, uid), "guarantor_tier", 0) or 0)


def bump_guarantor(s: Store, uid: int, settled: int = 0, disputes: int = 0) -> int:
    rec = get(s, uid)
    cases = int(rec.get("guarantor_cases", 0) or 0)
    ok = int(rec.get("guarantor_settled", 0) or 0)
    bad = int(rec.get("guarantor_disputes", 0) or 0)
    tier = 0
    if cases >= 1:
        tier = 1
    if cases >= 5 and ok >= 4:
        tier = 2
    if cases >= 15 and ok >= 13 and bad == 0:
        tier = 3
    if cases >= 40 and ok >= 37 and bad == 0:
        tier = 4
    if cases >= 100 and ok >= 96 and bad == 0:
        tier = 5
    update(s, uid, guarantor_tier=tier, guarantor_cases=cases,
           guarantor_settled=ok, guarantor_disputes=bad)
    return tier


def profile_card(s: Store, uid: int) -> dict:
    rec = get(s, uid)
    snap = rating.snapshot(s, uid)
    rec["score"] = snap["score"]
    rec["tier"] = snap["tier"]
    rec["pact_count"] = s.llen(s.k("u", uid, "pacts"))
    return rec


def is_eligible_counterparty(s: Store, uid: int) -> tuple[bool, str]:
    """Basic gate before a pact can even be drafted."""
    rec = get(s, uid)
    if not rec.get("joined_at"):
        return True, ""
    snap = rating.snapshot(s, uid)
    if snap["score"] < 20:
        return False, "у контрагента слишком низкий балл порядочности"
    if rec.get("pacts_broken", 0) >= 3:
        return False, "у контрагента слишком много нарушенных договорённостей"
    return True, ""


def recommend(s: Store, uid: int, limit: int = 5) -> list:
    """Peers with whom this person already has history."""
    peers = [int(x) for x in s.smembers(s.k("graph", uid)) if x.isdigit()]
    peers = [p for p in peers if p != uid][:limit]
    return [get(s, p) for p in peers]


def recent_joiners(s: Store, uid: int, limit: int = 5) -> list:
    out = []
    prefix = s.k("u", "")
    for k in s.scan_keys(s.k("u", "*")):
        suffix = k[len(prefix):] if k.startswith(prefix) else ""
        if not suffix.isdigit():
            continue
        rec = s.hgetall(k)
        if not rec or int(rec.get("id") or 0) == uid:
            continue
        if not rec.get("username"):
            continue
        s.hset(k, {"username": rec["username"]})
        s.set_str(s.k("username", rec["username"].lower()), str(rec["id"]))
        out.append(rec)
    out.sort(key=lambda r: r.get("joined_at", 0), reverse=True)
    return out[:limit]


def rank(s: Store, limit: int = 20) -> list:
    """Public leaderboard by reputation."""
    rows = []
    for k in s.scan_keys(s.k("score", "*")):
        rec = s.hgetall(k) or {}
        value = float(rec.get("v", terms.RATING_BASE) or terms.RATING_BASE)
        uid = k.rsplit(":", 1)[-1]
        if not uid.isdigit():
            continue
        user = get(s, int(uid))
        if not user.get("username") and not user.get("first_name"):
            continue
        rows.append({
            "uid": int(uid),
            "name": display(user),
            "username": user.get("username", ""),
            "photo_url": user.get("photo_url", ""),
            "score": value,
        })
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows[:limit]


def sync_username_index(s: Store, rec: dict) -> None:
    if rec.get("username"):
        s.set_str(s.k("username", rec["username"].lower().lstrip("@")), str(rec["id"]))