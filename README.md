# Order API — Instrumented & Monitored Cloud Service

A production-ready REST API for order processing, built with full observability:
structured logging, custom CloudWatch metrics, Golden Signals dashboard, tiered alerting, and incident response simulation.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│  Internet / Load Balancer                                            │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ HTTP
                ┌───────────▼──────────────┐
                │   EC2 (order-api)         │
                │   Gunicorn + Flask        │
                │   Port 5000               │
                │                           │
                │  ┌─────────────────────┐  │
                │  │  structlog → JSON   │  │──► /var/log/order-api/
                │  └─────────────────────┘  │
                │  ┌─────────────────────┐  │
                │  │  boto3 → CloudWatch │  │──► Custom Metrics
                │  └─────────────────────┘  │
                └──────────────────────┬────┘
                                       │
              ┌────────────────────────▼──────────────────────────┐
              │              CloudWatch                            │
              │  ┌──────────────┐  ┌────────────┐  ┌──────────┐  │
              │  │   Log Groups │  │  Metrics   │  │ Alarms   │  │
              │  │  /aws/order- │  │ OrderAPI/  │  │ 7 alarms │  │
              │  │  api         │  │ Production │  │          │  │
              │  └──────────────┘  └────────────┘  └────┬─────┘  │
              │  ┌──────────────────────────────┐        │        │
              │  │  Dashboard: OrderAPI-Prod     │        │        │
              │  └──────────────────────────────┘        │        │
              └───────────────────────────────────────────┼────────┘
                                                          │ SNS
                                              ┌───────────▼──────────┐
                                              │  Email notifications  │
                                              └──────────────────────┘
```

## Quick Start

### Local development

```bash
cd app
pip install -r requirements.txt
python server.py
```

Test it:
```bash
curl http://localhost:5000/health

curl -X POST http://localhost:5000/orders \
  -H "Content-Type: application/json" \
  -d '{"customer_id":"cust-1","items":[{"sku":"SKU-001","qty":2,"price":19.99}]}'
```

### Deploy to EC2

```bash
# SSH to your EC2 instance, then:
cd app
export AWS_DEFAULT_REGION=us-east-1
export ALERT_EMAIL=your@email.com
bash deploy.sh
```

### Generate load (populate metrics)

```bash
python app/load_test.py --url http://YOUR_EC2_IP:5000 --rps 10 --duration 300
```

### Inject failures (incident simulation)

```bash
# High latency
curl -X POST http://localhost:5000/chaos/latency -H "Content-Type: application/json" -d '{"seconds":3}'

# Force 500 errors
curl -X POST http://localhost:5000/chaos/error

# Memory pressure
curl -X POST http://localhost:5000/chaos/memory -H "Content-Type: application/json" -d '{"mb":200}'
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| POST | `/orders` | Create order |
| GET | `/orders` | List orders |
| GET | `/orders/:id` | Get order |
| DELETE | `/orders/:id` | Cancel order |
| GET | `/metrics` | Internal metric snapshot |
| POST | `/chaos/latency` | Inject latency |
| POST | `/chaos/error` | Inject 500 error |
| POST | `/chaos/memory` | Inject memory pressure |

---

## Key Features

- **Structured JSON logging** with correlation IDs on every request
- **6 custom CloudWatch metrics**: RequestRate, ErrorRate, P95LatencyMs, OrdersPerMinute, AvgOrderValue, ActiveSessions
- **Golden Signals dashboard** with 10 widgets covering all 4 signals
- **7 CloudWatch alarms** with warning + critical tiers and SNS email notifications
- **Chaos endpoints** for incident response simulation
- **Background metrics thread** publishing aggregates every 60 seconds

---

## Screenshots

See [evidence/](evidence/) for dashboard, alert, and incident screenshots.

---

## Repository Structure

```
├── app/
│   ├── server.py           # Flask application + metrics + logging
│   ├── config.py           # Environment-driven configuration
│   ├── requirements.txt
│   ├── load_test.py        # Load generator
│   └── deploy.sh           # EC2 deployment script
├── config/
│   ├── dashboard.json              # CloudWatch dashboard definition
│   ├── alarms.json                 # Alarm definitions + rationale
│   ├── cloudwatch-agent-config.json
│   └── deploy_alarms.sh           # SNS + alarm deployment script
├── docs/
│   ├── runbook.md          # Troubleshooting runbook
│   ├── dashboard-guide.md  # How to read the dashboard
│   └── deployment.md       # Step-by-step deployment
├── evidence/               # Screenshots from incidents
├── presentation/           # Slides + demo script
├── README.md
├── ARCHITECTURE.md
├── INSTRUMENTATION.md
├── MONITORING.md
├── ALERTING.md
└── INCIDENTS.md
```
