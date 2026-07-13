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

# Collection window / caps (override via env). 14 days back with a 500-msg
# per-channel ceiling gives us two weeks of history on slow channels and
# roughly a week on the busiest ones, while staying well inside Telegram's
# read limits and GitHub's 20-minute job timeout.
DAYS_BACK       = int(os.getenv("COLLECT_DAYS_BACK", "14"))
MAX_PER_CHANNEL = int(os.getenv("COLLECT_MAX_PER_CHANNEL", "500"))
SLEEP_BETWEEN   = float(os.getenv("COLLECT_SLEEP", "0.05"))

# Seed registry - pruned to what Telegram actually accepts. Entries that came
# back not_occupied / private_or_forbidden / UsernameInvalidError during the
# per-channel diagnostic have been removed; the ones with confirmed activity
# stay, and the "no_new_messages" (existing but quiet within the window) ones
# stay too - they may pick up on any given collect.
# The live source of truth for additions is channels_extra.txt; keep new
# approved channels there rather than editing this list.
SEED_CHANNELS = [
    ("israjobs", "jobs_it"), ("jobs_in_israel", "jobs_il"),
    ("ConnectJLMJobs", "jobs_il"), ("BROOTTO_Jobs", "jobs_il"),
    ("rabotaisraeli", "jobs_il"), ("rabotacoil", "jobs_il"),
    ("rabota_za_granicey", "jobs_abroad"),
    ("ezra_hadadit", "help_offers"),
    ("trempim_israel", "rides"), ("izrail_avito", "marketplace"),
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
    # Per-channel outcome tally so a run leaves an unambiguous record of which
    # seeds are live vs. dead vs. private, printed at the end.
    outcomes = {"alive": [], "no_new_messages": [], "not_occupied": [],
                "private_or_forbidden": [], "flood_wait": [], "error": []}
    for channel, category in channels:
        n = 0
        outcome = None
        detail = ""
        try:
            async for m in client.iter_messages(channel, limit=MAX_PER_CHANNEL):
                if m.date and m.date.astimezone(timezone.utc) < cutoff:
                    break
                save_message(conn, channel, category, m)
                n += 1
                await asyncio.sleep(SLEEP_BETWEEN)
            conn.commit()
            outcome = "alive" if n > 0 else "no_new_messages"
        except FloodWaitError as e:
            outcome = "flood_wait"
            detail = f"sleep {e.seconds}s"
            await asyncio.sleep(e.seconds + 1)
            conn.commit()
        except UsernameNotOccupiedError:
            outcome = "not_occupied"
        except ChannelPrivateError:
            outcome = "private_or_forbidden"
        except ValueError as e:
            # Telethon raises ValueError for "No user has 'foo' as username" too,
            # so treat as not_occupied unless the message says otherwise.
            msg = str(e).lower()
            if "no user has" in msg or "no entity" in msg:
                outcome = "not_occupied"
            else:
                outcome = "error"
                detail = e.__class__.__name__
        except Exception as e:
            outcome = "error"
            detail = f"{e.__class__.__name__}: {str(e)[:80]}"
        entry = f"{channel}" + (f" (+{n})" if n else "") + (f" [{detail}]" if detail else "")
        outcomes[outcome].append(entry)
        if n:
            print(f"  {channel}: +{n}")
        total += n

    conn.close()
    await client.disconnect()

    # Diagnostic footer - one line per outcome group so the workflow log tells
    # us at a glance which channels are alive, which are dead, and which need
    # attention. Live channels stay implicit (they already printed +N above).
    print(f"\nCollection done: {total} new messages across {len(channels)} channels.")
    print("--- per-channel outcomes ---")
    for group, entries in outcomes.items():
        print(f"  {group} ({len(entries)}): {', '.join(entries) if entries else '-'}")


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
