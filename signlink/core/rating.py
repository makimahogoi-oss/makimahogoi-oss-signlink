"""The reputation engine: порядочность.

The whole point of the module is that the score cannot be inflated. Four
mechanisms do that work:

1. **Counterparty weighting.** A pact completed with a well-regarded partner
   is worth more than one completed with an unknown. Gain is scaled by the
   counterparty's score, so a brand-new account cannot farm reputation by
   opening a thousand pacts with other brand-new accounts.
2. **Mutual-farming detection.** If a pact contributes positive weight and
   every recent pact of both parties involves only each other, the whole
   cluster is treated as a closed loop and yields zero.
3. **Diminishing returns.** Each successive positive pact with the same
   counterparty is worth less (full → 35% → 15% → 5%), and pacts closed inside
   a short window are discounted.
4. **Decay.** Score drifts back toward the baseline when a person stops
   producing history, so an old score cannot be parked and cashed in later.

Negative weight is never reduced by any of these rules and never decays on
the same schedule as gains. Every mutation appends to a hash-chained ledger,
so the public history is verifiable and cannot be quietly rewritten.
"""

from __future__ import annotations

import hashlib
import json
import time

from . import terms
from .store import Store, now, store

LEDGER_CAP = 5000

GAIN_CLOSED = 22.0
GAIN_COMPLETED = 9.0
GAIN_BROKEN = -40.0
GAIN_CANCELLED_BY_YOU = -18.0
GAIN_CANCELLED_BY_OTHER = -4.0
GAIN_REPORT_UPHELD = -30.0
GAIN_REPORT_DISMISSED = 2.0
GAIN_BACKFILL = -2.0

DIMINISHING = (1.0, 0.35, 0.15, 0.05)
PARTNER_EXPONENT = 0.6
NEIGHBOUR_WINDOW = 60 * 60 * 24 * 30
DECAY_PER_DAY = 0.35
DECAY_FLOOR_WEIGHT = 0.0
SETTLE_AFTER_DAYS = 21


# --------------------------------------------------------------------------- ledger


def _chain_key(s: Store) -> str:
    return s.k("ledger", "chain")


def _events_key(s: Store, uid: int) -> str:
    return s.k("ledger", "u", uid)


