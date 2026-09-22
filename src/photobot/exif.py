"""Reading and writing JPEG capture dates without touching the pixel data."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

import piexif

logger = logging.getLogger(__name__)

_JPEG_SUFFIXES = frozenset({".jpg", ".jpeg"})
# EXIF stores dates as "YYYY:MM:DD HH:MM:SS" ASCII, with no time zone.
_EXIF_FORMAT = "%Y:%m:%d %H:%M:%S"


def is_jpeg(path: Path) -> bool:
    """Whether ``path`` looks like a JPEG. Telegram's compressed photos always are."""
    return path.suffix.lower() in _JPEG_SUFFIXES


def has_capture_date(path: Path) -> bool:
    """Whether the file already carries an EXIF ``DateTimeOriginal``.

    A file we cannot parse as EXIF-bearing JPEG counts as having no date: the worst
    outcome is that we ask about a photo we cannot write to, and the write then fails
    loudly instead of silently skipping a photo that did need a date.
    """
    try:
        exif = piexif.load(str(path))
    except (piexif.InvalidImageDataError, ValueError, OSError):
        return False
    return piexif.ExifIFD.DateTimeOriginal in exif.get("Exif", {})


def set_capture_date(path: Path, when: datetime) -> None:
    """Write ``when`` as the capture date of the JPEG at ``path``.

    ``piexif.insert`` rewrites only the EXIF segment, so the compressed pixel data is
    copied through byte for byte. The new file is built next to the original and renamed
    over it, the same atomic pattern as ``storage.save_file``. The file's mtime is set to
    match, so tools that fall back to the filesystem date agree with the EXIF one.
    """
    stamp = when.strftime(_EXIF_FORMAT).encode("ascii")
    exif_bytes = piexif.dump(
        {
            "0th": {piexif.ImageIFD.DateTime: stamp},
            "Exif": {
                piexif.ExifIFD.DateTimeOriginal: stamp,
                piexif.ExifIFD.DateTimeDigitized: stamp,
            },
        }
    )

    partial = path.with_name(path.name + ".part")
    try:
        piexif.insert(exif_bytes, str(path), str(partial))
        partial.replace(path)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise

    ts = when.timestamp()
    os.utime(path, (ts, ts))
    logger.info("Set capture date %s on %s", when.isoformat(sep=" "), path.name)
