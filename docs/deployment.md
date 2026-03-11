# Deployment Guide

## Prerequisites

- AWS account with an EC2 instance (t3.micro or larger, Amazon Linux 2)
- IAM role attached to EC2 with these policies:
  - `CloudWatchAgentServerPolicy`
  - `CloudWatchFullAccess` (or a custom policy with `cloudwatch:PutMetricData`, `cloudwatch:PutDashboard`, `cloudwatch:PutMetricAlarm`, `logs:CreateLogGroup`, `logs:PutLogEvents`)
  - `AmazonSNSFullAccess`
- Security group: inbound TCP 5000 from your IP (or 0.0.0.0/0 for demo)
- Python 3.8+ on the instance

---

## Step-by-Step Deployment

### 1. Launch EC2 instance

```bash
# In AWS Console or CLI
aws ec2 run-instances \
  --image-id ami-0c02fb55956c7d316 \  # Amazon Linux 2 us-east-1
  --instance-type t3.micro \
  --key-name YOUR_KEY_PAIR \
  --security-group-ids sg-XXXXXXXX \
  --iam-instance-profile Name=CloudWatchAgentRole \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=order-api}]'
```

### 2. SSH to instance

```bash
ssh -i your-key.pem ec2-user@YOUR_EC2_PUBLIC_IP
```

### 3. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/ce-project-2-instrumented-monitored-service.git
cd ce-project-2-instrumented-monitored-service
```

### 4. Set environment variables

```bash
export AWS_DEFAULT_REGION=us-east-1
export ALERT_EMAIL=your@email.com
export DEPLOY_ENV=production
```

### 5. Run deployment script

```bash
cd app
chmod +x deploy.sh
./deploy.sh
```

The script will:
1. Install Python, pip, system packages
2. Install CloudWatch Agent
3. Copy app files to `/opt/order-api`
4. Install Python dependencies
5. Create CloudWatch log group
6. Configure and start CloudWatch Agent
7. Create systemd service and start it
8. Run a health check
9. Deploy SNS topic + all 7 CloudWatch alarms

### 6. Confirm email subscription

Check your email and click **Confirm subscription** in the SNS email.

### 7. Verify deployment

```bash
# Health check
curl http://localhost:5000/health

# Create a test order
curl -X POST http://localhost:5000/orders \
  -H "Content-Type: application/json" \
  -d '{"customer_id":"test-001","items":[{"sku":"SKU-001","qty":2,"price":19.99}]}'

# View metrics
curl http://localhost:5000/metrics | python3 -m json.tool
```

### 8. Deploy CloudWatch dashboard

```bash
cd /path/to/repo
bash config/deploy_alarms.sh
```

### 9. Generate traffic to populate metrics

```bash
python3 app/load_test.py \
  --url http://localhost:5000 \
  --rps 10 \
  --duration 120
```

---

## Post-Deployment Checks

| Check | Command | Expected |
|-------|---------|----------|
| Service running | `systemctl is-active order-api` | `active` |
| Health endpoint | `curl localhost:5000/health` | `{"status":"healthy"}` |
| CloudWatch Agent | `amazon-cloudwatch-agent-ctl -m ec2 -a status` | `running` |
| Metrics appearing | `aws cloudwatch list-metrics --namespace OrderAPI/Production` | 6+ metrics |
| Logs appearing | AWS Console → CloudWatch → Log groups → /aws/order-api | Log streams present |
| Alarms created | `aws cloudwatch describe-alarms --alarm-name-prefix OrderAPI` | 7 alarms |
| Dashboard | AWS Console → CloudWatch → Dashboards | OrderAPI-Production exists |

---

## Updating the Application

```bash
# On EC2 instance:
cd /path/to/repo
git pull
cp app/server.py app/config.py /opt/order-api/
sudo systemctl restart order-api
curl http://localhost:5000/health
```

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `SERVICE_NAME` | `order-api` | Service identifier in logs/metrics |
| `SERVICE_VERSION` | `1.0.0` | Version tag in logs |
| `ENVIRONMENT` | `development` | Environment dimension in metrics |
| `PORT` | `5000` | HTTP port |
| `AWS_REGION` | `us-east-1` | CloudWatch region |
| `METRICS_NAMESPACE` | `OrderAPI/Production` | CloudWatch namespace |
| `SIMULATE_LATENCY` | `false` | Add random 10–100ms jitter to responses |

---

## Teardown

```bash
# Stop and disable service
sudo systemctl stop order-api
sudo systemctl disable order-api

# Delete alarms
aws cloudwatch delete-alarms --alarm-names \
  OrderAPI-ErrorRate-Warning \
  OrderAPI-ErrorRate-Critical \
  OrderAPI-Latency-P95-Warning \
  OrderAPI-Latency-P95-Critical \
  OrderAPI-CPU-Critical \
  OrderAPI-Memory-Critical \
  OrderAPI-OrderRate-Drop

# Delete dashboard
aws cloudwatch delete-dashboards --dashboard-names OrderAPI-Production

# Delete SNS topic (get ARN first)
TOPIC_ARN=$(aws sns list-topics --query 'Topics[?contains(TopicArn, `OrderAPI-Alerts`)].TopicArn' --output text)
aws sns delete-topic --topic-arn $TOPIC_ARN

# Delete log group
aws logs delete-log-group --log-group-name /aws/order-api

# Terminate EC2 instance (via console or CLI)
```
