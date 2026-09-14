import os
import sqlite3
import time
import json
import urllib.request
import urllib.parse
import logging
from pathlib import Path

TOKEN = os.environ.get("BOT_TOKEN", "").strip()
POLL_TIMEOUT = int(os.environ.get("POLL_TIMEOUT", "50"))
DB_PATH = os.environ.get("DB_PATH", "/data/bot.db")

if not TOKEN:
    raise SystemExit("BOT_TOKEN is not set.")

Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("duplicate-video-bot")

BASE = f"https://api.telegram.org/bot{TOKEN}"

def api(method, data=None):
    data = data or {}
    encoded = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(f"{BASE}/{method}", data=encoded)
    with urllib.request.urlopen(req, timeout=POLL_TIMEOUT + 15) as r:
        payload = json.loads(r.read().decode())
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API error in {method}: {payload}")
    return payload["result"]

db = sqlite3.connect(DB_PATH, check_same_thread=False)

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
    return True if row is None else bool(row[0])

def set_enabled(chat_id, enabled):
    db.execute(
        "
