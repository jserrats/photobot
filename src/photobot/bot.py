"""Telegram handlers and application wiring."""

from __future__ import annotations

import html
import logging
import sys
import traceback

from telegram import Update
from telegram.constants import MessageLimit, ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from photobot.config import ConfigError, Settings
from photobot.storage import ensure_writable_dir, save_file

logger = logging.getLogger(__name__)

# Application.bot_data keys
SETTINGS_KEY = "settings"


def _settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    settings = context.bot_data.get(SETTINGS_KEY)
    if not isinstance(settings, Settings):
        raise RuntimeError("application was built without Settings in bot_data")
    return settings


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

    allowed = filters.Chat(chat_id=settings.allowed_chat_id)
    application.add_handler(CommandHandler("start", start, filters=allowed))
    application.add_handler(MessageHandler(allowed & (filters.PHOTO | filters.VIDEO), save_media))
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
    # Only ask Telegram for message updates; we do not handle anything else.
    application.run_polling(allowed_updates=[Update.MESSAGE])


if __name__ == "__main__":
    main()
