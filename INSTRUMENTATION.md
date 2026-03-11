# Instrumentation

## Logging Strategy

Every log line is a **JSON object** written to stdout and captured by the CloudWatch Agent.
No plain-text logs exist anywhere in the codebase.

### structlog Processor Pipeline

```
structlog.stdlib.add_log_level        → { "level": "info" }
structlog.processors.TimeStamper      → { "timestamp": "2024-01-15T10:23:45Z" }
add_service_info                      → { "service": "order-api", "environment": "production", "version": "1.0.0" }
add_correlation_id                    → { "correlation_id": "550e8400-e29b-41d4-a716-446655440000" }
structlog.processors.JSONRenderer     → single JSON line to stdout
```

### Correlation IDs

Every HTTP request gets a UUID correlation ID:
- If the client sends `X-Correlation-ID` header → use it (enables end-to-end tracing)
- Otherwise → generate a new UUID
- The ID is returned in the response `X-Correlation-ID` header
- The ID is injected into **every** log line for that request via Flask's `g` object

This lets you trace a single user request across all log lines:
```bash
# Find all logs for one request
aws logs filter-log-events \
  --log-group-name /aws/order-api \
  --filter-pattern '{ $.correlation_id = "550e8400-e29b-41d4-a716-446655440000" }'
```

### Log Levels

| Level | When used |
|-------|-----------|
| INFO | Normal events: request_started, request_completed, order_created, order_retrieved |
| WARNING | Expected errors: order not found, validation failures, chaos injection |
| ERROR | Unexpected failures: unhandled exceptions, downstream timeouts |

### Example Log Entries

**Successful order creation:**
```json
{
  "level": "info",
  "timestamp": "2024-01-15T10:23:45.123Z",
  "service": "order-api",
  "environment": "production",
  "version": "1.0.0",
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000",
  "event": "order_created",
  "order_id": "a1b2c3d4-...",
  "customer_id": "cust-42",
  "item_count": 3,
  "total": 89.97
}
```

**Request lifecycle:**
```json
{ "event": "request_started",   "method": "POST", "path": "/orders", "remote_addr": "10.0.0.5" }
{ "event": "order_created",     "order_id": "...", "total": 89.97 }
{ "event": "request_completed", "method": "POST", "path": "/orders", "status_code": 201, "duration_ms": 23.4 }
```

**Unhandled exception:**
```json
{
  "level": "error",
  "event": "unhandled_exception",
  "error": "Connection refused",
  "error_type": "ConnectionError",
  "exc_info": "Traceback (most recent call last):\n  ..."
}
```

**Aggregate metrics published:**
```json
{
  "level": "info",
  "event": "aggregate_metrics_published",
  "request_rate": 47,
  "error_rate_pct": 0.21,
  "p95_latency_ms": 43.2,
  "orders_per_minute": 12,
  "avg_order_value": 67.50
}
```

---

## Custom Metrics

All application metrics live under the namespace **`OrderAPI/Production`** with dimensions `Service=order-api` and `Environment=production`.

System metrics are published by the CloudWatch Agent under **`OrderAPI/System`**.

### Application Metrics (6 custom metrics)

| Metric | Unit | Type | Published | Why it matters |
|--------|------|------|-----------|----------------|
| `RequestRate` | Count/Minute | Technical | Every 60s | Golden Signal: Rate — baseline traffic level |
| `ErrorRate` | Percent | Technical | Every 60s | Golden Signal: Errors — service health KPI |
| `P95LatencyMs` | Milliseconds | Technical | Every 60s | Golden Signal: Latency — user experience |
| `ActiveSessions` | Count | Technical | Every 60s | Golden Signal: Saturation — concurrency level |
| `OrdersPerMinute` | Count | **Business** | Every 60s | Business health — silent failure detection |
| `AvgOrderValue` | None ($) | **Business** | Every 60s | Revenue indicator — anomaly detection |

### Per-Event Metrics (immediate publication)

| Metric | Unit | Trigger |
|--------|------|---------|
| `OrdersCreated` | Count | Every successful POST /orders |
| `OrderValue` | None ($) | Every successful POST /orders |
| `OrdersCancelled` | Count | Every DELETE /orders/:id |
| `OrderNotFound` | Count | GET/DELETE returns 404 |
| `ValidationErrors` | Count | 400 bad request |
| `UnhandledErrors` | Count | 500 unhandled exception |
| `ChaosErrors` | Count | POST /chaos/error called |

### System Metrics (CloudWatch Agent)

| Metric | Namespace | Description |
|--------|-----------|-------------|
| `cpu_usage_user` | OrderAPI/System | User-space CPU % |
| `cpu_usage_system` | OrderAPI/System | Kernel CPU % |
| `cpu_usage_iowait` | OrderAPI/System | I/O wait CPU % |
| `mem_used_percent` | OrderAPI/System | Memory utilization % |
| `used_percent` (disk) | OrderAPI/System | Disk utilization % |
| `bytes_sent/recv` | OrderAPI/System | Network throughput |
| `tcp_established` | OrderAPI/System | Active TCP connections |

### Why Business Metrics Matter

`OrdersPerMinute` is the most important alarm in the system. A bug in the `POST /orders` handler might cause 0% error rate (because the endpoint returns 200 but doesn't create an order) while the business metric `OrdersPerMinute` drops to zero. Technical metrics alone would miss this completely.

`AvgOrderValue` provides anomaly detection: if it suddenly drops from $67 to $2, either a pricing bug exists or customers are only buying the cheapest items (possible scraper activity).

---

## Implementation Details

### Latency Sampling

Latency samples are stored in a thread-safe list `_stats["latencies"]` and flushed every 60 seconds. The background thread computes the true P95 from all samples in that window (not a moving average), then resets the list.

### Thread Safety

All counter mutations use a single `threading.Lock()`. The background metrics thread holds the lock only long enough to copy and reset counters, minimizing contention with request handlers.

### Metric Batching

Per-event metrics (OrdersCreated, OrderValue) are published in a single `PutMetricData` call with two data points, avoiding two separate API calls per order.

Aggregate metrics are published in a single batch of 6 data points every 60 seconds.
