#!/usr/bin/env python3
"""Logs Analyser & Troubleshooter Agent (stdlib only).

Pipeline:  ingest -> template mining -> windowed anomaly detection -> clustering
           -> service dependency inference -> root-cause correlation (deploys/infra)
           -> cited diagnosis -> remediation playbook (dry-run by default)
"""
import argparse, json, re, statistics, subprocess, sys
from collections import defaultdict, Counter
from datetime import datetime, timedelta

LOG_RE = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\dT[\d:.]+Z)\s+(?P<level>[A-Z]+)\s+\[(?P<svc>[\w.-]+)\]\s+"
    r"host=(?P<host>\S+)(?:\s+trace_id=(?P<trace>\w+))?\s+(?P<msg>.*)$")
LEVEL_RANK = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3, "FATAL": 4}
WINDOW = 60  # seconds


# ---------------------------------------------------------------- ingestion
def parse(lines):
    recs = []
    for n, raw in enumerate(lines, 1):
        m = LOG_RE.match(raw.strip())
        if not m:
            continue
        d = m.groupdict()
        d["ts"] = datetime.strptime(d["ts"], "%Y-%m-%dT%H:%M:%S.%fZ")
        d["line_no"], d["raw"] = n, raw.strip()
        d["template"] = templatize(d["msg"])
        recs.append(d)
    return recs


_SUBS = [(re.compile(r"\b[0-9a-f]{12,}\b"), "<ID>"),
         (re.compile(r"\b[A-Z]{2,}-\d+\b"), "<KEY>"),
         (re.compile(r"=\s*[\w.:/-]+"), "=<V>"),
         (re.compile(r"\b\d+(\.\d+)?(ms|s|m)?\b"), "<N>")]


def templatize(msg):
    for rx, rep in _SUBS:
        msg = rx.sub(rep, msg)
    return msg


