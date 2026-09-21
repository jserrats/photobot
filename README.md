# photobot

A small Telegram bot that saves every photo and video sent from one authorized chat to a
directory on disk. Useful as a "send it to my server" inbox from your phone.

## How it works

- Only the chat whose id matches `DEVELOPER_CHAT_ID` is served. Messages from anyone else are
  logged and silently dropped, so the bot never confirms its existence to strangers.
- Photos are saved at the largest size Telegram offers. Videos are saved as-is.
- Files are named `YYYYMMDD_HHMMSS_<telegram-unique-id>.<ext>` so they sort chronologically
  and never collide. Downloads go to a `.part` file and are renamed only when complete.
- The Telegram Bot API refuses to serve files over 20 MB. The bot replies with the error
  instead of crashing.
- Unhandled exceptions are logged and forwarded to the authorized chat.

## Configuration

| Variable            | Required | Description                                                     |
| ------------------- | -------- | --------------------------------------------------------------- |
| `BOT_TOKEN`         | yes      | Token from [@BotFather](https://t.me/BotFather).                |
| `DEVELOPER_CHAT_ID` | yes      | Numeric id of the only chat allowed to use the bot.             |
| `DOWNLOAD_DIR`      | no       | Directory to save into. Default `/files`.                       |
| `LOG_LEVEL`         | no       | `DEBUG`, `INFO`, `WARNING` or `ERROR`. Default `INFO`.          |

To find your chat id: start the bot with any placeholder `DEVELOPER_CHAT_ID`, send it a
message, and read the id from the `Ignoring update from unauthorized chat` line in the logs.

## Running with Docker Compose

```sh
cp .env.example .env      # fill in BOT_TOKEN and DEVELOPER_CHAT_ID
mkdir -p files && sudo chown 1000:1000 files
docker compose up -d
```

Pictures and videos land in `./files` next to the compose file by default. To store them
elsewhere on the host, set `HOST_FILES_DIR` in `.env` to an absolute path, for example
`HOST_FILES_DIR=/mnt/storage/photos`. The directory must exist and be writable by the
container's user.

The container runs as uid 1000 with a read-only root filesystem and no capabilities. If your
storage directory is owned by a different user, set `PUID` and `PGID` in `.env` to match.

Images are published to `ghcr.io/jserrats/photobot` for `linux/amd64` and `linux/arm64` on
every push to `master` and on `v*` tags.

## Development

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync                    # create .venv with dev tools
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
BOT_TOKEN=... DEVELOPER_CHAT_ID=... DOWNLOAD_DIR=./files uv run photobot
```
