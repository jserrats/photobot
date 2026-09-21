from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock


@dataclass
class FakeFile:
    """Duck-typed stand-in for telegram.File (which is frozen and cannot be mocked)."""

    file_unique_id: str = "AQADuniq"
    file_path: str | None = "photos/file_12.jpg"
    file_size: int | None = 4
    download_to_drive: AsyncMock = field(default_factory=AsyncMock)


def make_file(
    file_unique_id: str = "AQADuniq",
    file_path: str | None = "photos/file_12.jpg",
    payload: bytes = b"data",
) -> Any:
    """A fake telegram.File whose download writes ``payload`` to the requested path."""

    async def fake_download(custom_path: Path) -> Path:
        Path(custom_path).write_bytes(payload)
        return Path(custom_path)

    return FakeFile(
        file_unique_id=file_unique_id,
        file_path=file_path,
        download_to_drive=AsyncMock(side_effect=fake_download),
    )


def make_message(*, photo: bool = False, video: bool = False) -> MagicMock:
    message = MagicMock()
    message.photo = [MagicMock(file_id="small"), MagicMock(file_id="large")] if photo else []
    message.video = MagicMock(file_id="vid") if video else None
    message.reply_text = AsyncMock()
    return message
