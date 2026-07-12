"""
RecruitRadar-IL cloud digest - the GitHub Actions entry point.

Runs one full scoring pass (agent/pipeline.py) and pushes the summary + top
leads to your Telegram chat via the Bot API. No polling, no long-running
process: fire, report, exit. Scheduled twice a day by
.github/workflows/telegram-digest.yml, and runnable on demand from the
Actions tab (works from the GitHub mobile app too).

Environment (set as repository secrets in CI):
  TELEGRAM_BOT_TOKEN   required - the @BotFather token
  BOT_OWNER_ID         recommended - your chat id. If missing or wrong, the
                       script falls back to getUpdates: whoever messaged the
                       bot most recently (within 24h) receives the digest,
                       together with the chat id to put in the secret.

Usage:
    python agent/cloud_digest.py           # fast scan (no LLM)
    python agent/cloud_digest.py --deep    # also run the LLM mid-band pass
                                           # (needs a reachable OLLAMA_HOST;
                                           #  abstains gracefully without one)
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "agent"))

import pipeline                 # noqa: E402
import telegram_bot as tb       # noqa: E402  (send/fmt helpers; main() is guarded)


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
    deep = "--deep" in sys.argv
    if not tb.TOKEN:
        sys.exit("TELEGRAM_BOT_TOKEN is not set - add it as a repository secret.")
    if not tb.api("getMe").get("ok"):
        sys.exit("Telegram rejected the token - check the TELEGRAM_BOT_TOKEN secret.")

    chat_id = tb.OWNER or discover_chat_id()
    if chat_id is None:
        sys.exit("No BOT_OWNER_ID secret and nobody messaged the bot in the last "
                 "24h, so there is no chat to deliver to. Open Telegram, send the "
                 "bot /start, and re-run this workflow.")

    print(f"Scanning (deep={deep}) ...")
    summary = pipeline.run_pipeline(use_llm=deep)
    print(f"Run {summary['run_id']}: {summary['n_messages']} messages, "
          f"{summary['n_flagged']} flagged.")

    tb.send(chat_id, "RecruitRadar-IL automatic digest\n\n" + tb.fmt_status(summary))
    tb.send(chat_id, tb.fmt_leads(pipeline.top_leads(10)))
    props = pipeline.list_proposals()
    if props:
        tb.send(chat_id, tb.fmt_proposals(props) +
                "\n\nApprove by adding the name to channels_extra.txt (or via "
                "the local bot's /approve).")
    if not tb.OWNER:
        tb.send(chat_id, f"Note: the BOT_OWNER_ID secret is not set (or wrong). "
                         f"Your chat id is {chat_id} - save it as the "
                         f"BOT_OWNER_ID repository secret so digests always "
                         f"reach you directly.")
    print("Digest delivered.")


if __name__ == "__main__":
    main()
