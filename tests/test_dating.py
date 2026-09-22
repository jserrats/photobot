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


async def test_the_picker_appears_immediately_and_once_per_photo(tmp_path: Path) -> None:
    tracker = BatchTracker()
    seen: list[list[str]] = []

    async def on_change(batch: PendingBatch) -> None:
        seen.append([p.name for p in batch.paths])

    loop = asyncio.get_running_loop()
    tracker.add(tmp_path / "a.jpg", window=WINDOW, loop=loop, on_change=on_change)
    await asyncio.sleep(0)  # let the refresh task run
    assert seen == [["a.jpg"]]

    tracker.add(tmp_path / "b.jpg", window=WINDOW, loop=loop, on_change=on_change)
    await asyncio.sleep(0)
    assert seen == [["a.jpg"], ["a.jpg", "b.jpg"]]

    # One batch throughout, registered for callbacks from the very first photo.
    assert tracker.open is not None
    assert list(tracker.pending) == [tracker.open.id]
    tracker.seal(tracker.open)


async def test_a_simultaneous_burst_costs_one_picker(tmp_path: Path) -> None:
    """An album arrives in one go: send one picker for all of it, not three."""
    tracker = BatchTracker()
    order: list[str] = []

    async def on_change(batch: PendingBatch) -> None:
        n = len(batch.paths)
        order.append(f"start{n}")
        await asyncio.sleep(0.01)
        order.append(f"end{n}")

    loop = asyncio.get_running_loop()
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        tracker.add(tmp_path / name, window=WINDOW, loop=loop, on_change=on_change)
    await asyncio.sleep(0.1)

    assert order == ["start3", "end3"]


async def test_a_photo_during_a_send_gets_its_own_refresh(tmp_path: Path) -> None:
    """Never leave the picker showing a stale count."""
    tracker = BatchTracker()
    order: list[str] = []

    async def on_change(batch: PendingBatch) -> None:
        n = len(batch.paths)
        order.append(f"start{n}")
        await asyncio.sleep(0.02)
        order.append(f"end{n}")

    loop = asyncio.get_running_loop()
    tracker.add(tmp_path / "a.jpg", window=WINDOW, loop=loop, on_change=on_change)
    await asyncio.sleep(0.01)  # mid-send
    tracker.add(tmp_path / "b.jpg", window=WINDOW, loop=loop, on_change=on_change)
    await asyncio.sleep(0.1)

    # Serialised, and the second run reports both photos.
    assert order == ["start1", "end1", "start2", "end2"]


async def test_photos_in_a_row_share_one_batch(tmp_path: Path) -> None:
    tracker = BatchTracker()

    async def on_change(batch: PendingBatch) -> None:
        batch.prompt_message_id = 100 + len(batch.paths)

    loop = asyncio.get_running_loop()
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        tracker.add(tmp_path / name, window=WINDOW, loop=loop, on_change=on_change)
        await asyncio.sleep(WINDOW / 4)

    assert len(tracker.pending) == 1
    batch = next(iter(tracker.pending.values()))
    assert [p.name for p in batch.paths] == ["a.jpg", "b.jpg", "c.jpg"]
    assert batch.prompt_message_id == 103  # the newest picker, not the first


async def test_a_photo_after_the_window_starts_a_new_batch(tmp_path: Path) -> None:
    tracker = BatchTracker()

    async def on_change(batch: PendingBatch) -> None:
        return None

    loop = asyncio.get_running_loop()
    tracker.add(tmp_path / "a.jpg", window=WINDOW, loop=loop, on_change=on_change)
    await asyncio.sleep(WINDOW * 3)
    assert tracker.open is None  # sealed, but still waiting for its answer
    assert len(tracker.pending) == 1

    tracker.add(tmp_path / "b.jpg", window=WINDOW, loop=loop, on_change=on_change)
    await asyncio.sleep(WINDOW * 3)

    assert len(tracker.pending) == 2
    first, second = tracker.pending.values()
    assert [p.name for p in first.paths] == ["a.jpg"]
    assert [p.name for p in second.paths] == ["b.jpg"]


async def test_an_answered_batch_does_not_collect_more_photos(tmp_path: Path) -> None:
    tracker = BatchTracker()

    async def on_change(batch: PendingBatch) -> None:
        return None

    loop = asyncio.get_running_loop()
    tracker.add(tmp_path / "a.jpg", window=WINDOW, loop=loop, on_change=on_change)
    await asyncio.sleep(0)
    answered = tracker.pop(next(iter(tracker.pending)))
    assert answered is not None
    assert tracker.open is None

    tracker.add(tmp_path / "b.jpg", window=WINDOW, loop=loop, on_change=on_change)
    await asyncio.sleep(0)

    assert [p.name for p in answered.paths] == ["a.jpg"]
    assert len(tracker.pending) == 1
    assert [p.name for p in next(iter(tracker.pending.values())).paths] == ["b.jpg"]


async def test_a_failing_first_prompt_drops_the_batch(tmp_path: Path) -> None:
    tracker = BatchTracker()

    async def on_change(batch: PendingBatch) -> None:
        raise RuntimeError("telegram is down")

    tracker.add(
        tmp_path / "a.jpg",
        window=WINDOW,
        loop=asyncio.get_running_loop(),
        on_change=on_change,
    )
    await asyncio.sleep(0)

    assert tracker.pending == {}


async def test_a_failing_resend_keeps_the_picker_already_on_screen(tmp_path: Path) -> None:
    tracker = BatchTracker()
    calls = 0

    async def on_change(batch: PendingBatch) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            batch.prompt_message_id = 101
            return
        raise RuntimeError("telegram is down")

    loop = asyncio.get_running_loop()
    tracker.add(tmp_path / "a.jpg", window=WINDOW, loop=loop, on_change=on_change)
    await asyncio.sleep(0)
    tracker.add(tmp_path / "b.jpg", window=WINDOW, loop=loop, on_change=on_change)
    await asyncio.sleep(0)

    # The first picker is still there and still answers for both photos.
    assert len(tracker.pending) == 1
    batch = next(iter(tracker.pending.values()))
    assert batch.prompt_message_id == 101
    assert [p.name for p in batch.paths] == ["a.jpg", "b.jpg"]
    tracker.seal(batch)


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


async def test_seal_stops_collection_without_forgetting_the_batch() -> None:
    tracker = BatchTracker()
    batch = PendingBatch(id="only")
    tracker.open = batch
    tracker.pending = {"only": batch}

    tracker.seal(batch)

    assert tracker.open is None
    assert tracker.get("only") is batch
