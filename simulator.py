"""Generates realistic multi-service logs with an injected incident.

Incident story (ground truth, hidden from the agent):
  14:00:00  deploy of `payment-service` v2.14.0 lowers DB pool size 50 -> 5
  14:03:00  payment-service starts timing out acquiring DB connections
  14:04:00  checkout-service gets 504s from payment-service, retries pile up
  14:06:00  api-gateway returns 502/503 to users (cascade)
"""
import json, random, uuid
from datetime import datetime, timedelta

random.seed(7)
START = datetime(2026, 11, 24, 13, 30, 0)
END = datetime(2026, 11, 24, 14, 20, 0)
DEPLOY_AT = datetime(2026, 11, 24, 14, 0, 0)
FAULT_AT = datetime(2026, 11, 24, 14, 3, 0)
CASCADE_AT = datetime(2026, 11, 24, 14, 4, 0)
GATEWAY_AT = datetime(2026, 11, 24, 14, 6, 0)

SERVICES = ["api-gateway", "checkout-service", "payment-service", "inventory-service", "user-service"]


def line(ts, svc, level, msg, trace=None, host=None):
    host = host or f"{svc}-{random.randint(1, 3)}"
    ts_s = ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{random.randint(0, 999):03d}Z"
    t = f" trace_id={trace}" if trace else ""
    return f"{ts_s} {level:<5} [{svc}] host={host}{t} {msg}"


def generate():
    logs, events = [], []
    events.append({"ts": DEPLOY_AT.isoformat(), "type": "deployment", "service": "payment-service",
                   "version": "v2.14.0", "prev_version": "v2.13.2", "author": "dev-ravi",
                   "commit": "a41c9e7", "change": "config: db.pool.max_size 50 -> 5 (cost tuning)"})
    events.append({"ts": (START + timedelta(minutes=5)).isoformat(), "type": "deployment",
                   "service": "user-service", "version": "v1.8.1", "prev_version": "v1.8.0",
                   "author": "dev-meera", "commit": "7be21d0", "change": "fix: avatar cache TTL"})
    events.append({"ts": (START + timedelta(minutes=12)).isoformat(), "type": "infra",
                   "service": "inventory-service", "change": "autoscale 3 -> 4 replicas"})

    ts = START
    while ts < END:
        for _ in range(random.randint(8, 14)):  # requests per second batch
            t = ts + timedelta(milliseconds=random.randint(0, 999))
            tid = uuid.uuid4().hex[:12]
            u = random.randint(1000, 9999)
            logs.append(line(t, "api-gateway", "INFO", f"GET /api/checkout user={u} status=200 latency={random.randint(40,180)}ms", tid))
            logs.append(line(t, "checkout-service", "INFO", f"order created order_id={random.randint(10**5,10**6)} user={u}", tid))
            logs.append(line(t, "inventory-service", "DEBUG", f"stock reserved sku=SKU-{random.randint(100,999)} qty={random.randint(1,3)}", tid))

            pay_fail = ts >= FAULT_AT and random.random() < 0.85
            chk_fail = ts >= CASCADE_AT and random.random() < 0.8
            gw_fail = ts >= GATEWAY_AT and random.random() < 0.7

            if pay_fail:
                logs.append(line(t, "payment-service", "ERROR",
                    f"HikariPool-1 - Connection is not available, request timed out after 30000ms (active=5, idle=0, waiting={random.randint(20,140)})", tid))
                logs.append(line(t, "payment-service", "ERROR",
                    f"Payment failed for order_id={random.randint(10**5,10**6)}: SQLTransientConnectionException", tid))
            else:
                logs.append(line(t, "payment-service", "INFO", f"payment authorised amount={random.randint(100,9000)} currency=INR", tid))
            if chk_fail:
                logs.append(line(t, "checkout-service", "WARN", f"upstream payment-service timeout, retry {random.randint(1,3)}/3", tid))
                logs.append(line(t, "checkout-service", "ERROR", f"HTTP 504 from payment-service POST /pay after 30012ms", tid))
            if gw_fail:
                logs.append(line(t, "api-gateway", "ERROR", f"upstream checkout-service returned status=502 user={u} latency=30100ms", tid))
        if random.random() < 0.15:  # background noise
            logs.append(line(ts, "user-service", "WARN", f"slow query took {random.randint(300,900)}ms table=sessions"))
        if ts >= FAULT_AT + timedelta(minutes=2) and random.random() < 0.3:
            logs.append(line(ts, "payment-service", "WARN", f"HikariPool-1 - Thread starvation or clock leap detected (housekeeper delta={random.randint(40,90)}s)"))
        ts += timedelta(seconds=1)
    logs.sort()
    return logs, events


if __name__ == "__main__":
    logs, events = generate()
    open("sample_logs.log", "w").write("\n".join(logs) + "\n")
    json.dump(events, open("sample_events.json", "w"), indent=2)
    print(f"wrote {len(logs)} log lines, {len(events)} events")
