"""Streamlit dashboard for the Logs Analyser & Troubleshooter Agent.

Run:  streamlit run app.py
Needs: streamlit, pandas  (agent.py + simulator.py in the same folder)
"""
import json
import os
from datetime import timedelta

import pandas as pd
import streamlit as st

import agent

st.set_page_config(page_title="Logs Analyser & Troubleshooter Agent", page_icon="🛠️", layout="wide")

LOG_PATH, EVT_PATH = "sample_logs.log", "sample_events.json"


# ------------------------------------------------------------------ data loading
@st.cache_data(show_spinner=False)
def sample_data():
    if not (os.path.exists(LOG_PATH) and os.path.exists(EVT_PATH)):
        import simulator
        logs, events = simulator.generate()
        open(LOG_PATH, "w").write("\n".join(logs) + "\n")
        json.dump(events, open(EVT_PATH, "w"), indent=2)
    return open(LOG_PATH).read(), open(EVT_PATH).read()


@st.cache_data(show_spinner="Parsing logs...")
def bounds(log_text):
    lines = log_text.splitlines()
    recs = agent.parse(lines)
    return min(r["ts"] for r in recs), max(r["ts"] for r in recs), len(recs)


@st.cache_data(show_spinner="Agent analysing logs...")
def run_agent(log_text, events_text, cutoff_iso, mode):
    lines = log_text.splitlines()
    if cutoff_iso:  # simulate real-time ingestion: only lines seen so far
        lines = [l for l in lines if l[:19] <= cutoff_iso[:19]]
    return agent.analyse(lines, json.loads(events_text), mode)


# ---------------------------------------------------------------------- sidebar
st.sidebar.title("Agent controls")
src = st.sidebar.radio("Data source", ["Built-in simulated outage", "Upload my own"])
if src == "Built-in simulated outage":
    log_text, events_text = sample_data()
else:
    up_l = st.sidebar.file_uploader("Log file (.log / .txt)", type=["log", "txt"])
    up_e = st.sidebar.file_uploader("Events JSON (deploys / infra)", type=["json"])
    if not (up_l and up_e):
        st.info("Upload a log file and an events JSON in the sidebar, or switch to the built-in sample.")
        st.stop()
    log_text, events_text = up_l.getvalue().decode(), up_e.getvalue().decode()

mode = st.sidebar.selectbox("Remediation mode", ["dry-run", "none"],
                            help="Execution is disabled in the dashboard for safety. Use `python agent.py --mode execute` from a terminal with cluster access.")

