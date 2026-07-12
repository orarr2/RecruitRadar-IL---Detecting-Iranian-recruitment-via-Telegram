"""
RecruitRadar-IL headless collector - non-interactive Telegram collection.

The main notebook logs in interactively (SMS code + 2FA) and keeps a
`.session` file. That is perfect on your laptop but impossible in the cloud,
where nobody can type a code. This script solves it with a Telethon
**StringSession**: you log in ONCE locally, it prints a long string, you store
that string as the `TELETHON_SESSION` repository secret, and from then on
GitHub Actions can connect as you with no prompts.

Two modes:

  1. Generate the session (run once, on your own machine):
        python agent/collect_headless.py --login
     Enter api_id / api_hash / phone / code (and 2FA if set); it prints the
     StringSession. Copy it into the TELETHON_SESSION secret. The string is a
     credential - treat it like a password, never commit it.

  2. Collect (what GitHub Actions runs automatically):
        python agent/collect_headless.py
     Reads TELEGRAM_API_ID / TELEGRAM_API_HASH / TELETHON_SESSION from the
     environment, connects, and appends new public-channel messages into
     data/recruitradar.db using the exact same schema as the notebook.

Channels collected = the seed list below + every line in channels_extra.txt
(the same file the bot's /approve appends to). Public channels only.

If Telethon or the credentials are missing, it prints a clear message and
exits 0 - the digest step still runs on whatever is already in the DB.
"""

import os
import sys
import json
import asyncio
from pathlib import Path
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import run_offline as base  # init_db, hash_user_id, DATA_DIR, RAW_DIR  # noqa: E402


