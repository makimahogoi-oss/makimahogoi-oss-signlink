"""Check that the configured Redis is reachable and usable.

Run: python tools/check_redis.py

Reads REDIS_URL from .env (or the environment), never prints the password, and
proves the exact operations Gramly uses: hash, list, set, counter, TTL.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gramly.core.store import now, store  # noqa: E402
from gramly.core import terms  # noqa: E402


def masked(url: str) -> str:
    if "@" in url:
        head, _, _ = url.partition("@")
        creds, _, host = head.rpartition("//")
        user = creds.split(":", 1)[0]
        return f"{creds.split(':', 1)[0].replace(user, user)}:***@{host}"
    return url


def main() -> int:
    from gramly.core.config import config

    url = config.redis_url
    print("REDIS_URL =", masked(url))
    p = urlparse(url)
    if p.scheme == "redis" and p.hostname in ("127.0.0.1", "localhost"):
        print("  note: this is a local Redis - it only works on your PC.")

    s = store()
    t0 = time.time()
    ok = s.ping()
    if not ok:
        print(f"PING FAILED after {(time.time() - t0) * 1000:.0f} ms")
        print("\nChecklist:")
        print("  * host, port, user and password copied from the Upstash console")
        print("  * TLS on  -> rediss://  (TLS off -> redis://)")
        print("  * a local URL only works on the machine where Redis runs")
        return 1
    print(f"PING ok in {(time.time() - t0) * 1000:.0f} ms")

    probe = s.k("probe", "check")
    try:
        s.hset(probe, {"ts": now(), "app": terms.APP_NAME})
        got = s.hgetall(probe)
        s.set_json(s.k("probe", "json"), {"a": [1, 2, 3]}, ttl=60)
        back = s.get_json(s.k("probe", "json"), {})
        s.rpush(s.k("probe", "list"), "x", "y")
        listed = s.lrange(s.k("probe", "list"), 0, -1)
        s.sadd(s.k("probe", "set"), "a", "b")
        members = sorted(s.smembers(s.k("probe", "set")))
        n = s.incr(s.k("probe", "counter"), 1)
        print("HASH   ->", got)
        print("JSON   ->", back)
        print("LIST   ->", listed)
        print("SET    ->", members)
        print("COUNTER->", n)
        print(f"\nOK - {terms.APP_NAME} can use this database.")
        print("Keys are prefixed with", s.k("__") .replace(":__", ":"),
              "- look for them in the Upstash console.")
        return 0
    except Exception as exc:  # noqa: BLE001
        print("FAIL write:", type(exc).__name__, exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())