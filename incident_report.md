# Incident Report — Autonomous RCA

**Root cause service:** `payment-service`  |  **Onset:** 2026-11-24T14:03:00.023000Z  |  **Confidence:** 95%

**Diagnosis:** Database connection pool exhaustion

**Trigger:** deployment of `payment-service` v2.14.0 at 2026-11-24T14:00:00Z (3.0 min before first anomaly) by dev-ravi (commit `a41c9e7`)
> Change: config: db.pool.max_size 50 -> 5 (cost tuning)

Analysed 166,110 log lines, collapsed into 11 templates; 6 anomalous clusters found.

## Blast radius (cascade)

Dependencies inferred from logs: api-gateway→checkout-service, checkout-service→payment-service

Upstream services impacted: `api-gateway`, `checkout-service`

## Anomaly timeline (clusters ordered by onset)

| # | Onset | Service | Lvl | Peak/min | Baseline | Signature |
|---|---|---|---|---|---|---|
| 1 | 14:03 | payment-service | ERROR | 603 | 0.0 | `Payment failed for order_id=<V> SQLTransientConnectionException` |
| 2 | 14:03 | payment-service | ERROR | 602 | 0.0 | `HikariPool-<N> - Connection is not available, request timed out after <N> (activ` |
| 3 | 14:04 | checkout-service | ERROR | 559 | 0.0 | `HTTP <N> from payment-service POST /pay after <N>` |
| 4 | 14:04 | checkout-service | WARN | 558 | 0.0 | `upstream payment-service timeout, retry <N>/<N>` |
| 5 | 14:05 | payment-service | WARN | 23 | 0.0 | `HikariPool-<N> - Thread starvation or clock leap detected (housekeeper delta=<V>` |
| 6 | 14:06 | api-gateway | ERROR | 496 | 0.0 | `upstream checkout-service returned status=<V> user=<V> latency=<V>` |

## Evidence (citations)

- [log L87619] `2026-11-24T14:03:00.248Z ERROR [payment-service] host=payment-service-2 trace_id=47d4e1c13572 Payment failed for order_id=461351: SQLTransientConnectionException`
- [log L87623] `2026-11-24T14:03:00.392Z ERROR [payment-service] host=payment-service-1 trace_id=1ee277944932 Payment failed for order_id=836555: SQLTransientConnectionException`
- [log L87611] `2026-11-24T14:03:00.072Z ERROR [payment-service] host=payment-service-1 trace_id=e3b8c64a8a23 HikariPool-1 - Connection is not available, request timed out after 30000ms `
- [log L87621] `2026-11-24T14:03:00.316Z ERROR [payment-service] host=payment-service-2 trace_id=47d4e1c13572 HikariPool-1 - Connection is not available, request timed out after 30000ms `
- [log L95035] `2026-11-24T14:05:00.611Z WARN  [payment-service] host=payment-service-3 HikariPool-1 - Thread starvation or clock leap detected (housekeeper delta=47s)`
- [log L95170] `2026-11-24T14:05:02.512Z WARN  [payment-service] host=payment-service-2 HikariPool-1 - Thread starvation or clock leap detected (housekeeper delta=67s)`

Trace `f054622bbbba` shows the failure propagating:
- [log L99199] 14:06:00 **payment-service** ERROR: HikariPool-1 - Connection is not available, request timed out after 30000ms (active=5, idle=0, waiting=135)
- [log L99233] 14:06:00 **api-gateway** ERROR: upstream checkout-service returned status=502 user=9107 latency=30100ms
- [log L99238] 14:06:00 **payment-service** ERROR: Payment failed for order_id=160288: SQLTransientConnectionException
- [log L99241] 14:06:00 **checkout-service** WARN: upstream payment-service timeout, retry 3/3
- [log L99272] 14:06:00 **checkout-service** ERROR: HTTP 504 from payment-service POST /pay after 30012ms

## Remediation playbook

1. **Roll back the offending release** _(auto-safe)_
   ```
   kubectl rollout undo deployment/payment-service -n prod
   ```
   → [dry-run] kubectl rollout undo deployment/payment-service -n prod
2. **Verify rollout health** _(auto-safe)_
   ```
   kubectl rollout status deployment/payment-service -n prod --timeout=120s
   ```
   → [dry-run] kubectl rollout status deployment/payment-service -n prod --timeout=120s
3. **If rollback is not possible: raise pool size via config** _(needs-approval)_
   ```
   kubectl set env deployment/payment-service DB_POOL_MAX_SIZE=50 -n prod
   ```
   → [dry-run] kubectl set env deployment/payment-service DB_POOL_MAX_SIZE=50 -n prod
4. **Confirm DB has headroom** _(auto-safe)_
   ```
   psql -c "select count(*) from pg_stat_activity;"
   ```
   → [dry-run] psql -c "select count(*) from pg_stat_activity;"

**Prevention:** Add a CI guard on db.pool.max_size (min bound), load-test config changes, canary releases with error-rate auto-rollback.
