"""Guarantor programme.

A guarantor vouches for a pact and earns Stars. Early in a guarantor's career
the cases are free (that is how you build a track record); past a monthly quota
both sides pay a Stars fee, which is what makes it a job rather than a favour.
"""

from __future__ import annotations

from . import format as fmt
from . import rating, terms, users
from .store import Store, now

TIER_NAMES = {
    0: "Не гарант",
    1: "Гарант",
    2: "Опытный гарант",
    3: "Старший гарант",
    4: "Главный гарант",
    5: "Страж",
}

TIER_EMOJI = {
    0: "⚪",
    1: "🛡",
    2: "🛡🛡",
    3: "⚔️",
    4: "🏛",
    5: "👑",
}

TIER_PERKS = {
    1: "Бесплатные кейсы: 1 в месяц",
    2: "Бесплатные кейсы: 3 в месяц, приоритет в выборе",
    3: "Бесплатные кейсы: 6 в месяц, Stars ×1.15",
    4: "Бесплатные кейсы: 12 в месяц, Stars ×1.35",
    5: "Бесплатные кейсы: без лимита, Stars ×1.6",
}

TIER_MULTIPLIER = {0: 1.0, 1: 1.0, 2: 1.0, 3: 1.15, 4: 1.35, 5: 1.6}


def tier(s: Store, uid: int) -> int:
    return users.guarantor_tier(s, uid)


def tier_info(s: Store, uid: int) -> dict:
    t = tier(s, uid)
    rec = users.get(s, uid)
    month = now() // 2592000
    used = int(s.get_int(s.k("gr", uid, "used", str(month)), 0))
    free = free_quota(t)
    return {
        "tier": t,
        "name": TIER_NAMES.get(t, TIER_NAMES[0]),
        "emoji": TIER_EMOJI.get(t, "⚪"),
        "cases": int(rec.get("guarantor_cases", 0) or 0),
        "settled": int(rec.get("guarantor_settled", 0) or 0),
        "disputes": int(rec.get("guarantor_disputes", 0) or 0),
        "free_quota": free,
        "free_used": used,
        "free_left": max(0, free - used),
        "perks": TIER_PERKS.get(t, "Начните с одного кейса, чтобы получить статус"),
        "multiplier": TIER_MULTIPLIER.get(t, 1.0),
        "stars": users.stars(s, uid),
        "score": rating.current(s, uid),
    }


def free_quota(t: int) -> int:
    return {0: 0, 1: 1, 2: 3, 3: 6, 4: 12, 5: 999}.get(t, 0)


