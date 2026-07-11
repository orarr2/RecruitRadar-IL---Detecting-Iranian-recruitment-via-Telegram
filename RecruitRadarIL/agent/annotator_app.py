"""
RecruitRadar-IL analyst annotator (M2 of docs/DESIGN.md).

Run from the RecruitRadarIL/ directory:

    streamlit run agent/annotator_app.py

Three tabs:
  Review queue      - messages ranked by p_recruitment; record accept /
                      reject / unclear. Verdicts append to data/verdicts.jsonl
                      and become LF4 on the next notebook run.
  Channel proposals - approve / reject discovery candidates. Approved
                      channels are appended to channels_extra.txt.
  Metrics           - the last weeks of data/metrics_weekly.jsonl.

Everything shown here is a lead for review, not a conclusion.
"""

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "recruitradar.db"
VERDICTS_PATH = ROOT / "data" / "verdicts.jsonl"
METRICS_PATH = ROOT / "data" / "metrics_weekly.jsonl"
CHANNELS_EXTRA = ROOT / "channels_extra.txt"

ANALYST_ID = os.getenv("ANALYST_ID", "analyst")
PAGE_SIZE = 25

st.set_page_config(page_title="RecruitRadar-IL annotator", layout="wide")
st.title("RecruitRadar-IL - analyst review")
st.caption("Leads for review, not conclusions. Public data only; "
           "verdicts stay on this machine.")

if not DB_PATH.exists():
    st.error(f"No database at {DB_PATH}. Run agent/AdaptiveRecruitRadar.ipynb first.")
    st.stop()


@st.cache_resource
def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


conn = get_conn()


def load_queue():
    q = pd.read_sql_query(
        """SELECT s.channel, s.msg_id, s.p_recruitment, s.llm_missing,
                  m.category, m.date, m.sender_hash, m.text
           FROM snorkel_labels s JOIN messages m
             ON s.channel = m.channel AND s.msg_id = m.msg_id
           ORDER BY s.p_recruitment DESC""", conn)
    votes = pd.read_sql_query(
        "SELECT channel, msg_id, lf_name, vote FROM lf_votes", conn)
    if not votes.empty:
        wide = votes.pivot_table(index=["channel", "msg_id"], columns="lf_name",
                                 values="vote", aggfunc="last").reset_index()
        q = q.merge(wide, on=["channel", "msg_id"], how="left")
    return q


def load_verdicted():
    if not VERDICTS_PATH.exists():
        return set()
    done = set()
    for line in VERDICTS_PATH.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
            done.add((d["channel"], int(d["msg_id"])))
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return done


def save_verdict(channel, msg_id, verdict, note):
    VERDICTS_PATH.parent.mkdir(exist_ok=True)
    with VERDICTS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "channel": channel, "msg_id": int(msg_id), "verdict": verdict,
            "note": note or "", "decided_at": datetime.now(timezone.utc).isoformat(),
            "analyst_id": ANALYST_ID,
        }, ensure_ascii=False) + "\n")


