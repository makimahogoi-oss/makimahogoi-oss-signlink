"""Environment configuration shared by the bot and the website."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv() -> None:
    """Read a plain KEY=VALUE .env file so `cp .env.example .env` works.

    No external dependency: only fills variables that are not already set in
    the real environment, so systemd/Docker settings always win.
    """
    for path in (Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"):
        try:
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if key and key not in os.environ:
                    os.environ[key] = value.strip().strip("'\"")
            return
        except OSError:
            continue


_load_dotenv()


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _int(name: str, default: int) -> int:
    raw = _env(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


@dataclass
class Config:
    bot_token: str = field(default_factory=lambda: _env("GRAMLY_BOT_TOKEN") or _env("BOT_TOKEN"))
    redis_url: str = field(default_factory=lambda: _env("REDIS_URL", "redis://127.0.0.1:6379/0"))
    key_prefix: str = field(default_factory=lambda: _env("GRAMLY_PREFIX", "g"))

    host: str = field(default_factory=lambda: _env("GRAMLY_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _int("GRAMLY_PORT", 8080))
    public_url: str = field(default_factory=lambda: _env("GRAMLY_PUBLIC_URL", "http://127.0.0.1:8080"))

    bot_username: str = field(default_factory=lambda: _env("GRAMLY_BOT_USERNAME"))
    secret_key: str = field(default_factory=lambda: _env("GRAMLY_SECRET_KEY", "dev-secret"))
    debug: bool = field(default_factory=lambda: _env("GRAMLY_DEBUG").lower() in {"1", "true", "yes"})

    # Comma separated Telegram user ids that may run the moderation panel.
    admin_ids: tuple = field(default_factory=lambda: tuple(
        int(x) for x in _env("GRAMLY_ADMIN_IDS").split(",") if x.strip().isdigit()
    ))

    # Lifetime of a web session created from Mini App initData, seconds.
    web_session_ttl: int = field(default_factory=lambda: _int("GRAMLY_WEB_SESSION_TTL", 86400))

    @property
    def is_admin_panel(self) -> bool:
        return bool(self.admin_ids)


config = Config()