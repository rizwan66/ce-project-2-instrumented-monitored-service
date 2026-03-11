# Presentation Demo Script

**Total time: 20 minutes + 5 min Q&A**

---

## Opening (30 seconds)

> "Today I'm going to show you a production-ready Order API with comprehensive observability. The app itself is simple — what makes it interesting is that you can see exactly what it's doing at every moment, diagnose problems in under 60 seconds, and get automatically paged when something breaks."

---

## Section 1: Architecture & Instrumentation (5 min)

### Slide: Architecture diagram (ARCHITECTURE.md diagram)

> "Here's the system: a Flask API running on EC2 behind Gunicorn, with two observability pipelines — structured logs going to CloudWatch Logs, and custom metrics going to CloudWatch Metrics via boto3. The CloudWatch Agent handles system metrics like CPU and memory."

### Live: Show a log entry

```bash
sudo journalctl -u order-api -n 20
```

> "Every single log line is a JSON object — no plain text anywhere. Each line has a correlation ID that lets me trace a single user request across all logs. Notice the service name, environment, timestamp — all automatically added by the structlog processor pipeline."

### Live: Create an order and show the log

```bash
curl -X POST http://localhost:5000/orders \
  -H "Content-Type: application/json" \
  -H "X-Correlation-ID: demo-001" \
  -d '{"customer_id":"demo-customer","items":[{"sku":"SKU-001","qty":2,"price":19.99}]}'
```

> "See that correlation ID — demo-001? If anything goes wrong with this request, I can pull every log line associated with it in one query."

### Slide: Custom Metrics Table (from INSTRUMENTATION.md)

> "I instrumented 6 custom CloudWatch metrics. Four are the Golden Signals — Rate, Errors, Latency, Saturation. Two are business metrics — OrdersPerMinute and AvgOrderValue. The business metrics are critical because a technical metric alone can miss silent failures."

---

## Section 2: Live Demo — Dashboard & Monitoring (5 min)

### Open CloudWatch Dashboard: OrderAPI-Production

> "This is the dashboard. It follows the Golden Signals layout: Traffic at the top, then Errors, Latency, and Saturation at the bottom. The most critical metrics — Error Rate and P95 Latency — are in the most prominent positions."

### Start load test in background

```bash
python3 load_test.py --url http://localhost:5000 --rps 10 --duration 300 &
```

> "Let me fire up the load generator — 10 requests per second, mix of creates, gets, and list calls."

### Watch dashboard update

> "Within 60 seconds, you'll see Request Rate climbing, Orders per Minute appearing, P95 latency stabilizing around 40ms. The dashboard shows you the full picture in one view — I don't have to SSH to the server and run commands."

### Logs Insights demo

In CloudWatch Console → Logs Insights → /aws/order-api:

```
fields @timestamp, event, customer_id, total
| filter event = "order_created"
| stats count() as orders, sum(total) as revenue by bin(1m)
| sort @timestamp desc
```

> "This is a real-time revenue graph built entirely from structured logs. I didn't have to build any analytics infrastructure — CloudWatch Logs Insights is SQL-like querying over my structured JSON logs."

---

## Section 3: Incident Response Simulation (5 min)

> "Let me inject a failure and show you how observability lets me diagnose it without guessing."

### Inject high latency

```bash
curl -X POST http://localhost:5000/chaos/latency \
  -H "Content-Type: application/json" \
  -d '{"seconds": 3}'
```

> "I've just injected a 3-second sleep — simulating a slow database or downstream service."

### Watch dashboard

> "Watch the P95 Latency widget. It just jumped from 40ms to over 3,000ms. The Warning alarm fired — I got an email — and the Critical alarm fired 60 seconds later."

### Diagnose using RED method

> "Let me diagnose this using RED:
> - **Rate:** request rate is normal — this isn't a traffic spike
> - **Errors:** error rate is zero — requests are completing, just slowly
> - **Duration:** P95 went from 40ms to 3,100ms in one period — this is sudden, not gradual"

> "That pattern — normal rate, zero errors, sudden latency spike — points to a slow dependency or artificial delay. Let me check logs:"

