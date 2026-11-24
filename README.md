# Logs Analyser & Troubleshooter Agent

Autonomous log triage: ingest -> cluster -> detect -> correlate with deploys -> cited root cause -> remediation runbook.

## Run
    python3 simulator.py          # makes sample_logs.log (166k lines) + sample_events.json
    python3 agent.py              # writes incident_report.md (dry-run remediation)
    python3 agent.py --logs prod.log --events changes.json --mode execute

## How it maps to the brief
| Deliverable | Implementation (agent.py) |
|---|---|
| Log ingestion & anomaly clustering | `parse`, `templatize` (variable masking -> 11 templates from 166k lines), `detect` (per service+template series, median/MAD spike detection, sustained-onset rule) |
| Root-cause correlation with trace evidence | `infer_dependencies` (call graph from logs), `find_root` (earliest anomaly with no anomalous callee), `correlate_changes` (deploy/infra events before onset, scored), `trace_chain` (one trace_id followed across services, each line cited by log line number) |
| Fix recommendation & playbook | `PLAYBOOKS` signature library with auto-safe vs needs-approval steps; `--mode dry-run|execute`; approval-gated steps are never auto-run |

## Result on the simulated outage
Root cause `payment-service`, deploy v2.14.0 (pool 50 -> 5) 3 min before onset, cascade payment -> checkout -> api-gateway, rollback runbook generated.

## Extending
- Replace regex templating with Drain3 for unseen formats; wire `follow` mode to Kafka/Loki/CloudWatch for real-time streaming.
- Add an LLM step after `analyse()` to turn the structured evidence into prose and propose playbooks for unknown signatures (keep citations mandatory).
- Swap `PLAYBOOKS` for your org runbooks.

## Dashboard
    pip install -r requirements.txt
    streamlit run app.py
Tabs: (1) ingestion and anomaly clustering, (2) root-cause correlation with trace evidence, (3) remediation playbook, plus the downloadable full report. The sidebar slider replays the logs up to a chosen minute to simulate real-time ingestion.
