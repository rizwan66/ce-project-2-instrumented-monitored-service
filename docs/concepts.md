# Observability Concepts — Complete Reference

This document explains every concept used in this project from first principles.
You should be able to answer any interview or presentation question after reading this.

---

## 1. What is Observability?

**Observability** is the ability to understand the internal state of a system
by examining its external outputs (logs, metrics, traces).

A system is "observable" if, when something goes wrong, you can answer:
- **What** is broken?
- **Where** is it broken?
- **Why** did it break?
- **When** did it start?

Without observability you are **guessing**. With it you are **investigating**.

### Observability vs Monitoring

| | Monitoring | Observability |
|---|---|---|
| **What** | Watches known failure modes | Explores unknown failure modes |
| **Approach** | "Is X above threshold?" | "What is the system actually doing?" |
| **Tooling** | Dashboards, alerts | Logs, metrics, traces + correlation |
| **Limitation** | Can't catch what you didn't predict | Requires instrumentation up front |

This project does **both**: dashboards + alerts (monitoring) built on top of
structured logs + custom metrics (observability).

---

## 2. The Three Pillars of Observability

### Pillar 1: Logs

**What:** A timestamped record of discrete events that happened in a system.

**Unstructured log (bad):**
```
2024-01-15 10:23:45 - Order created for customer 42, total $89.97
```
Problems: hard to parse programmatically, inconsistent field names, no correlation.

**Structured log (good):**
```json
{
  "timestamp": "2024-01-15T10:23:45Z",
  "level": "info",
  "event": "order_created",
  "customer_id": "cust-42",
  "total": 89.97,
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000"
}
```
Benefits: every field is queryable, consistent schema, machine-readable.

### Pillar 2: Metrics

**What:** Numeric measurements aggregated over time.

Examples:
- `ErrorRate = 2.3%` (over the last 60 seconds)
- `P95Latency = 342ms`
- `OrdersPerMinute = 47`

Metrics answer "**how much** and **how often**" — they give you the shape of
what's happening, but not the detail of *why*.

**Types used in this project:**
| Type | Example | Description |
|------|---------|-------------|
| Counter | `OrdersCreated` | Only goes up; reset per period |
| Gauge | `ActiveSessions` | Point-in-time snapshot |
| Histogram | `P95LatencyMs` | Distribution of values |
| Business | `AvgOrderValue` | Domain-specific health metric |

### Pillar 3: Traces (not implemented — bonus)

**What:** A record of a single request's path through multiple services.

Example: User clicks "Buy" → API Gateway → Order Service → Inventory Service → Payment Service.
A trace shows how long each hop took and where it failed.

This project uses **correlation IDs** as a lightweight substitute: a single UUID
follows one request through all its log lines within the service.

---

## 3. The Four Golden Signals

Defined by the Google SRE book. If you can only monitor four things, monitor these:

### Signal 1: Rate (Traffic)

**Definition:** How many requests per unit time is the system serving?

**Why it matters:**
- Establishes the baseline — everything else is interpreted relative to it
- Sudden spike → possible attack or viral traffic event
- Sudden drop → upstream problem or deployment killed traffic

**In this project:** `RequestRate` metric (req/min), `OrdersPerMinute` (business rate)

### Signal 2: Errors

**Definition:** What fraction of requests are failing?

**Why it matters:**
- Most direct measure of user-visible impact
- Even 1% error rate = 1 in 100 users sees a broken experience

**Important:** Measure *rate* not *count*. 100 errors at 10,000 RPS (1%) is very
different from 100 errors at 200 RPS (50%).

**In this project:** `ErrorRate` metric (%), separate counters by error type (400/404/500)

### Signal 3: Latency (Duration)

**Definition:** How long does it take to serve a request?

**Why it matters:**
- Slow is often worse than broken — users wait, then abandon
- Must distinguish *successful* latency from *error* latency

**Why P95, not average:**
```
10 requests: [10ms, 12ms, 11ms, 9ms, 13ms, 11ms, 10ms, 12ms, 11ms, 3000ms]
Average:  309ms  ← looks terrible but 9/10 users were fine
P95:      3000ms ← catches the 1 in 10 who suffered
P99:      3000ms ← catches the 1 in 100 who suffered
```
Average is pulled up by outliers but masks the majority experience.
P95/P99 specifically target the users in the "long tail".

**In this project:** `P95LatencyMs` metric, computed from samples in background thread

### Signal 4: Saturation

**Definition:** How "full" is the system? What resource is the bottleneck?

Resources that saturate:
- **CPU:** above ~80% → context switching overhead, queueing
- **Memory:** above ~90% → OOM kill risk, swap thrashing
- **Disk:** above ~85% → write failures, log loss
- **Network:** high queue depth, packet loss
- **Threads/Connections:** pool exhaustion

