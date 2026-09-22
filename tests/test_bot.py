import shutil
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import piexif
from telegram import CallbackQuery, Chat, Message, MessageEntity, PhotoSize, Update, User
from telegram.error import BadRequest
from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler

from helpers import make_file, make_message
from photobot.bot import (
    SETTINGS_KEY,
    TRACKER_KEY,
    build_application,
    date_chosen,
    date_typed,
    error_handler,
    save_media,
)
from photobot.config import Settings
from photobot.dating import BatchTracker, PendingBatch
from photobot.exif import set_capture_date

WHEN = datetime(2024, 5, 6, 7, 8, 9, tzinfo=UTC)
FIXTURE = Path(__file__).parent / "fixtures" / "tiny.jpg"


def make_context(download_dir: Path, file: object | None = None) -> MagicMock:
    settings = Settings(bot_token="t", allowed_chat_id=42, download_dir=download_dir)
    context = MagicMock()
    context.bot_data = {SETTINGS_KEY: settings, TRACKER_KEY: BatchTracker()}
    context.bot.get_file = AsyncMock(return_value=file)
    context.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    context.bot.edit_message_reply_markup = AsyncMock()
    return context


async def test_save_media_downloads_largest_photo(download_dir: Path) -> None:
    message = make_message(photo=True)
    message.date = WHEN
    update = MagicMock(effective_message=message)
    context = make_context(download_dir, make_file())

    await save_media(update, context)

    context.bot.get_file.assert_awaited_once_with("large")
    assert (download_dir / "20240506_070809_AQADuniq.jpg").exists()
    message.reply_text.assert_awaited_once_with("Saved photo as 20240506_070809_AQADuniq.jpg")


async def test_save_media_downloads_video(download_dir: Path) -> None:
    message = make_message(video=True)
    message.date = WHEN
    update = MagicMock(effective_message=message)
    context = make_context(download_dir, make_file(file_path="videos/file_1.mp4"))

    await save_media(update, context)

    context.bot.get_file.assert_awaited_once_with("vid")
    assert (download_dir / "20240506_070809_AQADuniq.mp4").exists()


async def test_save_media_reports_too_big_file(download_dir: Path) -> None:
    message = make_message(video=True)
    update = MagicMock(effective_message=message)
    context = make_context(download_dir)
    context.bot.get_file = AsyncMock(side_effect=BadRequest("File is too big"))

    await save_media(update, context)

    message.reply_text.assert_awaited_once_with("Could not download the video: File is too big")
    assert list(download_dir.iterdir()) == []


async def test_error_handler_notifies_operator_within_limit(download_dir: Path) -> None:
    context = make_context(download_dir)
    try:
        raise RuntimeError("x" * 10_000)
    except RuntimeError as exc:
        context.error = exc

    await error_handler(object(), context)

    context.bot.send_message.assert_awaited_once()
    kwargs = context.bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 42
    assert len(kwargs["text"]) <= 4096
    assert kwargs["text"].endswith("</pre>")


def _update(chat_id: int, *, photo: bool = False, text: str | None = None) -> Update:
    chat = Chat(id=chat_id, type=Chat.PRIVATE)
    entities = (
        [MessageEntity(type=MessageEntity.BOT_COMMAND, offset=0, length=len(text))]
        if text and text.startswith("/")
        else None
    )
    message = Message(
        message_id=1,
        date=WHEN,
        chat=chat,
        from_user=User(id=chat_id, first_name="u", is_bot=False),
        text=text,
        entities=entities,
        photo=[PhotoSize(file_id="p", file_unique_id="pu", width=1, height=1)] if photo else None,
    )
    return Update(update_id=1, message=message)


def test_build_application_restricts_handlers_to_allowed_chat(download_dir: Path) -> None:
    settings = Settings(bot_token="123:abc", allowed_chat_id=42, download_dir=download_dir)
    app = build_application(settings)
    assert app.bot_data[SETTINGS_KEY] is settings

    start_handler, media_handler, _text_handler, _callback_handler, reject_handler = app.handlers[0]
    assert isinstance(start_handler, CommandHandler)
    assert isinstance(media_handler, MessageHandler)
    assert isinstance(reject_handler, MessageHandler)

    # /start needs a bot username for CommandHandler; a plain Update without bot is enough
    # for the chat filter checks below.
    assert media_handler.check_update(_update(42, photo=True))
    assert not media_handler.check_update(_update(7, photo=True))
    assert not media_handler.check_update(_update(42, text="hello"))
    assert not reject_handler.check_update(_update(42, photo=True))
    assert not reject_handler.check_update(_update(42, text="hello"))
    assert reject_handler.check_update(_update(7, photo=True))
    assert reject_handler.check_update(_update(7, text="/start"))


