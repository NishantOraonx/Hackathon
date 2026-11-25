"""Cloud-friendly Streamlit UI for the AIOps autonomous debugging demo.

The Live demo advances a deterministic simulated incident every few seconds.
All remediation is simulated; no shell commands or production infrastructure
are ever executed by this dashboard.
"""
import json
from datetime import timedelta

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

import agent
import automation
import simulator

st.set_page_config(page_title="AIOps Logs Analyser & Troubleshooter", page_icon="🛠️", layout="wide")


@st.cache_data(show_spinner=False)
def get_scenario():
    return automation.load_scenario()


def reset_demo():
    logs, events = get_scenario()
    st.session_state.demo_logs = logs
    st.session_state.demo_events = events
    st.session_state.demo_minute = 0
    st.session_state.demo_status = "running"
    st.session_state.demo_result = None
    st.session_state.demo_remediation = None
    st.session_state.demo_incident_id = None
    st.session_state.demo_history = []


def render_incident(result, remediation=None):
    if result.get("status") == "healthy":
        st.success("No anomalies detected in the ingested log window.")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Log lines analysed", f"{result['n_lines']:,}")
    c2.metric("Unique templates", result["n_templates"])
    c3.metric("Anomalous clusters", len(result["clusters"]))
    c4.metric("RCA confidence", f"{result['confidence']:.0%}")
    st.error(f"Root cause: **{result['root_service']}** — {result['cause']}")

    tab1, tab2, tab3, tab4 = st.tabs([
        "1 · Anomaly detection", "2 · Root-cause correlation", "3 · Remediation & verification", "4 · Incident report"
    ])
    with tab1:
        st.subheader("Error and warning volume by service")
        n_win = len(result["clusters"][0]["series"])
        t_min = min(c["onset"] for c in result["clusters"]) - timedelta(seconds=(min(c["onset"] for c in result["clusters"]) - simulator.START).total_seconds())
        # The chart uses the original simulator time origin, matching agent.WINDOW.
        idx = [simulator.START + timedelta(seconds=i * agent.WINDOW) for i in range(n_win)]
        per_service = {}
        for cluster in result["clusters"]:
            values = per_service.setdefault(cluster["service"], [0] * n_win)
            for i, count in enumerate(cluster["series"]):
                values[i] += count
        st.line_chart(pd.DataFrame(per_service, index=pd.DatetimeIndex(idx)))
        st.subheader("Detected anomaly clusters")
        rows = [{
            "Onset": c["onset"].strftime("%H:%M:%S"), "Service": c["service"], "Level": c["level"],
            "Peak/min": c["peak_per_min"], "Baseline/min": c["baseline_per_min"],
            "Events": c["total"], "Signature": c["template"],
        } for c in result["clusters"]]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with tab2:
        left, right = st.columns(2)
        with left:
            st.subheader("Correlated change")
            change = result.get("change")
            if change:
                st.write(f"**{change['type'].title()}** · `{change['service']}` · {change.get('version', '')}")
                st.write(f"Time: {change['ts']}Z · {result['gap_min']} minutes before anomaly")
                st.info(change.get("change", "No change description supplied."))
            else:
                st.warning("No relevant deployment or infrastructure change was correlated.")
        with right:
            st.subheader("Dependency / blast radius")
            for caller, callee in result.get("deps", []):
                st.write(f"`{caller}` → `{callee}`")
            st.write("Impacted services: " + (", ".join(result.get("impacted", [])) or "None"))
        st.subheader("Trace evidence")
        if result.get("chain"):
            for trace_id, records in result["chain"]:
                st.markdown(f"Trace `{trace_id}`")
                evidence = [{"Time": r["ts"].strftime("%H:%M:%S"), "Service": r["svc"],
                             "Level": r["level"], "Message": r["msg"]}
                            for r in records if agent.LEVEL_RANK.get(r["level"], 0) >= 2]
                st.dataframe(pd.DataFrame(evidence), use_container_width=True, hide_index=True)
        else:
            st.caption("No cross-service error trace was identified in this window.")
        st.subheader("Source log evidence")
        for cluster in result["clusters"]:
            if cluster["service"] == result["root_service"]:
                for example in cluster["examples"][:2]:
                    st.code(f"[log L{example['line_no']}] {example['raw']}")

    with tab3:
        st.subheader("Recommended remediation playbook")
        for index, step in enumerate(result.get("playbook", []), 1):
            with st.expander(f"Step {index}: {step['title']} · {step['safety']}", expanded=True):
                st.code(step["command"], language="bash")
                st.caption(step.get("result", "Recommendation only"))
        if remediation:
            st.divider()
            st.subheader("Autonomous simulated remediation")
            for action in remediation.get("actions", []):
                st.write(f"✅ **{action['name']}** — {action['detail']}")
            if remediation.get("verification"):
                st.success(remediation["verification"])
            st.caption("Sandbox simulation only. No real kubectl, database, host, or production command was executed.")
        st.info("The original agent's shell execution path remains dry-run by default. This dashboard uses a separate simulated remediation engine.")

    with tab4:
        report = agent.render(result)
        if remediation:
            report += "\n\n## Simulated remediation and verification\n"
            for action in remediation.get("actions", []):
                report += f"- **{action['name']}**: {action['detail']} ({action['status']})\n"
            report += f"\n**Verification:** {remediation.get('verification', 'Not run')}\n"
        st.download_button("Download incident_report.md", report, file_name="incident_report.md", mime="text/markdown")
        st.markdown(report)


