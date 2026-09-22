import logging
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from photobot.config import ConfigError, Settings

VALID = {"BOT_TOKEN": "123:abc", "DEVELOPER_CHAT_ID": "42"}


def test_loads_valid_environment() -> None:
    s = Settings.from_env(
        {
            **VALID,
            "DOWNLOAD_DIR": "/tmp/x",
            "LOG_LEVEL": "debug",
            "TZ": "Europe/Madrid",
            "DATE_PROMPT_WINDOW_SECONDS": "5",
        }
    )
    assert s.bot_token == "123:abc"
    assert s.allowed_chat_id == 42
    assert s.download_dir == Path("/tmp/x")
    assert s.log_level == logging.DEBUG
    assert s.timezone == ZoneInfo("Europe/Madrid")
    assert s.date_prompt_window == 5.0


def test_defaults() -> None:
    s = Settings.from_env(VALID)
    assert s.download_dir == Path("/files")
    assert s.log_level == logging.INFO
    assert s.timezone == ZoneInfo("UTC")
    assert s.date_prompt_window == 30.0


def test_a_zero_window_prompts_immediately() -> None:
    assert Settings.from_env({**VALID, "DATE_PROMPT_WINDOW_SECONDS": "0"}).date_prompt_window == 0


@pytest.mark.parametrize(
    ("env", "fragment"),
    [
        ({"DEVELOPER_CHAT_ID": "42"}, "BOT_TOKEN"),
        ({"BOT_TOKEN": "  ", "DEVELOPER_CHAT_ID": "42"}, "BOT_TOKEN"),
        ({"BOT_TOKEN": "t"}, "DEVELOPER_CHAT_ID"),
        ({"BOT_TOKEN": "t", "DEVELOPER_CHAT_ID": "me"}, "integer"),
        ({**VALID, "LOG_LEVEL": "LOUD"}, "LOG_LEVEL"),
        ({**VALID, "TZ": "Mars/Olympus"}, "TZ"),
        ({**VALID, "TZ": "../../etc/passwd"}, "TZ"),
        ({**VALID, "DATE_PROMPT_WINDOW_SECONDS": "soon"}, "DATE_PROMPT_WINDOW_SECONDS"),
        ({**VALID, "DATE_PROMPT_WINDOW_SECONDS": "-1"}, "negative"),
    ],
)
def test_rejects_bad_environment(env: dict[str, str], fragment: str) -> None:
    with pytest.raises(ConfigError, match=fragment):
        Settings.from_env(env)


def test_negative_group_chat_id_is_allowed() -> None:
    assert Settings.from_env({**VALID, "DEVELOPER_CHAT_ID": "-1001"}).allowed_chat_id == -1001