def llm_rationale(text):
    h = hashlib.sha256((text or "").encode("utf-8")).hexdigest()
    row = conn.execute(
        "SELECT response_json FROM llm_cache WHERE text_hash=? "
        "ORDER BY ts DESC LIMIT 1", (h,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None


VOTE_LABEL = {1: "recruitment", 0: "not recruitment", -1: "abstain", None: "-"}
LF_DISPLAY = [("lf_rules", "rules"), ("lf_iso", "appearance"),
              ("lf_llm", "llm"), ("lf_verdicts", "verdicts")]

tab_queue, tab_channels, tab_metrics = st.tabs(
    ["Review queue", "Channel proposals", "Metrics"])

# ── Review queue ─────────────────────────────────────────────────────────────
with tab_queue:
    queue = load_queue()
    done = load_verdicted()
    if queue.empty:
        st.info("Queue is empty - run the adaptive notebook first.")
    else:
        hide_done = st.checkbox("Hide already-reviewed messages", value=True)
        if hide_done:
            mask = [(r.channel, int(r.msg_id)) not in done
                    for r in queue.itertuples()]
            queue = queue[pd.Series(mask, index=queue.index)]
        st.write(f"{len(queue)} message(s) awaiting review "
                 f"({len(done)} verdicts recorded so far)")

        for r in queue.head(PAGE_SIZE).itertuples():
            key = f"{r.channel}_{r.msg_id}"
            p = "-" if pd.isna(r.p_recruitment) else f"{r.p_recruitment:.3f}"
            with st.container(border=True):
                left, right = st.columns([3, 2])
                with left:
                    st.markdown(f"**p_recruitment = {p}**  ·  "
                                f"`{r.channel}` / {r.category or '-'}  ·  "
                                f"msg {r.msg_id}  ·  {str(r.date)[:16]}")
                    st.write(r.text if r.text else "_(no text)_")
                    votes = "  |  ".join(
                        f"{name}: **{VOTE_LABEL.get(getattr(r, col, None), '-')}**"
                        for col, name in LF_DISPLAY if hasattr(r, col))
                    st.caption(votes)
                    rat = llm_rationale(r.text)
                    if rat and rat.get("rationale_short"):
                        st.caption(
                            f"llm: {rat.get('rationale_short')} "
                            f"(confidence {rat.get('confidence', '-')}, "
                            f"payment {rat.get('payment_mentioned', '-')}, "
                            f"contact {rat.get('contact_method', '-')})")
                with right:
                    note = st.text_input("note (optional)", key=f"note_{key}")
                    c1, c2, c3 = st.columns(3)
                    if c1.button("accept", key=f"acc_{key}", type="primary"):
                        save_verdict(r.channel, r.msg_id, "accept", note)
                        st.rerun()
                    if c2.button("reject", key=f"rej_{key}"):
                        save_verdict(r.channel, r.msg_id, "reject", note)
                        st.rerun()
                    if c3.button("unclear", key=f"unc_{key}"):
                        save_verdict(r.channel, r.msg_id, "unclear", note)
                        st.rerun()

# ── Channel proposals ────────────────────────────────────────────────────────
with tab_channels:
    props = pd.read_sql_query(
        "SELECT * FROM channel_proposals ORDER BY status, "
        "0.6*base_rate_score + 0.4*centrality_score DESC", conn)
    pending = props[props["status"] == "pending"]
    st.write(f"{len(pending)} pending proposal(s); no channel is collected "
             "without approval here.")
    for r in pending.itertuples():
        with st.container(border=True):
            left, right = st.columns([3, 1])
            with left:
                st.markdown(f"**@{r.candidate}**  ·  source: {r.source}")
                st.caption(f"base rate {r.base_rate_score:.2f}  ·  "
                           f"centrality {r.centrality_score:.2f}  ·  "
                           f"seen in: {r.sample_msg_ids or '-'}")
            with right:
                a, b = st.columns(2)
                if a.button("approve", key=f"ap_{r.candidate}", type="primary"):
                    conn.execute(
                        "UPDATE channel_proposals SET status='approved', "
                        "decided_at=? WHERE candidate=?",
                        (datetime.now(timezone.utc).isoformat(), r.candidate))
                    conn.commit()
                    with CHANNELS_EXTRA.open("a", encoding="utf-8") as f:
                        f.write(f"\n# approved via annotator "
                                f"{datetime.now():%Y-%m-%d}\n"
                                f"{r.candidate}, uncategorized\n")
                    st.rerun()
                if b.button("reject", key=f"rj_{r.candidate}"):
                    conn.execute(
                        "UPDATE channel_proposals SET status='rejected', "
                        "decided_at=? WHERE candidate=?",
                        (datetime.now(timezone.utc).isoformat(), r.candidate))
                    conn.commit()
                    st.rerun()
    decided = props[props["status"] != "pending"]
    if not decided.empty:
        st.subheader("Decided")
        st.dataframe(decided, use_container_width=True, hide_index=True)

# ── Metrics ──────────────────────────────────────────────────────────────────
with tab_metrics:
    if not METRICS_PATH.exists():
        st.info("No metrics yet - run the adaptive notebook first.")
    else:
        m = pd.read_json(METRICS_PATH, lines=True).tail(8)
        st.dataframe(m, use_container_width=True, hide_index=True)
        chart_cols = [c for c in ("lf_coverage", "precision_top50") if c in m]
        if chart_cols and len(m) > 1:
            st.line_chart(m.set_index("run_id")[chart_cols])