```bash
aws logs filter-log-events \
  --log-group-name /aws/order-api \
  --filter-pattern '{ $.event = "chaos_latency_injected" }' \
  --start-time $(($(date +%s) - 300))000
```

> "Found it — `chaos_latency_injected` at exactly the time the spike started. Root cause confirmed in under 2 minutes, without SSHing to the server."

---

## Section 4: Alerting & Response (2 min)

### Show configured alarms

> "I have 7 alarms across 4 categories. The key design decision was tiering — Warning at 1% error rate gives me advance notice, Critical at 5% means users are definitely feeling it."

### Show alarm logic for OrderRate-Drop

> "This one is my favorite. It fires if no orders are created for 15 minutes. Error rate alone won't catch a silent bug where POST /orders returns 200 but doesn't create an order. This business metric alarm catches what technical metrics miss."

### Show email notification (screenshot if live demo not possible)

> "Both Warning and Critical alarms send to the same SNS topic. The OK action sends a recovery email when the alarm resolves — so I know when the incident is over without checking."

---

## Section 5: Learnings & Improvements (3 min)

### What I learned

> "The biggest thing I learned: **observability changes how you debug**. Before this project, when something broke, I'd SSH in and look around. Now I can diagnose most issues without touching the server — the evidence is already in CloudWatch."

> "The second thing: **business metrics catch what technical metrics miss**. The OrderRate-Drop alarm is the most important one I built. A bug that makes orders silently fail would take hours to discover from error rate alone — the business metric catches it in 15 minutes."

### What I'd do differently

> "I'd add distributed tracing with X-Ray. Correlation IDs let me trace within one service, but in a microservices environment I'd need trace IDs that span service boundaries."

> "I'd add anomaly detection alarms instead of static thresholds. Traffic patterns vary by time of day — a 1% error rate at 3am might be fine while the same at noon is a crisis."

### Production gaps

> "For real production: I'd add rate limiting (WAF), a load balancer with health checks, auto-scaling, and a real database instead of in-memory storage. The observability layer is production-ready; the application layer is a demo."

---

## Q&A Preparation

**"Why Flask over FastAPI?"**
> Flask is simpler for this demo. The observability patterns — structured logging, custom metrics, correlation IDs — are identical in both frameworks. In production, I'd evaluate async handling needs before choosing.

**"Why publish metrics directly via boto3 instead of an agent?"**
> Direct publication makes the code transparent — you can read exactly what metric is sent and when. An OpenTelemetry agent would be better for production (vendor-neutral, lower latency via batching) but adds infrastructure complexity that isn't the focus here.

**"Why CloudWatch and not Prometheus/Grafana?"**
> CloudWatch is native to AWS with no extra infrastructure — appropriate for a single-service deployment. Prometheus/Grafana makes sense when you have multiple services across multiple clouds, or need richer query capabilities (PromQL vs CloudWatch Math).

**"What would you do if the CloudWatch metrics are delayed?"**
> CloudWatch metrics have up to 5-minute delay at standard resolution. For real-time alerting, I'd use high-resolution custom metrics (1-second granularity) at higher cost, or add a local metrics endpoint (`/metrics`) as a live snapshot — which I did.

**"How would you handle this at 100x scale?"**
> Add an Application Load Balancer, Auto Scaling Group, and RDS. The observability layer scales naturally — CloudWatch handles millions of metrics/second. The correlation ID pattern extends to ECS/EKS containers without code changes.

---

## Backup Screenshots (if demo fails)

Location: `presentation/backup-screenshots/`

- `01-dashboard-healthy.png` — dashboard with normal metrics
- `02-error-rate-spike.png` — dashboard during INC-002 error injection
- `03-latency-spike.png` — dashboard during INC-001 latency injection
- `04-memory-pressure.png` — dashboard during INC-003 memory injection
- `05-alarm-email.png` — SNS email notification example
- `06-logs-insights-query.png` — Logs Insights revenue query result
- `07-structured-log-entry.png` — Example JSON log line in CloudWatch
- `08-all-alarms-ok.png` — All 7 alarms in OK state
