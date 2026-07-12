"""
RecruitRadar-IL cloud digest - the GitHub Actions entry point.

Runs one full scoring pass and delivers the new-leads CSV to your Telegram
chat via the Bot API. Scoring is entirely rule- and statistics-driven; no LLM
decides anything, no model is trained on message content.

Delivery rule: only messages that (a) score p_recruitment >= 0.5 and
(b) have not been included in any previous digest. Once a message goes out
it is recorded in the sent_leads table and will never appear again - even if
a later run pushes its p_recruitment higher. If a run finds nothing new,
the workflow logs it and delivers nothing (silence on the phone).

Environment (set as repository secrets in CI):
  TELEGRAM_BOT_TOKEN   required - the @BotFather token
  BOT_OWNER_ID         recommended - your chat id. If missing or wrong the
                       script falls back to getUpdates: whoever messaged the
                       bot most recently (within 24h) receives the digest,
                       together with the chat id to save as the secret.

Usage:
    python agent/cloud_digest.py
"""

import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "agent"))

import pipeline                 # noqa: E402
import telegram_bot as tb       # noqa: E402  (send/deliver helpers; main() is guarded)


def discover_chat_id():
    """Last-resort recipient: the most recent chat that messaged the bot."""
    data = tb.api("getUpdates")
    if not data.get("ok"):
        return None
    for upd in reversed(data.get("result", [])):
        msg = upd.get("message") or upd.get("edited_message")
        if msg:
            return msg["chat"]["id"]
    return None


def main():
    if not tb.TOKEN:
        sys.exit("TELEGRAM_BOT_TOKEN is not set - add it as a repository secret.")
    if not tb.api("getMe").get("ok"):
        sys.exit("Telegram rejected the token - check the TELEGRAM_BOT_TOKEN secret.")

    print("Scanning ...")
    summary = pipeline.run_pipeline()
    print(f"Run {summary['run_id']}: {summary['n_messages']} messages, "
          f"{summary['n_flagged']} flagged total (p>=0.5).")

    fresh = pipeline.unsent_flagged()
    n_new = len(fresh)
    print(f"Unsent flagged: {n_new}.")

    if n_new == 0:
        print("Nothing new to deliver - staying silent.")
        return

    chat_id = tb.OWNER or discover_chat_id()
    if chat_id is None:
        print("No BOT_OWNER_ID and nobody messaged the bot recently, so there is "
              "no chat to deliver to. Skipping.")
        return

    delivered = tb.deliver_digest(chat_id, summary)
    if delivered and not tb.OWNER:
        # Piggy-back a one-off note on the same chat: we found a fallback
        # recipient via getUpdates; ask them to set the secret so future runs
        # don't depend on someone messaging the bot in the last 24h.
        tb.send(chat_id, f"Note: no BOT_OWNER_ID secret is set. Your chat id is "
                         f"{chat_id} - save it as the BOT_OWNER_ID repository "
                         f"secret so digests always reach you.")


if __name__ == "__main__":
    main()
