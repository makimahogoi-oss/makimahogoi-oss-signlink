"""Redis storage layer.

Redis is the only datastore. Every entity lives in a hash keyed by a prefixed
name; ordered histories are Redis lists; counters are hash fields incremented
atomically. JSON is used for nested values so the schema stays flexible.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Iterable

import redis

from .config import Config, config as default_config


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load(raw: Any, fallback: Any = None) -> Any:
    if raw is None:
        return fallback
    if isinstance(raw, dict):
        out = {}
        for key, value in raw.items():
            if isinstance(key, bytes):
                key = key.decode("utf-8", "replace")
            out[key] = _load(value, value)
        return out
    if isinstance(raw, (list, tuple)):
        return [_load(item, item) for item in raw]
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    if not isinstance(raw, str) or not raw:
        return fallback
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return fallback


def new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


class Store:
    def __init__(self, cfg: Config = None):
        self.cfg = cfg or default_config
        self.p = self.cfg.key_prefix
        self.r = redis.Redis.from_url(
            self.cfg.redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            health_check_interval=30,
        )

    # ---------- plumbing ----------

    def ping(self) -> bool:
        try:
            return bool(self.r.ping())
        except redis.RedisError:
            return False

    def k(self, *parts: Any) -> str:
        return ":".join([self.p, *[str(x) for x in parts]])

    def close(self) -> None:
        try:
            self.r.close()
        except Exception:
            pass

    # ---------- generic hashes ----------

    def hget(self, key: str, field: str, fallback: Any = None) -> Any:
        return _load(self.r.hget(key, field), fallback)

    def hset(self, key: str, mapping: dict) -> None:
        clean = {k: _dump(v) for k, v in mapping.items() if v is not None}
        if clean:
            self.r.hset(key, mapping=clean)

    def hgetall(self, key: str) -> dict:
        return _load(self.r.hgetall(key), {}) or {}

    def hincr(self, key: str, field: str, amount: int = 1) -> int:
        return int(self.r.hincrby(key, field, amount))

    def hdel(self, key: str, *fields: str) -> None:
        if fields:
            self.r.hdel(key, *fields)

    def exists(self, key: str) -> bool:
        return bool(self.r.exists(key))

    # ---------- generic lists ----------

    def rpush(self, key: str, *values: Any, cap: int = 0) -> None:
        if not values:
            return
        pipe = self.r.pipeline()
        pipe.rpush(key, *[_dump(v) for v in values])
        if cap > 0:
            pipe.ltrim(key, -cap, -1)
        pipe.execute()

    def lpush(self, key: str, *values: Any, cap: int = 0) -> None:
        if not values:
            return
        pipe = self.r.pipeline()
        pipe.lpush(key, *[_dump(v) for v in values])
        if cap > 0:
            pipe.ltrim(key, 0, cap - 1)
        pipe.execute()

    def lpush_raw(self, key: str, value: str, cap: int = 0) -> None:
        pipe = self.r.pipeline()
        pipe.lpush(key, value)
        if cap > 0:
            pipe.ltrim(key, 0, cap - 1)
        pipe.execute()

    def lrange(self, key: str, start: int = 0, stop: int = -1) -> list:
        return [_load(x) for x in self.r.lrange(key, start, stop) if x is not None]

    def llen(self, key: str) -> int:
        return int(self.r.llen(key))

    def lrem(self, key: str, value: str, count: int = 0) -> None:
        """Remove list entries by their stored form.

        Lists written through rpush/lpush hold JSON, so the plain value is
        tried first and the encoded one second; either way the entry goes.
        """
        self.r.lrem(key, count, value)
        encoded = _dump(value)
        if encoded != value:
            self.r.lrem(key, count, encoded)

    def sadd(self, key: str, *members: str) -> None:
        if members:
            self.r.sadd(key, *members)

    def srem(self, key: str, *members: str) -> None:
        if members:
            self.r.srem(key, *members)

    def smembers(self, key: str) -> set:
        return set(self.r.smembers(key) or ())

    def scard(self, key: str) -> int:
        return int(self.r.scard(key) or 0)

    def sismember(self, key: str, member: str) -> bool:
        return bool(self.r.sismember(key, member))

    # ---------- counters / misc ----------

    def incr(self, key: str, amount: int = 1) -> int:
        return int(self.r.incrby(key, amount))

    def decr(self, key: str, amount: int = 1) -> int:
        return int(self.r.decrby(key, amount))

    def get_int(self, key: str, fallback: int = 0) -> int:
        raw = self.r.get(key)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return fallback

    def get_str(self, key: str, fallback: str = "") -> str:
        raw = self.r.get(key)
        return raw if isinstance(raw, str) else fallback

    def set_str(self, key: str, value: str, ttl: int = 0) -> None:
        if ttl > 0:
            self.r.setex(key, ttl, value)
        else:
            self.r.set(key, value)

    def getset_str(self, key: str, value: str) -> str:
        return self.r.getset(key, value) or ""

    def set_json(self, key: str, value: Any, ttl: int = 0) -> None:
        self.set_str(key, _dump(value), ttl)

    def get_json(self, key: str, fallback: Any = None) -> Any:
        return _load(self.r.get(key), fallback)

    def delete(self, *keys: str) -> None:
        if keys:
            self.r.delete(*keys)

    def scan_keys(self, pattern: str, count: int = 400) -> Iterable[str]:
        cursor = 0
        while True:
            cursor, batch = self.r.scan(cursor=cursor, match=pattern, count=count)
            yield from batch
            if cursor == 0:
                break


_store: Store | None = None


def store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store


def now() -> int:
    return int(time.time())