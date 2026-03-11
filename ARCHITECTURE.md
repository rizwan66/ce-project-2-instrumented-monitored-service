# Architecture

## System Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         AWS us-east-1                                   │
│                                                                         │
│   ┌──────────────────────────────────────────────────────────────────┐  │
│   │  EC2 t3.micro  (Amazon Linux 2)                                  │  │
│   │                                                                  │  │
│   │  ┌────────────────────────────────────────────────────────────┐  │  │
│   │  │  Gunicorn (2 workers, 4 threads, port 5000)                │  │  │
│   │  │                                                            │  │  │
│   │  │  ┌──────────────────────────────────────────────────────┐  │  │  │
│   │  │  │  Flask  — server.py                                  │  │  │  │
│   │  │  │                                                      │  │  │  │
│   │  │  │  Routes:  POST /orders  GET /orders  GET /health     │  │  │  │
│   │  │  │           DELETE /orders/:id  GET /metrics           │  │  │  │
│   │  │  │           POST /chaos/{latency,error,memory}         │  │  │  │
│   │  │  │                                                      │  │  │  │
│   │  │  │  Middleware:                                         │  │  │  │
│   │  │  │   before_request  → assign correlation ID           │  │  │  │
│   │  │  │   after_request   → log + record latency            │  │  │  │
│   │  │  │   errorhandler    → log + publish UnhandledError     │  │  │  │
│   │  │  │                                                      │  │  │  │
│   │  │  │  Background thread → publish agg metrics /60s       │  │  │  │
│   │  │  └──────────────────────────────────────────────────────┘  │  │  │
│   │  └────────────────────────────────────────────────────────────┘  │  │
│   │                                                                  │  │
│   │  ┌──────────────────────┐   ┌────────────────────────────────┐  │  │
│   │  │  structlog (JSON)    │   │  CloudWatch Agent              │  │  │
│   │  │  → stdout / logfile  │   │  → system metrics (CPU/mem)    │  │  │
│   │  └──────────┬───────────┘   └─────────────┬──────────────────┘  │  │
│   └─────────────│───────────────────────────────│────────────────────┘  │
│                 │                               │                        │
│        ┌────────▼───────────┐       ┌───────────▼─────────────────┐     │
│        │  CloudWatch Logs   │       │  CloudWatch Metrics          │     │
│        │  /aws/order-api    │       │  OrderAPI/Production         │     │
│        │   /application     │       │   RequestRate                │     │
│        │   /errors          │       │   ErrorRate                  │     │
│        │  /aws/order-api    │       │   P95LatencyMs               │     │
│        │   /system          │       │   OrdersPerMinute            │     │
│        └────────────────────┘       │   AvgOrderValue              │     │
│                                     │   ActiveSessions             │     │
│                                     │   OrdersCreated              │     │
│                                     │   OrderValue                 │     │
│                                     │  OrderAPI/System             │     │
│                                     │   cpu_usage_*                │     │
│                                     │   mem_used_percent           │     │
│                                     │   disk/net metrics           │     │
│                                     └─────────┬───────────────────┘     │
│                                               │                         │
│                              ┌────────────────▼──────────────────────┐  │
│                              │  CloudWatch Dashboard                  │  │
│                              │  "OrderAPI-Production"                 │  │
│                              │  10 widgets, Golden Signals layout     │  │
│                              └────────────────┬──────────────────────┘  │
│                                               │                         │
│                              ┌────────────────▼──────────────────────┐  │
│                              │  CloudWatch Alarms (7)                 │  │
│                              │  ErrorRate Warning + Critical          │  │
│                              │  P95Latency Warning + Critical         │  │
│                              │  CPU Critical                          │  │
│                              │  Memory Critical                       │  │
│                              │  OrderRate Drop                        │  │
│                              └────────────────┬──────────────────────┘  │
│                                               │ alarm action             │
│                              ┌────────────────▼──────────────────────┐  │
│                              │  SNS Topic: OrderAPI-Alerts            │  │
│                              │  → Email subscription                  │  │
│                              └───────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

## Components

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Web Server | Flask 3 + Gunicorn | HTTP request handling |
| Structured Logging | structlog | JSON log output with correlation IDs |
| Metrics Client | boto3 CloudWatch | Custom metric publication |
| System Metrics | CloudWatch Agent | CPU, memory, disk, network |
| Log Storage | CloudWatch Logs | Centralized, queryable log storage |
| Metrics Storage | CloudWatch Metrics | Time-series metric storage (15 months) |
| Visualization | CloudWatch Dashboard | Golden Signals monitoring |
| Alerting | CloudWatch Alarms + SNS | Tiered automated notifications |

## Data Flow

### Request Flow
```
Client → EC2:5000 → Gunicorn → Flask
  → before_request: assign correlation ID
  → route handler: business logic + immediate metrics (OrdersCreated, OrderValue)
  → after_request: log request + record latency sample
  → Response with X-Correlation-ID header
```

### Metrics Flow
```
Per-request:  OrdersCreated, OrderValue, ValidationErrors, etc. → CloudWatch (immediate)
Per-minute:   Background thread aggregates: RequestRate, ErrorRate, P95LatencyMs,
              ActiveSessions, OrdersPerMinute, AvgOrderValue → CloudWatch
System:       CloudWatch Agent → CPU, memory, disk, network → CloudWatch
```

### Log Flow
```
Flask → structlog → JSON → stdout
  → CloudWatch Agent tails log file → CloudWatch Logs /aws/order-api
```

### Alert Flow
```
CloudWatch Alarm (threshold breached) → SNS Topic → Email
CloudWatch Alarm (threshold recovered) → SNS Topic → OK Email
```

## Technology Choices

**Flask** over FastAPI: simpler for demos, no async complexity needed for this workload.

**structlog** over Python's stdlib `logging`: processor pipeline makes adding fields (correlation ID, service name) composable without boilerplate; JSON output is zero-config.

**boto3 direct** over embedded agents (otel, statsd): no sidecar needed, direct PutMetricData keeps the demo self-contained and clearly shows what's published.

**CloudWatch** over Prometheus/Grafana: native AWS, no extra infrastructure, integrates directly with alarms and SNS — appropriate for a single-service AWS deployment.

**Systemd** over Docker: simpler to demo and troubleshoot on a plain EC2 instance; avoids container complexity unrelated to the observability goals.
