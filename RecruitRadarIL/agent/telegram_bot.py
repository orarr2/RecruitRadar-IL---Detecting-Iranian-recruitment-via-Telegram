"""
RecruitRadar-IL Telegram control bot.

Runs on the researcher's own machine and is driven entirely from the Telegram
app on your phone. It uses long-polling - the script reaches OUT to Telegram's
servers - so it needs no public IP and no port forwarding. As long as this
machine is on and online, you can command it from anywhere.

The bot is only a remote control + inbox: the actual scoring runs here, locally,
against data/recruitradar.db and your local Ollama. Nothing is sent anywhere
except the summary messages you asked for.

Commands (also registered so Telegram shows hints):
  /scan          re-score the corpus: rules + appearance + label model + discovery
  /scan deep     same, but also run the local LLM on the undecided mid-band (slow on CPU)
  /top [N]       top N leads by p_recruitment (default 10)
  /proposals     pending channel-discovery proposals
  /approve NAME  approve a proposed channel (adds it to channels_extra.txt)
  /reject NAME   reject a proposed channel
  /status        summary of the last run
  /help          this message

Setup (once):
  1. In Telegram, message @BotFather -> /newbot -> follow prompts -> copy the token.
  2. Put it in agent/.env:   TELEGRAM_BOT_TOKEN=123456:ABC...
  3. Run:  python agent/telegram_bot.py
  4. Message your bot /start - it replies with your chat id. Put that in
     agent/.env as BOT_OWNER_ID=... and restart, so only you can drive it.

Everything it surfaces is a lead for review, not a conclusion.
"""

import os
import sys
import time
import threading
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "agent"))
import pipeline  # noqa: E402  (engine; see agent/pipeline.py)


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

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
# Tolerate a value that was pasted with surrounding quotes or whitespace
# (a common mistake when saving the secret) - strip them before parsing.
OWNER = os.getenv("BOT_OWNER_ID", "").strip().strip('"').strip("'").strip()
OWNER = int(OWNER) if OWNER.lstrip("-").isdigit() else None
API = f"https://api.telegram.org/bot{TOKEN}"

_scan_lock = threading.Lock()

HELP = (
    "RecruitRadar-IL control bot\n\n"
    "/scan - re-score the corpus (fast)\n"
    "/scan deep - also run the local LLM on the mid-band (slow on CPU)\n"
    "/top [N] - top N leads (default 10)\n"
    "/proposals - pending channel proposals\n"
    "/approve NAME - approve a proposed channel\n"
    "/reject NAME - reject a proposed channel\n"
    "/status - last run summary\n"
    "/help - this message\n\n"
    "Leads for review, not conclusions."
)


def api(method, **params):
    try:
        r = requests.post(f"{API}/{method}", json=params, timeout=60)
        return r.json()
    except requests.RequestException as e:
        print(f"[api] {method} failed: {e}")
        return {"ok": False}


def send(chat_id, text):
    # Telegram hard-limits a message to 4096 chars; chunk on line boundaries.
    text = text or "(empty)"
    while text:
        chunk, text = text[:3900], text[3900:]
        if text and "\n" in chunk:
            cut = chunk.rfind("\n")
            text, chunk = chunk[cut:] + text, chunk[:cut]
        r = api("sendMessage", chat_id=chat_id, text=chunk)
        # Surface Telegram-side failures instead of dropping messages silently -
        # e.g. 429 rate limit, blocked chat, or a bad chat_id. Printed to the
        # workflow log so the failure is visible without inspecting the API.
        if not r.get("ok"):
            print(f"[send] telegram rejected: {r.get('error_code')} "
                  f"{r.get('description')} (chat_id={chat_id}, "
                  f"chunk_len={len(chunk)})")


def _snippet(s, n=90):
    return " ".join((s or "").split())[:n]


def fmt_leads(leads):
    if not leads:
        return "No leads yet - run /scan first."
    out = [f"Top {len(leads)} leads by p_recruitment:\n"]
    for i, r in enumerate(leads, 1):
        out.append(f"{i}. p={r['p']:.3f}  [{r['channel']}]\n   {_snippet(r['text'])}")
    return "\n".join(out)


def fmt_proposals(props):
    if not props:
        return "No pending channel proposals."
    out = [f"{len(props)} pending proposals (approve with /approve NAME):\n"]
    for r in props:
        out.append(f"@{r['candidate']}  base={r['base_rate']:.2f} cent={r['centrality']:.2f}")
    return "\n".join(out)


def fmt_status(m):
    if not m:
        return "No run yet - send /scan."
    return (f"Last run {m['run_id']}\n"
            f"messages: {m['n_messages']} | channels: {m['n_channels']}\n"
            f"LF coverage: {m['lf_coverage']:.0%}\n"
            f"flagged (p>=0.5): {m.get('n_flagged', '-')}\n"
            f"pending proposals: {m['n_proposals_pending']}\n"
            f"verdicts so far: {m['n_verdicts']}\n"
            f"label model: {m['label_model']}\n"
            f"llm: {m['llm_calls']} calls"
            + (f" (skipped: {m['llm_skipped']})" if m.get('llm_skipped') else ""))