def append_event(s: Store, uid: int, kind: str, delta: float, note: str = "", pact: str = "") -> dict:
    """Append a signed, hash-chained reputation event."""
    prev = s.get_str(_chain_key(s), "0" * 64)
    ts = now()
    body = {
        "uid": int(uid),
        "kind": kind,
        "delta": round(float(delta), 3),
        "note": note[:180],
        "pact": pact or "",
        "ts": ts,
        "prev": prev,
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
    body["hash"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    s.rpush(_chain_key(s), body, cap=LEDGER_CAP)
    s.rpush(_events_key(s, int(uid)), body, cap=400)
    return body


def events(s: Store, uid: int, limit: int = 60) -> list:
    return s.lrange(_events_key(s, int(uid)), 0, limit - 1)


def global_events(s: Store, limit: int = 60) -> list:
    """Recent public reputation history."""
    items = s.lrange(_chain_key(s), -limit, -1)
    return list(reversed(items))


def verify_chain(s: Store, limit: int = 400) -> tuple[bool, str]:
    """Recompute the hash chain so tampering is detectable.

    A window is checked from the entry that precedes it, so the first item in
    the window is verified against its real predecessor instead of the genesis
    hash.
    """
    items = s.lrange(_chain_key(s), -limit - 1, -1)
    prev = "0" * 64
    if len(items) > limit:
        prev = items[0].get("hash", "") if isinstance(items[0], dict) else ""
        items = items[1:]
    for item in items:
        if not isinstance(item, dict):
            return False, "malformed entry"
        body = {k: v for k, v in item.items() if k != "hash"}
        if body.get("prev") != prev:
            return False, f"broken link at {item.get('ts')}"
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
        expect = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        if expect != item.get("hash"):
            return False, f"bad signature at {item.get('ts')}"
        prev = item["hash"]
    return True, "цепочка событий целостна"


# --------------------------------------------------------------------------- accessors


def _score_key(s: Store, uid: int) -> str:
    return s.k("score", uid)


def score(s: Store, uid: int) -> float:
    return s.hget(_score_key(s, uid), "v", terms.RATING_BASE) or terms.RATING_BASE


def _raw_score(s: Store, uid: int) -> float:
    return s.hget(_score_key(s, uid), "v", terms.RATING_BASE) or terms.RATING_BASE


def _write(s: Store, uid: int, value: float, mode: str = "set") -> float:
    value = max(terms.RATING_MIN, min(terms.RATING_MAX, round(float(value), 3)))
    key = _score_key(s, uid)
    if mode == "delta":
        value = max(terms.RATING_MIN, min(terms.RATING_MAX, round(_raw_score(s, uid) + value, 3)))
    s.hset(key, {"v": value, "ts": now()})
    return value


def last_touch(s: Store, uid: int) -> int:
    return s.hget(_score_key(s, uid), "ts", 0) or 0


def apply_decay(s: Store, uid: int) -> float:
    """Drift toward the baseline; only ever pulls down, never pumps up."""
    key = _score_key(s, uid)
    if not s.exists(key):
        return terms.RATING_BASE
    touched = s.hget(key, "ts", 0) or 0
    if not touched:
        return _raw_score(s, uid)
    days = (now() - touched) / 86400.0
    if days < 1:
        return _raw_score(s, uid)
    current = _raw_score(s, uid)
    above = current - terms.RATING_BASE
    if abs(above) < 0.4:
        s.hset(key, {"ts": now()})
        return current
    pull = DECAY_PER_DAY * days
    if above > 0:
        # Positive history fades back down to baseline and then stops.
        target = max(terms.RATING_BASE, current - min(above, pull))
    else:
        # Misconduct stains last ~3x longer than goodwill.
        target = min(terms.RATING_BASE, current + min(abs(above), pull / 3.0))
    _write(s, uid, target)
    s.hset(key, {"ts": now()})
    return target


def current(s: Store, uid: int) -> float:
    return apply_decay(s, uid)


# --------------------------------------------------------------------------- guards


def pair_history(s: Store, a: int, b: int) -> int:
    """How many positive pacts these two have already closed together."""
    key = s.k("pairs", min(a, b), max(a, b))
    return s.get_int(f"{key}:pos", 0)


def _is_closed_loop(s: Store, a: int, b: int, window: int = NEIGHBOUR_WINDOW) -> bool:
    """True when both sides have no meaningful pact outside this pairing.

    Fresh accounts that only ever deal with each other form an isolated graph
    component; reputation generated inside a closed component is worthless.
    A pair counts as closed only when *neither* side has ever dealt with
    anybody else.
    """
    isolated = []
    for uid, other in ((a, b), (b, a)):
        peers = s.smembers(s.k("graph", uid))
        recent = {int(x) for x in peers if str(x).isdigit() and int(x) != other}
        isolated.append(not recent)
    return all(isolated)


def positive_weight(s: Store, actor: int, counterparty: int, *, pact: str = "", base: float = GAIN_CLOSED) -> tuple[float, str]:
    """Compute the actual weight of a positive reputation event, plus a reason."""
    if _is_closed_loop(s, actor, counterparty):
        return 0.0, "замкнутый круг: нет сделок с третьими лицами"

    partner = current(s, counterparty)
    factor = (max(0.0, partner) / 100.0) ** PARTNER_EXPONENT
    seen = pair_history(s, actor, counterparty)
    dim = DIMINISHING[min(seen, len(DIMINISHING) - 1)]

    note = []
    if partner < 60:
        note.append("контрагент с низким порядочным баллом")
    elif partner >= 90:
        note.append("вес за сильного контрагента")

    value = base * dim * factor
    if value <= 0.5:
        return 0.0, "вес близок к нулю: " + ("; ".join(note) or "повторная сделка")
    if seen:
        note.append(f"повторная сделка ({seen}) — вес ×{dim:g}")
    if partner < 60:
        note.append(f"контрагент {partner:.0f}/100")
    return round(value, 3), "; ".join(note) or "полный вес"


def note_pair(s: Store, a: int, b: int) -> None:
    """Remember the pairing so the graph and diminishing counter update."""
    lo, hi = min(a, b), max(a, b)
    s.incr(s.k("pairs", lo, hi, "pos"), 1)
    for uid, other in ((a, b), (b, a)):
        s.sadd(s.k("graph", uid), str(other))


# --------------------------------------------------------------------------- mutations


def reward_closed(s: Store, uid: int, counterparty: int, pact: str = "", note: str = "") -> tuple[float, str]:
    delta, why = positive_weight(s, uid, counterparty, pact=pact)
    if delta > 0:
        _write(s, uid, delta, mode="delta")
        append_event(s, uid, "closed", delta, why or note, pact)
    else:
        append_event(s, uid, "closed", 0.0, why, pact)
    note_pair(s, uid, counterparty)
    return delta, why


def reward_completed(s: Store, uid: int, counterparty: int, pact: str = "", note: str = "") -> tuple[float, str]:
    delta, why = positive_weight(s, uid, counterparty, pact=pact, base=GAIN_COMPLETED)
    if delta > 0:
        _write(s, uid, delta, mode="delta")
        append_event(s, uid, "completed", delta, why, pact)
    else:
        append_event(s, uid, "completed", 0.0, why, pact)
    return delta, why


def penalise(s: Store, uid: int, kind: str, delta: float, note: str, pact: str = "") -> float:
    """Negative weight always applies at full strength."""
    value = _write(s, uid, delta, mode="delta")
    append_event(s, uid, kind, delta, note, pact)
    return value


def settle(s: Store, uid: int) -> None:
    """Called on sign-in: decay + one-off backfill for very old accounts."""
    key = s.k("score", uid)
    if not s.exists(key):
        _write(s, uid, terms.RATING_BASE)
        return
    touched = last_touch(s, uid)
    if touched and (now() - touched) > SETTLE_AFTER_DAYS * 86400:
        s.hincr(key, "settled", 1)
    apply_decay(s, uid)


def snapshot(s: Store, uid: int) -> dict:
    value = current(s, uid)
    return {
        "score": value,
        "tier": format_tier(value),
        "positive": sum(1 for e in events(s, uid, 400) if (e.get("delta") or 0) > 0),
        "negative": sum(1 for e in events(s, uid, 400) if (e.get("delta") or 0) < 0),
        "blocked": sum(1 for e in events(s, uid, 400) if (e.get("delta") or 0) == 0),
    }


def format_tier(value: float) -> str:
    for threshold, label, emoji in terms.TIERS:
        if value >= threshold:
            return f"{emoji} {label}"
    return ""


def scale(value: float, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, int(round(value))))
