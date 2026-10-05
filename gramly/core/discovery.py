"""Public deal board: search, filters and light ranking for the website."""

from __future__ import annotations

from . import pacts, rating, terms, users
from .store import Store

BOARD_CAP = 400


def board(s: Store, kind: str = "", status: str = "", query: str = "", limit: int = 20) -> list:
    rows = []
    for pid in s.lrange(s.k("pact", "all"), -BOARD_CAP, -1):
        pub = pacts.public(s, pid)
        if not pub:
            continue
        if kind and pub.get("kind") != kind:
            continue
        if status and pub.get("status") != status:
            continue
        if query:
            hay = f"{pub.get('title', '')} {pub.get('a', '')} {pub.get('b', '')}".lower()
            if query.lower() not in hay:
                continue
        rows.append(pub)
    rows.sort(key=lambda r: r.get("created_at", 0), reverse=True)
    return rows[:limit]


def open_invites(s: Store, limit: int = 20) -> list:
    out = []
    for pid in s.lrange(s.k("pact", "all"), -BOARD_CAP, -1):
        pact = pacts.get(s, pid)
        if not pact or pact.get("status") != "draft":
            continue
        if not pact.get("side_b"):
            continue
        out.append(pact)
        if len(out) >= limit:
            break
    return list(reversed(out))


def counters(s: Store) -> dict:
    st = pacts.stats(s)
    st["kind_partner"] = count_kind(s, "partner")
    st["kind_pr"] = count_kind(s, "pr")
    st["kind_exchange"] = count_kind(s, "exchange")
    return st


def count_kind(s: Store, kind: str) -> int:
    n = 0
    for pid in s.lrange(s.k("pact", "all"), -BOARD_CAP, -1):
        if s.hget(pacts.key(s, pid), "kind", "") == kind:
            n += 1
    return n


def trending(s: Store, limit: int = 6) -> list:
    rows = []
    for k in s.scan_keys(s.k("pub", "*")):
        pub = s.hgetall(k) or {}
        if not pub:
            continue
        if pub.get("status") in ("done", "broken", "cancelled"):
            continue
        rows.append(pub)
    rows.sort(key=lambda r: r.get("created_at", 0), reverse=True)
    return rows[:limit]


def top_people(s: Store, limit: int = 8) -> list:
    rows = users.rank(s, limit * 3)
    return rows[:limit]


def top_guarantors(s: Store, limit: int = 8) -> list:
    from . import guarantors
    return guarantors.leaderboard(s, limit)