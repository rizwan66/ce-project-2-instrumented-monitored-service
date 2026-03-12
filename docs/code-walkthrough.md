# Code Walkthrough — Line by Line

This document explains every significant piece of code in the project,
what it does, and *why* it was written that way.

---

## app/config.py

```python
import os

class Config:
    SERVICE_NAME    = os.environ.get("SERVICE_NAME", "order-api")
    SERVICE_VERSION = os.environ.get("SERVICE_VERSION", "1.0.0")
    ENVIRONMENT     = os.environ.get("ENVIRONMENT", "development")
    PORT            = int(os.environ.get("PORT", "5000"))
    AWS_REGION      = os.environ.get("AWS_REGION", "us-east-1")
    METRICS_NAMESPACE = os.environ.get("METRICS_NAMESPACE", "OrderAPI/Production")
    SIMULATE_LATENCY  = os.environ.get("SIMULATE_LATENCY", "false").lower() == "true"
```

### Why environment variables?

**Twelve-Factor App** principle: configuration belongs in the environment,
not in code. This means:
- No secrets in source code
- Same Docker image / code runs in dev, staging, production
- Override per-environment without code changes

```bash
# Development (defaults):      SERVICE_NAME=order-api, ENVIRONMENT=development
# Production (systemd unit):   SERVICE_NAME=order-api, ENVIRONMENT=production
# Staging (different):         SERVICE_NAME=order-api, ENVIRONMENT=staging
```

`os.environ.get("KEY", "default")` returns the default if the env var
is not set — safe for local development where you haven't set anything.

`int(os.environ.get("PORT", "5000"))` — environment variables are always
strings, so we cast explicitly. If PORT="abc" this would raise a ValueError,
which is the right behavior (fail fast on bad config).

`SIMULATE_LATENCY` pattern: env vars can only be strings, so we compare
the lowercased string to "true". This handles "True", "TRUE", "true" all correctly.

---

## app/server.py — Section by Section

### Section 1: Imports

```python
import json, time, uuid, random, threading, os
from datetime import datetime, timezone
from functools import wraps

import boto3
import structlog
from flask import Flask, request, jsonify, g
from botocore.exceptions import ClientError

from config import Config
```

**Standard library:**
- `time` — `time.time()` gives Unix timestamp in seconds (float)
- `uuid` — `uuid.uuid4()` generates a random UUID for correlation IDs
- `threading` — `threading.Lock()` for thread safety, `threading.Thread` for background worker
- `datetime` — timezone-aware timestamps for CloudWatch

**Third-party:**
- `boto3` — AWS SDK for Python. Handles authentication, request signing, retries
- `structlog` — structured logging library
- `flask` — web framework
  - `Flask` — the application object
  - `request` — proxy to the current HTTP request
  - `jsonify` — converts dict to JSON response with correct Content-Type header
  - `g` — Flask's per-request global storage (cleared between requests)
- `botocore.exceptions.ClientError` — base exception for AWS API errors

---

### Section 2: structlog Configuration

```python
def add_correlation_id(logger, method, event_dict):
    try:
        event_dict["correlation_id"] = g.get("correlation_id", "no-request-context")
    except RuntimeError:
        # RuntimeError is raised when accessing g outside request context
        event_dict["correlation_id"] = "no-request-context"
    return event_dict
```

**Why the try/except?**

Flask's `g` object only exists during an HTTP request. If `structlog` logs
something outside a request (e.g., at startup, in the background thread),
accessing `g` raises `RuntimeError: Working outside of application context`.

The fallback `"no-request-context"` makes background logs identifiable
and prevents crashes.

```python
def add_service_info(logger, method, event_dict):
    event_dict["service"] = Config.SERVICE_NAME
    event_dict["environment"] = Config.ENVIRONMENT
    event_dict["version"] = Config.SERVICE_VERSION
    return event_dict
```

Every single log line gets these three fields automatically. In a system
with multiple services, this lets you filter logs by service and environment
in CloudWatch Logs Insights without any per-log effort.