**In this project:** `ActiveSessions` (concurrency), CloudWatch Agent CPU/memory/disk metrics

---

## 4. RED Method

A framework for diagnosing **service-level** problems (request-driven services).

| Letter | Stands for | Question |
|--------|-----------|---------|
| **R** | Rate | How many requests per second? |
| **E** | Errors | How many of those requests are failing? |
| **D** | Duration | How long are requests taking? |

**How to use RED during an incident:**

1. **R** — Is traffic normal? (spike = load problem, drop = upstream problem)
2. **E** — Are errors high? What kind? (4xx = client bug, 5xx = server bug)
3. **D** — Is latency high? (slow requests → resource saturation or slow dependency)

**In this project:** The top two rows of the dashboard (Traffic + Errors + Latency)
are explicitly organized to support the RED method.

---

## 5. USE Method

A framework for diagnosing **resource-level** (infrastructure) problems.

| Letter | Stands for | Question |
|--------|-----------|---------|
| **U** | Utilization | What % of time is this resource busy? |
| **S** | Saturation | How much extra work is queuing up? |
| **E** | Errors | Are there hardware/driver errors? |

Apply USE to every resource: CPU, memory, disk, network, database connections.

**How to use USE during an incident:**

1. CPU utilization high? → CPU bottleneck
2. Memory utilization high + swap active? → Memory bottleneck
3. Disk iowait high? → Disk I/O bottleneck
4. Network queue depth high? → Network bottleneck

**In this project:** The bottom row (Saturation) of the dashboard supports USE.

---

## 6. Structured Logging

### What is structlog?

`structlog` is a Python library that builds log entries as a chain of processors,
each adding or transforming fields, producing a final JSON line.

### Processor Pipeline (this project)

```
Input event dict: {"event": "order_created"}
         │
         ▼
add_log_level         → {"event": "...", "level": "info"}
         │
         ▼
TimeStamper           → {"event": "...", "level": "info", "timestamp": "2024-01-15T10:23:45Z"}
         │
         ▼
add_service_info      → {..., "service": "order-api", "environment": "production", "version": "1.0.0"}
         │
         ▼
add_correlation_id    → {..., "correlation_id": "550e8400-..."}
         │
         ▼
JSONRenderer          → '{"event":"order_created","level":"info","timestamp":"...","service":"order-api",...}\n'
         │
         ▼
stdout → CloudWatch Agent → CloudWatch Logs
```

Every processor is a pure function: `(logger, method, event_dict) → event_dict`.
This makes the pipeline composable and testable.

### Log Levels — When to Use Each

```python
log.debug(...)   # Detailed internal state (disabled in production)
log.info(...)    # Normal business events: order_created, request_completed
log.warning(...) # Expected but noteworthy: order not found, validation failed
log.error(...)   # Unexpected failure: unhandled exception, downstream timeout
log.critical(...)# System is unusable (rarely used in application code)
```

**Rule:** An on-call engineer woken at 3am should never see `INFO` in their alert.
Only `ERROR` and above should trigger notifications.

---

## 7. Correlation IDs

### What is a Correlation ID?

A UUID generated (or forwarded) at the start of each HTTP request and attached
to every log line produced during that request's lifetime.

### Why They Matter

Without correlation IDs, finding all logs for one failed request requires:
- Filtering by timestamp (imprecise — many concurrent requests)
- Filtering by endpoint (returns all requests, not one)
- Guessing from context

With correlation IDs:
```bash
aws logs filter-log-events \
  --filter-pattern '{ $.correlation_id = "550e8400-e29b-41d4-a716-446655440000" }'
```
→ Returns exactly the 3–5 log lines for that one request, in order.

### How It Works in This Project

```python
# before_request runs before every Flask handler
@app.before_request
def before_request():
    # Use client's ID if provided (end-to-end tracing), else generate new
    g.correlation_id = (
        request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
    )

# add_correlation_id processor reads from Flask's g object
def add_correlation_id(logger, method, event_dict):
    event_dict["correlation_id"] = g.get("correlation_id", "no-request-context")
    return event_dict

# Every log line in any handler automatically gets the ID
log.info("order_created", order_id="...")
# → {"event": "order_created", "correlation_id": "550e8400-...", ...}

# ID is returned to the client in the response header
response.headers["X-Correlation-ID"] = g.correlation_id
```

---

## 8. CloudWatch Metrics — How They Work

### Namespace

A logical container for related metrics. Like a directory.

```
OrderAPI/Production
    ├── RequestRate
    ├── ErrorRate
    ├── P95LatencyMs
    ├── OrdersPerMinute
    ├── AvgOrderValue
    └── ActiveSessions

OrderAPI/System
    ├── cpu_usage_user
    ├── mem_used_percent
    └── ...
```

Namespaces prevent metric name collisions between services.

### Dimensions

