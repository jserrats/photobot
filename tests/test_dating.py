import asyncio
from datetime import date, datetime
from pathlib import Path

import pytest

from photobot.dating import (
    BatchTracker,
    PendingBatch,
    build_keyboard,
    capture_datetime,
    new_batch_id,
    parse_action_date,
    parse_callback,
    parse_typed_date,
    skip_only_keyboard,
)

TODAY = date(2024, 9, 22)  # a Sunday
WINDOW = 0.05


def test_keyboard_offers_the_last_five_days_then_other_and_skip() -> None:
    rows = build_keyboard("abc123", TODAY).inline_keyboard

    assert [[b.text for b in row] for row in rows] == [
        ["Today", "Yesterday"],
        ["Fri 20 Sep", "Thu 19 Sep", "Wed 18 Sep"],
        ["Other date…"],
        ["Skip"],
    ]
    assert [[b.callback_data for b in row] for row in rows] == [
        ["d:abc123:2024-09-22", "d:abc123:2024-09-21"],
        ["d:abc123:2024-09-20", "d:abc123:2024-09-19", "d:abc123:2024-09-18"],
        ["d:abc123:other"],
        ["d:abc123:skip"],
    ]


def test_callback_data_fits_telegram_limit() -> None:
    longest = max(
        b.callback_data or ""
        for row in build_keyboard(new_batch_id(), TODAY).inline_keyboard
        for b in row
    )
    assert len(longest.encode()) <= 64


def test_skip_only_keyboard_has_just_skip() -> None:
    rows = skip_only_keyboard("abc123").inline_keyboard
    assert [[b.callback_data for b in row] for row in rows] == [["d:abc123:skip"]]


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ("d:abc123:2024-09-22", ("abc123", "2024-09-22")),
        ("d:a-b_c:other", ("a-b_c", "other")),
        ("d:abc123:skip", ("abc123", "skip")),
    ],
)
def test_parse_callback(data: str, expected: tuple[str, str]) -> None:
    assert parse_callback(data) == expected


@pytest.mark.parametrize("data", ["", "d:", "d:abc", "x:abc:skip", "d:abc:skip:extra", "abc:skip"])
def test_parse_callback_rejects_junk(data: str) -> None:
    with pytest.raises(ValueError, match="malformed"):
        parse_callback(data)


def test_parse_action_date() -> None:
    assert parse_action_date("2024-09-22") == TODAY
    assert parse_action_date("other") is None
    assert parse_action_date("skip") is None


@pytest.mark.parametrize("text", ["2024-09-22", "22/09/2024", "22.09.2024", "  2024-09-22  "])
def test_parse_typed_date_accepts_every_documented_format(text: str) -> None:
    assert parse_typed_date(text) == TODAY


@pytest.mark.parametrize(
    "text", ["", "yesterday", "2024-13-01", "09/22/2024", "22-09-2024", "2024/09/22"]
)
def test_parse_typed_date_rejects_the_rest(text: str) -> None:
    assert parse_typed_date(text) is None


def test_capture_datetime_is_naive_noon() -> None:
    assert capture_datetime(TODAY) == datetime(2024, 9, 22, 12, 0, 0)


def test_batch_ids_are_short_and_unique() -> None:
    ids = {new_batch_id() for _ in range(100)}
    assert len(ids) == 100
    assert all(len(i) == 8 for i in ids)


async def test_adds_within_the_window_share_one_batch(tmp_path: Path) -> None:
    tracker = BatchTracker()
    ready: list[PendingBatch] = []

    async def on_ready(batch: PendingBatch) -> None:
        ready.append(batch)

    loop = asyncio.get_running_loop()
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        tracker.add(tmp_path / name, window=WINDOW, loop=loop, on_ready=on_ready)
        await asyncio.sleep(WINDOW / 4)

    assert ready == []  # still collecting
    await asyncio.sleep(WINDOW * 3)

    assert len(ready) == 1
    assert [p.name for p in ready[0].paths] == ["a.jpg", "b.jpg", "c.jpg"]
    assert tracker.open is None
    assert tracker.pending == {ready[0].id: ready[0]}


async def test_a_later_photo_starts_a_new_batch(tmp_path: Path) -> None:
    tracker = BatchTracker()
    ready: list[PendingBatch] = []

    async def on_ready(batch: PendingBatch) -> None:
        ready.append(batch)

    loop = asyncio.get_running_loop()
    tracker.add(tmp_path / "a.jpg", window=WINDOW, loop=loop, on_ready=on_ready)
    await asyncio.sleep(WINDOW * 3)
    tracker.add(tmp_path / "b.jpg", window=WINDOW, loop=loop, on_ready=on_ready)
    await asyncio.sleep(WINDOW * 3)

    assert len(ready) == 2
    assert ready[0].id != ready[1].id
    assert [p.name for p in ready[0].paths] == ["a.jpg"]
    assert [p.name for p in ready[1].paths] == ["b.jpg"]


async def test_a_failing_prompt_drops_the_batch(tmp_path: Path) -> None:
    tracker = BatchTracker()

    async def on_ready(batch: PendingBatch) -> None:
        raise RuntimeError("telegram is down")

    tracker.add(
        tmp_path / "a.jpg",
        window=WINDOW,
        loop=asyncio.get_running_loop(),
        on_ready=on_ready,
    )
    await asyncio.sleep(WINDOW * 3)

    assert tracker.pending == {}


async def test_lookup_helpers(tmp_path: Path) -> None:
    tracker = BatchTracker()
    first = PendingBatch(id="first", prompt_message_id=10, awaiting_text=True)
    second = PendingBatch(id="second", prompt_message_id=20)
    tracker.pending = {"first": first, "second": second}

    assert tracker.get("first") is first
    assert tracker.get("nope") is None
    assert tracker.by_prompt(20) is second
    assert tracker.by_prompt(99) is None
    assert tracker.awaiting_text() is first

    second.awaiting_text = True
    assert tracker.awaiting_text() is second  # the most recent one wins

    assert tracker.pop("first") is first
    assert tracker.pop("first") is None
