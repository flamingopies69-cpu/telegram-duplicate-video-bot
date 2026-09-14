import os
import sqlite3
import time
import json
import urllib.request
import urllib.parse
import logging
from pathlib import Path

TOKEN = os.environ.get("BOT_TOKEN", "").strip()
POLL_TIMEOUT = 50
DB_PATH = os.environ.get("DB_PATH", "/data/bot.db")

if not TOKEN:
    raise SystemExit("BOT_TOKEN is not set.")

Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

log = logging.getLogger("duplicate-video-bot")

BASE = "https://api.telegram.org/bot" + TOKEN


def api(method, data=None):
    if data is None:
        data = {}

    encoded = urllib.parse.urlencode(data).encode()

    request = urllib.request.Request(
        BASE + "/" + method,
        data=encoded
    )

    with urllib.request.urlopen(
        request,
        timeout=POLL_TIMEOUT + 15
    ) as response:
        result = json.loads(response.read().decode())

    if not result.get("ok"):
        raise RuntimeError(
            "Telegram API error: " + str(result)
        )

    return result["result"]


db = sqlite3.connect(
    DB_PATH,
    check_same_thread=False
)

db.execute("""
CREATE TABLE IF NOT EXISTS seen_videos (
    chat_id TEXT NOT NULL,
    file_unique_id TEXT NOT NULL,
    first_message_id INTEGER NOT NULL,
    first_seen_at INTEGER NOT NULL,
    PRIMARY KEY (chat_id, file_unique_id)
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS settings (
    chat_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS stats (
    chat_id TEXT PRIMARY KEY,
    videos_seen INTEGER NOT NULL DEFAULT 0,
    duplicates_deleted INTEGER NOT NULL DEFAULT 0
)
""")

db.commit()


def is_enabled(chat_id):
    row = db.execute(
        "SELECT enabled FROM settings WHERE chat_id=?",
        (str(chat_id),)
    ).fetchone()

    if row is None:
        return True

    return bool(row[0])


def set_enabled(chat_id, enabled):
    db.execute(
        """
        INSERT INTO settings(chat_id, enabled)
        VALUES(?, ?)
        ON CONFLICT(chat_id)
        DO UPDATE SET enabled=excluded.enabled
        """,
        (str(chat_id), int(enabled))
    )

    db.commit()


def bump(chat_id, field):
    db.execute(
        f"""
        INSERT INTO stats(chat_id, {field})
        VALUES(?, 1)
        ON CONFLICT(chat_id)
        DO UPDATE SET {field}={field}+1
        """,
        (str(chat_id),)
    )

    db.commit()


def get_stats(chat_id):
    row = db.execute(
        """
        SELECT videos_seen, duplicates_deleted
        FROM stats
        WHERE chat_id=?
        """,
        (str(chat_id),)
    ).fetchone()

    if row is None:
        return 0, 0

    return row


def remember(chat_id, unique_id, message_id):
    try:
        db.execute(
            """
            INSERT INTO seen_videos
            (chat_id, file_unique_id, first_message_id, first_seen_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                str(chat_id),
                unique_id,
                message_id,
                int(time.time())
            )
        )

        db.commit()
        return True

    except sqlite3.IntegrityError:
        return False


def clear_chat(chat_id):
    db.execute(
        "DELETE FROM seen_videos WHERE chat_id=?",
        (str(chat_id),)
    )

    db.commit()


def send_message(chat_id, text):
    try:
        api(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text
            }
        )

    except Exception as error:
        log.warning(
            "Could not send message: %s",
            error
        )


def command_text(message):
    text = message.get("text", "")

    if not text:
        return ""

    return text.split()[0].split("@")[0].lower()


def handle_command(message):
    command = command_text(message)
    chat_id = message["chat"]["id"]

    if command == "/start":
        send_message(
            chat_id,
            "Duplicate Video Cleaner is running."
        )

    elif command == "/status":
        enabled = "ON" if is_enabled(chat_id) else "OFF"
        seen, deleted = get_stats(chat_id)

        send_message(
            chat_id,
            "Duplicate cleaner: " + enabled +
            "\nVideos seen: " + str(seen) +
            "\nDuplicates deleted: " + str(deleted)
        )

    elif command == "/on":
        set_enabled(chat_id, True)
        send_message(
            chat_id,
            "Duplicate checking is ON."
        )

    elif command == "/off":
        set_enabled(chat_id, False)
        send_message(
            chat_id,
            "Duplicate checking is OFF."
        )

    elif command == "/stats":
        seen, deleted = get_stats(chat_id)

        send_message(
            chat_id,
            "Videos seen: " + str(seen) +
            "\nDuplicates deleted: " + str(deleted)
        )

    elif command == "/clear":
        clear_chat(chat_id)

        send_message(
            chat_id,
            "Duplicate history cleared."
        )


def process_message(message):
    if "chat" not in message:
        return

    if (
        "text" in message
        and message.get("text", "").startswith("/")
    ):
        handle_command(message)
        return

    video = message.get("video")

    if video is None:
        video = message.get("video_note")

    if video is None:
        return

    chat_id = message["chat"]["id"]

    bump(chat_id, "videos_seen")

    if not is_enabled(chat_id):
        return

    unique_id = video.get("file_unique_id")

    if not unique_id:
        return

    first_copy = remember(
        chat_id,
        unique_id,
        message["message_id"]
    )

    if first_copy:
        log.info(
            "First copy received: %s",
            unique_id
        )
        return

    try:
        api(
            "deleteMessage",
            {
                "chat_id": chat_id,
                "message_id": message["message_id"]
            }
        )

        bump(
            chat_id,
            "duplicates_deleted"
        )

        log.info(
            "Duplicate deleted: %s",
            unique_id
        )

    except Exception as error:
        log.error(
            "Could not delete duplicate: %s",
            error
        )


def main():
    me = api("getMe")

    log.info(
        "Bot started successfully: @%s",
        me.get("username")
    )

    offset = 0

    while True:
        try:
            updates = api(
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": POLL_TIMEOUT,
                    "allowed_updates": json.dumps([
                        "message",
                        "channel_post",
                        "edited_channel_post"
                    ])
                }
            )

            for update in updates:
                offset = update["update_id"] + 1

                if "message" in update:
                    process_message(
                        update["message"]
                    )

                if "channel_post" in update:
                    process_message(
                        update["channel_post"]
                    )

                if "edited_channel_post" in update:
                    process_message(
                        update["edited_channel_post"]
                    )

        except Exception as error:
            log.exception(
                "Polling error: %s",
                error
            )

            time.sleep(3)


if __name__ == "__main__":
    main()
