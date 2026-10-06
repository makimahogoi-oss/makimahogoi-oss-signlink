"""Mini App initData validation.

Telegram signs initData with HMAC-SHA256 keyed by HMAC-SHA256 of the bot token
under the constant "WebAppData". Validation fails closed, the signature is
compared in constant time, and auth_date is checked for staleness.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from ..core.config import config

MAX_AGE = 86400
MAX_SKEW = 60


def parse(raw: str) -> dict:
    if not raw:
        return {}
    return dict(parse_qsl(raw, keep_blank_values=True))


def _data_check_string(params: dict, drop: tuple = ()) -> str:
    return "\n".join(
        f"{k}={v}" for k, v in sorted(params.items()) if k not in drop
    )


def validate(raw: str, bot_token: str = "", max_age: int = MAX_AGE) -> tuple[bool, dict, str]:
    """Returns (ok, payload, reason). Never trusts unsigned input."""
    bot_token = bot_token or config.bot_token
    if not raw:
        return False, {}, "нет initData"
    if not bot_token:
        return False, {}, "не настроен токен бота"

    params = parse(raw)
    received = params.pop("hash", "")
    if not received:
        return False, params, "нет подписи hash"

    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()

    # Telegram documents the check string as every received field except hash.
    # Some builds also ship an Ed25519 "signature" field, which is not part of
    # the HMAC input, so both variants are accepted - neither can be forged
    # without the bot token.
    candidates = [_data_check_string(params)]
    if "signature" in params:
        candidates.append(_data_check_string(params, drop=("signature",)))

    matched = False
    for check in candidates:
        expect = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(expect, received):
            matched = True
            break
    if not matched:
        return False, params, "подпись не совпадает"

    raw_date = params.get("auth_date", "")
    if not raw_date:
        return False, params, "нет auth_date"
    try:
        auth_date = int(raw_date)
    except (TypeError, ValueError):
        return False, params, "auth_date не число"
    age = int(time.time()) - auth_date
    if age < -MAX_SKEW:
        return False, params, "auth_date из будущего"
    if max_age and age > max_age:
        return False, params, "данные устарели"

    out = dict(params)
    for field in ("user", "receiver", "chat"):
        raw_val = out.get(field)
        if raw_val:
            try:
                out[field] = json.loads(raw_val)
            except (ValueError, TypeError):
                out[field] = {}
    return True, out, ""


def user_from(payload: dict) -> dict | None:
    u = payload.get("user") or {}
    if not u.get("id"):
        return None
    return {
        "id": int(u["id"]),
        "first_name": u.get("first_name") or "",
        "last_name": u.get("last_name") or "",
        "username": u.get("username") or "",
        "language_code": u.get("language_code") or "",
        "is_premium": bool(u.get("is_premium")),
        "photo_url": u.get("photo_url") or "",
    }


def login(raw: str, max_age: int = MAX_AGE) -> tuple[bool, dict, str]:
    ok, payload, reason = validate(raw, max_age=max_age)
    if not ok:
        return False, {}, reason
    user = user_from(payload)
    if not user:
        return False, {}, "в initData нет пользователя"
    return True, {"user": user, "start_param": payload.get("start_param", ""),
                  "chat_instance": payload.get("chat_instance", "")}, ""