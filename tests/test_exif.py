import shutil
from datetime import datetime
from pathlib import Path

import piexif
import pytest

from photobot.exif import has_capture_date, is_jpeg, set_capture_date

FIXTURE = Path(__file__).parent / "fixtures" / "tiny.jpg"
WHEN = datetime(2024, 1, 15, 12, 0, 0)


@pytest.fixture
def photo(download_dir: Path) -> Path:
    target = download_dir / "20240506_070809_AQADuniq.jpg"
    shutil.copy(FIXTURE, target)
    return target


@pytest.mark.parametrize(
    ("name", "expected"),
    [("a.jpg", True), ("a.JPEG", True), ("a.mp4", False), ("a", False)],
)
def test_is_jpeg_goes_by_suffix(name: str, expected: bool) -> None:
    assert is_jpeg(Path(name)) is expected


def test_telegram_photo_has_no_capture_date(photo: Path) -> None:
    assert has_capture_date(photo) is False


def test_set_capture_date_writes_all_three_tags(photo: Path) -> None:
    set_capture_date(photo, WHEN)

    exif = piexif.load(str(photo))
    stamp = b"2024:01:15 12:00:00"
    assert exif["0th"][piexif.ImageIFD.DateTime] == stamp
    assert exif["Exif"][piexif.ExifIFD.DateTimeOriginal] == stamp
    assert exif["Exif"][piexif.ExifIFD.DateTimeDigitized] == stamp
    assert has_capture_date(photo) is True


def test_set_capture_date_leaves_no_partial(photo: Path, download_dir: Path) -> None:
    set_capture_date(photo, WHEN)
    assert list(download_dir.iterdir()) == [photo]


def test_set_capture_date_also_sets_mtime(photo: Path) -> None:
    set_capture_date(photo, WHEN)
    assert datetime.fromtimestamp(photo.stat().st_mtime) == WHEN


def test_set_capture_date_keeps_the_pixels(photo: Path) -> None:
    # piexif rewrites only the metadata segment, so the compressed scan survives intact.
    scan = FIXTURE.read_bytes().split(b"\xff\xda", 1)[1]
    set_capture_date(photo, WHEN)
    assert photo.read_bytes().split(b"\xff\xda", 1)[1] == scan


def test_non_jpeg_data_has_no_date(download_dir: Path) -> None:
    junk = download_dir / "junk.jpg"
    junk.write_bytes(b"definitely not a jpeg")
    assert has_capture_date(junk) is False


def test_set_capture_date_on_junk_cleans_up(download_dir: Path) -> None:
    junk = download_dir / "junk.jpg"
    junk.write_bytes(b"definitely not a jpeg")

    with pytest.raises(piexif.InvalidImageDataError):
        set_capture_date(junk, WHEN)

    assert list(download_dir.iterdir()) == [junk]  # no .part left behind


def test_set_capture_date_on_missing_file_raises(download_dir: Path) -> None:
    with pytest.raises(OSError, match="No such file"):
        set_capture_date(download_dir / "gone.jpg", WHEN)
    assert list(download_dir.iterdir()) == []
