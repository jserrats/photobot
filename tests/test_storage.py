from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from helpers import make_file
from photobot.storage import build_filename, ensure_writable_dir, save_file

WHEN = datetime(2024, 5, 6, 7, 8, 9, tzinfo=UTC)


def test_filename_uses_timestamp_unique_id_and_extension() -> None:
    assert build_filename(make_file(), WHEN) == "20240506_070809_AQADuniq.jpg"


@pytest.mark.parametrize(
    "remote_path",
    ["../../etc/passwd", "photos/../x.jpg/..", "a/b", None, "noext", "x." + "y" * 20],
)
def test_filename_never_trusts_remote_path(remote_path: str | None) -> None:
    name = build_filename(make_file(file_path=remote_path), WHEN)
    assert "/" not in name
    assert ".." not in name
    assert name.startswith("20240506_070809_AQADuniq")


def test_filename_sanitizes_unique_id() -> None:
    evil = make_file(file_unique_id="../evil id")
    assert build_filename(evil, WHEN) == "20240506_070809_evilid.jpg"
    assert build_filename(make_file(file_unique_id="..."), WHEN) == "20240506_070809_file.jpg"


async def test_save_file_writes_atomically(download_dir: Path) -> None:
    file = make_file(payload=b"hello")
    path = await save_file(file, download_dir, WHEN)
    assert path == download_dir / "20240506_070809_AQADuniq.jpg"
    assert path.read_bytes() == b"hello"
    assert list(download_dir.iterdir()) == [path]  # no .part left behind


async def test_save_file_cleans_up_partial_on_failure(download_dir: Path) -> None:
    file = make_file()

    async def boom(custom_path: Path) -> Path:
        Path(custom_path).write_bytes(b"trunc")
        raise OSError("network")

    file.download_to_drive = AsyncMock(side_effect=boom)
    with pytest.raises(OSError, match="network"):
        await save_file(file, download_dir, WHEN)
    assert list(download_dir.iterdir()) == []


async def test_save_file_does_not_overwrite_existing(download_dir: Path) -> None:
    existing = download_dir / "20240506_070809_AQADuniq.jpg"
    existing.write_bytes(b"original")
    file = make_file(payload=b"new")
    path = await save_file(file, download_dir, WHEN)
    assert path == existing
    assert existing.read_bytes() == b"original"
    file.download_to_drive.assert_not_awaited()


def test_ensure_writable_dir_creates_nested(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b"
    ensure_writable_dir(target)
    assert target.is_dir()


def test_ensure_writable_dir_rejects_readonly(tmp_path: Path) -> None:
    target = tmp_path / "ro"
    target.mkdir(mode=0o500)
    try:
        with pytest.raises(PermissionError):
            ensure_writable_dir(target)
    finally:
        target.chmod(0o700)