```python
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,       # adds "level": "info"
        structlog.processors.TimeStamper(fmt="iso", utc=True),  # adds "timestamp": "2024-..."
        add_service_info,                      # adds service/environment/version
        add_correlation_id,                    # adds correlation_id
        structlog.processors.StackInfoRenderer(),   # renders stack_info= kwarg
        structlog.processors.format_exc_info,       # renders exc_info=True
        structlog.processors.JSONRenderer(),         # converts dict → JSON string
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),  # prints to stdout
)

log = structlog.get_logger()
```

**`structlog.PrintLoggerFactory()`** writes to stdout. On EC2 with systemd,
stdout is captured by the journal. The CloudWatch Agent reads from the journal
or a log file and ships to CloudWatch Logs.

**Order matters:** `add_correlation_id` runs after `add_service_info` because
it reads from Flask's `g`, which requires the app context that exists by the
time processors run.

---

### Section 3: CloudWatch Client & Metric Helpers

```python
cloudwatch = boto3.client("cloudwatch", region_name=Config.AWS_REGION)
```

`boto3.client` creates a low-level service client. It automatically uses:
1. Environment variables (`AWS_ACCESS_KEY_ID`, etc.)
2. EC2 IAM instance profile (what we use in production)
3. `~/.aws/credentials` (for local dev)

```python
def publish_metric(metric_name, value, unit="Count", dimensions=None):
    if dimensions is None:
        dimensions = [{"Name": "Service", "Value": Config.SERVICE_NAME},
                      {"Name": "Environment", "Value": Config.ENVIRONMENT}]
    try:
        cloudwatch.put_metric_data(
            Namespace=Config.METRICS_NAMESPACE,
            MetricData=[{
                "MetricName": metric_name,
                "Value": value,
                "Unit": unit,
                "Timestamp": datetime.now(timezone.utc),
                "Dimensions": dimensions,
            }]
        )
    except ClientError as e:
        log.warning("cloudwatch_publish_failed", metric=metric_name, error=str(e))
```

**Why catch `ClientError` and not let it propagate?**

If CloudWatch is temporarily unavailable (network blip, AWS regional issue),
we don't want to crash the main application — orders should still work.
We log a warning so the failure is visible without breaking user-facing requests.

This is a key observability design principle: **the observability system
must not become a single point of failure for the application**.

```python
def publish_metrics_batch(metric_data):
    try:
        cloudwatch.put_metric_data(
            Namespace=Config.METRICS_NAMESPACE,
            MetricData=metric_data,   # Up to 20 data points in one API call
        )
    except ClientError as e:
        log.warning("cloudwatch_batch_publish_failed", error=str(e))
```

CloudWatch's `PutMetricData` API accepts up to 20 metrics per call.
Batching reduces API calls (and cost) when publishing multiple metrics at once.

---

### Section 4: In-Memory State

```python
orders: dict = {}
_lock = threading.Lock()

_stats = {
    "requests_total": 0,
    "requests_success": 0,
    "requests_error": 0,
    "orders_created": 0,
    "total_order_value": 0.0,
    "latencies": [],
    "active_sessions": 0,
}
```

**`orders` dict** — simulates a database. In production this would be
RDS/DynamoDB/Redis. The dict uses `order_id` (UUID string) as the key.

**`_stats` dict** — rolling counters reset every 60 seconds by the background
thread. Prefixed with `_` by convention = "private to this module".

**`_lock`** — a single mutex protects both `_stats` mutations. Using one lock
for everything is simpler than per-field locks and avoids deadlocks.
Performance is acceptable here because the lock is held for microseconds.

```python
def _increment(key, amount=1):
    with _lock:
        _stats[key] += amount

def _append_latency(val):
    with _lock:
        _stats["latencies"].append(val)
```

Separate helper functions prevent callers from having to manage the lock
directly — encapsulation prevents future bugs.

---

### Section 5: Flask Application & Middleware

```python
app = Flask(__name__)
```

`__name__` tells Flask the name of the module, used to locate templates and
static files relative to the module's location.

