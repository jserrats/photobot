"""Saving Telegram files to disk with safe names and atomic writes."""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from telegram import File

logger = logging.getLogger(__name__)

# Keep only conservative characters in anything derived from remote input.
_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")
_MAX_EXT_LEN = 8


def _safe_suffix(file_path: str | None) -> str:
    """Return a sanitized extension (with leading dot) from a Telegram ``file_path``."""
    if not file_path:
        return ""
    suffix = Path(file_path).suffix.lower()
    if not suffix or len(suffix) > _MAX_EXT_LEN:
        return ""
    cleaned = _SAFE_CHARS.sub("", suffix.lstrip("."))
    return f".{cleaned}" if cleaned else ""


def build_filename(file: File, when: datetime | None = None) -> str:
    """Build a unique, filesystem-safe filename for a Telegram file.

    Uses the message timestamp for sortability and Telegram's ``file_unique_id``
    to avoid collisions. Never trusts the remote ``file_path`` beyond its extension.
    """
    when = when or datetime.now(UTC)
    stamp = when.astimezone(UTC).strftime("%Y%m%d_%H%M%S")
    unique = _SAFE_CHARS.sub("", file.file_unique_id).strip(".") or "file"
    return f"{stamp}_{unique}{_safe_suffix(file.file_path)}"


def ensure_writable_dir(path: Path) -> None:
    """Create ``path`` if needed and verify the process can write to it."""
    path.mkdir(parents=True, exist_ok=True)
    if not os.access(path, os.W_OK | os.X_OK):
        raise PermissionError(f"download directory {path} is not writable")


async def save_file(file: File, directory: Path, when: datetime | None = None) -> Path:
    """Download ``file`` into ``directory`` atomically and return the final path.

    The download goes to a ``.part`` file first and is renamed only once complete,
    so a crash mid-transfer never leaves a truncated file that looks finished.
    If the target already exists it is left untouched.
    """
    target = directory / build_filename(file, when)
    if target.exists():
        logger.info("Skipping %s: already exists", target.name)
        return target

    partial = target.with_name(target.name + ".part")
    try:
        await file.download_to_drive(partial)
        partial.replace(target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise

    logger.info("Saved %s (%s bytes)", target.name, file.file_size)
    return target