# ------------------------------------------------------- anomaly detection
def window_of(ts, t0):
    return int((ts - t0).total_seconds() // WINDOW)


def detect(recs, min_level="WARN", k=6.0, min_count=5):
    """Per (service, template) error-count series; robust (median/MAD) spike detection."""
    t0 = min(r["ts"] for r in recs)
    nwin = window_of(max(r["ts"] for r in recs), t0) + 1
    series = defaultdict(lambda: [0] * nwin)
    members = defaultdict(list)
    for r in recs:
        if LEVEL_RANK.get(r["level"], 0) >= LEVEL_RANK[min_level]:
            key = (r["svc"], r["level"], r["template"])
            series[key][window_of(r["ts"], t0)] += 1
            members[key].append(r)
    clusters = []
    for key, s in series.items():
        med = statistics.median(s)
        mad = statistics.median(abs(x - med) for x in s) or 1.0
        thresh = max(med + k * 1.4826 * mad, min_count)
        hot = [i for i, c in enumerate(s) if c > thresh]
        if not hot:
            continue
        # onset = first hot window that begins a sustained run (>=2 of next 3 windows hot)
        onset = next((i for i in hot if sum(1 for j in range(i, i + 3) if j in hot) >= 2), hot[0])
        svc, level, tpl = key
        recs_k = members[key]
        clusters.append({
            "service": svc, "level": level, "template": tpl,
            "onset": t0 + timedelta(seconds=onset * WINDOW),
            "peak_per_min": max(s), "baseline_per_min": med, "total": sum(s),
            "trace_ids": {r["trace"] for r in recs_k if r["trace"] and r["ts"] >= t0 + timedelta(seconds=onset * WINDOW)},
            "examples": sorted(recs_k, key=lambda r: r["ts"])[:0] or
                        [r for r in sorted(recs_k, key=lambda r: r["ts"]) if r["ts"] >= t0 + timedelta(seconds=onset * WINDOW)][:3],
            "series": s,
        })
    clusters.sort(key=lambda c: c["onset"])
    return clusters, t0


# ---------------------------------------------- dependency + root-cause logic
def infer_dependencies(recs, services):
    """Edges caller -> callee from messages that mention another known service."""
    edges = Counter()
    for r in recs:
        for other in services:
            if other != r["svc"] and other in r["msg"]:
                edges[(r["svc"], other)] += 1
    return {e for e, c in edges.items() if c >= 3}


def trace_chain(recs, trace_ids, limit=1):
    """Return the ordered path of services touched by an erroring trace (trace evidence)."""
    by = defaultdict(list)
    for r in recs:
        if r["trace"] in trace_ids:
            by[r["trace"]].append(r)
    out = []
    for tid, rs in list(by.items()):
        rs.sort(key=lambda r: (r["ts"], r["line_no"]))
        errs = [r for r in rs if LEVEL_RANK[r["level"]] >= 2]
        if len({r["svc"] for r in errs}) >= 2:
            out.append((tid, rs))
    out.sort(key=lambda x: -len({r["svc"] for r in x[1] if LEVEL_RANK[r["level"]] >= 2}))
    return out[:limit]


def find_root(clusters, deps):
    """Root = anomalous service whose anomaly has no anomalous upstream *callee* causing it."""
    anomalous = {c["service"] for c in clusters}
    candidates = []
    for svc in anomalous:
        callees_bad = {b for (a, b) in deps if a == svc and b in anomalous}
        candidates.append((svc, len(callees_bad)))
    roots = [s for s, n in candidates if n == 0]
    first = {}
    for c in clusters:
        first.setdefault(c["service"], c["onset"])
    roots.sort(key=lambda s: first[s])
    return roots[0] if roots else clusters[0]["service"]


def correlate_changes(root, onset, events, lookback_min=45):
    scored = []
    for e in events:
        et = datetime.fromisoformat(e["ts"])
        gap = (onset - et).total_seconds() / 60
        if 0 <= gap <= lookback_min:
            score = (0.6 if e["service"] == root else 0.1) + 0.4 * (1 - gap / lookback_min)
            if e["type"] == "deployment":
                score += 0.1
            scored.append((round(min(score, 1.0), 2), gap, e))
    scored.sort(key=lambda x: -x[0])
    return scored


# ------------------------------------------------------- remediation library
PLAYBOOKS = [
    (re.compile(r"Connection is not available|pool.*(exhaust|timed out)", re.I), {
        "cause": "Database connection pool exhaustion",
        "steps": [
            ("Roll back the offending release", "kubectl rollout undo deployment/{svc} -n prod", "auto-safe"),
            ("Verify rollout health", "kubectl rollout status deployment/{svc} -n prod --timeout=120s", "auto-safe"),
            ("If rollback is not possible: raise pool size via config", "kubectl set env deployment/{svc} DB_POOL_MAX_SIZE=50 -n prod", "needs-approval"),
            ("Confirm DB has headroom", "psql -c \"select count(*) from pg_stat_activity;\"", "auto-safe"),
        ],
        "prevention": "Add a CI guard on db.pool.max_size (min bound), load-test config changes, canary releases with error-rate auto-rollback."}),
    (re.compile(r"OutOfMemory|OOMKilled|heap space", re.I), {
        "cause": "Memory exhaustion",
        "steps": [("Restart with higher limit", "kubectl set resources deployment/{svc} --limits=memory=2Gi -n prod", "needs-approval"),
                  ("Capture heap dump", "kubectl exec deploy/{svc} -- jmap -dump:live,file=/tmp/h.hprof 1", "auto-safe")],
        "prevention": "Add memory alerts at 80% and profile the leak."}),
    (re.compile(r"timeout|timed out", re.I), {
        "cause": "Downstream dependency timeout",
        "steps": [("Inspect dependency health", "kubectl get pods -l app={svc} -n prod", "auto-safe"),
                  ("Enable circuit breaker / shed load", "kubectl annotate deployment/{svc} circuit-breaker=open -n prod", "needs-approval")],
        "prevention": "Add circuit breakers, bounded retries with jitter."}),
]


def pick_playbook(templates):
    for rx, pb in PLAYBOOKS:
        if any(rx.search(t) for t in templates):
            return pb
    return {"cause": "Unknown", "steps": [("Escalate to on-call", "echo 'page on-call'", "needs-approval")],
            "prevention": "Add a runbook for this signature."}


def run_step(cmd, mode):
    if mode == "dry-run":
        return "[dry-run] " + cmd
    if mode == "execute":
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=130)
        return (p.stdout + p.stderr).strip()
    return ""


