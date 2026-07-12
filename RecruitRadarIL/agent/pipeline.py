"""
RecruitRadar-IL adaptive pipeline - importable headless engine.

Same scoring / discovery logic as agent/AdaptiveRecruitRadar.ipynb, packaged as
plain functions so the Telegram bot (and cron, and the notebook) can drive it
without a UI. Live Telegram collection stays in the main notebook or
agent/collect_headless.py; this engine scores and ranks whatever is already
in data/recruitradar.db, fits the Snorkel label model, refreshes the
channel-discovery proposals, and writes the exports.

Scoring is entirely rule- and statistics-driven: LF1 regex lexicon +
LF2 IsolationForest + LF4 analyst verdicts, fused by a Snorkel label model.
No LLM ever decides whether a message is recruitment; no model is trained on
message content.

Everything it produces is a lead for review, not a conclusion.

CLI (also the cron entry point):
    python agent/pipeline.py
"""

import os
import re
import sys
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

# This engine always operates from the project root so the relative data/ and
# exports/ trees line up with the rest of the project.
ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
import run_offline as base   # RULES / apply_rules / init_db / hash_user_id


# ── Configuration (override in agent/.env) ───────────────────────────────────
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

RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "90"))
MAX_PROPOSALS  = 20
FLAG_THRESHOLD = 0.5

ABSTAIN, NOT_REC, REC = -1, 0, 1
LF1_HI          = 2.0
LF2_HI, LF2_LO  = 0.75, 0.25
LF_COLS = ["lf_rules", "lf_iso", "lf_verdicts"]

DATA_DIR      = ROOT / "data"
EXPORT_DIR    = ROOT / "exports"
DB_PATH       = DATA_DIR / "recruitradar.db"
VERDICTS_PATH = DATA_DIR / "verdicts.jsonl"
TRACE_PATH    = DATA_DIR / "agent_trace.jsonl"
METRICS_PATH  = DATA_DIR / "metrics_weekly.jsonl"
CHANNELS_EXTRA = ROOT / "channels_extra.txt"

SCHEMA = """
CREATE TABLE IF NOT EXISTS lf_votes (
    channel TEXT NOT NULL, msg_id INTEGER NOT NULL, lf_name TEXT NOT NULL,
    vote INTEGER NOT NULL, ts TEXT, PRIMARY KEY (channel, msg_id, lf_name));
CREATE TABLE IF NOT EXISTS snorkel_labels (
    channel TEXT NOT NULL, msg_id INTEGER NOT NULL, p_recruitment REAL,
    label_model_version TEXT, ts TEXT,
    PRIMARY KEY (channel, msg_id));
CREATE TABLE IF NOT EXISTS channel_proposals (
    candidate TEXT PRIMARY KEY, source TEXT, base_rate_score REAL,
    centrality_score REAL, sample_msg_ids TEXT, status TEXT DEFAULT 'pending',
    decided_at TEXT);
CREATE TABLE IF NOT EXISTS sent_leads (
    channel TEXT NOT NULL, msg_id INTEGER NOT NULL, sent_at TEXT,
    PRIMARY KEY (channel, msg_id));
CREATE TABLE IF NOT EXISTS translation_cache (
    text_hash TEXT PRIMARY KEY, source_lang TEXT, target_lang TEXT,
    translation TEXT, ts TEXT);
"""

URL_RE     = re.compile(r"https?://|t\.me/|www\.")
MENTION_RE = re.compile(r"@\w+")
HASHTAG_RE = re.compile(r"#\w+")
PHONE_RE   = re.compile(r"(?:\+?\d[\d\-\s]{7,}\d)")
WALLET_RE  = re.compile(r"\b(?:0x[a-fA-F0-9]{40}|[13][a-km-zA-HJ-NP-Z1-9]{25,34}|T[A-Za-z1-9]{33})\b")
EMOJI_RE   = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]")
HEB_RE     = re.compile(r"[֐-׿]")
CYR_RE     = re.compile(r"[Ѐ-ӿ]")
LAT_RE     = re.compile(r"[A-Za-z]")
TGUSER_RE  = re.compile(r"tg://user")
MENTION_CAND_RE = re.compile(r"(?<![\w.@])@([A-Za-z][A-Za-z0-9_]{3,31})\b")
TME_CAND_RE     = re.compile(r"t\.me/(?:s/)?([A-Za-z][A-Za-z0-9_]{3,31})\b", re.IGNORECASE)
RESERVED = {"addlist", "joinchat", "share", "proxy", "socks", "iv", "boost"}