st.title("🛠️ AIOps Logs Analyser & Troubleshooter Agent")
st.caption("PS-01A · Autonomous debugging · Log ingestion → anomaly detection → root-cause analysis → simulated remediation")

mode = st.sidebar.radio("Operating mode", ["Live automated demo", "Manual log analysis"])
st.sidebar.markdown("---")
st.sidebar.caption("Remediation is simulated and safe. The app never executes infrastructure commands.")

if mode == "Live automated demo":
    if "demo_status" not in st.session_state:
        reset_demo()

    if st.session_state.demo_status == "running":
        st_autorefresh(interval=2000, limit=None, key="aioops_live_refresh")
        st.session_state.demo_minute = min(st.session_state.demo_minute + 1, automation.SCENARIO_MINUTES)

    logs = st.session_state.demo_logs
    events = st.session_state.demo_events
    minute = st.session_state.demo_minute
    visible = automation.visible_logs(logs, minute)

    if st.session_state.demo_status == "running" and minute >= automation.DETECTION_MINUTE:
        with st.spinner("Agent correlating anomalies, traces and deployment events..."):
            result = automation.analyse_at(logs, events, minute)
        st.session_state.demo_result = result
        if result.get("status") == "incident":
            remediation = automation.simulate_remediation(result)
            incident_id = automation.save_incident(result, remediation)
            st.session_state.demo_remediation = remediation
            st.session_state.demo_incident_id = incident_id
            st.session_state.demo_history.append({
                "Incident": incident_id, "Root service": result.get("root_service"),
                "Cause": result.get("cause"), "Status": remediation.get("status"),
                "Log lines": result.get("n_lines"),
            })
            st.session_state.demo_status = "resolved"
        else:
            st.session_state.demo_status = "healthy"

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Replay clock", f"{minute:02d} / {automation.SCENARIO_MINUTES} min")
    m2.metric("Logs ingested", f"{len(visible):,}")
    m3.metric("Services monitored", len(simulator.SERVICES))
    m4.metric("Automation state", st.session_state.demo_status.upper())
    st.progress(min(minute / automation.SCENARIO_MINUTES, 1.0))

    controls = st.columns([1, 1, 3])
    if controls[0].button("↻ Restart scenario", use_container_width=True):
        reset_demo()
        st.rerun()
    if controls[1].button("Pause / resume", use_container_width=True):
        st.session_state.demo_status = "paused" if st.session_state.demo_status == "running" else "running"
        st.rerun()
    controls[2].caption("One replay minute advances every 2 seconds. The injected outage becomes detectable around minute 34.")

    st.subheader("Live log stream (latest 25 events)")
    st.code("\n".join(visible[-25:]) if visible else "Waiting for first simulated events...", language="text")

    if st.session_state.demo_result:
        render_incident(st.session_state.demo_result, st.session_state.demo_remediation)
    elif st.session_state.demo_status == "running":
        st.info("Monitoring services. The agent is waiting for enough evidence to distinguish normal traffic from an incident.")
    elif st.session_state.demo_status == "paused":
        st.warning("Replay paused. Select Resume to continue.")

    if st.session_state.demo_history:
        st.subheader("Incident audit history")
        st.dataframe(pd.DataFrame(st.session_state.demo_history), use_container_width=True, hide_index=True)

else:
    st.sidebar.subheader("Input logs")
    source = st.sidebar.radio("Data source", ["Built-in simulated outage", "Upload my own"])
    if source == "Built-in simulated outage":
        logs, events = get_scenario()
        log_text, events_text = "\n".join(logs), json.dumps(events)
    else:
        uploaded_logs = st.sidebar.file_uploader("Log file (.log / .txt)", type=["log", "txt"])
        uploaded_events = st.sidebar.file_uploader("Events JSON (deployments / infrastructure)", type=["json"])
        if not uploaded_logs or not uploaded_events:
            st.info("Upload both a log file and an events JSON file, or switch to the built-in sample.")
            st.stop()
        log_text = uploaded_logs.getvalue().decode("utf-8", errors="replace")
        events_text = uploaded_events.getvalue().decode("utf-8", errors="replace")

    lines = log_text.splitlines()
    records = agent.parse(lines)
    if not records:
        st.error("No log lines matched the expected format. Check timestamp, level, service, host and message fields.")
        st.stop()
    t_min, t_max = min(r["ts"] for r in records), max(r["ts"] for r in records)
    duration = max(1, int((t_max - t_min).total_seconds() // 60))
    upto = st.sidebar.slider("Analyse logs up to (minutes from start)", 1, duration, duration)
    cutoff = t_min + timedelta(minutes=upto)
    selected_lines = [line for line in lines if line[:19] <= cutoff.strftime("%Y-%m-%dT%H:%M:%S")]
    try:
        result = agent.analyse(selected_lines, json.loads(events_text), mode="none")
    except Exception as exc:
        st.exception(exc)
        st.stop()
    st.caption(f"Manual analysis · {len(selected_lines):,} log lines · cutoff {cutoff.strftime('%H:%M:%S')}Z")
    render_incident(result)

with st.expander("Recent persisted incident records (this app instance)"):
    try:
        history = automation.recent_incidents()
        if history:
            st.dataframe(pd.DataFrame(history), use_container_width=True, hide_index=True)
        else:
            st.caption("No persisted incidents yet. Run the live scenario to create one.")
    except Exception as exc:
        st.caption(f"Incident store is not available: {exc}")
