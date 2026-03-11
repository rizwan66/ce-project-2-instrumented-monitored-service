# Incidents

## Incident Summary Template

```
## INC-XXX: [Title]
- Date: YYYY-MM-DD
- Duration: HH:MM
- Severity: Warning / Critical
- Affected: endpoint or feature
- Status: Resolved

### Timeline
| Time  | Event |
|-------|-------|
| HH:MM | Alert fired |

### Root Cause
...

### Impact
...

### Investigation (RED/USE)
**Rate:** ...
**Errors:** ...
**Duration (Latency):** ...
**Utilization:** ...
**Saturation:** ...
**Errors (system):** ...

### Fix Applied
...

### Lessons Learned
...

### Follow-up Actions
- [ ] Action item
```

---

## INC-001: Artificial High Latency Injection

- **Date:** Day 2 of project
- **Duration:** 8 minutes
- **Severity:** Critical
- **Affected:** All endpoints (global latency increase)
- **Status:** Resolved

### Timeline

| Time  | Event |
|-------|-------|
| T+0   | `POST /chaos/latency {"seconds": 3}` called |
| T+1   | P95 latency spikes from ~40ms to ~3100ms |
| T+2   | `OrderAPI-Latency-P95-Warning` alarm fires → email received |
| T+3   | `OrderAPI-Latency-P95-Critical` alarm fires → email received |
| T+5   | Investigation identifies chaos endpoint call in logs |
| T+6   | Chaos effect wears off (single request, not persistent) |
| T+8   | Both alarms recover → OK emails received |

### Root Cause

Manual injection via `/chaos/latency` endpoint to simulate a slow downstream dependency (e.g., a database with high query latency). The 3-second sleep was applied per-request, causing P95 to spike immediately.

### Impact

- 100% of requests during the 1-minute window were slowed to >3s
- P95 latency reached 3,100ms (3.1x the critical SLO)
- No orders were lost (latency only, no errors)
- 0 users affected (controlled test environment)

### Investigation (RED/USE)

**Rate:** Request rate remained normal (~10 req/min) — ruled out traffic spike as cause.

**Errors:** Error rate stayed at 0% — this is a pure latency problem, not a failure.

**Duration:** P95 jumped from 40ms to 3,100ms in one measurement period. Clearly not gradual degradation — something changed instantly.

**Utilization:** CPU stayed at 5%, memory unchanged — ruled out resource saturation.

**Saturation:** Active sessions briefly increased (requests queueing behind the slow handler) then normalized.

**Log investigation:**
```bash
aws logs filter-log-events \
  --log-group-name /aws/order-api \
  --filter-pattern '{ $.event = "chaos_latency_injected" }' \
  --start-time $(date -d '10 min ago' +%s)000
```
→ Found entry: `"event": "chaos_latency_injected", "seconds": 3`

**Root cause confirmed:** Chaos endpoint was called. No further action needed.

### Fix Applied

- No code fix needed (intentional injection)
- In production, this would be: fix the slow downstream call (add timeout, circuit breaker, or cache)

### Lessons Learned

1. The alarms fired within 2 minutes of injection — latency alerting works correctly
2. P95 (not average) was the right choice — average would have been ~1.5s, masking the severity
3. Correlation ID in logs allowed pinpointing the exact request that triggered the chaos call
4. A real database timeout scenario would look identical — runbook should check downstream health first

### Follow-up Actions

- [x] Confirm alarm timings documented
- [ ] Add circuit breaker to production database calls
- [ ] Add downstream health check endpoint

---

## INC-002: Error Rate Spike (Injected 500 Errors)

- **Date:** Day 2 of project
- **Duration:** 12 minutes
- **Severity:** Critical
- **Affected:** POST /chaos/error endpoint, error rate metric
- **Status:** Resolved

### Timeline

| Time  | Event |
|-------|-------|
| T+0   | Load test running at 10 RPS; `/chaos/error` called repeatedly in loop |
| T+1   | Error rate climbs: 0% → 8% → 15% |
| T+2   | `OrderAPI-ErrorRate-Warning` fires (>1%) |
| T+3   | `OrderAPI-ErrorRate-Critical` fires (>5%) |
| T+4   | Chaos loop stopped |
| T+7   | Error rate returns to 0% |
| T+10  | Warning alarm recovers |
| T+12  | Critical alarm recovers |

### Root Cause

`POST /chaos/error` was called in a shell loop at ~3 RPS while the load test ran at 10 RPS, producing a 23% error rate (3/13 requests were forced 500s).

### Impact

- Error rate peaked at 23% over 2 consecutive 60-second windows
- `OrdersCancelled` metric unaffected (errors before order processing)
- 0 real orders lost (chaos endpoint, not the order endpoint)

### Investigation (RED/USE)

**Rate:** Load test traffic was normal at 10 RPS.

**Errors:** `Errors by Type` widget showed spike in `UnhandledErrors` and `ChaosErrors` metrics simultaneously, not `ValidationErrors` or `OrderNotFound` — pointed to the chaos endpoint.

**Duration:** Latency was normal (~40ms) — errors were fast 500s, not slow failures.

