# RecruitRadar-IL

End-to-end research pipeline for surfacing posts on **public** Telegram channels
that match documented patterns of Iranian recruitment of Israelis - low-effort
"missions" for fast cash, crypto payment, photographing sites, poster-hanging,
moving contact to private apps. Each user runs the pipeline locally against
their own Telegram account and channel list; no central server, no shared data.

The notebook is a **runnable** pipeline: connect → collect → store →
pseudonymize → rule-based weak supervision → behavioral features → ranked
review queue → anonymized export.

On top of it sits an **optional adaptive layer** ([`RecruitRadarIL/agent/`](RecruitRadarIL/agent)) -
a separate notebook that adds a Snorkel label model over all the signals, a
local-LLM classifier for the ambiguous middle band, analyst feedback that
sharpens the ranking run after run, and human-approved channel discovery. The
baseline works fine without it; see [`agent/README.md`](RecruitRadarIL/agent/README.md).

---

## Quick start

### 1. Clone and install

```bash
git clone https://github.com/orarr2/RecruitRadar-IL---Detecting-Iranian-recruitment-via-Telegram.git
cd RecruitRadar-IL---Detecting-Iranian-recruitment-via-Telegram/RecruitRadarIL
pip install -r requirements.txt
```

All code, configuration, and data live under the [`RecruitRadarIL/`](RecruitRadarIL) folder - run every command shown below from inside it.

Python 3.9+ is required.

### 2. Get your own Telegram API credentials

1. Go to **https://my.telegram.org** and log in with the phone number on your
   Telegram account.
2. Click **API development tools**.
3. Fill in any **App title** and **Short name** (lowercase letters/digits,
   5-32 chars; no special characters), pick **Desktop** as platform, write a
   short description in plain ASCII.
4. On the next page you get your **`api_id`** (a number) and **`api_hash`**
   (a 32-char hex string). Treat `api_hash` like a password - it's shown in
   full only on that page.

### 3. Configure credentials (optional - the notebook will prompt)

You can put credentials in a `.env` file, **or skip this step entirely** and let
the notebook prompt you for each value interactively when you run section 1
(the API hash, login code and 2FA password are typed in hidden). Any value
that's missing is simply asked for; if you leave it blank, the live-collection
cells stay disabled and the offline analysis still runs - nothing crashes.

To use a `.env` instead of prompts:

```bash
cp .env.example .env
```

Open `.env` and fill in:

| Variable | What to put | Notes |
|---|---|---|
| `TELEGRAM_API_ID` | The number from step 2 | e.g. `12345678` |
| `TELEGRAM_API_HASH` | The 32-char hex from step 2 | Keep secret |
| `TELEGRAM_PHONE` | Your phone in **international format** | e.g. `+1234567890` - no spaces / dashes |
| `HASH_SALT` | 64-char random hex | Generate with `python -c "import secrets; print(secrets.token_hex(32))"` - use a fixed value to keep pseudonyms stable across runs |

`.env` is gitignored - it never leaves your machine.

### 4. Open the notebook

```bash
jupyter notebook RecruitRadarIL.ipynb
```

Run cells top-to-bottom.

- **Section 1:** if any credential is missing it's requested interactively
  (API id/hash, phone, salt). Missing answers don't crash - they just disable
  live collection.
- **Section 2:** Telethon prompts for the login code Telegram sends to your
  **Telegram app** (not SMS), then your two-step password if 2FA is enabled. A
  `recruitradar.session` file is created next to the notebook - future runs skip
  the login. Any failure leaves `client = None` and the offline pipeline runs on.
- **Section 3 (cell 10):** the seed list of public channels - verify each is
  live and replace/extend with channels you've confirmed.
- **Section 6 (cell 15):** runs the live collection **automatically** once you're
  logged in (no uncommenting needed).

### 5. (Optional) Run the headless analyzer

After (or instead of) collecting through the notebook, you can run the analysis
pipeline as a script:

```bash
python run_offline.py
```

It reads from `data/recruitradar.db`, scores every message, builds a
sender-level behavioral feature set, and writes an anonymized review queue to
`exports/review_queue_<timestamp>.csv` plus a `timeline.png` chart.

If your DB is empty, the script seeds a small synthetic Hebrew sample under
channel `__demo__` so the whole pipeline runs end-to-end as a demo.

