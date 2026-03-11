# Monitoring

## Dashboard: OrderAPI-Production

The dashboard is organized top-to-bottom by the **four Golden Signals** (Rate → Errors → Latency → Saturation), with an alarm status panel at the bottom.

### Visual Hierarchy

```
Row 0: Dashboard title banner
Row 1: ── Traffic (Rate) ──────────────────────────────────
Row 2: [Request Rate]  [Orders/min + Cancellations]  [Order Value $]
Row 8: ── Errors ──────────────────────────────────────────
Row 9: [Error Rate %]  [Errors by type]  [Rate vs Error correlation]
Row 15: ── Latency ──────────────────────────────────────────
Row 16: [P95 Latency ms]  [Active Sessions]
Row 22: ── Saturation ───────────────────────────────────────
Row 23: [CPU %]  [Memory %]  [Disk %]
Row 29: ── Alarm Status ─────────────────────────────────────
Row 30: [Alarm status panel — all 7 alarms at a glance]
```

---

## Widget-by-Widget Guide

### Traffic

**Request Rate (req/min)**
- Metric: `RequestRate`, Sum, 60s period
- Annotations: Warning@500, Critical@1000 rps
- Use: Baseline traffic; sudden spike → possible load test or attack; sudden drop → upstream issue

**Orders Created per Minute**
- Metrics: `OrdersPerMinute` (green) + `OrdersCancelled` (orange)
- Use: Business health at a glance; cancellations rising relative to orders → UX or pricing issue

**Average & Total Order Value ($)**
- Metrics: `AvgOrderValue` (left axis) + `OrderValue` Sum (right axis)
- Use: Revenue trend; avg value dropping → pricing bug or coupon abuse

### Errors

**Error Rate (%)**
- Metric: `ErrorRate`, Average, 60s period
- Annotations: Warning@1%, Critical@5%
- **Most important single widget** — first place to look during an incident

**HTTP Errors by Type**
- Metrics: 400 ValidationErrors, 404 OrderNotFound, 500 UnhandledErrors, ChaosErrors
- Use: Drill into *what kind* of errors are occurring; 404s rising → client bug; 500s rising → server bug

**Rate vs Error Correlation**
- Metrics: RequestRate (left) + ErrorRate (right, dual axis)
- Use: Determine if errors are proportional to traffic (load problem) or independent (code bug)

### Latency

**API Latency P95 (ms)**
- Metric: `P95LatencyMs`, Average, 60s period
- Annotations: Warning@500ms, Critical@1000ms
- Use: P95 catches tail latency that affects 1 in 20 users; average hides this

**Active Sessions**
- Metric: `ActiveSessions`, Average, 60s period
- Use: Confirms saturation hypothesis; rising sessions + rising latency = queue buildup

### Saturation

**CPU Utilization (%)**
- Metrics: cpu_usage_user + cpu_usage_system + cpu_usage_iowait stacked
- Annotation: Critical@80%
- Use: iowait spike → disk I/O bottleneck; user spike → CPU-bound processing

**Memory Used (%)**
- Metric: `mem_used_percent`, Average
- Annotations: Warning@75%, Critical@90%
- Use: Monotonically increasing memory → memory leak; sudden spike → /chaos/memory was called

**Disk Used (%)**
- Metric: `used_percent`, Average, 300s period
- Annotations: Warning@70%, Critical@85%
- Use: Log rotation issues, large files accumulating

---

## Using the Dashboard for Troubleshooting

### Alert fires → How to triage in 60 seconds

1. **Open dashboard** — which section is red/elevated?
2. **Rate section**: Is traffic normal, spike, or drop?
3. **Error section**: What % and what type?
4. **Correlation widget**: Are errors proportional to load?
5. **Latency**: Slow or fast errors?
6. **Saturation**: Is the instance resource-constrained?

### RED Method (Requests, Errors, Duration)
- Requests → Request Rate widget
- Errors → Error Rate + Error by Type widgets
- Duration → P95 Latency widget

### USE Method (Utilization, Saturation, Errors)
- Utilization → CPU + Memory + Disk widgets
- Saturation → Active Sessions widget
- Errors → CloudWatch agent logs + Unhandled Errors metric

---

## Dashboard Deployment

```bash
# Deploy dashboard (replaces ACCOUNT_ID placeholder)
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BODY=$(sed "s/ACCOUNT_ID/${ACCOUNT_ID}/g" config/dashboard.json)
aws cloudwatch put-dashboard \
  --dashboard-name "OrderAPI-Production" \
  --dashboard-body "${BODY}" \
  --region us-east-1
```

View at:
```
https://us-east-1.console.aws.amazon.com/cloudwatch/home#dashboards:name=OrderAPI-Production
```

---

## Logs Insights Saved Queries

### Error rate in last hour
```
fields @timestamp, level, event, correlation_id, status_code
| filter level = "error"
| stats count() as error_count by bin(5m)
| sort @timestamp desc
```

### Slowest requests
```
fields @timestamp, path, duration_ms, correlation_id
| filter ispresent(duration_ms)
| sort duration_ms desc
| limit 20
```

### Orders by customer
```
fields @timestamp, customer_id, order_id, total
| filter event = "order_created"
| stats count() as order_count, sum(total) as revenue by customer_id
| sort revenue desc
```

### Find all logs for one request
```
fields @timestamp, level, event
| filter correlation_id = "PASTE-CORRELATION-ID-HERE"
| sort @timestamp asc
```
