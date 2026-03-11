"""
Application configuration — driven by environment variables.
Sane defaults for local development; override in production via env.
"""
import os


class Config:
    # ── Service identity ────────────────────────────────────────────────────
    SERVICE_NAME    = os.environ.get("SERVICE_NAME", "order-api")
    SERVICE_VERSION = os.environ.get("SERVICE_VERSION", "1.0.0")
    ENVIRONMENT     = os.environ.get("ENVIRONMENT", "development")

    # ── Server ──────────────────────────────────────────────────────────────
    PORT = int(os.environ.get("PORT", "5000"))

    # ── AWS / CloudWatch ────────────────────────────────────────────────────
    AWS_REGION        = os.environ.get("AWS_REGION", "us-east-1")
    METRICS_NAMESPACE = os.environ.get("METRICS_NAMESPACE", "OrderAPI/Production")

    # ── Feature flags ───────────────────────────────────────────────────────
    # Set SIMULATE_LATENCY=true in dev to add random jitter to responses
    SIMULATE_LATENCY = os.environ.get("SIMULATE_LATENCY", "false").lower() == "true"