```python
@app.before_request
def before_request():
    g.start_time = time.time()
    g.correlation_id = (
        request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
    )
    _increment("requests_total")
    _increment("active_sessions")
    log.info(
        "request_started",
        method=request.method,
        path=request.path,
        remote_addr=request.remote_addr,
    )
```

`g.start_time = time.time()` — stores the request start time in Flask's
per-request context. `after_request` uses `time.time() - g.start_time`
to compute duration. Using `g` instead of a local variable ensures the
timing is consistent even if the route handler redirects or raises.

`request.headers.get("X-Correlation-ID") or str(uuid.uuid4())` —
the `or` means: use the header value if it's truthy (non-empty string),
otherwise generate a new UUID. This allows clients to pass their own
correlation IDs for end-to-end tracing.

```python
@app.after_request
def after_request(response):
    duration = time.time() - g.start_time
    _append_latency(duration)

    status_class = response.status_code // 100
    if status_class == 2:
        _increment("requests_success")
    elif status_class >= 4:
        _increment("requests_error")

    _increment("active_sessions", -1)

    log.info(
        "request_completed",
        method=request.method,
        path=request.path,
        status_code=response.status_code,
        duration_ms=round(duration * 1000, 2),
    )

    response.headers["X-Correlation-ID"] = g.correlation_id
    return response
```

`response.status_code // 100` — integer division. `201 // 100 = 2`,
`404 // 100 = 4`, `500 // 100 = 5`. This groups all 2xx as success,
all 4xx and 5xx as errors.

`_increment("active_sessions", -1)` — decrement. `active_sessions` is a
gauge showing how many requests are currently in-flight. It goes up in
`before_request` and down in `after_request`, so it's always accurate.

`response.headers["X-Correlation-ID"] = g.correlation_id` — return the ID
to the client. This is critical: if a user reports a bug, they can provide
the correlation ID from the response header, and you can find all related logs.

---

### Section 6: Route Handlers

#### POST /orders

```python
@app.route("/orders", methods=["POST"])
def create_order():
    body = request.get_json(silent=True)
    if not body:
        log.warning("create_order_bad_request", reason="missing_body")
        publish_metric("ValidationErrors", 1)
        return jsonify({"error": "Request body required",
                        "correlation_id": g.correlation_id}), 400
```

`request.get_json(silent=True)` — parses JSON body. `silent=True` returns
`None` instead of raising an exception if the body is not valid JSON or
Content-Type is wrong. We handle the None case explicitly.

**Always return the correlation_id in error responses.** This allows
clients to include it in bug reports, linking their complaint to your logs.

```python
    order_id = str(uuid.uuid4())
    total = sum(i["qty"] * i["price"] for i in items)
    order = {
        "order_id": order_id,
        "customer_id": customer_id,
        "items": items,
        "total": round(total, 2),
        "status": "confirmed",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "correlation_id": g.correlation_id,   # stored in order for traceability
    }
```

`str(uuid.uuid4())` — UUID4 is randomly generated (not time-based),
making it unpredictable and safe to expose to clients.

`round(total, 2)` — floating point arithmetic is imprecise.
`0.1 + 0.2 = 0.30000000000000004` in Python. `round(..., 2)` fixes this
for display. In real production, use `Decimal` for money calculations.

`datetime.now(timezone.utc).isoformat()` — always store timestamps in UTC.
Local time is ambiguous (DST, timezones). ISO 8601 format is universally parseable.

```python
    with _lock:
        orders[order_id] = order
        _stats["orders_created"] += 1
        _stats["total_order_value"] += total
```

Both mutations happen inside one lock acquisition — atomic. If we released
the lock between the two operations, another thread could read inconsistent state.

```python
    publish_metrics_batch([
        {"MetricName": "OrdersCreated", "Value": 1, "Unit": "Count", ...},
        {"MetricName": "OrderValue",    "Value": total, "Unit": "None", ...},
    ])

    return jsonify(order), 201
```

HTTP 201 Created (not 200 OK) for resource creation — correct REST semantics.
`201` tells the client a new resource was created and they can find it at the
`Location` header URL (we omit this for simplicity but would include it in production).