def _load_env(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if v.strip():
            os.environ.setdefault(k.strip(), v.strip())

_load_env(ROOT / ".env")
_load_env(ROOT / "agent" / ".env")

# Collection window / caps (override via env). Kept conservative so a cloud run
# stays well inside Telegram's read limits and GitHub's job timeout.
DAYS_BACK       = int(os.getenv("COLLECT_DAYS_BACK", "3"))
MAX_PER_CHANNEL = int(os.getenv("COLLECT_MAX_PER_CHANNEL", "200"))
SLEEP_BETWEEN   = float(os.getenv("COLLECT_SLEEP", "0.05"))

# Seed registry - a SNAPSHOT of the notebook's CHANNELS (section 3). The live
# source of truth for additions is channels_extra.txt; keep new approved
# channels there rather than editing this list.
SEED_CHANNELS = [
    ("israjobs", "jobs_it"), ("jobs_in_israel", "jobs_il"),
    ("ConnectJLMJobs", "jobs_il"), ("BROOTTO_Jobs", "jobs_il"),
    ("rabotaisraeli", "jobs_il"), ("rabotacoil", "jobs_il"),
    ("rabota_za_granicey", "jobs_abroad"), ("jobs_abroad_il", "jobs_abroad"),
    ("freelance_il", "freelance"), ("freelancim", "freelance"),
    ("perevodchiki_rabota", "translation"), ("tsalamim_il", "photo_video"),
    ("shlichuyot_il", "field_work"), ("avoda_baregel", "field_work"),
    ("ezra_hadadit", "help_offers"), ("tiyulim_israel", "trips_travel"),
    ("trempim_israel", "rides"), ("izrail_avito", "marketplace"),
    ("yad2_il", "yad2"), ("kupi_proday_israel", "marketplace"),
    ("rahitim_yad2", "furniture"), ("crypto_exchange_il", "crypto_exchange"),
    ("obmen_valut_israel", "crypto_exchange"),
]

VALID_CATEGORIES = {
    "jobs_il", "jobs_abroad", "jobs_it", "freelance", "translation",
    "field_work", "photo_video", "help_offers", "trips_travel",
    "yad2", "marketplace", "furniture", "crypto_exchange", "rides", "tutoring",
}


def load_channels():
    channels, seen = [], set()
    for user, cat in SEED_CHANNELS:
        key = user.lstrip("@").lower()
        if key not in seen:
            seen.add(key)
            channels.append((user.lstrip("@"), cat))
    extra = ROOT / "channels_extra.txt"
    if extra.exists():
        for line in extra.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            user = parts[0].lstrip("@")
            cat = parts[1] if len(parts) > 1 and parts[1] in VALID_CATEGORIES else "help_offers"
            key = user.lower()
            if user and key not in seen:
                seen.add(key)
                channels.append((user, cat))
    return channels


def _import_telethon():
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        from telethon.errors import (
            FloodWaitError, ChannelPrivateError, UsernameNotOccupiedError,
        )
        return TelegramClient, StringSession, (
            FloodWaitError, ChannelPrivateError, UsernameNotOccupiedError)
    except ImportError:
        return None, None, None


def save_message(conn, channel, category, m):
    replies = m.replies.replies if getattr(m, "replies", None) else 0
    conn.execute(
        """INSERT OR IGNORE INTO messages
           (channel, category, msg_id, date, sender_hash, text, has_media,
            forwards, views, replies, is_forwarded, collected_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (channel, category, m.id,
         m.date.astimezone(timezone.utc).isoformat() if m.date else None,
         base.hash_user_id(m.sender_id), m.text or "",
         1 if m.media is not None else 0, m.forwards or 0, m.views or 0,
         replies, 1 if m.fwd_from is not None else 0,
         datetime.now(timezone.utc).isoformat()))


async def _collect():
    TelegramClient, StringSession, errs = _import_telethon()
    if TelegramClient is None:
        print("Telethon not installed - skipping collection (digest still runs).")
        return
    FloodWaitError, ChannelPrivateError, UsernameNotOccupiedError = errs

    api_id = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    session = os.getenv("TELETHON_SESSION", "").strip()
    if not (api_id.isdigit() and api_hash and session):
        print("TELEGRAM_API_ID / TELEGRAM_API_HASH / TELETHON_SESSION not all set - "
              "skipping collection (digest still runs on existing data).")
        return

    client = TelegramClient(StringSession(session), int(api_id), api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        print("Stored session is not authorized - regenerate it with "
              "`python agent/collect_headless.py --login`.")
        await client.disconnect()
        return

    conn = base.init_db()
    channels = load_channels()
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)
    total = 0
    for channel, category in channels:
        n = 0
        try:
            async for m in client.iter_messages(channel, limit=MAX_PER_CHANNEL):
                if m.date and m.date.astimezone(timezone.utc) < cutoff:
                    break
                save_message(conn, channel, category, m)
                n += 1
                await asyncio.sleep(SLEEP_BETWEEN)
            conn.commit()
        except FloodWaitError as e:
            print(f"  FloodWait on {channel}: sleeping {e.seconds}s")
            await asyncio.sleep(e.seconds + 1)
            conn.commit()
        except (ChannelPrivateError, UsernameNotOccupiedError, ValueError):
            print(f"  skip {channel} (unavailable)")
        except Exception as e:
            print(f"  ERROR {channel}: {e.__class__.__name__}")
        if n:
            print(f"  {channel}: +{n}")
        total += n
    conn.close()
    await client.disconnect()
    print(f"Collection done: {total} new messages across {len(channels)} channels.")


async def _login():
    TelegramClient, StringSession, _ = _import_telethon()
    if TelegramClient is None:
        sys.exit("Install Telethon first:  pip install telethon")
    api_id = input("api_id: ").strip()
    api_hash = input("api_hash: ").strip()
    if not (api_id.isdigit() and api_hash):
        sys.exit("api_id must be a number and api_hash must be non-empty.")
    client = TelegramClient(StringSession(), int(api_id), api_hash)
    await client.start()  # prompts for phone, code, and 2FA as needed
    s = client.session.save()
    print("\n=== TELETHON_SESSION (store as a repository secret; keep it private) ===")
    print(s)
    print("=== end ===")
    await client.disconnect()


def main():
    if "--login" in sys.argv:
        asyncio.run(_login())
    else:
        asyncio.run(_collect())


if __name__ == "__main__":
    main()
