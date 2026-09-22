"""Grouping freshly saved photos into one batch and asking the user when they were taken.

Telegram strips EXIF from photos sent "compressed", so the bot asks for the capture date
instead of guessing. Photos that arrive together (an album, or a burst of sends) should
produce a single question, so each arrival restarts a short quiet-period timer and only
the last one fires the prompt.

Batches live in memory. A restart forgets any unanswered prompt; the files are already on
disk with the date they were received, so nothing is lost.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

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
    timer: asyncio.TimerHandle | None = None
    # Strong reference to the task running ``on_ready``; asyncio only holds a weak one.
    task: asyncio.Task[None] | None = None


class BatchTracker:
    """The one batch still collecting photos, plus every batch awaiting an answer."""

    def __init__(self) -> None:
        self.open: PendingBatch | None = None
        self.pending: dict[str, PendingBatch] = {}

    def add(
        self,
        path: Path,
        *,
        window: float,
        loop: asyncio.AbstractEventLoop,
        on_ready: Callable[[PendingBatch], Awaitable[None]],
    ) -> PendingBatch:
        """Add ``path`` to the open batch and (re)start its quiet-period timer.

        ``on_ready`` is awaited ``window`` seconds after the last photo of the batch.
        """
        batch = self.open
        if batch is None:
            batch = PendingBatch(id=new_batch_id())
            self.open = batch
        elif batch.timer is not None:
            batch.timer.cancel()
            batch.timer = None

        batch.paths.append(path)
        batch.last_added = loop.time()
        batch.timer = loop.call_later(window, self._fire, batch, loop, on_ready)
        return batch

    def _fire(
        self,
        batch: PendingBatch,
        loop: asyncio.AbstractEventLoop,
        on_ready: Callable[[PendingBatch], Awaitable[None]],
    ) -> None:
        batch.timer = None
        if self.open is batch:
            self.open = None
        self.pending[batch.id] = batch
        batch.task = loop.create_task(self._run_ready(batch, on_ready))

    async def _run_ready(
        self,
        batch: PendingBatch,
        on_ready: Callable[[PendingBatch], Awaitable[None]],
    ) -> None:
        try:
            await on_ready(batch)
        except Exception:
            # Without the prompt there is nothing to answer, so drop the batch rather
            # than leave it waiting forever.
            self.pending.pop(batch.id, None)
            logger.exception("Could not ask for the capture date of %d photo(s)", len(batch.paths))

    def get(self, batch_id: str) -> PendingBatch | None:
        return self.pending.get(batch_id)

    def pop(self, batch_id: str) -> PendingBatch | None:
        return self.pending.pop(batch_id, None)

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