t_min, t_max, n_total = bounds(log_text)
minutes = int((t_max - t_min).total_seconds() // 60)
st.sidebar.markdown("**Replay (simulated real-time ingestion)**")
upto = st.sidebar.slider("Logs ingested up to (minutes from start)", 1, minutes, minutes)
cutoff = t_min + timedelta(minutes=upto)
st.sidebar.caption(f"Cutoff: {cutoff.strftime('%H:%M:%S')}Z  ·  drag left to see what the agent knew earlier")

res = run_agent(log_text, events_text, cutoff.isoformat(), mode)

# ------------------------------------------------------------------------ header
st.title("🛠️ Logs Analyser & Troubleshooter Agent")
st.caption("AIOps · Autonomous debugging — ingest logs, find root cause, prescribe remediation")

if res["status"] == "healthy":
    st.success(f"No anomalies detected in logs up to {cutoff.strftime('%H:%M:%S')}Z. All services within baseline.")
    st.stop()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Log lines analysed", f"{res['n_lines']:,}")
c2.metric("Unique templates", res["n_templates"], help="Raw lines collapsed by masking IDs/numbers")
c3.metric("Anomalous clusters", len(res["clusters"]))
c4.metric("Confidence", f"{res['confidence']:.0%}")

st.error(f"**Root cause service: `{res['root_service']}`** — {res['cause']} (onset {res['onset'][11:19]}Z)")

tab1, tab2, tab3, tab4 = st.tabs([
    "1 · Ingestion & anomaly clustering",
    "2 · Root-cause correlation",
    "3 · Remediation playbook",
    "Full report",
])

# ------------------------------------------------- tab 1: ingestion + clustering
with tab1:
    st.subheader("Error & warning volume per minute, by service")
    n_win = len(res["clusters"][0]["series"])
    idx = [t_min + timedelta(seconds=i * agent.WINDOW) for i in range(n_win)]
    per_svc = {}
    for c in res["clusters"]:
        s = per_svc.setdefault(c["service"], [0] * n_win)
        for i, v in enumerate(c["series"]):
            s[i] += v
    df = pd.DataFrame(per_svc, index=pd.DatetimeIndex(idx))
    st.line_chart(df)

    st.subheader("Anomaly clusters (ordered by onset)")
    rows = [{
        "Onset": c["onset"].strftime("%H:%M"),
        "Service": c["service"],
        "Level": c["level"],
        "Peak / min": c["peak_per_min"],
        "Baseline / min": c["baseline_per_min"],
        "Total events": c["total"],
        "Signature (template)": c["template"],
    } for c in res["clusters"]]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.caption("Each row is a message template (variables masked) whose rate spiked far above its own median baseline.")

# ------------------------------------------------- tab 2: correlation + evidence
with tab2:
    left, right = st.columns(2)
    with left:
        st.subheader("Triggering change")
        ch = res["change"]
        if ch:
            st.markdown(
                f"**{ch['type'].title()}** of `{ch['service']}` {ch.get('version', '')}  \n"
                f"Time: {ch['ts']}Z  ·  **{res['gap_min']} min** before first anomaly  \n"
                f"Author: {ch.get('author', '?')}  ·  Commit: `{ch.get('commit', '-')}`"
            )
            st.info(ch["change"])
        else:
            st.warning("No deployment or infrastructure change found shortly before onset. Confidence is low.")
    with right:
        st.subheader("Cascade / blast radius")
        deps = res["deps"]
        st.markdown("Dependencies inferred from logs:")
        for a, b in deps:
            st.markdown(f"- `{a}` → `{b}`")
        st.markdown("Impacted upstream: " + (", ".join(f"`{s}`" for s in res["impacted"]) or "none"))
        # cascade timeline: first anomaly per service
        first = {}
        for c in res["clusters"]:
            first.setdefault(c["service"], c["onset"])
        st.markdown("**First anomaly per service**")
        st.dataframe(pd.DataFrame(
            [{"Service": s, "First anomaly": t.strftime("%H:%M")} for s, t in sorted(first.items(), key=lambda x: x[1])]),
            hide_index=True, use_container_width=True)

    st.subheader("Trace evidence")
    if res["chain"]:
        for tid, rs in res["chain"]:
            st.markdown(f"Trace `{tid}` — failure propagating across services:")
            st.dataframe(pd.DataFrame([{
                "Log line": f"L{r['line_no']}",
                "Time": r["ts"].strftime("%H:%M:%S"),
                "Service": r["svc"],
                "Level": r["level"],
                "Message": r["msg"],
            } for r in rs if agent.LEVEL_RANK[r["level"]] >= 2]), hide_index=True, use_container_width=True)
    else:
        st.caption("No single trace crossed multiple failing services.")

    st.subheader("Cited log evidence (root service)")
    for c in res["clusters"]:
        if c["service"] == res["root_service"]:
            for ex in c["examples"][:2]:
                st.code(f"[log L{ex['line_no']}] {ex['raw']}", language="text")

# ------------------------------------------------------- tab 3: remediation
with tab3:
    st.subheader(f"Playbook: {res['cause']}")
    for i, s in enumerate(res["playbook"], 1):
        badge = "🟢 auto-safe" if s["safety"] == "auto-safe" else "🟠 needs approval"
        with st.expander(f"Step {i}: {s['title']}  —  {badge}", expanded=True):
            st.code(s["command"], language="bash")
            if s["result"]:
                st.caption(s["result"])
    st.success(f"**Prevention:** {res['prevention']}")
    st.caption("Steps marked needs-approval are never run automatically by the agent.")

# ------------------------------------------------------------------ full report
with tab4:
    report = agent.render(res)
    st.download_button("Download incident_report.md", report, file_name="incident_report.md", mime="text/markdown")
    st.markdown(report)
