#!/usr/bin/env bash
# deploy_alarms.sh — Create SNS topic, subscribe email, and deploy all CloudWatch alarms
set -euo pipefail

REGION="${AWS_DEFAULT_REGION:-us-east-1}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ALERT_EMAIL="${ALERT_EMAIL:-ops@example.com}"
TOPIC_NAME="OrderAPI-Alerts"

echo "==> Creating SNS topic ${TOPIC_NAME}..."
TOPIC_ARN=$(aws sns create-topic --name "${TOPIC_NAME}" --region "${REGION}" --query TopicArn --output text)
echo "    ARN: ${TOPIC_ARN}"

echo "==> Subscribing ${ALERT_EMAIL} to alerts..."
aws sns subscribe \
    --topic-arn "${TOPIC_ARN}" \
    --protocol email \
    --notification-endpoint "${ALERT_EMAIL}" \
    --region "${REGION}" || true
echo "    Check ${ALERT_EMAIL} to confirm subscription!"

echo "==> Deploying CloudWatch alarms..."

# Helper: create or update an alarm
create_alarm() {
    local name=$1; shift
    aws cloudwatch put-metric-alarm --alarm-name "${name}" "$@" --region "${REGION}"
    echo "    [OK] ${name}"
}

# 1. Error Rate Warning
create_alarm "OrderAPI-ErrorRate-Warning" \
    --alarm-description "Error rate > 1% for 2/3 minutes" \
    --namespace "OrderAPI/Production" \
    --metric-name "ErrorRate" \
    --dimensions Name=Service,Value=order-api Name=Environment,Value=production \
    --statistic Average \
    --period 60 \
    --evaluation-periods 3 \
    --datapoints-to-alarm 2 \
    --threshold 1.0 \
    --comparison-operator GreaterThanThreshold \
    --treat-missing-data notBreaching \
    --alarm-actions "${TOPIC_ARN}" \
    --ok-actions "${TOPIC_ARN}"

# 2. Error Rate Critical
create_alarm "OrderAPI-ErrorRate-Critical" \
    --alarm-description "CRITICAL: Error rate > 5% for 2/2 minutes" \
    --namespace "OrderAPI/Production" \
    --metric-name "ErrorRate" \
    --dimensions Name=Service,Value=order-api Name=Environment,Value=production \
    --statistic Average \
    --period 60 \
    --evaluation-periods 2 \
    --datapoints-to-alarm 2 \
    --threshold 5.0 \
    --comparison-operator GreaterThanThreshold \
    --treat-missing-data notBreaching \
    --alarm-actions "${TOPIC_ARN}" \
    --ok-actions "${TOPIC_ARN}"

# 3. P95 Latency Warning
create_alarm "OrderAPI-Latency-P95-Warning" \
    --alarm-description "P95 latency > 500ms for 3/5 minutes" \
    --namespace "OrderAPI/Production" \
    --metric-name "P95LatencyMs" \
    --dimensions Name=Service,Value=order-api Name=Environment,Value=production \
    --statistic Average \
    --period 60 \
    --evaluation-periods 5 \
    --datapoints-to-alarm 3 \
    --threshold 500 \
    --comparison-operator GreaterThanThreshold \
    --treat-missing-data notBreaching \
    --alarm-actions "${TOPIC_ARN}" \
    --ok-actions "${TOPIC_ARN}"

# 4. P95 Latency Critical
create_alarm "OrderAPI-Latency-P95-Critical" \
    --alarm-description "CRITICAL: P95 latency > 1000ms for 2/3 minutes" \
    --namespace "OrderAPI/Production" \
    --metric-name "P95LatencyMs" \
    --dimensions Name=Service,Value=order-api Name=Environment,Value=production \
    --statistic Average \
    --period 60 \
    --evaluation-periods 3 \
    --datapoints-to-alarm 2 \
    --threshold 1000 \
    --comparison-operator GreaterThanThreshold \
    --treat-missing-data notBreaching \
    --alarm-actions "${TOPIC_ARN}" \
    --ok-actions "${TOPIC_ARN}"

# 5. CPU Critical
create_alarm "OrderAPI-CPU-Critical" \
    --alarm-description "CRITICAL: CPU > 80% for 4/5 minutes" \
    --namespace "OrderAPI/System" \
    --metric-name "cpu_usage_user" \
    --statistic Average \
    --period 60 \
    --evaluation-periods 5 \
    --datapoints-to-alarm 4 \
    --threshold 80 \
    --comparison-operator GreaterThanThreshold \
    --treat-missing-data notBreaching \
    --alarm-actions "${TOPIC_ARN}" \
    --ok-actions "${TOPIC_ARN}"

# 6. Memory Critical
create_alarm "OrderAPI-Memory-Critical" \
    --alarm-description "CRITICAL: Memory > 90% for 3/3 minutes" \
    --namespace "OrderAPI/System" \
    --metric-name "mem_used_percent" \
    --statistic Average \
    --period 60 \
    --evaluation-periods 3 \
    --datapoints-to-alarm 3 \
    --threshold 90 \
    --comparison-operator GreaterThanThreshold \
    --treat-missing-data notBreaching \
    --alarm-actions "${TOPIC_ARN}" \
    --ok-actions "${TOPIC_ARN}"

# 7. Order Rate Drop (business metric)
create_alarm "OrderAPI-OrderRate-Drop" \
    --alarm-description "Orders/5min < 1 for 3/3 periods (silent create-order failure)" \
    --namespace "OrderAPI/Production" \
    --metric-name "OrdersPerMinute" \
    --dimensions Name=Service,Value=order-api Name=Environment,Value=production \
    --statistic Sum \
    --period 300 \
    --evaluation-periods 3 \
    --datapoints-to-alarm 3 \
    --threshold 1 \
    --comparison-operator LessThanThreshold \
    --treat-missing-data breaching \
    --alarm-actions "${TOPIC_ARN}" \
    --ok-actions "${TOPIC_ARN}"

# Create CloudWatch dashboard
echo "==> Creating CloudWatch dashboard..."
DASHBOARD_BODY=$(sed "s/ACCOUNT_ID/${ACCOUNT_ID}/g" "$(dirname "$0")/dashboard.json")
aws cloudwatch put-dashboard \
    --dashboard-name "OrderAPI-Production" \
    --dashboard-body "${DASHBOARD_BODY}" \
    --region "${REGION}"

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Alarms deployed!                                            ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  SNS Topic : ${TOPIC_ARN}"
echo "║  Email     : ${ALERT_EMAIL}"
echo "║  Dashboard : https://${REGION}.console.aws.amazon.com/cloudwatch/home#dashboards:name=OrderAPI-Production"
echo "╚══════════════════════════════════════════════════════════════╝"