# ------------------------------------------------------------------ the agent
def analyse(log_lines, events, mode="dry-run"):
    recs = parse(log_lines)
    if not recs:
        raise SystemExit("no parseable log lines")
    services = sorted({r["svc"] for r in recs})
    clusters, t0 = detect(recs)
    if not clusters:
        return {"status": "healthy", "summary": "No anomalies detected."}
    deps = infer_dependencies(recs, services)
    root = find_root(clusters, deps)
    root_clusters = [c for c in clusters if c["service"] == root]
    onset = root_clusters[0]["onset"]
    changes = correlate_changes(root, onset, events)
    pb = pick_playbook([c["template"] for c in root_clusters])
    chain = trace_chain(recs, set().union(*(c["trace_ids"] for c in clusters)))

    # blast radius = services reachable upstream of the root via dependency edges
    impacted, frontier = set(), {root}
    while frontier:
        nxt = {a for (a, b) in deps if b in frontier and a not in impacted}
        impacted |= nxt
        frontier = nxt
    impacted &= {c["service"] for c in clusters}

    top_change = changes[0] if changes else None
    # heuristic score, capped: correlation is evidence, not proof
    confidence = min(top_change[0], 0.95) if top_change else 0.35

    steps = []
    for title, cmd, safety in pb["steps"]:
        cmd = cmd.format(svc=root)
        out = run_step(cmd, mode) if safety == "auto-safe" or mode == "dry-run" else "[skipped: needs human approval]"
        steps.append({"title": title, "command": cmd, "safety": safety, "result": out})

    return {
        "status": "incident", "root_service": root, "onset": onset.isoformat(), "confidence": confidence,
        "cause": pb["cause"], "change": top_change[2] if top_change else None,
        "gap_min": round(top_change[1], 1) if top_change else None,
        "deps": sorted(deps), "impacted": sorted(impacted),
        "clusters": clusters, "chain": chain, "playbook": steps, "prevention": pb["prevention"],
        "n_lines": len(recs), "n_templates": len({r["template"] for r in recs}),
    }


# --------------------------------------------------------------------- report
def render(res):
    if res["status"] == "healthy":
        return "# No incident\nAll services within baseline.\n"
    o = []
    o.append("# Incident Report — Autonomous RCA\n")
    o.append(f"**Root cause service:** `{res['root_service']}`  |  **Onset:** {res['onset']}Z  |  **Confidence:** {res['confidence']:.0%}\n")
    o.append(f"**Diagnosis:** {res['cause']}")
    if res["change"]:
        c = res["change"]
        o.append(f"\n**Trigger:** {c['type']} of `{c['service']}` {c.get('version','')} at {c['ts']}Z "
                 f"({res['gap_min']} min before first anomaly) by {c.get('author','?')} (commit `{c.get('commit','-')}`)\n"
                 f"> Change: {c['change']}")
    o.append(f"\nAnalysed {res['n_lines']:,} log lines, collapsed into {res['n_templates']} templates; "
             f"{len(res['clusters'])} anomalous clusters found.\n")
    o.append("## Blast radius (cascade)\n")
    o.append(f"Dependencies inferred from logs: " + ", ".join(f"{a}→{b}" for a, b in res["deps"]))
    o.append(f"\nUpstream services impacted: {', '.join('`'+s+'`' for s in res['impacted']) or 'none'}\n")
    o.append("## Anomaly timeline (clusters ordered by onset)\n")
    o.append("| # | Onset | Service | Lvl | Peak/min | Baseline | Signature |\n|---|---|---|---|---|---|---|")
    for i, c in enumerate(res["clusters"], 1):
        o.append(f"| {i} | {c['onset'].strftime('%H:%M')} | {c['service']} | {c['level']} | {c['peak_per_min']} | {c['baseline_per_min']} | `{c['template'][:80]}` |")
    o.append("\n## Evidence (citations)\n")
    for c in res["clusters"]:
        if c["service"] == res["root_service"]:
            for ex in c["examples"][:2]:
                o.append(f"- [log L{ex['line_no']}] `{ex['raw'][:170]}`")
    for tid, rs in res["chain"]:
        o.append(f"\nTrace `{tid}` shows the failure propagating:")
        for r in rs:
            if LEVEL_RANK[r["level"]] >= 2:
                o.append(f"- [log L{r['line_no']}] {r['ts'].strftime('%H:%M:%S')} **{r['svc']}** {r['level']}: {r['msg'][:110]}")
    o.append("\n## Remediation playbook\n")
    for i, s in enumerate(res["playbook"], 1):
        o.append(f"{i}. **{s['title']}** _({s['safety']})_\n   ```\n   {s['command']}\n   ```\n   → {s['result']}")
    o.append(f"\n**Prevention:** {res['prevention']}\n")
    return "\n".join(o)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default="sample_logs.log")
    ap.add_argument("--events", default="sample_events.json")
    ap.add_argument("--mode", choices=["dry-run", "execute", "none"], default="dry-run",
                    help="execute runs only auto-safe steps; needs-approval steps are never auto-run")
    ap.add_argument("--out", default="incident_report.md")
    a = ap.parse_args()
    lines = open(a.logs).read().splitlines()
    events = json.load(open(a.events))
    res = analyse(lines, events, a.mode)
    report = render(res)
    open(a.out, "w",encoding="utf-8").write(report)
    print(report)