def do_scan(chat_id, deep):
    if not _scan_lock.acquire(blocking=False):
        send(chat_id, "A scan is already running - hold on.")
        return
    try:
        send(chat_id, f"Scanning{' (deep, LLM on - this can take a while on CPU)' if deep else ''}...")
        prog = {"last": 0}

        def on_progress(i, total):
            # throttle progress pings so we do not spam the chat
            if i - prog["last"] >= 100 or i == total:
                prog["last"] = i
                send(chat_id, f"  LLM {i}/{total} classified...")

        summary = pipeline.run_pipeline(use_llm=deep, on_progress=on_progress)
        send(chat_id, "Scan complete.\n\n" + fmt_status(summary))
        send(chat_id, fmt_leads(pipeline.top_leads(5)))
    except Exception as e:
        send(chat_id, f"Scan failed: {e.__class__.__name__}: {e}")
    finally:
        _scan_lock.release()


def handle(update):
    msg = update.get("message") or update.get("edited_message")
    if not msg or "text" not in msg:
        return
    chat_id = msg["chat"]["id"]
    text = msg["text"].strip()
    cmd, _, arg = text.partition(" ")
    cmd, arg = cmd.lower(), arg.strip()

    if cmd in ("/start", "start"):
        if OWNER is None:
            send(chat_id, f"Your chat id is {chat_id}.\nAdd BOT_OWNER_ID={chat_id} to "
                          f"agent/.env and restart the bot so only you can use it.\n\n" + HELP)
        elif chat_id == OWNER:
            send(chat_id, "Ready.\n\n" + HELP)
        else:
            send(chat_id, "This bot is private.")
        return

    # Owner guard for every real command.
    if OWNER is None:
        send(chat_id, "Set BOT_OWNER_ID in agent/.env first (send /start to get your id).")
        return
    if chat_id != OWNER:
        return  # ignore strangers silently

    if cmd in ("/help", "/menu"):
        send(chat_id, HELP)
    elif cmd == "/scan":
        threading.Thread(target=do_scan, args=(chat_id, arg.lower() == "deep"),
                         daemon=True).start()
    elif cmd == "/top":
        n = int(arg) if arg.isdigit() else 10
        send(chat_id, fmt_leads(pipeline.top_leads(min(n, 25))))
    elif cmd in ("/proposals", "/discover"):
        send(chat_id, fmt_proposals(pipeline.list_proposals()))
    elif cmd == "/approve":
        if not arg:
            send(chat_id, "Usage: /approve channel_name")
        else:
            r = pipeline.approve_channel(arg)
            send(chat_id, f"Approved @{r} - it enters CHANNELS on the next collect."
                 if r else f"No pending proposal named '{arg}'.")
    elif cmd == "/reject":
        if not arg:
            send(chat_id, "Usage: /reject channel_name")
        else:
            r = pipeline.reject_channel(arg)
            send(chat_id, f"Rejected @{r}." if r else f"No pending proposal named '{arg}'.")
    elif cmd == "/status":
        send(chat_id, fmt_status(pipeline.last_run()))
    else:
        send(chat_id, "Unknown command. /help for the list.")


def main():
    if not TOKEN:
        sys.exit("TELEGRAM_BOT_TOKEN is not set. Create a bot with @BotFather, "
                 "then put the token in agent/.env (see agent/.env.example).")
    me = api("getMe")
    if not me.get("ok"):
        sys.exit("Telegram rejected the token. Double-check TELEGRAM_BOT_TOKEN in agent/.env.")
    print(f"Bot @{me['result']['username']} is up. "
          f"Owner: {OWNER if OWNER else 'UNSET - send /start to your bot to learn your id'}.")
    api("setMyCommands", commands=[
        {"command": "scan", "description": "re-score the corpus (add 'deep' for LLM)"},
        {"command": "top", "description": "top N leads by p_recruitment"},
        {"command": "proposals", "description": "pending channel proposals"},
        {"command": "approve", "description": "approve a proposed channel"},
        {"command": "reject", "description": "reject a proposed channel"},
        {"command": "status", "description": "last run summary"},
        {"command": "help", "description": "command list"},
    ])

    offset = None
    print("Polling for commands (Ctrl+C to stop)...")
    while True:
        try:
            r = requests.get(f"{API}/getUpdates",
                             params={"offset": offset, "timeout": 50}, timeout=60)
            data = r.json()
        except requests.RequestException as e:
            print(f"[poll] {e}; retrying in 3s")
            time.sleep(3)
            continue
        if not data.get("ok"):
            time.sleep(3)
            continue
        for update in data["result"]:
            offset = update["update_id"] + 1
            try:
                handle(update)
            except Exception as e:
                print(f"[handle] {e.__class__.__name__}: {e}")


if __name__ == "__main__":
    main()
