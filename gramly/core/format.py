"""Text helpers shared by the bot renderer and the website."""

from __future__ import annotations

import re
from datetime import datetime, timezone

try:
    import zoneinfo
except ImportError:  # pragma: no cover
    zoneinfo = None

RU_MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
RU_MONTHS_SHORT = [
    "янв", "фев", "мар", "апр", "мая", "июн",
    "июл", "авг", "сен", "окт", "ноя", "дек",
]


def esc(text) -> str:
    """Escape for Telegram HTML parse mode. gramly does not escape for us."""
    s = "" if text is None else str(text)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def dt(ts: int | float | None = None) -> datetime:
    if not ts:
        ts = 0
    return datetime.fromtimestamp(int(ts), tz=timezone.utc)


def fmt_datetime(ts: int | None) -> str:
    """28.08.2026 11:39:57 — matches the Gramly pact card."""
    d = dt(ts)
    return f"{d.day:02d}.{d.month:02d}.{d.year} {d.hour:02d}:{d.minute:02d}:{d.second:02d}"


def fmt_date(ts: int | None) -> str:
    d = dt(ts)
    return f"{d.day:02d}.{d.month:02d}.{d.year}"


def fmt_date_long(ts: int | None) -> str:
    d = dt(ts)
    return f"{d.day} {RU_MONTHS[d.month - 1]} {d.year}"


def fmt_relative(ts: int | None) -> str:
    if not ts:
        return "—"
    import time
    delta = int(time.time()) - int(ts)
    if delta < 60:
        return "только что"
    if delta < 3600:
        return f"{delta // 60} мин назад"
    if delta < 86400:
        return f"{delta // 3600} ч назад"
    if delta < 86400 * 30:
        return f"{delta // 86400} дн назад"
    return fmt_date(ts)


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def username_link(username: str | None) -> str:
    """@nick (https://t.me/nick) in the Gramly house style."""
    if not username:
        return "—"
    return f"@{esc(username)} (https://t.me/{esc(username)})"


def mention(user_id: int, label: str | None = None, username: str | None = None) -> str:
    """A clickable name; text_mention when the id is unknown to the client."""
    text = esc(label or username or str(user_id))
    if username:
        return f'{text} (<a href="tg://user?id={user_id}">https://t.me/{esc(username)}</a>)'
    return f'<a href="tg://user?id={user_id}">{text}</a>'


def full_name(first: str | None, last: str | None = None) -> str:
    return " ".join(x for x in (first, last) if x)


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


def truncate(text: str, limit: int = 120) -> str:
    s = " ".join((text or "").split())
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def bullet_list(items) -> str:
    return "\n".join(f"▪️ {esc(i)}" for i in items if i)


def initials(first: str | None, last: str | None = None) -> str:
    a = (first or "?")[:1]
    b = (last or "")[:1]
    return (a + b).upper() or "?"


def tier_label(score: float) -> str:
    from . import terms
    for threshold, label, _ in terms.TIERS:
        if score >= threshold:
            return label
    return terms.TIERS[-1][1]


def tier_emoji(score: float) -> str:
    from . import terms
    for threshold, _label, emoji in terms.TIERS:
        if score >= threshold:
            return emoji
    return terms.TIERS[-1][2]