def month_key() -> str:
    return str(now() // 2592000)


def consume_free(s: Store, uid: int) -> bool:
    """Try to spend one free slot. False means a Stars fee is required."""
    t = tier(s, uid)
    if t < 1:
        return False
    mk = month_key()
    k = s.k("gr", uid, "used", mk)
    used = s.get_int(k, 0)
    if used >= free_quota(t):
        return False
    s.set_str(k, str(used + 1), ttl=2678400)
    return True


def refund_free(s: Store, uid: int) -> None:
    t = tier(s, uid)
    if t < 1:
        return
    k = s.k("gr", uid, "used", month_key())
    used = s.get_int(k, 0)
    if used > 0:
        s.set_str(k, str(used - 1), ttl=2678400)


def fee_for(s: Store, uid: int) -> int:
    """Stars the guarantor earns on the next paid case."""
    info = tier_info(s, uid)
    return int(round(terms.STAR_FEE_PER_SIDE * 2 * info["multiplier"]))


def quote(s: Store, guarantor: int) -> dict:
    """What a pact costs with this guarantor right now."""
    t = tier(s, guarantor)
    free = free_quota(t) - int(s.get_int(s.k("gr", guarantor, "used", month_key()), 0))
    if t >= 1 and free > 0:
        return {"free": True, "side_stars": 0, "guarantor_stars": 0, "left": free}
    side = terms.STAR_FEE_PER_SIDE
    payout = int(round(side * 2 * TIER_MULTIPLIER.get(t, 1.0)))
    return {"free": False, "side_stars": side, "guarantor_stars": payout, "left": max(0, free)}


def is_guarantor(s: Store, uid: int) -> bool:
    return tier(s, uid) >= 1


def apply_application(s: Store, uid: int) -> tuple[bool, str]:
    """Accept a guarantor application; requires a clean, active profile."""
    rec = users.get(s, uid)
    score = rating.current(s, uid)
    if int(rec.get("pacts_closed", 0) or 0) < 1:
        return False, "нужна хотя бы одна исполненная договорённость"
    if int(rec.get("pacts_broken", 0) or 0) > 0:
        return False, "нарушенные договорённости закрывают дорогу к гаранту"
    if score < 65:
        return False, f"нужен балл порядочности от 65 (сейчас {score:.0f})"
    users.update(s, uid, guarantor_tier=1)
    return True, "статус гаранта присвоен"


def settle_case(s: Store, uid: int, good: bool = True) -> int:
    rec = users.get(s, uid)
    if good:
        rec["guarantor_settled"] = int(rec.get("guarantor_settled", 0) or 0) + 1
    else:
        rec["guarantor_disputes"] = int(rec.get("guarantor_disputes", 0) or 0) + 1
    s.hset(users.key(s, uid), rec)
    new_tier = users.bump_guarantor(s, uid)
    payout = int(round(terms.STAR_FEE_PER_SIDE * 2 * TIER_MULTIPLIER.get(new_tier, 1.0))) if good else 0
    if payout:
        users.add_stars(s, uid, payout, "за кейс гаранта")
    return new_tier


def portfolio(s: Store, uid: int, limit: int = 30) -> list:
    """Pacts this person vouches for."""
    out = []
    from . import pacts
    for pid in s.lrange(s.k("gr", uid, "cases"), 0, limit - 1):
        pact = pacts.get(s, pid)
        if pact:
            out.append(pact)
    return out


def add_case(s: Store, uid: int, pid: str) -> None:
    s.rpush(s.k("gr", uid, "cases"), pid, cap=200)
    rec = users.get(s, uid)
    users.update(s, uid, guarantor_cases=int(rec.get("guarantor_cases", 0) or 0) + 1)
    if int(users.guarantor_tier(s, uid)) < 1:
        users.update(s, uid, guarantor_tier=1)


def leaderboard(s: Store, limit: int = 20) -> list:
    rows = []
    prefix = s.k("u", "")
    for k in s.scan_keys(s.k("u", "*")):
        suffix = k[len(prefix):] if k.startswith(prefix) else ""
        if not suffix.isdigit():
            continue
        rec = s.hgetall(k) or {}
        t = int(rec.get("guarantor_tier", 0) or 0)
        if t < 1:
            continue
        rows.append({
            "uid": int(rec.get("id", 0) or 0),
            "name": users.display(rec),
            "username": rec.get("username", ""),
            "photo_url": rec.get("photo_url", ""),
            "tier": t,
            "emoji": TIER_EMOJI.get(t, "?"),
            "tier_name": TIER_NAMES.get(t, TIER_NAMES[1]),
            "cases": int(rec.get("guarantor_cases", 0) or 0),
            "settled": int(rec.get("guarantor_settled", 0) or 0),
            "disputes": int(rec.get("guarantor_disputes", 0) or 0),
            "score": rating.current(s, int(rec.get("id", 0) or 0)),
        })
    rows.sort(key=lambda r: (r["tier"], r["settled"], r["score"]), reverse=True)
    return rows[:limit]


def summary_line(s: Store, uid: int) -> str:
    info = tier_info(s, uid)
    if info["tier"] < 1:
        return "Вы пока не гарант"
    left = info["free_left"]
    free_part = f"бесплатных кейсов осталось: {left}" if info["free_quota"] < 900 else "безлимит"
    return (
        f"{info['emoji']} {info['name']}\n"
        f"Кейсов: {info['cases']} · Исполнено: {info['settled']} · Споров: {info['disputes']}\n"
        f"{free_part}"
    )