---

## Customizing the detection

### Channels (cell 10)

Edit `CHANNELS` to a list of `("@username_or_id", "category")` tuples. Use
categories like `jobs`, `rides`, `tutoring`, `yad2_mirror`, `marketplace` -
they're used to compare base rates across channel types.

To find candidate channels, search these public directories for Hebrew job /
errand / classified keywords (`דרושים`, `ג'ובים`, `לוח`, `יד2`, `טרמפים`,
`שיעורים פרטיים`) in your region:

- [telegramsearchengine.com](https://telegramsearchengine.com/)
- [tgstat.com](https://tgstat.com/)
- [telemetryapp.io](https://www.telemetryapp.io/)
- [nicegram.app/hub/search](https://nicegram.app/hub/search)
- [tgdb.org](https://www.tgdb.org/)

### Rules (cell 21)

`RULES` is a dict of `{category: [hebrew_regex, ...]}` with weights in
`RULE_WEIGHTS`. The shipped lexicon targets recruitment patterns documented in
publicly reported Israeli cases. The rules are intentionally **noisy** - they
generate a positive seed set for a downstream model (Snorkel / weak
supervision), not a verdict.

To target a different language or country, replace the regexes with your
own - the rest of the pipeline (storage, behavioral features, suspicion score,
export) is language-agnostic.

### Suspicion score (cell 23)

`suspicion_score()` combines `rule_score` with light behavioral signals
(multi-channel + low-engagement sender, non-forwarded original). Adjust the
weights, or replace the function entirely with a trained classifier once you
have labels.

### Thresholds

- `DAYS_BACK` (cell 10) - how far back to collect per channel
- `MAX_PER_CHANNEL` (cell 10) - cap to keep first runs cheap
- `SLEEP_BETWEEN` (cell 10) - seconds between messages; do **not** lower below
  `0.5` - Telegram will rate-limit you
- `THRESHOLD` (cell 24) - minimum suspicion score for the review queue

---

## Project structure

```
.
├── README.md                This file
└── RecruitRadarIL/
    ├── RecruitRadarIL.ipynb     Main notebook (sections 1-13)
    ├── run_offline.py           Headless analyzer (rule-scoring only)
    ├── channels_extra.txt       Extra channels, loaded automatically (no code edits)
    ├── requirements.txt         Python dependencies
    ├── .env.example             Template for credentials (.env is gitignored)
    ├── .gitignore               Blocks .env, *.session, data/, exports/
    ├── LICENSE                  MIT
    ├── docs/
    │   └── DESIGN.md            Design doc for the adaptive layer
    └── agent/                   Optional adaptive layer (independent of the above)
        ├── AdaptiveRecruitRadar.ipynb   Snorkel + local LLM + discovery pipeline
        ├── annotator_app.py             Streamlit analyst UI (verdicts, approvals)
        ├── requirements.txt             Extra dependencies for this layer
        └── .env.example                 Optional agent configuration
```

Created at runtime inside `RecruitRadarIL/` (all gitignored):

```
├── .env                     Your real credentials
├── recruitradar.session     Telethon session - grants access to your Telegram
├── data/
│   ├── recruitradar.db      SQLite store of all collected messages
│   ├── raw/<channel>.jsonl  Raw provenance archive, one file per channel
│   └── txt/<channel>.txt    Plain-text snapshot DB (diffable over time)
│       + _runs_log.txt      Per-run message counts per channel
└── exports/
    ├── review_queue_*.csv   Anonymized ranked output (keyword + appearance)
    └── dashboard.html       Self-contained anomaly dashboard (open in a browser)
```

### Two detectors

1. **Keyword rules** (section 9) - a trilingual (Hebrew / Russian / English)
   lexicon of the documented recruitment playbook.
2. **Appearance anomalies** (section 11) - a per-channel Isolation Forest that
   learns "normal for this group" and flags posts that simply look odd, even
   with zero keyword hits. The two are blended into one ranked dashboard.

### Adding channels

Either edit `CHANNELS` in the notebook, or just append `username, category`
lines to `channels_extra.txt` - section 3 loads and de-duplicates them on the
next run, no code changes needed.

---

## License

MIT - see [LICENSE](RecruitRadarIL/LICENSE).
