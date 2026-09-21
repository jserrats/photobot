from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from telegram import Chat, Message, MessageEntity, PhotoSize, Update, User
from telegram.error import BadRequest
from telegram.ext import CommandHandler, MessageHandler

from helpers import make_file, make_message
from photobot.bot import SETTINGS_KEY, build_application, error_handler, save_media
from photobot.config import Settings

WHEN = datetime(2024, 5, 6, 7, 8, 9, tzinfo=UTC)


def make_context(download_dir: Path, file: object | None = None) -> MagicMock:
    settings = Settings(bot_token="t", allowed_chat_id=42, download_dir=download_dir)
    context = MagicMock()
    context.bot_data = {SETTINGS_KEY: settings}
    context.bot.get_file = AsyncMock(return_value=file)
    context.bot.send_message = AsyncMock()
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

    start_handler, media_handler, reject_handler = app.handlers[0]
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
