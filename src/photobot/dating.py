"""Grouping freshly saved photos into one batch and asking the user when they were taken.

Telegram strips EXIF from photos sent "compressed", so the bot asks for the capture date
instead of guessing. Photos that arrive together (an album, or a burst of sends) share one
question: the picker appears as soon as the first photo lands and is deleted and resent on
every later one, so it stays at the bottom of the chat and its count stays honest.

A batch stops accepting photos once it has been quiet for the batching window, or as soon
as the user starts answering it. The next photo then opens a batch of its own.

Batches live in memory. A restart forgets any unanswered prompt; the files are already on
disk with the date they were received, so nothing is lost.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "d"
OTHER_ACTION = "other"
SKIP_ACTION = "skip"

# Days offered as their own buttons, after Today and Yesterday.
_EXTRA_DAYS = (2, 3, 4)

_CALLBACK_RE = re.compile(rf"^{CALLBACK_PREFIX}:(?P<batch>[A-Za-z0-9_-]+):(?P<action>[\w-]+)$")

# Formats accepted when the user types a date instead of tapping a button.
_TYPED_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y")
TYPED_FORMATS_HELP = "YYYY-MM-DD, DD/MM/YYYY or DD.MM.YYYY"

# EXIF has no time zone, so a date alone needs an arbitrary but sensible time of day.
CAPTURE_TIME = time(12, 0, 0)


@dataclass
class PendingBatch:
    """Photos that will share one capture date."""

    id: str
    paths: list[Path] = field(default_factory=list)
    last_added: float = 0.0
    # Message carrying the question, so we can edit it once answered.
    prompt_message_id: int | None = None
    # Set once the user picked "Other date…" and we expect a typed reply.
    awaiting_text: bool = False
    # Fires when the batch has been quiet long enough to stop accepting photos.
    timer: asyncio.TimerHandle | None = None
    # Serialises the delete-and-resend of the prompt, so a burst of photos cannot
    # interleave two refreshes and strand the picker halfway up the chat.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # A refresh is queued but has not started reading ``paths`` yet, so photos arriving
    # now will be counted by it and need no refresh of their own.
    refreshing: bool = False


class BatchTracker:
    """The one batch still collecting photos, plus every batch awaiting an answer."""

    def __init__(self) -> None:
        self.open: PendingBatch | None = None
        self.pending: dict[str, PendingBatch] = {}
        # Strong references to in-flight refreshes; asyncio only holds weak ones.
        self._tasks: set[asyncio.Task[None]] = set()

    def add(
        self,
        path: Path,
        *,
        window: float,
        loop: asyncio.AbstractEventLoop,
        on_change: Callable[[PendingBatch], Awaitable[None]],
    ) -> PendingBatch:
        """Add ``path`` to the open batch, opening one if needed, and refresh its prompt.

        ``on_change`` is awaited straight away — the user sees the picker right after the
        photo. It runs again for every later photo of the same batch, which is what keeps
        the picker at the bottom of the chat. Photos that land before a queued refresh has
        started share it rather than each triggering a send of their own.
        """
        batch = self.open
        if batch is None:
            batch = PendingBatch(id=new_batch_id())
            self.open = batch
            # Registered before the prompt exists so a tap on a stale picker from an
            # earlier batch can never be mistaken for this one.
            self.pending[batch.id] = batch
        elif batch.timer is not None:
            batch.timer.cancel()

        batch.paths.append(path)
        batch.last_added = loop.time()
        batch.timer = loop.call_later(window, self.seal, batch)
        if not batch.refreshing:
            batch.refreshing = True
            self._spawn(loop, self._refresh(batch, on_change))
        return batch

    def _spawn(self, loop: asyncio.AbstractEventLoop, coro: Coroutine[Any, Any, None]) -> None:
        task = loop.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _refresh(
        self,
        batch: PendingBatch,
        on_change: Callable[[PendingBatch], Awaitable[None]],
    ) -> None:
        async with batch.lock:
            # Cleared before the send, not after: a photo arriving while the picker is
            # in flight must queue another refresh, or its count is never shown.
            batch.refreshing = False
            if batch.id not in self.pending:
                # Answered in the moment between the photo landing and this running.
                logger.warning(
                    "Batch %s was answered before %d photo(s) could join it",
                    batch.id,
                    len(batch.paths),
                )
                return
            try:
                await on_change(batch)
            except Exception:
                logger.exception("Could not show the capture-date picker for batch %s", batch.id)
                if batch.prompt_message_id is None:
                    # No picker on screen means nothing can answer this batch.
                    self.pop(batch.id)

    def seal(self, batch: PendingBatch) -> None:
        """Stop letting new photos join ``batch``; it keeps waiting for its answer."""
        if batch.timer is not None:
            batch.timer.cancel()
            batch.timer = None
        if self.open is batch:
            self.open = None

    def get(self, batch_id: str) -> PendingBatch | None:
        return self.pending.get(batch_id)

    def pop(self, batch_id: str) -> PendingBatch | None:
        batch = self.pending.pop(batch_id, None)
        if batch is not None:
            self.seal(batch)
        return batch

    def awaiting_text(self) -> PendingBatch | None:
        """The most recent batch whose user chose "Other date…"."""
        for batch in reversed(self.pending.values()):
            if batch.awaiting_text:
                return batch
        return None

    def by_prompt(self, message_id: int) -> PendingBatch | None:
        """The batch whose question is the message with ``message_id``."""
        for batch in reversed(self.pending.values()):
            if batch.prompt_message_id == message_id:
                return batch
        return None


def new_batch_id() -> str:
    """A short id that fits comfortably inside Telegram's 64-byte ``callback_data``."""
    return secrets.token_urlsafe(6)[:8]