def make_callback_update(data: str, chat_id: int = 42) -> MagicMock:
    query = MagicMock(data=data)
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    return MagicMock(callback_query=query, effective_chat=MagicMock(id=chat_id))


def make_batch(download_dir: Path, count: int = 2) -> PendingBatch:
    """A batch of real, EXIF-less JPEGs on disk, as the tracker would hold it."""
    batch = PendingBatch(id="abc123", prompt_message_id=99)
    for n in range(count):
        target = download_dir / f"photo{n}.jpg"
        shutil.copy(FIXTURE, target)
        batch.paths.append(target)
    return batch


def capture_dates(batch: PendingBatch) -> list[bytes]:
    return [piexif.load(str(p))["Exif"].get(piexif.ExifIFD.DateTimeOriginal) for p in batch.paths]


async def test_photo_without_exif_is_queued_for_dating(download_dir: Path) -> None:
    message = make_message(photo=True)
    message.date = WHEN
    update = MagicMock(effective_message=message, effective_chat=MagicMock(id=42))
    context = make_context(download_dir, make_file(payload=FIXTURE.read_bytes()))

    await save_media(update, context)

    tracker = context.bot_data[TRACKER_KEY]
    assert tracker.open is not None
    assert [p.name for p in tracker.open.paths] == ["20240506_070809_AQADuniq.jpg"]
    tracker.open.timer.cancel()


async def test_photo_that_already_has_a_date_is_not_queued(download_dir: Path) -> None:
    dated = download_dir / "dated.jpg"
    shutil.copy(FIXTURE, dated)
    set_capture_date(dated, datetime(2020, 1, 1, 12, 0))

    message = make_message(photo=True)
    message.date = WHEN
    update = MagicMock(effective_message=message, effective_chat=MagicMock(id=42))
    context = make_context(download_dir, make_file(payload=dated.read_bytes()))

    await save_media(update, context)

    assert context.bot_data[TRACKER_KEY].open is None


async def test_video_is_never_queued_for_dating(download_dir: Path) -> None:
    message = make_message(video=True)
    message.date = WHEN
    update = MagicMock(effective_message=message, effective_chat=MagicMock(id=42))
    context = make_context(download_dir, make_file(file_path="videos/f.mp4"))

    await save_media(update, context)

    assert context.bot_data[TRACKER_KEY].open is None


async def test_chosen_date_is_written_to_every_photo(download_dir: Path) -> None:
    context = make_context(download_dir)
    batch = make_batch(download_dir)
    context.bot_data[TRACKER_KEY].pending[batch.id] = batch

    await date_chosen(make_callback_update("d:abc123:2024-09-21"), context)

    assert capture_dates(batch) == [b"2024:09:21 12:00:00"] * 2
    assert context.bot_data[TRACKER_KEY].pending == {}


async def test_chosen_date_edits_the_prompt(download_dir: Path) -> None:
    context = make_context(download_dir)
    batch = make_batch(download_dir)
    context.bot_data[TRACKER_KEY].pending[batch.id] = batch
    update = make_callback_update("d:abc123:2024-09-21")

    await date_chosen(update, context)

    update.callback_query.answer.assert_awaited_once()
    update.callback_query.edit_message_text.assert_awaited_once_with(
        text="Set 2024-09-21 on 2 photo(s)", reply_markup=None
    )


async def test_missing_files_are_skipped(download_dir: Path) -> None:
    context = make_context(download_dir)
    batch = make_batch(download_dir)
    batch.paths.append(download_dir / "gone.jpg")
    context.bot_data[TRACKER_KEY].pending[batch.id] = batch
    update = make_callback_update("d:abc123:2024-09-21")

    await date_chosen(update, context)

    assert update.callback_query.edit_message_text.await_args.kwargs["text"] == (
        "Set 2024-09-21 on 2 photo(s)"
    )


async def test_skip_leaves_the_photos_alone(download_dir: Path) -> None:
    context = make_context(download_dir)
    batch = make_batch(download_dir)
    context.bot_data[TRACKER_KEY].pending[batch.id] = batch
    update = make_callback_update("d:abc123:skip")

    await date_chosen(update, context)

    assert capture_dates(batch) == [None, None]
    assert context.bot_data[TRACKER_KEY].pending == {}
    update.callback_query.edit_message_text.assert_awaited_once_with(
        text="Kept received date for 2 photo(s)", reply_markup=None
    )


