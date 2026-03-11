# Dashboard Guide — OrderAPI-Production

## Opening the Dashboard

```
AWS Console → CloudWatch → Dashboards → OrderAPI-Production
```

Or directly:
```
https://us-east-1.console.aws.amazon.com/cloudwatch/home#dashboards:name=OrderAPI-Production
```

Set time range to **Last 1 hour** for real-time monitoring. Use **Last 3 hours** when investigating an incident.

---

## Dashboard Layout

```
┌────────────────────────────────────────────────────────────────────┐
│  Header: "Order API — Production Dashboard"                        │
├────────────────────────────────────────────────────────────────────┤
│  Row: Traffic (Rate)                                               │
│  [Request Rate]    [Orders/min + Cancellations]  [Order Value $]  │
├────────────────────────────────────────────────────────────────────┤
│  Row: Errors                                                       │
│  [Error Rate %]    [Errors by Type]   [Rate vs Error correlation]  │
├────────────────────────────────────────────────────────────────────┤
│  Row: Latency                                                      │
│  [P95 Latency ms]              [Active Sessions]                   │
├────────────────────────────────────────────────────────────────────┤
│  Row: Saturation                                                   │
│  [CPU %]           [Memory %]         [Disk %]                     │
├────────────────────────────────────────────────────────────────────┤
│  Row: Alarm Status                                                 │
│  [All 7 alarms — green/red at a glance]                            │
└────────────────────────────────────────────────────────────────────┘
```

---

## Reading Each Widget

### Request Rate
- **Normal:** 5–50 req/min (development/demo)
- **Orange line:** 500 req/min warning
- **Red line:** 1000 req/min critical
- **Sudden spike:** possible load test or attack
- **Sudden drop to 0:** service may be down

### Orders Created per Minute
- **Green line:** orders being created (healthy business)
- **Orange line:** cancellations (small number is normal)
- **If green drops to 0 but requests are normal:** the POST /orders endpoint is broken silently

### Average & Total Order Value
- **Left axis (purple):** average value of each order
- **Right axis (cyan):** total revenue for the period
- **Avg value drops significantly:** pricing bug or coupon abuse
- **Total drops but avg stays same:** fewer orders (check OrdersPerMinute)

### Error Rate %
- **Green zone (0–1%):** healthy
- **Orange zone (1–5%):** warning, investigate
- **Red zone (>5%):** critical, immediate action
- **Horizontal annotations** show threshold lines for reference

### HTTP Errors by Type
| Color | Metric | Meaning |
|-------|--------|---------|
| Orange | ValidationErrors (400) | Bad client input |
| Yellow | OrderNotFound (404) | Stale/wrong IDs (sometimes normal) |
| Red | UnhandledErrors (500) | Server bugs |
| Brown | ChaosErrors | Intentional injection |

### Rate vs Error Correlation
- If errors rise **with** request rate → load problem (scale up)
- If errors rise **independently** → code bug or dependency failure

### P95 Latency (ms)
- **Healthy:** < 100ms
- **Warning annotation:** 500ms
- **Critical annotation:** 1000ms
- P95 means 95% of requests are faster than this value
- Sudden spike: check Active Sessions and CPU

### Active Sessions
- Shows concurrent in-flight requests
- Climbing sessions + climbing latency = requests queuing (saturation)
- Dropping to 0 unexpectedly = service down or no traffic

### CPU %
| Line | Meaning |
|------|---------|
| Blue (user) | Application code |
| Orange (system) | Kernel/OS |
| Red (iowait) | Waiting for disk (swap!) |

- iowait spike → check memory (may be swapping)
- Red annotation at 80% = critical threshold

### Memory %
- **Healthy:** < 60%
- **Warning:** 75%
- **Critical:** 90% — restart immediately
- **Gradual climb over hours:** memory leak

### Disk %
- **5-minute granularity** (less urgent)
- **Warning:** 70%, **Critical:** 85%
- Usually stable unless logs are accumulating without rotation

---

## Incident Investigation Workflow

**Step 1:** Is the Alarm Status row showing red alarms?

**Step 2:** Which Golden Signal section is elevated?
- Rate anomaly → traffic problem
- Error anomaly → code or dependency problem
- Latency anomaly → performance or resource problem
- Saturation anomaly → capacity problem

**Step 3:** Use the Correlation widget (Rate vs Error) to determine if errors track traffic.

**Step 4:** Check timestamp — when did the anomaly start? Did anything change at that time?

**Step 5:** Open CloudWatch Logs Insights and correlate log events with the metric spike time.

**Step 6:** Document findings in INCIDENTS.md.

---

## Adding the Dashboard via CLI

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BODY=$(sed "s/ACCOUNT_ID/${ACCOUNT_ID}/g" config/dashboard.json)
aws cloudwatch put-dashboard \
  --dashboard-name "OrderAPI-Production" \
  --dashboard-body "${BODY}"
```
