"""Telegram handlers and application wiring."""

from __future__ import annotations

import asyncio
import html
import logging
import sys
import traceback
from datetime import date, datetime
from pathlib import Path

from telegram import CallbackQuery, InlineKeyboardMarkup, Update
from telegram.constants import MessageLimit, ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from photobot import dating, exif
from photobot.config import ConfigError, Settings
from photobot.dating import BatchTracker, PendingBatch
from photobot.storage import ensure_writable_dir, save_file

logger = logging.getLogger(__name__)

# Application.bot_data keys
SETTINGS_KEY = "settings"
TRACKER_KEY = "date_batches"

EXPIRED_TEXT = "This prompt has expired; the photos kept the date they were received."


def _settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    settings = context.bot_data.get(SETTINGS_KEY)
    if not isinstance(settings, Settings):
        raise RuntimeError("application was built without Settings in bot_data")
    return settings


def _tracker(context: ContextTypes.DEFAULT_TYPE) -> BatchTracker:
    tracker = context.bot_data.get(TRACKER_KEY)
    if not isinstance(tracker, BatchTracker):
        raise RuntimeError("application was built without a BatchTracker in bot_data")
    return tracker


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Greet the authorized user."""
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(
        "Send me photos or videos and I will save them to disk. "
        "Send them as files to keep the original quality."
    )


async def save_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Download a photo or video from an authorized chat."""
    message = update.effective_message
    if message is None:
        return

    if message.photo:
        file_id = message.photo[-1].file_id  # largest available size
        kind = "photo"
    elif message.video:
        file_id = message.video.file_id
        kind = "video"
    else:
        return

    try:
        file = await context.bot.get_file(file_id)
        path = await save_file(file, _settings(context).download_dir, message.date)
    except BadRequest as exc:
        # Most commonly "File is too big": the Bot API refuses downloads over 20 MB.
        logger.warning("Could not fetch %s: %s", kind, exc.message)
        await message.reply_text(f"Could not download the {kind}: {exc.message}")
        return

    await message.reply_text(f"Saved {kind} as {path.name}")

    if kind == "photo":
        _queue_for_dating(update, context, path)


def _queue_for_dating(update: Update, context: ContextTypes.DEFAULT_TYPE, path: Path) -> None:
    """Line the photo up for a capture-date question, unless it already has one.

    Only photos get here; videos are never queued because MP4 has no EXIF.
    """
    if not exif.is_jpeg(path) or exif.has_capture_date(path):
        return

    chat = update.effective_chat
    if chat is None:
        return
    chat_id = chat.id
    settings = _settings(context)
    tracker = _tracker(context)

    async def on_change(batch: PendingBatch) -> None:
        await _refresh_prompt(context, chat_id, batch, settings)

    tracker.add(
        path,
        window=settings.date_prompt_window,
        loop=asyncio.get_running_loop(),
        on_change=on_change,
    )


async def _refresh_prompt(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    batch: PendingBatch,
    settings: Settings,
) -> None:
    """Put the question at the bottom of the chat, with an up-to-date count.

    Telegram cannot move a message, so a batch that grew gets a brand new picker and the
    previous one is deleted. The new one is sent first: if the delete then fails, a stale
    picker is left above, which still answers for the same batch, whereas deleting first
    and failing to send would leave the photos with no way to be dated at all.
    """
    previous = batch.prompt_message_id
    today = datetime.now(settings.timezone).date()
    prompt = await context.bot.send_message(
        chat_id=chat_id,
        text=(f"{len(batch.paths)} photo(s) arrived without a capture date. When were they taken?"),
        reply_markup=dating.build_keyboard(batch.id, today),
    )
    batch.prompt_message_id = prompt.message_id

    if previous is not None:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=previous)
        except TelegramError as exc:
            logger.warning("Could not delete the previous date picker: %s", exc)


def _apply_date(batch: PendingBatch, day: date) -> int:
    """Write ``day`` into every photo of the batch; return how many were updated."""
    when = dating.capture_datetime(day)
    written = 0
    for path in batch.paths:
        if not path.exists():
            logger.warning("Skipping %s: no longer on disk", path.name)
            continue
        try:
            exif.set_capture_date(path, when)
        except (OSError, ValueError) as exc:
            logger.warning("Could not set the capture date on %s: %s", path.name, exc)
            continue
        written += 1
    return written