async def test_other_then_typed_date_applies(download_dir: Path) -> None:
    context = make_context(download_dir)
    tracker = context.bot_data[TRACKER_KEY]
    batch = make_batch(download_dir)
    tracker.pending[batch.id] = batch

    await date_chosen(make_callback_update("d:abc123:other"), context)
    assert batch.awaiting_text is True
    assert tracker.pending == {batch.id: batch}

    reply = MagicMock(text="15/01/2024", reply_to_message=None, chat_id=42)
    reply.reply_text = AsyncMock()
    await date_typed(MagicMock(effective_message=reply), context)

    assert capture_dates(batch) == [b"2024:01:15 12:00:00"] * 2
    reply.reply_text.assert_awaited_once_with("Set 2024-01-15 on 2 photo(s)")
    assert tracker.pending == {}
    context.bot.edit_message_reply_markup.assert_awaited_once_with(
        chat_id=42, message_id=99, reply_markup=None
    )


async def test_typed_reply_picks_the_batch_it_replies_to(download_dir: Path) -> None:
    context = make_context(download_dir)
    tracker = context.bot_data[TRACKER_KEY]
    other = PendingBatch(id="older", prompt_message_id=50, awaiting_text=True)
    batch = make_batch(download_dir)
    tracker.pending = {other.id: other, batch.id: batch}

    reply = MagicMock(text="2024-01-15", chat_id=42)
    reply.reply_to_message = MagicMock(message_id=99)
    reply.reply_text = AsyncMock()
    await date_typed(MagicMock(effective_message=reply), context)

    assert capture_dates(batch) == [b"2024:01:15 12:00:00"] * 2
    assert tracker.pending == {other.id: other}


async def test_unreadable_typed_date_keeps_the_batch_waiting(download_dir: Path) -> None:
    context = make_context(download_dir)
    tracker = context.bot_data[TRACKER_KEY]
    batch = make_batch(download_dir)
    batch.awaiting_text = True
    tracker.pending[batch.id] = batch

    reply = MagicMock(text="last tuesday", reply_to_message=None, chat_id=42)
    reply.reply_text = AsyncMock()
    await date_typed(MagicMock(effective_message=reply), context)

    assert "YYYY-MM-DD" in reply.reply_text.await_args.args[0]
    assert tracker.pending == {batch.id: batch}
    assert capture_dates(batch) == [None, None]


async def test_plain_text_without_a_pending_question_is_ignored(download_dir: Path) -> None:
    context = make_context(download_dir)
    message = MagicMock(text="hello", reply_to_message=None, chat_id=42)
    message.reply_text = AsyncMock()

    await date_typed(MagicMock(effective_message=message), context)

    message.reply_text.assert_not_awaited()


async def test_callback_from_a_foreign_chat_is_ignored(download_dir: Path) -> None:
    context = make_context(download_dir)
    batch = make_batch(download_dir)
    context.bot_data[TRACKER_KEY].pending[batch.id] = batch
    update = make_callback_update("d:abc123:2024-09-21", chat_id=7)

    await date_chosen(update, context)

    update.callback_query.answer.assert_not_awaited()
    update.callback_query.edit_message_text.assert_not_awaited()
    assert capture_dates(batch) == [None, None]


async def test_callback_for_a_forgotten_batch_says_so(download_dir: Path) -> None:
    context = make_context(download_dir)
    update = make_callback_update("d:gone:2024-09-21")

    await date_chosen(update, context)

    text = update.callback_query.edit_message_text.await_args.kwargs["text"]
    assert "expired" in text


def test_build_application_registers_the_date_picker(download_dir: Path) -> None:
    settings = Settings(bot_token="123:abc", allowed_chat_id=42, download_dir=download_dir)
    app = build_application(settings)
    assert isinstance(app.bot_data[TRACKER_KEY], BatchTracker)

    handlers = app.handlers[0]
    callback_handlers = [h for h in handlers if isinstance(h, CallbackQueryHandler)]
    assert len(callback_handlers) == 1
    assert callback_handlers[0].check_update(_callback_update(42, "d:abc123:2024-09-21"))
    assert not callback_handlers[0].check_update(_callback_update(42, "other:thing"))

    text_handler = next(
        h for h in handlers if isinstance(h, MessageHandler) and h.callback.__name__ == "date_typed"
    )
    assert text_handler.check_update(_update(42, text="2024-09-21"))
    assert not text_handler.check_update(_update(7, text="2024-09-21"))
    assert not text_handler.check_update(_update(42, text="/start"))


def _callback_update(chat_id: int, data: str) -> Update:
    chat = Chat(id=chat_id, type=Chat.PRIVATE)
    user = User(id=chat_id, first_name="u", is_bot=False)
    message = Message(message_id=1, date=WHEN, chat=chat, from_user=user)
    return Update(
        update_id=1,
        callback_query=CallbackQuery(
            id="1", from_user=user, chat_instance="ci", data=data, message=message
        ),
    )