Key-value pairs that further identify a metric. Like tags.

```json
"Dimensions": [
  {"Name": "Service",     "Value": "order-api"},
  {"Name": "Environment", "Value": "production"}
]
```

This lets you have the same metric for multiple services/environments
and filter/aggregate by dimension:
- `ErrorRate WHERE Service=order-api AND Environment=production`
- `ErrorRate WHERE Service=payment-api AND Environment=staging`

### Resolution

- **Standard (60s):** Stored for 15 months, cheaper
- **High-resolution (1s):** Stored for 3 hours at 1s, then down-sampled

This project uses standard 60s — appropriate for most alerting scenarios.

### Statistics

When querying a metric over a time period, CloudWatch aggregates using:
- `Sum` — total count (use for: RequestRate, OrdersCreated)
- `Average` — mean value (use for: ErrorRate, P95LatencyMs)
- `Maximum` — worst case (use for: latency spike detection)
- `Minimum` — best case
- `SampleCount` — number of data points

### PutMetricData API (boto3)

```python
cloudwatch.put_metric_data(
    Namespace="OrderAPI/Production",
    MetricData=[{
        "MetricName": "ErrorRate",
        "Value": 2.3,
        "Unit": "Percent",
        "Timestamp": datetime.now(timezone.utc),
        "Dimensions": [
            {"Name": "Service", "Value": "order-api"},
            {"Name": "Environment", "Value": "production"},
        ]
    }]
)
```

CloudWatch charges per metric per month, and per PutMetricData API call.
This is why we batch metrics (publish 6 at once instead of 6 separate calls).

---

## 9. CloudWatch Alarms — How They Work

### Anatomy of an Alarm

```
Alarm evaluates: Average(ErrorRate) over the last N * period seconds
                 ↓
If value > threshold for M out of N periods → ALARM state
If value ≤ threshold → OK state
If no data → TreatMissingData state
```

### Evaluation Periods vs DatapointsToAlarm

```
EvaluationPeriods=3, DatapointsToAlarm=2 means:
"Look at the last 3 one-minute periods.
 If at least 2 of them breach the threshold → ALARM."

This is called a "2 out of 3" alarm.
```

**Why not just 1 out of 1?**
A single bad data point could be a momentary spike, a measurement error,
or a single failed request. 2/3 requires sustained breach, eliminating noise.

**Why not 3 out of 3?**
You'd only alarm after 3 full minutes — too slow for critical issues.
Critical alarms in this project use 2/2 (fires after 2 sustained minutes).

### TreatMissingData

What to do when no data points exist for a period:
- `notBreaching` (default): treat missing data as if the metric is OK
- `breaching`: treat missing data as if the threshold is breached
- `ignore`: keep the current alarm state
- `missing`: put alarm in INSUFFICIENT_DATA state

**When to use `breaching`:**
The `OrderAPI-OrderRate-Drop` alarm uses `breaching` because missing data
means the service is probably down — it should alarm, not silently pass.

### SNS Integration

```
CloudWatch Alarm state change → SNS Topic → All subscribers get notified
                                          → Email
                                          → Lambda (auto-remediation)
                                          → PagerDuty/Slack (via Lambda)
                                          → HTTP endpoint
```

`AlarmActions` fires when alarm transitions to ALARM state.
`OKActions` fires when alarm transitions back to OK state (recovery notification).

---

## 10. Tiered Alerting — Warning vs Critical

| | Warning | Critical |
|--|---------|---------|
| **Trigger** | Early sign of degradation | User-visible impact |
| **Response** | Investigate within 30 min | Wake someone up immediately |
| **Error Rate** | > 1% | > 5% |
| **P95 Latency** | > 500ms | > 1000ms |
| **Sensitivity** | More relaxed (3/5 datapoints) | Stricter (2/2 datapoints) |

**Why tier?**

If every warning is a page, engineers become desensitized (alert fatigue).
If only critical fires, you lose early warning — problems become crises.

The tier system gives you advance notice without burning out the team.

---

## 11. Business Metrics vs Technical Metrics

**Technical metrics** measure the system's behavior:
- CPU usage, memory, request rate, error rate, latency

**Business metrics** measure the system's purpose:
- Orders per minute, average order value, cart abandonment rate

**Why business metrics matter:**

Imagine a bug where `POST /orders` returns HTTP 200 but doesn't actually
save the order (a silent data loss bug). Technical metrics show:
- Error rate: 0% ✓
- Latency: normal ✓
- CPU/memory: normal ✓

Everything looks fine! But the business metric:
- OrdersPerMinute: **0** ✗

The `OrderAPI-OrderRate-Drop` alarm catches the business failure that
all technical metrics missed. This is why business metrics are not optional.

---

## 12. Gunicorn — The Production WSGI Server