def _connect():
    conn = base.init_db(DB_PATH)
    conn.executescript(SCHEMA)
    # A restored cache from an older pipeline version may still carry a table
    # that is not in the current schema. Drop it so a clean start is
    # guaranteed even without wiping the cache.
    conn.execute("DROP TABLE IF EXISTS llm_cache")
    # An old cache may also still contain synthetic rows that a previous
    # pipeline version seeded under channel '__demo__'. Wipe them defensively
    # every time we connect - real runs never insert into this channel, so
    # this is a no-op on a clean corpus.
    for tbl in ("lf_votes", "snorkel_labels", "sent_leads", "messages"):
        try:
            conn.execute(f"DELETE FROM {tbl} WHERE channel = '__demo__'")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    return conn


def _trace(run_id, step, kind, input_ref="", output_ref=""):
    with TRACE_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "run_id": run_id, "step": step, "kind": kind,
            "input_ref": str(input_ref), "output_ref": str(output_ref),
            "ts": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False) + "\n")


def _appearance_features(text, has_media, views, forwards, replies, is_forwarded):
    t = text or ""
    L = max(len(t), 1)
    letters = HEB_RE.findall(t), CYR_RE.findall(t), LAT_RE.findall(t)
    nl = sum(len(x) for x in letters) or 1
    words = t.split()
    return {
        "len": len(t), "n_words": len(words),
        "avg_word_len": sum(len(w) for w in words) / max(len(words), 1),
        "digit_ratio": sum(c.isdigit() for c in t) / L,
        "upper_ratio": sum(c.isupper() for c in t) / L,
        "n_urls": len(URL_RE.findall(t)), "n_mentions": len(MENTION_RE.findall(t)),
        "n_hashtags": len(HASHTAG_RE.findall(t)), "n_emoji": len(EMOJI_RE.findall(t)),
        "has_phone": int(bool(PHONE_RE.search(t))),
        "has_wallet": int(bool(WALLET_RE.search(t))),
        "has_tguser": int(bool(TGUSER_RE.search(t))),
        "heb_ratio": len(letters[0]) / nl, "cyr_ratio": len(letters[1]) / nl,
        "lat_ratio": len(letters[2]) / nl, "has_media": int(bool(has_media)),
        "views": float(views or 0), "forwards": float(forwards or 0),
        "replies": float(replies or 0), "is_forwarded": int(bool(is_forwarded)),
    }


def _compute_lf_rules(df):
    res = df["text"].apply(base.apply_rules)
    df["rule_score"] = res.apply(lambda r: r[0])
    df["rule_hits"]  = res.apply(lambda r: list(r[1].keys()))
    df["lf_rules"] = np.where(df["rule_score"] >= LF1_HI, REC,
                     np.where(df["rule_score"] == 0, NOT_REC, ABSTAIN))


def _compute_lf_iso(df):
    from sklearn.ensemble import IsolationForest
    feat_rows = df.apply(lambda r: _appearance_features(
        r["text"], r["has_media"], r["views"], r.get("forwards", 0),
        r.get("replies", 0), r["is_forwarded"]), axis=1)
    FEATS = pd.DataFrame(list(feat_rows), index=df.index)
    df["appearance_anomaly"] = 0.0
    for ch, idx in df.groupby("channel").groups.items():
        sub = FEATS.loc[idx]
        X = sub.fillna(0.0).values
        if len(idx) >= 30 and np.ptp(X, axis=0).sum() > 0:
            iso = IsolationForest(n_estimators=200, contamination="auto", random_state=42)
            iso.fit(X)
            raw = -iso.score_samples(X)
            rng = raw.max() - raw.min()
            norm = (raw - raw.min()) / rng if rng > 0 else np.zeros_like(raw)
        else:
            z = sub[["len", "n_urls", "has_wallet", "has_phone"]].fillna(0).sum(axis=1)
            norm = ((z - z.min()) / (z.max() - z.min())).values if z.max() > z.min() \
                   else np.zeros(len(idx))
        df.loc[idx, "appearance_anomaly"] = norm
    df["lf_iso"] = np.where(df["appearance_anomaly"] >= LF2_HI, REC,
                   np.where(df["appearance_anomaly"] <= LF2_LO, NOT_REC, ABSTAIN))