async def date_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle a tap on the date picker."""
    query = update.callback_query
    chat = update.effective_chat
    if query is None or query.data is None:
        return
    if chat is None or chat.id != _settings(context).allowed_chat_id:
        return

    await query.answer()
    try:
        batch_id, action = dating.parse_callback(query.data)
    except ValueError:
        logger.warning("Ignoring malformed callback data %r", query.data)
        return

    tracker = _tracker(context)
    batch = tracker.get(batch_id)
    if batch is None:
        await _edit_prompt(query, EXPIRED_TEXT)
        return

    if action == dating.SKIP_ACTION:
        tracker.pop(batch_id)
        await _edit_prompt(query, f"Kept received date for {len(batch.paths)} photo(s)")
        return

    if action == dating.OTHER_ACTION:
        # The user is answering, so later photos start a batch of their own rather than
        # joining one whose question is already half-answered.
        tracker.seal(batch)
        batch.awaiting_text = True
        await _edit_prompt(
            query,
            f"Reply to this message with the date those {len(batch.paths)} photo(s) "
            f"were taken ({dating.TYPED_FORMATS_HELP}).",
            reply_markup=dating.skip_only_keyboard(batch_id),
        )
        return

    day = dating.parse_action_date(action)
    if day is None:
        logger.warning("Ignoring unknown callback action %r", action)
        return

    tracker.pop(batch_id)
    written = _apply_date(batch, day)
    await _edit_prompt(query, f"Set {day.isoformat()} on {written} photo(s)")


async def date_typed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle a typed date after the user chose "Other date…"."""
    message = update.effective_message
    if message is None or not message.text:
        return

    tracker = _tracker(context)
    # An explicit reply names its batch even if the user never tapped "Other date…";
    # otherwise only a batch that asked for a typed date may claim the message.
    if message.reply_to_message is not None:
        batch = tracker.by_prompt(message.reply_to_message.message_id)
    else:
        batch = tracker.awaiting_text()
    if batch is None:
        return

    day = dating.parse_typed_date(message.text)
    if day is None:
        await message.reply_text(
            f"I could not read that date. Accepted formats: {dating.TYPED_FORMATS_HELP}."
        )
        return

    tracker.pop(batch.id)
    written = _apply_date(batch, day)
    await message.reply_text(f"Set {day.isoformat()} on {written} photo(s)")
    if batch.prompt_message_id is not None:
        await _clear_prompt_keyboard(context, message.chat_id, batch.prompt_message_id)


async def _edit_prompt(
    query: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup | None = None
) -> None:
    """Rewrite the prompt message, tolerating a message that can no longer be edited."""
    try:
        await query.edit_message_text(text=text, reply_markup=reply_markup)
    except BadRequest as exc:
        logger.warning("Could not update the date prompt: %s", exc.message)


async def _clear_prompt_keyboard(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int
) -> None:
    try:
        await context.bot.edit_message_reply_markup(
            chat_id=chat_id, message_id=message_id, reply_markup=None
        )
    except BadRequest as exc:
        logger.warning("Could not clear the date prompt keyboard: %s", exc.message)


async def reject_unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log and ignore updates from anyone other than the allowed chat.

    Nothing is sent back, so the bot does not confirm its existence to strangers.
    The chat id is logged so the operator can find their own id during setup.
    """
    chat = update.effective_chat
    user = update.effective_user
    logger.warning(
        "Ignoring update from unauthorized chat id=%s type=%s user=%s",
        chat.id if chat else None,
        chat.type if chat else None,
        user.id if user else None,
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and notify the operator over Telegram."""
    logger.error("Exception while handling an update:", exc_info=context.error)
    if context.error is None:
        return

    tb = "".join(traceback.format_exception(context.error))
    if isinstance(update, Update) and update.effective_message:
        where = f"chat {update.effective_chat.id if update.effective_chat else '?'}"
    else:
        where = "outside an update"

    body = f"<b>Error while handling {html.escape(where)}</b>\n<pre>{html.escape(tb)}</pre>"
    limit = MessageLimit.MAX_TEXT_LENGTH
    if len(body) > limit:
        body = body[: limit - len("…</pre>")] + "…</pre>"

    try:
        await context.bot.send_message(
            chat_id=_settings(context).allowed_chat_id,
            text=body,
            parse_mode=ParseMode.HTML,
        )
    except TelegramError:
        logger.exception("Failed to notify the operator about the previous error")


def build_application(settings: Settings) -> Application:  # type: ignore[type-arg]
    """Create the PTB application with all handlers registered."""
    application = Application.builder().token(settings.bot_token).build()
    application.bot_data[SETTINGS_KEY] = settings
    application.bot_data[TRACKER_KEY] = BatchTracker()

    allowed = filters.Chat(chat_id=settings.allowed_chat_id)
    application.add_handler(CommandHandler("start", start, filters=allowed))
    application.add_handler(MessageHandler(allowed & (filters.PHOTO | filters.VIDEO), save_media))
    application.add_handler(MessageHandler(allowed & filters.TEXT & ~filters.COMMAND, date_typed))
    application.add_handler(CallbackQueryHandler(date_chosen, pattern=r"^d:"))
    # Anything from anyone else: log and drop.
    application.add_handler(MessageHandler(~allowed, reject_unauthorized))
    application.add_error_handler(error_handler)
    return application


def main() -> None:
    """Entry point: load config, prepare storage, run long polling until stopped."""
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        print(f"photobot: configuration error: {exc}", file=sys.stderr)
        sys.exit(2)

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=settings.log_level,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        ensure_writable_dir(settings.download_dir)
    except OSError as exc:
        logger.critical("%s", exc)
        sys.exit(2)

    logger.info(
        "Starting photobot; saving media from chat %s to %s",
        settings.allowed_chat_id,
        settings.download_dir,
    )
    application = build_application(settings)
    # Messages carry the media; callback queries carry the date picker's answers.
    application.run_polling(allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY])


if __name__ == "__main__":
    main()
