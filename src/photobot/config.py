"""Configuration loaded from environment variables, validated at startup."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class ConfigError(ValueError):
    """Raised when required configuration is missing or malformed."""


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    allowed_chat_id: int
    download_dir: Path
    log_level: int = logging.INFO

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env

        token = env.get("BOT_TOKEN", "").strip()
        if not token:
            raise ConfigError("BOT_TOKEN is not set")

        raw_chat_id = env.get("DEVELOPER_CHAT_ID", "").strip()
        if not raw_chat_id:
            raise ConfigError("DEVELOPER_CHAT_ID is not set")
        try:
            chat_id = int(raw_chat_id)
        except ValueError:
            raise ConfigError("DEVELOPER_CHAT_ID must be an integer Telegram chat id") from None

        download_dir = Path(env.get("DOWNLOAD_DIR", "/files"))

        level_name = env.get("LOG_LEVEL", "INFO").strip().upper()
        level = logging.getLevelNamesMapping().get(level_name)
        if level is None:
            raise ConfigError(f"LOG_LEVEL {level_name!r} is not a valid logging level")

        return cls(
            bot_token=token,
            allowed_chat_id=chat_id,
            download_dir=download_dir,
            log_level=level,
        )