def _compute_lf_verdicts(df):
    df["lf_verdicts"] = ABSTAIN
    n = 0
    # verdicts.jsonl is tracked in the repo and starts empty; guard for both
    # "no file" and "file exists but has zero records / no verdict column yet"
    # so a fresh clone or the first cloud run does not crash.
    if VERDICTS_PATH.exists() and VERDICTS_PATH.stat().st_size > 0:
        v = pd.read_json(VERDICTS_PATH, lines=True)
        if "verdict" not in v.columns:
            return 0
        v = v[v["verdict"].isin(["accept", "reject"])]
        n = len(v)
        if n:
            v["label"] = (v["verdict"] == "accept").astype(int)
            exact = v.sort_values("decided_at").drop_duplicates(
                ["channel", "msg_id"], keep="last").set_index(["channel", "msg_id"])["label"]
            keyed = df.set_index(["channel", "msg_id"]).index
            df.loc[keyed.isin(exact.index), "lf_verdicts"] = \
                exact.reindex(keyed[keyed.isin(exact.index)]).values
            vm = v.merge(df[["channel", "msg_id", "sender_hash"]], on=["channel", "msg_id"])
            maj = vm.groupby(["sender_hash", "channel"])["label"].mean()
            maj = maj[(maj != 0.5) & (maj.index.get_level_values(0) != "")].round().astype(int)
            hist_idx = df.set_index(["sender_hash", "channel"]).index
            use = hist_idx.isin(maj.index) & (df["lf_verdicts"] == ABSTAIN).values
            df.loc[use, "lf_verdicts"] = maj.reindex(hist_idx[use]).values
    return n


def _fit_labels(df):
    L = df[LF_COLS].to_numpy(dtype=int)
    try:
        from snorkel.labeling.model import LabelModel
        lm = LabelModel(cardinality=2, verbose=False)
        try:
            lm.fit(L_train=L, n_epochs=500, log_freq=200, seed=42, progress_bar=False)
        except TypeError:
            lm.fit(L_train=L, n_epochs=500, log_freq=200, seed=42)
        p = lm.predict_proba(L)[:, 1]
        version = "snorkel-labelmodel-v2"
    except Exception:
        # 3 LFs now: rules, iso, verdicts. Verdicts get the heaviest weight
        # because they are direct human judgment; rules > iso because rules
        # are targeted and iso is a coarse anomaly signal.
        W = np.array([1.0, 0.7, 3.0])
        voted = (L != ABSTAIN)
        num = (np.where(voted, L, 0) * W).sum(axis=1)
        den = (voted * W).sum(axis=1)
        p = np.where(den > 0, num / np.maximum(den, 1e-9), np.nan)
        version = "fallback-weighted-vote-v2"
    df["p_recruitment"] = p
    return version


def _discover(df, conn):
    known = {c.lower() for c in df["channel"].unique()}
    if CHANNELS_EXTRA.exists():
        for line in CHANNELS_EXTRA.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                known.add(line.split(",")[0].strip().lstrip("@").lower())
    hits = []
    for r in df.itertuples():
        cands = {m.group(1).lower() for m in MENTION_CAND_RE.finditer(r.text)}
        cands |= {m.group(1).lower() for m in TME_CAND_RE.finditer(r.text)}
        for cand in cands:
            if cand in known or cand in RESERVED or cand.endswith("bot"):
                continue
            hits.append((cand, r.channel, int(r.msg_id), r.rule_score >= LF1_HI))
    conn.execute("DELETE FROM channel_proposals WHERE status='pending'")
    if hits:
        cand_df = pd.DataFrame(hits, columns=["candidate", "channel", "msg_id", "rule_hit"])
        agg = cand_df.groupby("candidate").agg(
            base_rate=("rule_hit", "mean"), n_channels=("channel", "nunique"))
        agg["centrality"] = agg["n_channels"] / max(df["channel"].nunique(), 1)
        agg["score"] = 0.6 * agg["base_rate"] + 0.4 * agg["centrality"]
        agg = agg.sort_values("score", ascending=False).head(MAX_PROPOSALS)
        samples = cand_df.groupby("candidate")[["channel", "msg_id"]].apply(
            lambda g: ",".join(f"{c}:{m}" for c, m in
                               zip(g["channel"].head(5), g["msg_id"].head(5))))
        conn.executemany(
            "INSERT OR IGNORE INTO channel_proposals VALUES (?,?,?,?,?, 'pending', NULL)",
            [(c, "mention_graph", float(row.base_rate), float(row.centrality),
              samples.get(c, "")) for c, row in agg.iterrows()])
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM channel_proposals WHERE status='pending'").fetchone()[0]