Flask's built-in development server (`app.run()`) is single-threaded and
not suitable for production. **Gunicorn** is a production-grade WSGI server.

### Configuration Used

```
gunicorn --workers 2 --threads 4 --bind 0.0.0.0:5000 server:app
```

- `--workers 2`: 2 separate Python processes (bypasses GIL for CPU parallelism)
- `--threads 4`: 4 threads per worker (handles concurrent I/O-bound requests)
- Total concurrency: 2 × 4 = **8 simultaneous requests**
- `server:app`: import `app` from `server.py`

### Why This Matters for Metrics

The background metrics thread (`_publish_aggregate_metrics`) runs in each worker
process independently. With 2 workers, you'd get 2 threads publishing metrics.
In production, you'd use a dedicated metrics sidecar (StatsD, OpenTelemetry)
to avoid this duplication. For this project, the simplicity is worth the tradeoff.

---

## 13. Systemd — Managing the Service

`systemd` is the Linux init system that manages long-running services.

### Key Directives in This Project

```ini
[Unit]
After=network.target         # Don't start until network is up

[Service]
Type=simple                  # Process stays in foreground (Gunicorn does this)
User=ubuntu                  # Run as non-root (security best practice)
WorkingDirectory=...         # Where to run the process
Environment=KEY=VALUE        # Inject config as env vars
ExecStart=...                # The command to run
Restart=always               # Restart on crash
RestartSec=5                 # Wait 5s before restart
StandardOutput=journal       # Capture stdout to systemd journal
StandardError=journal        # Capture stderr to systemd journal

[Install]
WantedBy=multi-user.target   # Start in normal boot mode
```

### Why `Restart=always`?

If the Python process crashes (OOM kill, unhandled exception), systemd
automatically restarts it after 5 seconds. This is "self-healing" — the
service recovers without human intervention.

### Useful Commands

```bash
systemctl status order-api     # Is it running?
systemctl start order-api      # Start it
systemctl stop order-api       # Stop it
systemctl restart order-api    # Restart it
journalctl -u order-api -f     # Follow live logs
journalctl -u order-api -n 50  # Last 50 log lines
```

---

## 14. Flask Middleware — before_request / after_request

Flask's request lifecycle:

```
HTTP Request arrives
       │
       ▼
  before_request()      ← runs before every route handler
       │                   used for: correlation ID, auth, rate limiting
       ▼
  Route handler()       ← your actual business logic
       │
       ▼
  after_request()       ← runs after every route handler, before response
       │                   used for: logging, metrics recording
       ▼
HTTP Response sent
```

### This Project's Middleware

```python
@app.before_request
def before_request():
    g.start_time = time.time()           # Start timing
    g.correlation_id = (                  # Assign ID
        request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
    )
    _increment("requests_total")          # Count request
    _increment("active_sessions")         # Track concurrency
    log.info("request_started", ...)      # Log entry

@app.after_request
def after_request(response):
    duration = time.time() - g.start_time # Calculate latency
    _append_latency(duration)              # Store for P95 calculation
    if response.status_code >= 400:
        _increment("requests_error")       # Count errors
    _increment("active_sessions", -1)      # Decrement concurrency
    log.info("request_completed", ...)     # Log completion
    response.headers["X-Correlation-ID"] = g.correlation_id  # Return ID
    return response
```

The key insight: **every route handler automatically gets logging and metrics
for free** — no code duplication needed.

---

## 15. Thread Safety in Python

The `_stats` dictionary is shared between:
- The main Gunicorn worker threads (handling HTTP requests)
- The background metrics thread

Without protection, two threads could read-then-write the same counter
simultaneously, losing increments (race condition).

### Solution: threading.Lock()

```python
_lock = threading.Lock()

def _increment(key, amount=1):
    with _lock:              # Acquire lock — only one thread enters at a time
        _stats[key] += amount
    # Lock automatically released when 'with' block exits
```

The background thread holds the lock only long enough to copy and reset counters,
then releases it immediately — minimizing contention with HTTP request threads.

---

## 16. The P95 Latency Calculation

```python
# During each request (after_request):
_stats["latencies"].append(duration)   # Append to list

# Background thread every 60s:
lats = sorted(_stats["latencies"])     # Sort all samples
n = len(sorted_lats)
p95 = sorted_lats[int(n * 0.95)]      # 95th percentile index

# Reset for next window
_stats["latencies"] = []
```

**Example:**
```
60 latency samples: [10, 11, 12, ..., 950ms]
Sorted: [10, 11, 12, ..., 950]
n = 60
index = int(60 * 0.95) = 57
P95 = sorted_lats[57]   # The 57th fastest sample
```

This is a **true P95** over the 60-second window, not an approximation.
In production at high scale you'd use reservoir sampling or a sketch algorithm
(HdrHistogram, DDSketch) to avoid unbounded memory growth.
