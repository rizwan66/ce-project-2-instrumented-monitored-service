# Alerting

## Alert Strategy

**Tiered alerting** ensures the right person gets paged at the right severity.

| Tier | Threshold | Response Time | Action |
|------|-----------|---------------|--------|
| Warning | Degraded but tolerable | Within 30 min | Investigate proactively |
| Critical | User-visible impact | Immediately | Wake someone up |

**All alarms use `TreatMissingData: notBreaching`** (except OrderRate Drop which uses `breaching`) so a brief CloudWatch outage doesn't generate false alerts.

---

## Alarm Definitions

### 1. ErrorRate-Warning
| Parameter | Value |
|-----------|-------|
| Metric | `ErrorRate` (OrderAPI/Production) |
| Threshold | > 1% |
| Evaluation | 3 periods of 60s, alarm if 2/3 breach |
| Rationale | 1% is the leading indicator of degradation. 2/3 datapoints avoids alerting on a single bad request. |

### 2. ErrorRate-Critical
| Parameter | Value |
|-----------|-------|
| Metric | `ErrorRate` (OrderAPI/Production) |
| Threshold | > 5% |
| Evaluation | 2 periods of 60s, alarm if 2/2 breach |
| Rationale | 5% = 1 in 20 orders failing — unacceptable. 2/2 means confirmed, not a blip. |

### 3. Latency-P95-Warning
| Parameter | Value |
|-----------|-------|
| Metric | `P95LatencyMs` (OrderAPI/Production) |
| Threshold | > 500ms |
| Evaluation | 5 periods of 60s, alarm if 3/5 breach |
| Rationale | 500ms is half the SLO; gives time to investigate before critical. More relaxed datapoints-to-alarm because latency is noisier than error rate. |

### 4. Latency-P95-Critical
| Parameter | Value |
|-----------|-------|
| Metric | `P95LatencyMs` (OrderAPI/Production) |
| Threshold | > 1000ms |
| Evaluation | 3 periods of 60s, alarm if 2/3 breach |
| Rationale | 1s P95 is the agreed SLO. Using P95 (not average) ensures we catch tail latency that affects 1 in 20 users. |

### 5. CPU-Critical
| Parameter | Value |
|-----------|-------|
| Metric | `cpu_usage_user` (OrderAPI/System) |
| Threshold | > 80% |
| Evaluation | 5 periods of 60s, alarm if 4/5 breach |
| Rationale | Sustained 80% CPU causes queueing and latency spikes. 4/5 datapoints avoids false alarms during legitimate traffic bursts. |

### 6. Memory-Critical
| Parameter | Value |
|-----------|-------|
| Metric | `mem_used_percent` (OrderAPI/System) |
| Threshold | > 90% |
| Evaluation | 3 periods of 60s, alarm if 3/3 breach |
| Rationale | 90% is the point where Linux OOM killer may terminate the process. 3/3 because memory doesn't drop on its own — sustained = real problem. |

### 7. OrderRate-Drop *(Business Metric Alarm)*
| Parameter | Value |
|-----------|-------|
| Metric | `OrdersPerMinute` (OrderAPI/Production) |
| Threshold | < 1 order per 5-min window |
| Evaluation | 3 periods of 300s, alarm if 3/3 breach |
| TreatMissingData | **breaching** |
| Rationale | Zero orders for 15 minutes during business hours indicates the create-order flow is broken — a silent failure that error rate alone cannot detect. Missing data is treated as breaching because no data = service is down. |

---

## SNS Configuration

```bash
# Create topic
TOPIC_ARN=$(aws sns create-topic --name OrderAPI-Alerts --query TopicArn --output text)

# Subscribe email
aws sns subscribe \
  --topic-arn $TOPIC_ARN \
  --protocol email \
  --notification-endpoint ops@example.com

# Confirm the subscription email before alarms can deliver
```

---

## Alert Response Procedures

### On receiving ErrorRate-Warning
1. Open CloudWatch Dashboard → Error section
2. Check "Errors by Type" widget — is it 400s, 404s, or 500s?
3. Run Logs Insights: `filter level = "error" | stats count() by event`
4. Check recent deployments (git log, CodeDeploy history)
5. If 500s: check for dependency failures (DB, downstream services)
6. Document findings in incident log

### On receiving ErrorRate-Critical
1. **Immediately open dashboard** — is this a blast-radius or targeted issue?
2. Check correlation: errors proportional to traffic? → load problem; independent → code bug
3. Consider rollback if recent deployment correlates
4. If error rate > 20%: consider taking service out of rotation
5. Open INCIDENTS.md, create new incident entry
6. Page on-call engineer

### On receiving Latency-P95-Critical
1. Check Active Sessions — is there a queue buildup?
2. Check CPU and Memory saturation
3. Check for /chaos/latency calls in logs (filter by event = "chaos_latency_injected")
4. Run: `curl http://HOST:5000/metrics` — look at p95_latency_ms live
5. If CPU high + latency high: scale up instance or reduce load
6. If CPU normal + latency high: check downstream dependencies

### On receiving CPU-Critical
1. SSH to instance: `top -bn1 | head -20`
2. Find the process: `ps aux --sort=-%cpu | head -10`
3. Check for stress test: `ps aux | grep stress`
4. Check for runaway loop in logs: `journalctl -u order-api --since "5 min ago"`
5. If legitimate load: scale out (add instance or upgrade instance type)
6. If attack: block source IP via security group

### On receiving Memory-Critical
1. `free -m` — confirm actual memory usage
2. `ps aux --sort=-%mem | head -10` — find memory hog
3. Check for /chaos/memory in recent logs
4. If memory leak: restart service (`sudo systemctl restart order-api`)
5. Long-term: add memory profiling, implement object pooling

### On receiving OrderRate-Drop
1. Test the endpoint manually: `curl -X POST http://HOST:5000/orders ...`
2. Check error rate — if high, follow ErrorRate runbook
3. Check health endpoint: `curl http://HOST:5000/health`
4. Check service status: `sudo systemctl status order-api`
5. Check for silent 200 errors (endpoint returns 200 but doesn't create order)
6. Review logs: `filter event = "order_created"` — are any appearing?

---

## False Positive Management

After each incident, review:
- Did this alarm fire correctly?
- Was the threshold appropriate?
- Were evaluation periods too short (noisy) or too long (slow to fire)?

Update thresholds in `config/alarms.json` and redeploy with `config/deploy_alarms.sh`.
