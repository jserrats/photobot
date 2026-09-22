"""Configuration loaded from environment variables, validated at startup."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC_ZONE = ZoneInfo("UTC")


class ConfigError(ValueError):
    """Raised when required configuration is missing or malformed."""


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    allowed_chat_id: int
    download_dir: Path
    log_level: int = logging.INFO
    # Local time zone, used only to decide what "today" means in the date picker.
    timezone: ZoneInfo = UTC_ZONE
    # Seconds of quiet after the last photo before the capture-date prompt is sent.
    date_prompt_window: float = 30.0

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

        tz_name = env.get("TZ", "").strip() or "UTC"
        try:
            timezone = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            raise ConfigError(f"TZ {tz_name!r} is not a known time zone name") from None

        raw_window = env.get("DATE_PROMPT_WINDOW_SECONDS", "").strip()
        if raw_window:
            try:
                window = float(raw_window)
            except ValueError:
                raise ConfigError(
                    "DATE_PROMPT_WINDOW_SECONDS must be a number of seconds"
                ) from None
            if window < 0:
                raise ConfigError("DATE_PROMPT_WINDOW_SECONDS must not be negative")
        else:
            window = 30.0

        return cls(
            bot_token=token,
            allowed_chat_id=chat_id,
            download_dir=download_dir,
            log_level=level,
            timezone=timezone,
            date_prompt_window=window,
        )
