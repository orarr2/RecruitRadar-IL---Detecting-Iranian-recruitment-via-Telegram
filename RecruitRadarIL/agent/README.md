# agent/ — Adaptive Detection & Discovery

The optional autonomous layer of RecruitRadar-IL. It implements the design
approved in [`../docs/DESIGN.md`](../docs/DESIGN.md) (Architecture A:
deterministic pipeline + Snorkel) **without touching the baseline** — the
main notebook and `run_offline.py` keep working exactly as before, and this
folder can be ignored entirely.

## What it adds

| Delta | Feature |
|---|---|
| **D1** | Channel discovery: mines already-collected messages for `@usernames` / `t.me/...` links, scores candidates by rule base-rate × cross-channel centrality, and proposes up to 20 per run. Nothing is collected until a human approves. |
| **D2** | Tiered scoring: a **local** LLM (Ollama) classifies only the messages where the cheap detectors disagree or abstain. Responses are cached by `(sha256(text), prompt_version)` — re-runs on an unchanged corpus are free, and the cost of a run is always $0 because the model is local. |
| **D3** | Closed feedback loop: rules, IsolationForest, the LLM and the analyst's own verdicts all become Labeling Functions; a Snorkel label model learns their accuracies from the data and emits `p_recruitment` per message. Verdicts persist, so the system sharpens with every review session. |

## Contents

| File | Purpose |
|---|---|
| `AdaptiveRecruitRadar.ipynb` | The whole adaptive pipeline, end to end (sections A0–A11 map to milestones M0–M4 of the design). |
| `pipeline.py` | The same pipeline as an importable headless engine (`run_pipeline`, `top_leads`, `list_proposals`, `approve_channel`, ...). Also a cron entry point: `python agent/pipeline.py [--deep]`. |
| `telegram_bot.py` | A Telegram control bot: drive the whole thing from your phone (`/scan`, `/top`, `/approve`, ...). |
| `annotator_app.py` | Streamlit UI for the analyst: review queue, channel-proposal approval, weekly metrics. |
| `requirements.txt` | Extra dependencies for this layer. |
| `.env.example` | Optional configuration (model choice, budget, retention, bot token). |

## Quickstart

```bash
cd RecruitRadarIL
python -m venv .venv && .venv\Scripts\activate     # or source .venv/bin/activate
pip install -r agent/requirements.txt

# optional - the local LLM layer (skipped gracefully when absent):
#   install Ollama from https://ollama.com, then
ollama pull llama3.2:3b        # or: ollama pull qwen2.5:7b

jupyter notebook agent/AdaptiveRecruitRadar.ipynb   # run all cells
streamlit run agent/annotator_app.py                # review the results
```

The notebook seeds a synthetic demo corpus when the database is empty, so it
runs with zero setup. For real data, collect with the main notebook first.

## Drive it from your phone (Telegram bot)

`telegram_bot.py` turns the pipeline into something you run and read entirely
from the Telegram app on your phone — no public IP, no port forwarding. The bot
uses long-polling (it reaches *out* to Telegram), so as long as this machine is
on and online you can command it from anywhere. The phone is the remote control
and inbox; the scoring still runs here, locally.

```bash
# 1. Create a bot: in Telegram, message @BotFather -> /newbot -> copy the token
# 2. Put the token in agent/.env:   TELEGRAM_BOT_TOKEN=123456:ABC...
# 3. Start it (from RecruitRadarIL/):
python agent/telegram_bot.py
# 4. Message your bot /start - it replies with your chat id. Put that in
#    agent/.env as BOT_OWNER_ID=... and restart, so only you can drive it.
```

| Command | Does |
|---|---|
| `/scan` | Re-score the corpus (rules + appearance + label model + discovery). |
| `/scan deep` | Same, but also run the local LLM on the undecided mid-band (slow on CPU). |
| `/top [N]` | Top N leads by `p_recruitment` (default 10). |
| `/proposals` | Pending channel-discovery proposals. |
| `/approve NAME` | Approve a proposed channel (appended to `channels_extra.txt`). |
| `/reject NAME` | Reject a proposed channel. |
| `/status` | Summary of the last run. |

Only `BOT_OWNER_ID` can use the bot; messages from anyone else are ignored.

**Twice-a-day automatic push.** For a proactive digest (rather than pulling with
`/scan`), schedule the headless engine with Windows Task Scheduler:

```
Program:   python
Arguments: agent/pipeline.py
Start in:  <full path to RecruitRadarIL>
Trigger:   daily, repeat every 12 hours
```

Tick *"Wake the computer to run this task"* so it fires even when the laptop is
asleep (a closed lid). A shut-down machine can't run it — that's when an
always-on box (e.g. a Raspberry Pi running the same `pipeline.py`) is the next
step. The engine only writes the exports/DB; pair it with a one-line bot push if
you want the summary to land in Telegram automatically.

## The weekly loop

1. Collect (main notebook) → 2. score (this notebook) → 3. review
(annotator) → 4. your verdicts re-enter the next fit as LF4 → repeat.
Metrics for each run are appended to `data/metrics_weekly.jsonl`; targets
live in DESIGN section 9.

## Data artifacts (all local, all under `data/` and `exports/`)

| Artifact | What it holds |
|---|---|
| `lf_votes`, `llm_cache`, `snorkel_labels`, `channel_proposals` (SQLite) | Vote matrix, cached LLM output, fitted probabilities, discovery candidates. |
| `data/verdicts.jsonl` | Append-only analyst decisions (LF4 input). |
| `data/agent_trace.jsonl` | Append-only step log — every automated decision is reconstructible from disk (N4). |
| `data/metrics_weekly.jsonl` | One metrics row per run. |
| `exports/review_queue_adaptive_*.csv`, `exports/channel_proposals_*.md` | Ranked queue and proposal digest. |

## Configuration

Copy `.env.example` to `.env` in this folder. Notable knobs: `OLLAMA_MODEL`
(`llama3.2:3b` default, `qwen2.5:7b` for better multilingual), `MAX_LLM_CALLS`
(budget ceiling — the layer-2 pass aborts rather than overruns), and
`RETENTION_DAYS` (default 90; messages older than that are dropped each run).

## Troubleshooting

- **Ollama not installed / not running** — LF3 abstains, everything else
  works. The affected rows carry `llm_missing = 1`.
- **snorkel won't install** (it pulls in torch) — the notebook automatically
  falls back to a transparent weighted vote and says so.
- **Empty review queue in the annotator** — run the notebook first; the
  annotator only reads what the pipeline wrote.

## Ground rules (inherited, unchanged)

Public channels only · everything runs on the researcher's own machine ·
sender ids are stored pseudonymised · no message is ever sent by the system ·
no channel is monitored without explicit human approval · every output is a
lead for review, **not** a conclusion.
