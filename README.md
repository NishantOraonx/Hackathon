# AIOps Logs Analyser & Troubleshooter Agent (PS-01A)

A hackathon-ready, safe simulation of an autonomous debugging workflow:

1. Replays multi-service logs from `simulator.py`.
2. Uses the existing `agent.py` to parse logs, cluster anomalies, correlate dependencies/traces and identify a likely root cause.
3. Runs a **simulated** remediation (no shell commands or production infrastructure are touched).
4. Verifies recovery in the simulation and records an incident/action audit in SQLite.
5. Shows the workflow in a Streamlit dashboard.

## Files

- `app.py` — Streamlit dashboard; live automated replay and manual log analysis.
- `agent.py` — original RCA/anomaly detection engine (kept intact).
- `simulator.py` — original multi-service outage simulator (kept intact).
- `automation.py` — orchestration, simulated remediation, verification and SQLite audit history.
- `requirements.txt` — Python dependencies.

## Run locally (Windows / PyCharm)

Open the project folder in PyCharm and use its configured project interpreter. In the PyCharm terminal:

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

If the `python` command is not on PATH but the Python Launcher works, use:

```powershell
py -3.14 -m pip install -r requirements.txt
py -3.14 -m streamlit run app.py
```

If PyCharm created `.venv314`, you can call it directly:

```powershell
.\.venv314\Scripts\python.exe -m pip install -r requirements.txt
.\.venv314\Scripts\python.exe -m streamlit run app.py
```

## Live demo

Choose **Live automated demo** in the sidebar. The replay advances one simulated minute every two seconds. The injected payment database-pool incident becomes detectable around replay minute 34. The agent analyses the partial log stream, applies a simulated rollback/pool restoration, and displays a simulated health verification. Use **Restart scenario** to repeat the demo.

The **Manual log analysis** mode preserves the original ability to analyse the built-in scenario or upload a `.log`/`.txt` file plus an events JSON file.

## Deploy free on Streamlit Community Cloud

1. Create a GitHub repository and upload all project files (`app.py`, `agent.py`, `simulator.py`, `automation.py`, `requirements.txt`, `README.md`).
2. Go to https://share.streamlit.io/ and sign in with GitHub.
3. Select **Create app**, choose the repository and branch, and set the main file path to `app.py`.
4. Deploy. Streamlit installs packages from `requirements.txt`.
5. Open the generated public app URL and choose **Live automated demo**.

## Important hosting limitation

This free deployment is designed for an interactive hackathon demonstration, not guaranteed 24/7 production monitoring. The simulation advances while the Streamlit app is active and being visited. Free app hosting can sleep/restart, and its local filesystem is not guaranteed to persist across restarts. SQLite (`incidents.db`) is an audit store for the current app instance; use a managed external database if durable cross-restart/cloud history is required.

## Safety

All dashboard remediation is simulated. Do not change the original `agent.py` CLI to `--mode execute` on a real machine or cluster for this demo. The remediation engine deliberately does not invoke `subprocess`, `kubectl`, SQL clients or operating-system commands.
