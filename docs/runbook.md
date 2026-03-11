# Runbook — Order API

## Service Overview

| Property | Value |
|----------|-------|
| Service | order-api |
| Port | 5000 |
| Systemd unit | order-api.service |
| App directory | /opt/order-api |
| Log group | /aws/order-api |
| Metrics namespace | OrderAPI/Production |
| Dashboard | OrderAPI-Production (CloudWatch) |

---

## Quick Commands

```bash
# Service status
sudo systemctl status order-api

# Restart service
sudo systemctl restart order-api

# View live logs
sudo journalctl -u order-api -f

# View last 100 lines
sudo journalctl -u order-api -n 100

# Health check
curl http://localhost:5000/health

# Internal metrics snapshot
curl http://localhost:5000/metrics

# Memory/CPU usage
free -m
top -bn1 | head -20
```

---

## Common Issues & Fixes

### Service won't start

**Symptoms:** `sudo systemctl status order-api` shows `failed` or `activating`.

**Steps:**
```bash
# Check for port conflict
sudo lsof -i :5000

# Check Python dependencies
cd /opt/order-api && pip3 check

# Check for syntax errors
cd /opt/order-api && python3 -c "import server"

# Check logs
sudo journalctl -u order-api --since "5 min ago"
```

**Fix:** If port conflict → kill the conflicting process. If dependency error → `pip3 install -r requirements.txt`.

---

### High error rate (> 1%)

**Symptoms:** `OrderAPI-ErrorRate-Warning` or `-Critical` alarm fires.

**Steps:**
1. Open CloudWatch Dashboard → Error Rate widget
2. Check "Errors by Type" widget:
   - High `ValidationErrors` (400): Client is sending malformed requests → check recent API contract changes
   - High `OrderNotFound` (404): Client using stale order IDs → normal or client bug
   - High `UnhandledErrors` (500): Server bug → check logs immediately

```bash
# Find 500 errors in last 30 minutes
aws logs filter-log-events \
  --log-group-name /aws/order-api \
  --filter-pattern '{ $.level = "error" }' \
  --start-time $(($(date +%s) - 1800))000

# Get error details with correlation IDs
aws logs start-query \
  --log-group-name /aws/order-api \
  --start-time $(($(date +%s) - 1800)) \
  --end-time $(date +%s) \
  --query-string 'fields @timestamp, event, error, correlation_id | filter level = "error" | sort @timestamp desc | limit 20'
```

3. For 500 errors: check traceback in logs, identify the code path
4. Consider rollback if correlated with recent deployment

---

### High P95 latency (> 500ms)

**Symptoms:** `OrderAPI-Latency-P95-Warning` or `-Critical` alarm fires.

**Steps:**
1. Check Active Sessions widget — is there a queue buildup?
2. Check CPU — is the instance saturated?
3. Check Memory — is GC pressure causing pauses?

```bash
# Live latency check
curl http://localhost:5000/metrics | python3 -m json.tool | grep latency

# CPU/Memory snapshot
vmstat 1 5

# Check for chaos injection
aws logs filter-log-events \
  --log-group-name /aws/order-api \
  --filter-pattern '{ $.event = "chaos_latency_injected" }' \
  --start-time $(($(date +%s) - 600))000
```

4. If CPU > 80%: scale up instance or reduce load
5. If memory > 85%: restart service to reclaim memory
6. If neither: check for downstream dependency (database) timeouts

---

### High CPU (> 80%)

**Symptoms:** `OrderAPI-CPU-Critical` alarm fires.

**Steps:**
```bash
# Find CPU-intensive processes
top -bn1 | head -20
ps aux --sort=-%cpu | head -10

# Check for stress test
ps aux | grep -E "stress|load_test"

# Check request rate (load attack?)
curl http://localhost:5000/metrics | python3 -m json.tool | grep request_rate
```

**Fixes:**
- Runaway process: `kill -9 <pid>`
- Load attack: block source IPs via security group in AWS Console
- Legitimate load: upgrade instance type (t3.micro → t3.small → t3.medium)

---

### High memory (> 90%)

**Symptoms:** `OrderAPI-Memory-Critical` alarm fires. OOM kill risk.

**Steps:**
```bash
# Check actual memory
free -m

# Find memory-heavy processes
ps aux --sort=-%mem | head -10

# Check for chaos injection
aws logs filter-log-events \
  --log-group-name /aws/order-api \
  --filter-pattern '{ $.event = "chaos_memory_injected" }' \
  --start-time $(($(date +%s) - 600))000
```

**Immediate fix:** Restart the service
```bash
sudo systemctl restart order-api
```

**Verify:**
```bash
free -m
curl http://localhost:5000/health
```

---

### Zero orders being created (OrderRate-Drop alarm)

**Symptoms:** `OrderAPI-OrderRate-Drop` fires. Business metric shows 0 orders for 15+ min.

**Steps:**
1. Test the endpoint manually:
```bash
curl -X POST http://localhost:5000/orders \
  -H "Content-Type: application/json" \
  -d '{"customer_id":"test","items":[{"sku":"SKU-001","qty":1,"price":9.99}]}'
```

2. If 200 but no orders in logs:
```bash
aws logs filter-log-events \
  --log-group-name /aws/order-api \
  --filter-pattern '{ $.event = "order_created" }' \
  --start-time $(($(date +%s) - 900))000
```

3. If 500: check error rate runbook
4. If service is down: `sudo systemctl restart order-api`
5. If missing data (service crashed): check `sudo systemctl status order-api`

---

### CloudWatch metrics not appearing

**Symptoms:** Dashboard shows "No data" or metrics are missing.

**Steps:**
```bash
# Check CloudWatch Agent status
sudo amazon-cloudwatch-agent-ctl -m ec2 -a status

# Restart CloudWatch Agent
sudo amazon-cloudwatch-agent-ctl -m ec2 -a start

# Check app can reach CloudWatch
aws cloudwatch list-metrics --namespace OrderAPI/Production

# Verify IAM permissions (must have cloudwatch:PutMetricData)
aws sts get-caller-identity
```

Check EC2 IAM role has `CloudWatchAgentServerPolicy` and `CloudWatchFullAccess` (or equivalent).

---

## Log Queries Reference

```bash
# Start a Logs Insights query
aws logs start-query \
  --log-group-name /aws/order-api \
  --start-time $(($(date +%s) - 3600)) \
  --end-time $(date +%s) \
  --query-string 'QUERY_HERE'

# Get query results
aws logs get-query-results --query-id <id>
```

### Useful queries:

**Error summary:**
```
fields @timestamp, event, error_type
| filter level = "error"
| stats count() as count by event
| sort count desc
```

**Slowest requests:**
```
fields @timestamp, method, path, duration_ms
| filter ispresent(duration_ms)
| sort duration_ms desc
| limit 20
```

**Orders per customer:**
```
fields customer_id, total
| filter event = "order_created"
| stats count() as orders, sum(total) as revenue by customer_id
| sort revenue desc
```

**Trace a correlation ID:**
```
fields @timestamp, level, event
| filter correlation_id = "PASTE-ID-HERE"
| sort @timestamp asc
```
