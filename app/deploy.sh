#!/usr/bin/env bash
# deploy.sh — Deploy Order API to an Amazon Linux 2 EC2 instance
# Usage: ./deploy.sh [--env production|staging]
set -euo pipefail

ENVIRONMENT="${DEPLOY_ENV:-production}"
SERVICE_NAME="order-api"
PORT=5000
APP_DIR="/opt/${SERVICE_NAME}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
LOG_GROUP="/aws/${SERVICE_NAME}"
METRICS_NAMESPACE="OrderAPI/Production"
AWS_REGION="${AWS_DEFAULT_REGION:-us-east-1}"

echo "==> Deploying ${SERVICE_NAME} (${ENVIRONMENT}) to $(hostname)"

# ── 1. System dependencies ───────────────────────────────────────────────────
echo "==> Installing system packages..."
sudo yum update -y -q
sudo yum install -y python3 python3-pip python3-devel gcc -q

# ── 2. CloudWatch Agent ──────────────────────────────────────────────────────
if ! command -v amazon-cloudwatch-agent-ctl &>/dev/null; then
    echo "==> Installing CloudWatch Agent..."
    sudo yum install -y amazon-cloudwatch-agent -q
fi

# ── 3. App directory ─────────────────────────────────────────────────────────
echo "==> Setting up app directory ${APP_DIR}..."
sudo mkdir -p "${APP_DIR}"
sudo cp server.py config.py requirements.txt "${APP_DIR}/"
sudo pip3 install -q -r "${APP_DIR}/requirements.txt"

# ── 4. CloudWatch Logs group ─────────────────────────────────────────────────
echo "==> Creating CloudWatch log group ${LOG_GROUP}..."
aws logs create-log-group --log-group-name "${LOG_GROUP}" --region "${AWS_REGION}" 2>/dev/null || true
aws logs put-retention-policy \
    --log-group-name "${LOG_GROUP}" \
    --retention-in-days 30 \
    --region "${AWS_REGION}" || true

# ── 5. CloudWatch Agent config ───────────────────────────────────────────────
echo "==> Configuring CloudWatch Agent..."
sudo cp ../config/cloudwatch-agent-config.json /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json
sudo amazon-cloudwatch-agent-ctl \
    -a fetch-config \
    -m ec2 \
    -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json \
    -s

# ── 6. systemd service ───────────────────────────────────────────────────────
echo "==> Writing systemd unit..."
sudo tee "${SERVICE_FILE}" > /dev/null <<EOF
[Unit]
Description=Order API Service
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=${APP_DIR}
Environment=SERVICE_NAME=${SERVICE_NAME}
Environment=SERVICE_VERSION=1.0.0
Environment=ENVIRONMENT=${ENVIRONMENT}
Environment=PORT=${PORT}
Environment=AWS_REGION=${AWS_REGION}
Environment=METRICS_NAMESPACE=${METRICS_NAMESPACE}
ExecStart=/usr/bin/python3 -m gunicorn \
    --workers 2 \
    --threads 4 \
    --bind 0.0.0.0:${PORT} \
    --access-logfile - \
    --error-logfile - \
    server:app
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"

# ── 7. Health check ──────────────────────────────────────────────────────────
echo "==> Waiting for service to start..."
sleep 5
for i in {1..10}; do
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${PORT}/health")
    if [ "${STATUS}" = "200" ]; then
        echo "==> Service is healthy (HTTP 200)"
        break
    fi
    echo "   Attempt ${i}/10 — HTTP ${STATUS}"
    sleep 3
done

# ── 8. SNS + Alarms ─────────────────────────────────────────────────────────
echo "==> Deploying CloudWatch alarms..."
bash "$(dirname "$0")/../config/deploy_alarms.sh"

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║  Deployment complete!                            ║"
echo "╠══════════════════════════════════════════════════╣"
echo "║  Health:   http://$(hostname -I | awk '{print $1}'):${PORT}/health  ║"
echo "║  Logs:     ${LOG_GROUP}              ║"
echo "║  Metrics:  ${METRICS_NAMESPACE}         ║"
echo "╚══════════════════════════════════════════════════╝"