---

### Section 7: Chaos Endpoints

```python
@app.route("/chaos/latency", methods=["POST"])
def inject_latency():
    body = request.get_json(silent=True) or {}
    seconds = float(body.get("seconds", 2))
    log.warning("chaos_latency_injected", seconds=seconds)
    time.sleep(seconds)
    return jsonify({"message": f"Slept {seconds}s", ...}), 200
```

`log.warning(...)` — chaos injection is logged at WARNING level (not INFO)
because it's intentional but abnormal. This makes it easy to find in logs.

`time.sleep(seconds)` — blocks the entire thread for this many seconds.
With Gunicorn's 4-thread workers, injecting latency on multiple concurrent
requests will queue other requests, causing them to wait too.

```python
@app.route("/chaos/memory", methods=["POST"])
def inject_memory():
    mb = int(body.get("mb", 100))
    app._chaos_memory = bytearray(mb * 1024 * 1024)
    return jsonify({"message": f"Allocated {mb}MB", ...}), 200
```

`bytearray(mb * 1024 * 1024)` — allocates a mutable byte array.
`bytearray` is preferred over `bytes` because Python may optimize `bytes`
allocations and not actually use the memory until it's written.

`app._chaos_memory = ...` — stored on the `app` object. The Python garbage
collector won't reclaim it as long as `app` holds a reference. This simulates
a memory leak that persists until service restart.

---

### Section 8: Background Metrics Thread

```python
def _publish_aggregate_metrics():
    while True:
        time.sleep(60)
        with _lock:
            total = _stats["requests_total"]
            errors = _stats["requests_error"]
            orders_created = _stats["orders_created"]
            order_value = _stats["total_order_value"]
            active = _stats["active_sessions"]
            lats = list(_stats["latencies"])

            # Reset rolling counters
            _stats["requests_total"] = 0
            _stats["requests_success"] = 0
            _stats["requests_error"] = 0
            _stats["orders_created"] = 0
            _stats["total_order_value"] = 0.0
            _stats["latencies"] = []
```

**Why copy and reset inside one lock?**

If we released the lock between copying and resetting, request threads could
increment counters between our copy and our reset — losing those increments.
Doing both inside one lock acquisition ensures we capture everything up to
the moment we reset.

`lats = list(_stats["latencies"])` — creates a copy of the list. After we
reset `_stats["latencies"] = []`, the local `lats` still holds the original
data for P95 calculation outside the lock.

```python
        # After lock release, compute P95 (can be slow without blocking requests)
        if lats:
            sorted_lats = sorted(lats)
            n = len(sorted_lats)
            p95 = sorted_lats[int(n * 0.95)] * 1000
        else:
            p95 = 0

        error_rate = (errors / total * 100) if total > 0 else 0
        avg_order_value = (order_value / orders_created) if orders_created > 0 else 0
```

`if total > 0` and `if orders_created > 0` — guard against division by zero
for periods with no traffic. When the service starts up, `total=0` initially.

```python
        metrics = [
            {"MetricName": "RequestRate",     "Value": total,          "Unit": "Count/Minute", ...},
            {"MetricName": "ErrorRate",        "Value": error_rate,     "Unit": "Percent", ...},
            {"MetricName": "P95LatencyMs",     "Value": p95,            "Unit": "Milliseconds", ...},
            {"MetricName": "ActiveSessions",   "Value": active,         "Unit": "Count", ...},
            {"MetricName": "OrdersPerMinute",  "Value": orders_created, "Unit": "Count", ...},
            {"MetricName": "AvgOrderValue",    "Value": avg_order_value,"Unit": "None", ...},
        ]
        publish_metrics_batch(metrics)
```

All 6 metrics published in one `PutMetricData` API call — efficient and atomic.

```python
if __name__ == "__main__":
    t = threading.Thread(target=_publish_aggregate_metrics, daemon=True)
    t.start()

    app.run(host="0.0.0.0", port=Config.PORT, threaded=True)
```