**Log query to identify error source:**
```
fields @timestamp, event, path, status_code
| filter status_code = 500
| stats count() by event
```
Result: `chaos_error_injected: 36`, `unhandled_exception: 2`

**Confirmed:** 36 intentional chaos errors, 2 real errors (looked into those separately — were race conditions in the test).

### Fix Applied

- No fix needed for chaos injection
- The 2 real unhandled exceptions: traced via correlation ID, found they were from concurrent requests hitting the same order ID during the stress test — added double-check in DELETE handler

### Lessons Learned

1. `ChaosErrors` metric being a separate dimension from `UnhandledErrors` was extremely helpful — immediately distinguished intentional from real errors
2. Checking latency alongside error rate quickly ruled out resource exhaustion as a cause
3. 2/2 evaluation period for Critical alarm meant it fired 1 minute faster than 2/3 — right call for high-severity alert

---

## INC-003: Memory Pressure (Complex Failure)

- **Date:** Day 3 of project
- **Duration:** 22 minutes
- **Severity:** Critical (multi-factor)
- **Affected:** All endpoints — memory exhaustion causing GC pressure and latency spikes
- **Status:** Resolved

### Timeline

| Time  | Event |
|-------|-------|
| T+0   | `POST /chaos/memory {"mb": 400}` called |
| T+2   | Memory climbs: 35% → 72% |
| T+4   | `OrderAPI-Memory-Critical` warning fires (>75% — actually using warning threshold) |
| T+5   | Memory reaches 91% |
| T+6   | `OrderAPI-Memory-Critical` fires |
| T+7   | Python GC pressure causes latency spike; P95 climbs 40ms → 680ms |
| T+8   | `OrderAPI-Latency-P95-Warning` fires |
| T+9   | Error rate begins rising (timeout/OOM errors): 0% → 3.2% |
| T+10  | `OrderAPI-ErrorRate-Warning` fires |
| T+11  | Correlation analysis: Memory ↑, Latency ↑, Error ↑ — classic resource exhaustion cascade |
| T+14  | Service restarted: `sudo systemctl restart order-api` |
| T+15  | Memory drops to 34% |
| T+16  | Latency returns to 40ms |
| T+18  | Error rate returns to 0% |
| T+20  | All alarms recover |
| T+22  | Post-incident review begins |

### Root Cause

400MB allocated in a single request via `/chaos/memory`. Python's garbage collector does not immediately reclaim the memory (the `bytearray` is held in `app._chaos_memory`). At 91% memory utilization, the Linux kernel begins swapping, causing high I/O wait and GC pauses. This created a **cascade failure**:

```
Memory pressure → GC pauses → Request queueing → P95 latency spike → Timeouts → Error rate rise
```

Three separate alarms fired for what was one root cause — demonstrating why correlation analysis on the dashboard matters more than individual alarm counts.

### Investigation (RED/USE)

**Rate:** Stable — request rate unchanged. Ruled out traffic cause.

**Errors:** 3.2% error rate — started after memory was already high. Secondary symptom, not root cause.

**Duration:** P95 latency spiked after memory spiked. Timeline order: Memory → Latency → Errors.

**Utilization:** Memory 91% (clear culprit), CPU showed high iowait (swap activity).

**Saturation:** Active sessions climbed as requests queued behind GC pauses.

**Key dashboard observation:** Memory widget (Row 23) was elevated **before** the Latency widget (Row 16) spiked. This temporal ordering on the dashboard confirmed the causal direction.

**Logs Insights query:**
```
fields @timestamp, event, level
| filter level in ["warning", "error"]
| sort @timestamp asc
```
First warning event: `chaos_memory_injected` — confirmed the trigger.

### Fix Applied

**Immediate:** Restarted the service to release the allocated memory.

**Short-term:**
- Added `del app._chaos_memory` to a new cleanup endpoint `/chaos/reset`
- Added memory limit to systemd unit: `MemoryMax=512M` (triggers OOM kill before system swap)

**Long-term proposals:**
1. Add memory leak detection (publish Python `gc.get_count()` as a CloudWatch metric)
2. Set `MemoryMax` in production systemd unit
3. Add auto-remediation: Lambda triggered by Memory alarm to restart service
4. Implement health check that returns 503 when memory > 85% (allows ALB to route away)

### Lessons Learned

1. **Cascade failures look like multiple problems** — always check what fired first. The memory alarm at T+6 was before latency (T+8) and errors (T+10).
2. **Dashboard timeline is investigation tool** — scrolling through metric history showed the sequence clearly.
3. **Restart is valid for memory leaks** — but only as a band-aid. The real fix is finding and patching the leak.
4. **`TreatMissingData: breaching` matters** — if the process had OOM-killed, missing metrics would correctly keep alarms in ALARM state.
5. **Three alarms, one incident** — in the post-mortem, we would link all three alarm notifications to this single incident in PagerDuty.

### Follow-up Actions

- [x] Service restarted, memory restored
- [x] Added MemoryMax to systemd unit
- [ ] Implement auto-remediation Lambda for memory alarm
- [ ] Add gc metrics to instrumentation
- [ ] Health check should return 503 at high memory utilization
- [ ] Document memory leak investigation procedure in runbook