def _retention(conn):
    if RETENTION_DAYS <= 0:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
    old = conn.execute("SELECT COUNT(*) FROM messages WHERE date < ?",
                       (cutoff,)).fetchone()[0]
    if old:
        for tbl in ("lf_votes", "snorkel_labels", "sent_leads"):
            conn.execute(f"""DELETE FROM {tbl} WHERE (channel, msg_id) IN
                (SELECT channel, msg_id FROM messages WHERE date < ?)""",
                         (cutoff,))
        conn.execute("DELETE FROM messages WHERE date < ?", (cutoff,))
        conn.commit()
    return old


# ── Public API ───────────────────────────────────────────────────────────────

def run_pipeline():
    """Score the whole corpus, fit the label model, refresh proposals, write
    the review-queue CSV, append a metrics row. Returns a summary dict."""
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    conn = _connect()
    now_iso = datetime.now(timezone.utc).isoformat()
    df = pd.read_sql_query("SELECT * FROM messages", conn, parse_dates=["date"])
    df["text"] = df["text"].fillna("")

    if df.empty:
        conn.close()
        metrics = {
            "run_id": run_id, "ts": now_iso, "n_messages": 0, "n_channels": 0,
            "lf_coverage": 0.0, "label_model": "-", "n_flagged": 0,
            "n_proposals_pending": 0, "n_verdicts": 0, "retention_dropped": 0,
            "queue_csv": None,
        }
        with METRICS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(metrics, ensure_ascii=False) + "\n")
        _trace(run_id, "run_pipeline", "stage", output_ref="empty corpus")
        return metrics

    _compute_lf_rules(df)
    _compute_lf_iso(df)
    n_verdicts = _compute_lf_verdicts(df)

    rows = [(r.channel, int(r.msg_id), lf, int(getattr(r, lf)), now_iso)
            for r in df.itertuples() for lf in LF_COLS]
    conn.executemany("INSERT OR REPLACE INTO lf_votes VALUES (?,?,?,?,?)", rows)
    conn.commit()
    coverage = float((df[LF_COLS] != ABSTAIN).any(axis=1).mean())

    version = _fit_labels(df)
    conn.executemany(
        "INSERT OR REPLACE INTO snorkel_labels (channel, msg_id, p_recruitment, "
        "label_model_version, ts) VALUES (?,?,?,?,?)",
        [(r.channel, int(r.msg_id),
          None if pd.isna(r.p_recruitment) else float(r.p_recruitment),
          version, now_iso) for r in df.itertuples()])
    conn.commit()

    n_proposals = _discover(df, conn)

    stamp = f"{datetime.now():%Y%m%d_%H%M}"
    queue_path = EXPORT_DIR / f"review_queue_adaptive_{stamp}.csv"
    export_cols = ["channel", "category", "date", "msg_id", "sender_hash",
                   "p_recruitment"] + LF_COLS + \
                  ["rule_score", "appearance_anomaly", "text"]
    queue = df.sort_values("p_recruitment", ascending=False, na_position="last")
    queue[export_cols].to_csv(queue_path, index=False, encoding="utf-8-sig")

    n_flagged = int((df["p_recruitment"] >= FLAG_THRESHOLD).sum())
    dropped = _retention(conn)
    conn.close()

    metrics = {
        "run_id": run_id, "ts": now_iso, "n_messages": int(len(df)),
        "n_channels": int(df["channel"].nunique()), "lf_coverage": round(coverage, 4),
        "label_model": version, "n_flagged": n_flagged,
        "n_proposals_pending": int(n_proposals), "n_verdicts": int(n_verdicts),
        "retention_dropped": int(dropped), "queue_csv": queue_path.name,
    }
    with METRICS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(metrics, ensure_ascii=False) + "\n")
    _trace(run_id, "run_pipeline", "stage", output_ref=metrics)
    return metrics


