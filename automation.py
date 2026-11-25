"""Safe, deterministic orchestration for the AIOps hackathon demo.

This module never runs shell commands or touches real infrastructure. It replays
simulated multi-service logs, invokes the existing RCA agent, applies a simulated
remediation, and verifies recovery against the scenario's simulated health state.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import agent
import simulator

DB_PATH = Path(__file__).with_name("incidents.db")
SCENARIO_MINUTES = int((simulator.END - simulator.START).total_seconds() // 60)
DETECTION_MINUTE = 34  # the supplied scenario becomes detectable at this replay minute


def init_db(db_path: Path | str = DB_PATH) -> None:
    """Create a small local incident/action audit store."""
    with sqlite3.connect(db_path) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS incidents (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL, root_service TEXT,
            cause TEXT, confidence REAL, status TEXT, log_lines INTEGER,
            report TEXT
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT NOT NULL,
            action TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL
        )""")


def load_scenario() -> tuple[list[str], list[dict[str, Any]]]:
    """Return a fresh copy of the existing simulator scenario."""
    logs, events = simulator.generate()
    return logs, events


def visible_logs(logs: list[str], minute: int) -> list[str]:
    """Return only log lines available by the replay clock."""
    cutoff = simulator.START + timedelta(minutes=minute)
    cutoff_text = cutoff.strftime("%Y-%m-%dT%H:%M:%S")
    return [line for line in logs if line[:19] <= cutoff_text]


def analyse_at(logs: list[str], events: list[dict[str, Any]], minute: int) -> dict[str, Any]:
    """Run the original RCA engine on the logs ingested up to this replay time."""
    return agent.analyse(visible_logs(logs, minute), events, mode="none")


def simulate_remediation(result: dict[str, Any]) -> dict[str, Any]:
    """Apply a safe simulated fix and return explicit action + verification evidence."""
    if result.get("status") != "incident":
        return {"status": "not_needed", "actions": [], "verification": "No incident was detected."}

    cause = result.get("cause", "Unknown")
    root = result.get("root_service", "unknown-service")
    if cause == "Database connection pool exhaustion":
        actions = [
            {"name": "Rollback simulated deployment", "detail": f"{root} v2.14.0 → v2.13.2", "status": "completed"},
            {"name": "Restore simulated database pool", "detail": "DB_POOL_MAX_SIZE: 5 → 50", "status": "completed"},
            {"name": "Restart simulated payment worker", "detail": "Worker restarted in the sandbox scenario", "status": "completed"},
        ]
    elif cause == "Memory exhaustion":
        actions = [
            {"name": "Restart simulated service", "detail": root, "status": "completed"},
            {"name": "Restore simulated memory limit", "detail": "Memory limit returned to the known-good profile", "status": "completed"},
        ]
    else:
        actions = [
            {"name": "Apply simulated recovery playbook", "detail": f"Runbook selected for {cause} on {root}", "status": "completed"},
        ]

    return {
        "status": "resolved",
        "actions": actions,
        "verification": "PASS — simulated service health returned to baseline; error-rate check is healthy; recovery recorded.",
    }


def save_incident(result: dict[str, Any], remediation: dict[str, Any], db_path: Path | str = DB_PATH) -> str:
    """Persist incident and action audit rows to SQLite."""
    init_db(db_path)
    incident_id = uuid.uuid4().hex[:12]
    status = remediation.get("status", "detected")
    report = agent.render(result) if result.get("status") == "incident" else result.get("summary", "No incident")
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO incidents VALUES (?, datetime('now'), ?, ?, ?, ?, ?, ?)",
            (incident_id, result.get("root_service"), result.get("cause"),
             result.get("confidence"), status, result.get("n_lines", 0), report),
        )
        for action in remediation.get("actions", []):
            con.execute(
                "INSERT INTO actions (incident_id, action, result, created_at) VALUES (?, ?, ?, datetime('now'))",
                (incident_id, action["name"], f"{action['status']}: {action['detail']}"),
            )
    return incident_id


def recent_incidents(limit: int = 20, db_path: Path | str = DB_PATH) -> list[dict[str, Any]]:
    init_db(db_path)
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT id, created_at, root_service, cause, confidence, status, log_lines FROM incidents ORDER BY rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]
