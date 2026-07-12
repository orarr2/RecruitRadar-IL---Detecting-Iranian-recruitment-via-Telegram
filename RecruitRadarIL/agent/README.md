# agent/ — Adaptive Detection & Discovery

The optional layer of RecruitRadar-IL on top of the baseline. The main notebook
and `run_offline.py` keep working exactly as before, and this folder can be
ignored entirely. Related plan: [`../docs/DESIGN.md`](../docs/DESIGN.md) (the
LLM layer originally described there was later removed by decision — the
current pipeline is rules + IsolationForest + verdicts + Snorkel only).

## What it adds

| Delta | Feature |
|---|---|
| **D1** | Channel discovery: mines already-collected messages for `@usernames` / `t.me/...` links, scores candidates by rule base-rate × cross-channel centrality, and proposes up to 20 per run. Nothing is collected until a human approves. |
| **D2** | Closed feedback loop: rules, IsolationForest and the analyst's own verdicts all become Labeling Functions; a Snorkel label model learns their accuracies from the data and emits `p_recruitment` per message. Verdicts persist, so the system sharpens with every review session. |
| **D3** | CSV digest delivered to Telegram: each run ships one file with the leads that scored `p_recruitment >= 0.5` **and** were not shipped in any previous digest. Russian rows are auto-translated to Hebrew (Google Translate, cached per message) and the `text` cell is word-wrapped for phone-readable display. Once a lead ships, it is recorded in `sent_leads` and never resurfaces. |

**No LLM decides whether a message is recruitment.** No model is trained on
message content. Scoring is entirely rule- and statistics-driven.

## Contents

| File | Purpose |
|---|---|
| `AdaptiveRecruitRadar.ipynb` | The whole adaptive pipeline, end to end. |
| `pipeline.py` | Importable headless engine (`run_pipeline`, `unsent_flagged`, `mark_sent`, `top_leads`, `list_proposals`, `approve_channel`, ...). Also a cron entry point: `python agent/pipeline.py`. |
| `telegram_bot.py` | A Telegram control bot: `/scan` (delivers a CSV of new leads), `/top`, `/approve`, ... |
| `cloud_digest.py` | The GitHub Actions entry point - one scan, one CSV, done. |
| `annotator_app.py` | Streamlit UI for the analyst: review queue, channel-proposal approval, weekly metrics. |
| `collect_headless.py` | Non-interactive collector (StringSession) for cloud runs. |
| `requirements.txt` | Extra dependencies for this layer. |
| `.env.example` | Optional configuration (retention, bot token). |

## Quickstart

```bash
cd RecruitRadarIL
python -m venv .venv && .venv\Scripts\activate     # or source .venv/bin/activate
pip install -r agent/requirements.txt

jupyter notebook agent/AdaptiveRecruitRadar.ipynb   # run all cells
streamlit run agent/annotator_app.py                # review the results
```

Collect first with the main notebook or `python agent/collect_headless.py`
(the cloud workflow does this automatically) - the adaptive layer only scores
messages that are already in the DB.

## Drive it from your phone (Telegram bot)

`telegram_bot.py` turns the pipeline into something you run and read entirely
from the Telegram app on your phone — no public IP, no port forwarding. The bot
uses long-polling (it reaches *out* to Telegram), so as long as this machine is
on and online you can command it from anywhere.

```bash
# 1. Create a bot: in Telegram, message @BotFather -> /newbot -> copy the token
# 2. Put the token in agent/.env:   TELEGRAM_BOT_TOKEN=123456:ABC...
# 3. Start it (from RecruitRadarIL/):
python agent/telegram_bot.py
# 4. Message your bot /start - it replies with your chat id. Put that in
#    agent/.env as BOT_OWNER_ID=... and restart, so only you can drive it.
```

Full step-by-step (Hebrew + English): [`TELEGRAM_SETUP.md`](TELEGRAM_SETUP.md).

| Command | Does |
|---|---|
| `/scan` | Re-score the corpus and receive the new-leads CSV (marks them as sent). |
| `/top [N]` | Quick text preview of top N flagged messages. Does not mark as sent. |
| `/proposals` | Pending channel-discovery proposals. |
| `/approve NAME` | Approve a proposed channel (appended to `channels_extra.txt`). |
| `/reject NAME` | Reject a proposed channel. |
| `/status` | Summary of the last run. |

Only `BOT_OWNER_ID` can use the bot; messages from anyone else are ignored.

**Twice-a-day automatic push.** The cloud workflow already does this on the
default schedule (see [`CLOUD_SETUP.md`](CLOUD_SETUP.md)). If you prefer a
local schedule instead, wire `python agent/cloud_digest.py` into Windows Task
Scheduler with *"Wake the computer to run this task"* so it fires even when
the laptop is asleep. A shut-down machine can't run it — that's when the cloud
workflow (or a Raspberry Pi) is the next step.

## The loop

1. Collect (main notebook or `collect_headless.py`)
2. Score (`pipeline.py`)
3. Review (annotator UI or the CSV in your bot chat)
4. Your verdicts land in `data/verdicts.jsonl`; `git push` and they enter the
   next cloud run as LF4
5. Repeat

Metrics for each run are appended to `data/metrics_weekly.jsonl`.

## Data artifacts (all local, all under `data/` and `exports/`)

| Artifact | What it holds |
|---|---|
| `lf_votes`, `snorkel_labels`, `channel_proposals`, `sent_leads` (SQLite) | Vote matrix, fitted probabilities, discovery candidates, per-run "already delivered" set. |
| `data/verdicts.jsonl` | Append-only analyst decisions (LF4 input). Tracked in git so cloud runs see it. |
| `data/agent_trace.jsonl` | Append-only step log — every automated decision is reconstructible from disk. |
| `data/metrics_weekly.jsonl` | One metrics row per run. |
| `exports/review_queue_adaptive_*.csv`, `exports/channel_proposals_*.md` | Ranked queue and proposal digest, local only. |

## Configuration

Copy `.env.example` to `.env` in this folder. Notable knobs: `RETENTION_DAYS`
(default 90; messages older than that are dropped each run), plus the bot
token and owner id.

## Troubleshooting

- **snorkel won't install** (it pulls in torch) — the pipeline automatically
  falls back to a transparent weighted vote and says so in the metrics.
- **Empty review queue in the annotator** — run the pipeline first; the
  annotator only reads what the pipeline wrote.
- **Cloud digest silent** — that's the intended behavior when there are no
  new leads. Check the "Run scan and deliver digest to Telegram" step in the
  Actions log; it prints `Nothing new to deliver` when the run genuinely
  produced no fresh leads.

## Ground rules (inherited, unchanged)

Public channels only · everything runs on the researcher's own machine ·
sender ids are stored pseudonymised · no message is ever sent by the system ·
no channel is monitored without explicit human approval · every output is a
lead for review, **not** a conclusion.