def top_leads(n=10, min_p=0.0):
    """Return the top-n messages by p_recruitment as plain dicts (does not
    consider or update the sent-leads state - use unsent_flagged() for that)."""
    conn = _connect()
    rows = conn.execute(
        """SELECT s.channel, s.msg_id, s.p_recruitment, m.category, m.date, m.text
           FROM snorkel_labels s JOIN messages m
             ON s.channel=m.channel AND s.msg_id=m.msg_id
           WHERE s.p_recruitment IS NOT NULL AND s.p_recruitment >= ?
           ORDER BY s.p_recruitment DESC LIMIT ?""",
        (min_p, int(n))).fetchall()
    conn.close()
    return [{"channel": r[0], "msg_id": r[1], "p": r[2], "category": r[3],
             "date": r[4], "text": r[5] or ""} for r in rows]


# ── Sent-leads tracking ──────────────────────────────────────────────────────
# A digest carries only messages that (a) exceed FLAG_THRESHOLD and (b) have
# not been included in any previous digest. Once a message goes out, we insert
# it into sent_leads and it will never appear in another digest - even if a
# later run raises its p_recruitment.

def unsent_flagged(min_p=FLAG_THRESHOLD):
    """Return a DataFrame of flagged messages that have not been delivered yet,
    ordered by p_recruitment DESC. Columns match the digest CSV schema."""
    conn = _connect()
    df = pd.read_sql_query(
        """SELECT m.channel, m.msg_id, s.p_recruitment, m.category, m.date,
                  m.sender_hash, m.text
           FROM snorkel_labels s JOIN messages m
             ON s.channel = m.channel AND s.msg_id = m.msg_id
           LEFT JOIN sent_leads l
             ON l.channel = m.channel AND l.msg_id = m.msg_id
           WHERE s.p_recruitment IS NOT NULL
             AND s.p_recruitment >= ?
             AND l.channel IS NULL
           ORDER BY s.p_recruitment DESC""",
        conn, params=(min_p,))
    # Attach rule_hits for CSV context. Cheaper to re-derive than to store.
    if not df.empty:
        df["rule_hits"] = df["text"].apply(
            lambda t: ",".join(base.apply_rules(t or "")[1].keys()))
    conn.close()
    return df


def mark_sent(pairs):
    """Insert (channel, msg_id) pairs into sent_leads with the current UTC
    timestamp. Idempotent - repeat inserts are no-ops."""
    if not pairs:
        return 0
    conn = _connect()
    ts = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        "INSERT OR IGNORE INTO sent_leads (channel, msg_id, sent_at) VALUES (?,?,?)",
        [(c, int(m), ts) for c, m in pairs])
    conn.commit()
    n = conn.total_changes
    conn.close()
    return n


def list_proposals(status="pending", n=20):
    conn = _connect()
    rows = conn.execute(
        "SELECT candidate, source, base_rate_score, centrality_score, sample_msg_ids "
        "FROM channel_proposals WHERE status=? "
        "ORDER BY 0.6*base_rate_score + 0.4*centrality_score DESC LIMIT ?",
        (status, int(n))).fetchall()
    conn.close()
    return [{"candidate": r[0], "source": r[1], "base_rate": r[2],
             "centrality": r[3], "samples": r[4]} for r in rows]


def _decide_proposal(name, status):
    cand = name.strip().lstrip("@").lower()
    conn = _connect()
    row = conn.execute("SELECT status FROM channel_proposals WHERE candidate=?",
                       (cand,)).fetchone()
    if row is None:
        conn.close()
        return None
    conn.execute("UPDATE channel_proposals SET status=?, decided_at=? WHERE candidate=?",
                 (status, datetime.now(timezone.utc).isoformat(), cand))
    conn.commit()
    conn.close()
    return cand


def approve_channel(name):
    """Approve a proposed channel and append it to channels_extra.txt."""
    cand = _decide_proposal(name, "approved")
    if cand:
        with CHANNELS_EXTRA.open("a", encoding="utf-8") as f:
            f.write(f"\n# approved via bot {datetime.now():%Y-%m-%d}\n{cand}, uncategorized\n")
    return cand


def reject_channel(name):
    return _decide_proposal(name, "rejected")


