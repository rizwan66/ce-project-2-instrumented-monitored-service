# Deep Dive — Complete Project Understanding

This is the master reference document. It ties together every piece of the
project — the "why", the "what", and the "how" — in one place.

Start here if you're trying to understand the project end-to-end.
Go to specific docs for depth on each topic.

---

## Table of Contents

1. [The Problem This Project Solves](#1-the-problem-this-project-solves)
2. [What Was Built](#2-what-was-built)
3. [How Everything Connects — End to End](#3-how-everything-connects--end-to-end)
4. [Every File Explained](#4-every-file-explained)
5. [Every AWS Service Used](#5-every-aws-service-used)
6. [The Full Request Lifecycle](#6-the-full-request-lifecycle)
7. [The Full Metrics Lifecycle](#7-the-full-metrics-lifecycle)
8. [The Full Alerting Lifecycle](#8-the-full-alerting-lifecycle)
9. [Incident Investigation Playbook](#9-incident-investigation-playbook)
10. [Design Decisions and Trade-offs](#10-design-decisions-and-trade-offs)
11. [What Would Be Different in Real Production](#11-what-would-be-different-in-real-production)
12. [How to Answer Common Questions](#12-how-to-answer-common-questions)

---

## 1. The Problem This Project Solves

### The world without observability

Imagine you're on-call. At 2am, your phone buzzes: "Orders page broken".
You SSH to the server. You run `ps aux`. You grep some logs. You stare at
the screen. You don't know:

- When did it start breaking?
- Is it getting worse or better?
- Is it the database, the API, the frontend, or the network?
- How many users are affected?
- What changed?

You're **guessing**. Every minute of guessing costs money and user trust.

### The world with observability

Same 2am alert. You open your phone, pull up CloudWatch dashboard. In 30 seconds:

- Error rate spiked from 0% to 18% at exactly 1:47am
- P95 latency is normal (500ms errors, not slow errors — points to code bug)
- Request rate is normal (it's not a traffic attack)
- Memory and CPU are normal (not resource exhaustion)
- The spike correlates with a deploy at 1:46am (someone pushed broken code)

You rollback in 5 minutes. Incident resolved. Users barely noticed.

**That's why observability matters.**

---

## 2. What Was Built

A simple **Order Processing REST API** as the vehicle for demonstrating
production-grade observability. The application deliberately kept simple
so the observability layer is the focus.

### What the application does

```
POST /orders        Create a new order (customer_id + list of items)
GET  /orders/:id    Retrieve an order by ID
GET  /orders        List all orders
DELETE /orders/:id  Cancel an order
GET  /health        Health check (used by load balancers)
GET  /metrics       Internal metrics snapshot (for debugging)

POST /chaos/latency   Simulate slow dependency
POST /chaos/error     Force HTTP 500 errors
POST /chaos/memory    Allocate memory (simulate leak)
```

### What the observability layer does

```
Logging     → Every request produces structured JSON log lines
              with correlation IDs, event names, and relevant fields.
              Logs flow to CloudWatch Logs.

Metrics     → 6 custom application metrics published to CloudWatch
              every 60 seconds. Plus per-event metrics on each order action.
              Plus system metrics (CPU/memory/disk) from CloudWatch Agent.

Dashboard   → 10 widgets organized by Golden Signals (Rate/Errors/Latency/Saturation).
              One place to see the entire system state.

Alarms      → 7 alarms with Warning and Critical tiers.
              Email notifications via SNS.
              Business metric alarm to catch silent failures.
```

---

## 3. How Everything Connects — End to End

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  1. USER sends HTTP request                                                     │
│                                                                                 │
│  curl -X POST http://EC2-IP:5000/orders -d '{"customer_id":"c1","items":[...]}'│
└─────────────────────────────────────┬───────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  2. FLASK MIDDLEWARE (before_request)                                           │
│                                                                                 │
│  • Reads X-Correlation-ID header (or generates UUID)                           │
│  • Stores in Flask's g object                                                   │
│  • Increments requests_total and active_sessions counters                       │
│  • Logs: {"event":"request_started","method":"POST","path":"/orders",...}       │
└─────────────────────────────────────┬───────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  3. ROUTE HANDLER (create_order)                                                │
│                                                                                 │
│  • Validates request body                                                       │
│  • Generates order_id (UUID)                                                    │
│  • Stores order in dict                                                         │
│  • Logs: {"event":"order_created","order_id":"...","total":89.97,...}           │
│  • Publishes to CloudWatch:                                                     │
│      OrdersCreated=1 (Count)                                                    │
│      OrderValue=89.97 (None/$)                                                  │
│  • Returns {"order_id":"...","status":"confirmed",...} with HTTP 201            │
└─────────────────────────────────────┬───────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  4. FLASK MIDDLEWARE (after_request)                                            │
│                                                                                 │
│  • Calculates duration: time.time() - g.start_time                             │
│  • Appends duration to latencies list                                           │
│  • Increments requests_success (201 is 2xx)                                     │
│  • Decrements active_sessions                                                   │
│  • Logs: {"event":"request_completed","status_code":201,"duration_ms":23.4,...} │
│  • Sets X-Correlation-ID response header                                        │
└─────────────────────────────────────┬───────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  5. RESPONSE returns to user                                                    │
│     Headers include X-Correlation-ID: <uuid>                                   │
└─────────────────────────────────────────────────────────────────────────────────┘

══ Every 60 seconds (background thread) ══════════════════════════════════════════

┌─────────────────────────────────────────────────────────────────────────────────┐
│  6. BACKGROUND THREAD wakes up                                                  │
│                                                                                 │
│  • Acquires lock                                                                │
│  • Copies counters: total=47, errors=1, latencies=[23,45,12,...], etc.          │
│  • Resets all counters to 0                                                     │
│  • Releases lock                                                                │
│  • Computes: ErrorRate=2.1%, P95=340ms, AvgOrderValue=$67.50                   │
│  • Publishes 6 metrics to CloudWatch in one PutMetricData call:                │
│      RequestRate=47, ErrorRate=2.1, P95LatencyMs=340,                          │
│      ActiveSessions=3, OrdersPerMinute=12, AvgOrderValue=67.50                 │
│  • Logs: {"event":"aggregate_metrics_published","request_rate":47,...}          │
└─────────────────────────────────────┬───────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  7. CLOUDWATCH receives metrics                                                 │
│                                                                                 │
│  • Stores data points in namespace OrderAPI/Production                         │
│  • Evaluates alarm conditions every period                                     │
│  • If ErrorRate > 1% for 2/3 periods → transition to ALARM state              │
└─────────────────────────────────────┬───────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  8. ALARM fires → SNS → EMAIL                                                  │
│                                                                                 │
│  "ALARM: OrderAPI-ErrorRate-Warning is in ALARM state.                         │
│   Threshold: 1.0, Current value: 2.1 (Average over 60s)"                       │
└─────────────────────────────────────────────────────────────────────────────────┘

══ CloudWatch Logs flow ══════════════════════════════════════════════════════════

All log.info/warning/error calls → stdout
  → systemd journal captures stdout
  → CloudWatch Agent reads journal
  → Ships to CloudWatch Logs: /aws/order-api/<instance-id>/application
  → Available for Logs Insights queries
```

---

## 4. Every File Explained

### Application Files

| File | Purpose | Key concepts |
|------|---------|-------------|
| `app/server.py` | Main Flask application | Middleware, correlation IDs, metrics, chaos endpoints, background thread |
| `app/config.py` | Configuration | 12-Factor App, environment variables |
| `app/requirements.txt` | Python dependencies | Flask, boto3, structlog, gunicorn |
| `app/load_test.py` | Traffic generator | Threading, HTTP client, load distribution |
| `app/deploy.sh` | EC2 deployment script | systemd, CloudWatch Agent, health checks |

### Configuration Files

| File | Purpose | Key concepts |
|------|---------|-------------|
| `config/dashboard.json` | CloudWatch dashboard definition | Widget layout, Golden Signals, dual-axis charts |
| `config/alarms.json` | Alarm definitions with rationale | Tiered alerting, DatapointsToAlarm, TreatMissingData |
| `config/cloudwatch-agent-config.json` | CloudWatch Agent config | System metrics collection, log shipping |
| `config/deploy_alarms.sh` | Deploy SNS + alarms via CLI | AWS CLI, SNS, `put-metric-alarm` |

### Root Documentation

| File | Purpose |
|------|---------|
| `README.md` | Quick start, API reference, architecture overview |
| `ARCHITECTURE.md` | System diagram, component table, data flow, tech choices |
| `INSTRUMENTATION.md` | Logging strategy, all metrics documented, example log entries |
| `MONITORING.md` | Dashboard layout, widget guide, RED/USE method, Logs Insights queries |
| `ALERTING.md` | All 7 alarms with thresholds, rationale, and response procedures |
| `INCIDENTS.md` | 3 full incident reports with timelines and root cause analysis |
| `DEEP_DIVE.md` | This file — master understanding document |

### docs/ Files

| File | Purpose |
|------|---------|
| `docs/concepts.md` | Observability theory: Golden Signals, RED, USE, structured logging, P95 |
| `docs/code-walkthrough.md` | Line-by-line code explanation |
| `docs/runbook.md` | Troubleshooting procedures for each alarm |
| `docs/dashboard-guide.md` | How to read and use the dashboard |
| `docs/deployment.md` | Step-by-step deployment guide |

---

## 5. Every AWS Service Used

### EC2 (Elastic Compute Cloud)

**What it is:** A virtual machine running in AWS.

**How it's used:** Runs the Flask/Gunicorn application process.
The app process is managed by `systemd` which restarts it on crashes.

**Why not Lambda/ECS/Fargate?**
EC2 is the simplest to understand and debug — you can SSH in and run commands.
Lambda and ECS add complexity (cold starts, container builds) that isn't
relevant to the observability focus of this project.

### CloudWatch Logs

**What it is:** A managed log aggregation and querying service.

**How it's used:**
- Receives JSON log lines from the Flask application via the CloudWatch Agent
- Stores logs durably for 30 days (configured retention policy)
- Supports Logs Insights — SQL-like queries over structured JSON logs

**Key concept:** Because logs are structured JSON, every field is queryable.
`filter event = "order_created" | stats sum(total) by bin(1h)` gives you
hourly revenue directly from logs — no analytics database needed.

### CloudWatch Metrics

**What it is:** A time-series metrics storage and visualization service.

**How it's used:**
- Receives custom application metrics via `boto3.put_metric_data()`
- Receives system metrics from the CloudWatch Agent
- Stores metrics for 15 months at various resolutions
- Powers dashboards and alarms

**Retention policy:**
```
1-second resolution → kept for 3 hours
1-minute resolution → kept for 15 days
5-minute resolution → kept for 63 days
1-hour resolution   → kept for 15 months
```

### CloudWatch Dashboards

**What it is:** A customizable visualization layer over CloudWatch Metrics.

**How it's used:**
- Defined as JSON (`config/dashboard.json`)
- Deployed via `aws cloudwatch put-dashboard` CLI command
- 10 widgets organized by Golden Signals
- Shows real-time data, refreshes automatically

**Key benefit:** No additional infrastructure (no Grafana servers to manage).
Everything is managed by AWS.

### CloudWatch Alarms

**What it is:** Rules that watch a metric and trigger actions when thresholds are breached.

**How it's used:**
- 7 alarms configured (error rate warning/critical, latency warning/critical,
  CPU critical, memory critical, order rate drop)
- Each alarm sends to the SNS topic when it triggers
- Alarms also send "OK" notifications when they recover

**Alarm states:**
```
INSUFFICIENT_DATA  → Not enough data to evaluate (usually first few minutes)
OK                 → Metric is within threshold
ALARM              → Threshold breached for required number of periods
```

### SNS (Simple Notification Service)

**What it is:** A managed publish-subscribe messaging service.

**How it's used:**
- One topic: `OrderAPI-Alerts`
- One subscription: email to `rizwan.nasir@hotmail.com`
- All 7 alarms publish to this topic

**The publish-subscribe model:**
```
Publisher (CloudWatch Alarm)
         │
         ▼
    SNS Topic
    OrderAPI-Alerts
         │
    ┌────┼──────────────┐
    ▼    ▼              ▼
  Email Lambda     PagerDuty
               (auto-remediation)
```

Multiple subscribers can receive the same notification. Adding Slack
or PagerDuty integration would just be adding another subscription.

### CloudWatch Agent

**What it is:** A daemon process that collects system metrics and ships logs
from the EC2 instance to CloudWatch.

**How it's used:**
- Configured via `config/cloudwatch-agent-config.json`
- Collects CPU, memory, disk, network metrics every 60 seconds
- Ships logs from the application log file to CloudWatch Logs

**Why needed:** EC2's built-in metrics (available without the agent) only
include CPU credit usage, network I/O, and disk I/O at the instance level.
The agent adds memory utilization, per-CPU breakdown, and custom log shipping.

---

## 6. The Full Request Lifecycle

Every HTTP request to the Order API goes through these exact steps:

```
1.  TCP connection established to EC2:5000

2.  Gunicorn worker accepts the connection
    (Round-robin across 2 workers, each with 4 threads = 8 concurrent max)

3.  Flask parses HTTP request into the 'request' proxy object

4.  Flask runs before_request():
    a. Records start time in g.start_time
    b. Assigns/forwards correlation ID in g.correlation_id
    c. Increments requests_total and active_sessions (thread-safe)
    d. Logs request_started event

5.  Flask routes to the matching handler function (e.g., create_order)

6.  Handler runs:
    a. Validates input (400 if invalid, logs warning, publishes ValidationErrors)
    b. Executes business logic
    c. Publishes per-event metrics (OrdersCreated, OrderValue)
    d. Logs business event (order_created)
    e. Returns (response_dict, status_code)

7.  Flask calls jsonify on the return value → JSON response object

8.  Flask runs after_request(response):
    a. Calculates duration
    b. Appends to latencies list (for P95 calculation)
    c. Increments requests_success or requests_error
    d. Decrements active_sessions
    e. Logs request_completed with status_code and duration_ms
    f. Adds X-Correlation-ID to response headers

9.  Gunicorn sends HTTP response to client

10. Client receives response with X-Correlation-ID header
```

If an unhandled exception occurs at step 6, Flask's `errorhandler(Exception)`
fires instead of `after_request` — it logs the error with traceback and
publishes an `UnhandledErrors` metric.

---

## 7. The Full Metrics Lifecycle

### Immediate (per-event) metrics

```
Handler calls publish_metrics_batch([...])
  ↓
boto3.put_metric_data() makes HTTPS call to CloudWatch API
  ↓ (authenticated via EC2 IAM instance profile)
CloudWatch stores data point:
  Namespace: OrderAPI/Production
  MetricName: OrdersCreated
  Value: 1
  Timestamp: 2024-01-15T10:23:45Z
  Dimensions: Service=order-api, Environment=production
  ↓
Available for dashboards and alarm evaluation after ~1 minute
```

### Aggregate (rolling) metrics

```
Background thread wakes every 60 seconds
  ↓
Acquires lock → copies and resets counters → releases lock
  ↓
Computes ErrorRate, P95LatencyMs, AvgOrderValue
  ↓
publish_metrics_batch([6 metrics]) → CloudWatch
  ↓
Same path as immediate metrics above
```

### System metrics (CloudWatch Agent)

```
CloudWatch Agent process (separate from Flask)
  ↓
Every 60 seconds:
  Reads /proc/stat        → CPU metrics
  Reads /proc/meminfo     → Memory metrics
  Reads /proc/diskstats   → Disk metrics
  ↓
Sends to CloudWatch via HTTPS
  ↓
Namespace: OrderAPI/System
  Metrics: cpu_usage_user, mem_used_percent, used_percent, etc.
  Dimensions: InstanceId=i-xxx, InstanceType=t3.micro
```

---

## 8. The Full Alerting Lifecycle

### When a threshold is breached

```
CloudWatch evaluates MetricName every period (60 seconds)

  Period 1: ErrorRate = 0.3%  → below 1% → data point = OK
  Period 2: ErrorRate = 1.8%  → above 1% → data point = BREACH
  Period 3: ErrorRate = 2.1%  → above 1% → data point = BREACH
                                            2 out of 3 periods breached
                                            DatapointsToAlarm = 2 ✓
                                            → Alarm state transitions to ALARM

CloudWatch publishes to SNS topic OrderAPI-Alerts:
  {
    "AlarmName": "OrderAPI-ErrorRate-Warning",
    "NewStateValue": "ALARM",
    "NewStateReason": "Threshold Crossed: 2 out of the last 3 datapoints
                       were greater than the threshold (1.0)",
    "MetricName": "ErrorRate",
    "StateChangeTime": "2024-01-15T10:25:00Z"
  }

SNS delivers to all subscribers:
  → Email delivered to rizwan.nasir@hotmail.com within seconds
```

### When the metric recovers

```
  Period 4: ErrorRate = 0.2%  → data point = OK
  Period 5: ErrorRate = 0.1%  → data point = OK
  Period 6: ErrorRate = 0.0%  → data point = OK
                                All OK → alarm state transitions to OK

CloudWatch publishes to SNS:
  { "NewStateValue": "OK", "AlarmName": "OrderAPI-ErrorRate-Warning", ... }

OKActions → SNS → Email: "RESOLVED: OrderAPI-ErrorRate-Warning is back to normal"
```

---

## 9. Incident Investigation Playbook

When an alarm fires, follow this exact process:

### Step 1: Identify the signal (< 1 min)

Open CloudWatch Dashboard. Find the red/elevated widget.
Answer: Is this Rate, Errors, Latency, or Saturation?

### Step 2: Apply RED (2-3 min)

**R — Rate:** Open Request Rate widget.
- Normal traffic? → Not a load problem
- Sudden spike? → Possible attack or viral event
- Sudden drop? → Upstream problem or service crashed

**E — Errors:** Open Error Rate and Errors by Type widgets.
- What % are failing?
- What type? (400=bad input, 404=not found, 500=server bug)
- Errors proportional to traffic? → Load problem. Independent? → Code bug.

**D — Duration:** Open P95 Latency widget.
- How slow? Above warning/critical threshold?
- Did latency spike before errors? → Resource exhaustion causing timeouts

### Step 3: Apply USE for resource issues (2-3 min)

If Saturation section is elevated:
- CPU high + latency high? → CPU bottleneck, need to scale
- Memory high? → Memory leak or large allocation, restart service
- Disk high? → Check logs growing, rotation misconfigured
- iowait high? → Disk I/O bottleneck, may be swap activity (→ check memory)

### Step 4: Query logs for root cause (5 min)

Open CloudWatch Logs Insights:
```
# Find error patterns
fields @timestamp, level, event, error, correlation_id
| filter level = "error"
| stats count() by event
| sort count desc

# Find the exact error
fields @timestamp, event, error, error_type
| filter level = "error"
| sort @timestamp desc
| limit 20

# Trace a specific request
fields @timestamp, level, event
| filter correlation_id = "PASTE-ID-FROM-ALERT-OR-CLIENT"
| sort @timestamp asc
```

### Step 5: Identify root cause and fix

Based on evidence:

| Evidence | Root Cause | Fix |
|----------|-----------|-----|
| Errors started at deploy time | Code bug in new version | Rollback |
| Errors + high CPU | Service overloaded | Scale up/out |
| Errors + high memory | Memory leak | Restart, then find leak |
| Errors + slow latency | Dependency timeout | Add circuit breaker, check DB |
| 0 orders but 0 errors | Silent business failure | Check business logic |
| `chaos_*` in logs | Intentional injection | Stop chaos, verify recovery |

### Step 6: Document the incident

Write an entry in `INCIDENTS.md` using the template:
- Timeline with exact timestamps
- RED/USE analysis findings
- Root cause (specific, not vague)
- Fix applied
- Lessons learned and follow-up actions

---

## 10. Design Decisions and Trade-offs

### Why not OpenTelemetry?

OpenTelemetry (OTel) is the industry-standard, vendor-neutral approach to
instrumentation. It provides unified APIs for logs, metrics, and traces.

**We chose direct boto3 + structlog because:**
- Zero external infrastructure (no OTel collector sidecar needed)
- Code is explicit — you can read exactly what metric is published and when
- Lower learning curve for demonstrating concepts
- CloudWatch-native fits the all-AWS architecture

**In production at scale:** Use OpenTelemetry. It lets you switch from
CloudWatch to Datadog/New Relic without changing application code.

### Why one CloudWatch namespace per environment vs per service?

This project uses `OrderAPI/Production` for all application metrics.

**Alternative:** One namespace per service + environment:
`Services/order-api/production`, `Services/payment-api/production`

**Trade-off:** With one namespace you must use dimensions to filter by service.
With multiple namespaces, filtering by service is implicit but cross-service
dashboard widgets become verbose. One namespace per service is better at scale.

### Why in-memory storage instead of a database?

**Because the focus is observability, not application complexity.**

In-memory storage means zero database setup, zero connection pooling,
zero DB monitoring. The observability patterns are identical whether you
use memory or RDS — but DB setup would take 2 hours and teach nothing
about observability.

**In production:** Use RDS (relational) or DynamoDB (key-value).
Add DB connection pool metrics, query latency metrics, and connection
count alarms as additional observability layers.

### Why 60-second metric publication interval?

CloudWatch standard-resolution metrics have 1-minute granularity.
Publishing more frequently than every 60 seconds at standard resolution
wastes API calls — the data gets aggregated to 1-minute buckets anyway.

**For sub-minute alerting:** Use high-resolution metrics (1-second granularity).
They cost 3x more and are only retained at high resolution for 3 hours.
Appropriate for extremely latency-sensitive services, not this demo.

### Why P95 and not P99 or P50?

| Percentile | Meaning | When to use |
|-----------|---------|-------------|
| P50 (median) | Typical user experience | Understanding the "normal" user |
| P95 | 1 in 20 users is slower than this | **Standard SLO metric** |
| P99 | 1 in 100 users is slower than this | Tail-sensitive services (trading, gaming) |
| P999 | 1 in 1000 is slower | Ultra-high-reliability services |

P95 is the industry standard because it captures the "long tail" without being
overly sensitive to rare outliers. P99 can cause false alarms on low-traffic
services (10 requests/min → P99 is just 1 request).

---

## 11. What Would Be Different in Real Production

### Application Layer

| This project | Production |
|-------------|-----------|
| In-memory dict storage | RDS PostgreSQL or DynamoDB |
| Single EC2 instance | Auto Scaling Group behind ALB |
| Manual deployment (deploy.sh) | CI/CD pipeline (CodeDeploy, GitHub Actions) |
| No authentication | JWT/OAuth2 with API Gateway |
| No rate limiting | WAF + throttling rules |
| Flask dev middleware | No debug mode, proper WSGI config |

### Observability Layer

| This project | Production |
|-------------|-----------|
| Direct boto3 metric publication | OpenTelemetry SDK + Collector sidecar |
| stdout → CloudWatch Agent | Fluentd/Fluent Bit log aggregation |
| Correlation IDs (single service) | Distributed tracing (X-Ray, Jaeger) |
| Static alarm thresholds | Anomaly detection alarms |
| Email notifications only | PagerDuty + Slack + email |
| Manual incident docs | PagerDuty incident management |
| No SLO tracking | SLO/SLA tracking with error budgets |
| Background thread for metrics | Dedicated metrics sidecar (StatsD) |

### Infrastructure

| This project | Production |
|-------------|-----------|
| Single region | Multi-region with Route 53 failover |
| No CDN | CloudFront for static assets |
| No VPC configuration | Private subnets, NAT gateway, security groups |
| Root IAM permissions | Least-privilege IAM roles |
| No backup/disaster recovery | RDS snapshots, cross-region replication |

---

## 12. How to Answer Common Questions

**"Why not just use print statements for debugging?"**

Print statements are synchronous, unstructured, and disappear when the
process restarts. Structured JSON logs are queryable across time, filterable
by any field, and shipped to durable storage automatically. One Logs Insights
query can answer "show me all 500 errors in the last hour with their stack traces"
— impossible with print statements.

**"What's the difference between a metric and a log?"**

A metric is a number that changes over time (error rate = 2.3%).
A log is a discrete event with context (this specific request failed with this error).
Metrics tell you *how much* and *how often*. Logs tell you *what specifically happened*.
You need both — metrics to know something is wrong, logs to know why.

**"Why send metrics to CloudWatch if the logs already have the data?"**

Logs Insights queries run in seconds to minutes. CloudWatch metrics evaluate
in real-time. You can't trigger an alarm that fires within 1 minute based on
log queries — the latency is too high. Metrics are pre-aggregated for fast
threshold evaluation. Logs are for drill-down investigation after the alert fires.

**"Why 7 alarms? Isn't that too many?"**

7 alarms cover distinct failure modes:
- 2 for error rate (Warning, Critical) — same symptom, different severity
- 2 for latency (Warning, Critical) — same symptom, different severity
- 1 for CPU — resource exhaustion
- 1 for memory — resource exhaustion
- 1 for order rate drop — **business** failure

Each alarm answers a different question. You need both tiers because warning
fires early (time to investigate) while critical fires when users are impacted.
The business metric alarm catches failures all technical alarms miss.

**"What's the cost of this setup?"**

Approximate AWS costs (us-east-1, as of 2024):
```
EC2 t3.micro:           ~$8.50/month
CloudWatch custom metrics:  $0.30 per metric/month × 13 metrics = ~$4/month
CloudWatch Logs:         $0.50/GB ingested (logs are tiny)
CloudWatch Dashboards:   $3/dashboard/month
CloudWatch Alarms:       $0.10/alarm/month × 7 = $0.70/month
SNS:                     Free tier covers this workload
Total:                   ~$17/month
```

For a production service, this is essentially free. The cost scales with
the number of metrics, not with traffic volume.

**"What would you add next?"**

Priority order:
1. **Distributed tracing (X-Ray)** — to trace requests across future microservices
2. **Anomaly detection alarms** — CloudWatch ML-based thresholds that adapt to traffic patterns
3. **SLO tracking dashboard** — 30-day error budget view
4. **Auto-remediation Lambda** — triggered by Memory alarm to restart service
5. **Synthetic monitoring** — scheduled Lambda that hits `/health` every minute from outside the VPC

---

## Reference: Quick Links

| What you need | Where to find it |
|--------------|-----------------|
| Start from scratch | `docs/deployment.md` |
| Understand a concept | `docs/concepts.md` |
| Read the code | `docs/code-walkthrough.md` |
| Troubleshoot an alert | `docs/runbook.md` |
| Read the dashboard | `docs/dashboard-guide.md` |
| Understand an incident | `INCIDENTS.md` |
| Understand the alarms | `ALERTING.md` |
| Understand the metrics | `INSTRUMENTATION.md` |
| Understand the dashboard design | `MONITORING.md` |
| System architecture | `ARCHITECTURE.md` |
