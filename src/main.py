import html
import json
import logging
import traceback
import os
from pathlib import Path
from telegram import Update, Bot, error
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

DEVELOPER_CHAT_ID = os.getenv("DEVELOPER_CHAT_ID")
BOT_TOKEN = os.getenv("BOT_TOKEN")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and send a telegram message to notify the developer."""
    # Log the error before we do anything else, so we can see it even if something breaks.
    logger.error("Exception while handling an update:", exc_info=context.error)

    # traceback.format_exception returns the usual python message about an exception, but as a
    # list of strings rather than a single string, so we have to join them together.
    tb_list = traceback.format_exception(
        None, context.error, context.error.__traceback__)
    tb_string = "".join(tb_list)

    # Build the message with some markup and additional information about what happened.
    # You might need to add some logic to deal with messages longer than the 4096 character limit.
    update_str = update.to_dict() if isinstance(update, Update) else str(update)
    message = (
        f"An exception was raised while handling an update\n"
        f"<pre>update = {html.escape(json.dumps(update_str, indent=2, ensure_ascii=False))}"
        "</pre>\n\n"
        f"<pre>context.chat_data = {html.escape(str(context.chat_data))}</pre>\n\n"
        f"<pre>context.user_data = {html.escape(str(context.user_data))}</pre>\n\n"
        f"<pre>{html.escape(tb_string)}</pre>"
    )

    # Finally, send the message
    await context.bot.send_message(
        chat_id=DEVELOPER_CHAT_ID, text=message, parse_mode=ParseMode.HTML
    )


async def bad_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Raise an error to trigger the error handler."""
    await context.bot.wrong_method_name()  # type: ignore[attr-defined]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays info on how to trigger an error."""
    check_user(update, context.bot)
    await update.effective_message.reply_html(
        "Use /bad_command to cause an error.\n"
        f"Your chat id is <code>{update.effective_chat.id}</code>."
    )


async def image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    check_user(update, context.bot)
    file_id = update.message.photo[-1].file_id
    await download_file(file_id, context.bot)
    await update.effective_message.reply_html("Downloaded image")


async def video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    check_user(update, context.bot)
    file_id = update.message.video.file_id
    await download_file(file_id, context.bot)
    await update.effective_message.reply_html(
        "Use /bad_command to cause an error.\n"
        f"Your chat id is <code>{update.effective_chat.id}</code>."
    )
    await update.effective_message.reply_html("Downloaded video")


async def download_file(file_id, bot: Bot) -> None:
    new_file = await bot.get_file(file_id)
    filename = new_file.file_path.split("/")[-1]
    await new_file.download_to_drive(f"/files/{filename}")


def check_user(update: Update, bot: Bot) -> None:
    if not update.effective_chat.id == int(DEVELOPER_CHAT_ID):
        raise error.Forbidden("Unauthorized user")


def main() -> None:
    """Run the bot."""
    logger.info(f"Starting the bot with id {DEVELOPER_CHAT_ID}")
    # Create the Application and pass it your bot's token.
    application = Application.builder().token(BOT_TOKEN).build()

    # Register the commands...
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("bad_command", bad_command))
    application.add_handler(MessageHandler(filters.PHOTO, image))
    application.add_handler(MessageHandler(filters.VIDEO, video))

    # ...and the error handler
    application.add_error_handler(error_handler)

    # Run the bot until the user presses Ctrl-C
    application.run_polling()


if __name__ == "__main__":
    main()