def _label(day: date) -> str:
    return f"{day:%a} {day.day} {day:%b}"


def _button(batch_id: str, text: str, action: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=f"{CALLBACK_PREFIX}:{batch_id}:{action}")


def build_keyboard(batch_id: str, today: date) -> InlineKeyboardMarkup:
    """The date picker: the last few days, a typed-date escape hatch, and Skip."""
    rows = [
        [
            _button(batch_id, "Today", today.isoformat()),
            _button(batch_id, "Yesterday", (today - timedelta(days=1)).isoformat()),
        ],
        [
            _button(batch_id, _label(day), day.isoformat())
            for day in (today - timedelta(days=n) for n in _EXTRA_DAYS)
        ],
        [_button(batch_id, "Other date…", OTHER_ACTION)],
        [_button(batch_id, "Skip", SKIP_ACTION)],
    ]
    return InlineKeyboardMarkup(rows)


def skip_only_keyboard(batch_id: str) -> InlineKeyboardMarkup:
    """What is left on screen while we wait for a typed date."""
    return InlineKeyboardMarkup([[_button(batch_id, "Skip", SKIP_ACTION)]])


def parse_callback(data: str) -> tuple[str, str]:
    """Split ``d:<batch>:<action>`` into its batch id and action.

    The action is an ISO date, ``other`` or ``skip``.
    """
    match = _CALLBACK_RE.match(data)
    if match is None:
        raise ValueError(f"malformed callback data {data!r}")
    return match["batch"], match["action"]


def parse_action_date(action: str) -> date | None:
    """The date an action names, or ``None`` for ``other``/``skip``/anything unparsable."""
    try:
        return date.fromisoformat(action)
    except ValueError:
        return None


def parse_typed_date(text: str) -> date | None:
    """Read a date the user typed, or ``None`` if it is in none of the accepted formats."""
    candidate = text.strip()
    for fmt in _TYPED_FORMATS:
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue
    return None


def capture_datetime(day: date) -> datetime:
    """The naive timestamp written into EXIF for ``day``."""
    return datetime.combine(day, CAPTURE_TIME)