`daemon=True` — the thread is a "daemon" thread, meaning Python will not
wait for it to finish when the main thread exits. Without this, `Ctrl+C`
wouldn't terminate the process (it would wait for the background thread's
60-second sleep to finish).

`host="0.0.0.0"` — listen on all network interfaces. Without this, Flask
only listens on `127.0.0.1` (loopback) and is unreachable from outside the machine.

---

## app/load_test.py

```python
def _do_request(base_url: str):
    action = random.choices(
        ["create", "get", "list", "health"],
        weights=[50, 30, 15, 5],
    )[0]
```

`random.choices` with weights — generates realistic traffic distribution.
50% creates, 30% gets, 15% lists, 5% health checks. Weights are proportional
(don't need to sum to 100).

```python
    conn = http.client.HTTPConnection(host, port, timeout=10)
```

Using Python's built-in `http.client` instead of `requests` — avoids an
extra dependency and is sufficient for load testing. `timeout=10` prevents
the test from hanging indefinitely on a slow server.

```python
def _worker(base_url, interval, stop_event):
    while not stop_event.is_set():
        _do_request(base_url)
        time.sleep(interval)
```

Each worker thread sleeps for `1/rps` seconds between requests.
With `rps=10` workers each sleeping 1 second, total throughput ≈ 10 RPS.
`stop_event.is_set()` is the clean shutdown signal — more reliable than
daemon threads for this pattern.

---

## config/dashboard.json — Key Concepts

### Widget structure

```json
{
  "type": "metric",           ← "metric", "text", or "alarm"
  "x": 0, "y": 2,            ← Grid position (24 columns wide)
  "width": 8, "height": 6,   ← Size in grid units
  "properties": {
    "title": "Request Rate",
    "region": "us-east-1",   ← REQUIRED: which AWS region to query
    "view": "timeSeries",    ← "timeSeries" or "singleValue"
    "metrics": [
      [
        "OrderAPI/Production",    ← Namespace
        "RequestRate",            ← Metric name
        "Service", "order-api",   ← Dimension key-value pairs (must be even)
        "Environment", "production",
        {                         ← Rendering options (last element)
          "stat": "Sum",
          "period": 60,
          "label": "Requests/min",
          "color": "#1f77b4"
        }
      ]
    ]
  }
}
```

### Dual-axis charts

```json
"metrics": [
  ["...", "RequestRate", ..., {"stat": "Sum", "yAxis": "left"}],
  ["...", "ErrorRate",   ..., {"stat": "Average", "yAxis": "right"}]
],
"yAxis": {
  "left":  {"min": 0},
  "right": {"min": 0, "max": 100}
}
```

The dual axis allows two metrics with very different scales to be
displayed on the same chart (requests: 0–1000/min vs error rate: 0–100%).
This is the "correlation" widget — it shows whether errors track with traffic.

---

## config/alarms.json — Key Concepts

### The "2 out of 3" Pattern

```json
{
  "EvaluationPeriods": 3,
  "DatapointsToAlarm": 2,
  "Period": 60
}
```

CloudWatch evaluates the last 3 one-minute periods.
Alarm fires if **at least 2** of them breach the threshold.

Timeline:
```
Period 1: ErrorRate = 0.3%   ← OK
Period 2: ErrorRate = 1.5%   ← BREACH (1/3)
Period 3: ErrorRate = 2.1%   ← BREACH (2/3) → ALARM fires
```

If period 2 had been a blip, period 3 would return to normal (1/3 → no alarm).

### TreatMissingData: breaching for OrderRate-Drop

```json
{
  "AlarmName": "OrderAPI-OrderRate-Drop",
  "TreatMissingData": "breaching"
}
```

If the service crashes and stops publishing `OrdersPerMinute`, CloudWatch
receives no data for that period. `breaching` means "treat silence as a
threshold breach" — the alarm fires even with no data points.

This is correct for the order rate alarm: no data = service is probably down.
It's wrong for the error rate alarms (we use `notBreaching` there): no data
could mean no traffic (quiet period), not an error condition.