# ── Russian → Hebrew translation (Google Translate) ──────────────────────────
# Messages from RU-language channels dominate the collected corpus. Reading a
# CSV in Cyrillic is slow work for a Hebrew reader; auto-translation makes the
# digest actionable at a glance. Trade-off (accepted): message text is sent to
# Google Translate's public endpoint over TLS; deep-translator handles the
# request without an API key. Cache aggressively so repeat runs stay free
# (in latency, since the endpoint is free either way).

_TRANSLATOR = None
def _translator():
    """Lazy-load deep-translator to keep pipeline import light when translation
    is not needed (e.g. the plain /top text preview)."""
    global _TRANSLATOR
    if _TRANSLATOR is not None:
        return _TRANSLATOR
    try:
        from deep_translator import GoogleTranslator
        # 'iw' is Google's historical code for Hebrew ('he' also works on
        # modern deployments, but 'iw' is what deep-translator canonically uses).
        _TRANSLATOR = GoogleTranslator(source="ru", target="iw")
        return _TRANSLATOR
    except Exception as e:
        print(f"[translate] deep-translator unavailable: {e.__class__.__name__} {e}")
        return None


def _text_hash(t):
    import hashlib
    return hashlib.sha256((t or "").encode("utf-8")).hexdigest()


def _is_russian(text):
    """Heuristic: at least 10 letters total and >= 40% of them Cyrillic."""
    if not text:
        return False
    cyr = len(CYR_RE.findall(text))
    heb = len(HEB_RE.findall(text))
    lat = len(LAT_RE.findall(text))
    total = cyr + heb + lat
    return total >= 10 and cyr / total >= 0.4


def _get_cached_translation(conn, h):
    row = conn.execute(
        "SELECT translation FROM translation_cache WHERE text_hash=?",
        (h,)).fetchone()
    return row[0] if row else None


def _put_cached_translation(conn, h, translation):
    conn.execute(
        "INSERT OR REPLACE INTO translation_cache "
        "(text_hash, source_lang, target_lang, translation, ts) "
        "VALUES (?,?,?,?,?)",
        (h, "ru", "iw", translation, datetime.now(timezone.utc).isoformat()))
    conn.commit()


def _translate_one(text, tries=3):
    tr = _translator()
    if tr is None:
        return None
    # Google's public endpoint occasionally 429s under bursts. Small backoff.
    import time
    for i in range(tries):
        try:
            return tr.translate(text[:4500])  # endpoint caps request size
        except Exception as e:
            if i == tries - 1:
                print(f"[translate] gave up on {text[:40]!r}: "
                      f"{e.__class__.__name__} {str(e)[:80]}")
                return None
            time.sleep(0.5 * (i + 1))
    return None


def translate_series(series):
    """Return a Series of Hebrew translations aligned with `series`. Russian
    rows get translated; non-Russian rows return empty strings. Repeat texts
    are looked up once (via translation_cache) and answered from cache on
    subsequent rows. Missing / failed translations are empty, never fatal."""
    conn = _connect()
    russian_texts = sorted({t for t in series if _is_russian(t)})
    hashes = {t: _text_hash(t) for t in russian_texts}

    # Load cached translations in a single pass
    resolved = {}
    for t in russian_texts:
        h = hashes[t]
        cached = _get_cached_translation(conn, h)
        if cached is not None:
            resolved[t] = cached
    remaining = [t for t in russian_texts if t not in resolved]

    if remaining:
        from concurrent.futures import ThreadPoolExecutor
        # 4 threads keeps us comfortably under Google's public rate window.
        # Higher parallelism triggers 429s that _translate_one retries but
        # eventually gives up on, leaving text_he empty for those rows.
        with ThreadPoolExecutor(max_workers=4) as ex:
            for t, out in zip(remaining, ex.map(_translate_one, remaining)):
                if out:
                    resolved[t] = out
                    _put_cached_translation(conn, hashes[t], out)
    conn.close()
    return series.map(lambda t: resolved.get(t, "") if _is_russian(t) else "")


def last_run():
    if not METRICS_PATH.exists():
        return None
    lines = [l for l in METRICS_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    return json.loads(lines[-1]) if lines else None


if __name__ == "__main__":
    print("running pipeline ...")
    summary = run_pipeline()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\nunsent flagged leads:", len(unsent_flagged()))
