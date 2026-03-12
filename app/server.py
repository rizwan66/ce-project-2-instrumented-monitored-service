"""
Order Processing API
Production-ready service with structured logging, custom CloudWatch metrics,
correlation IDs, and health checks.
"""

import json
import time
import uuid
import random
import threading
import os
from datetime import datetime, timezone
from functools import wraps

import boto3
import structlog
from flask import Flask, request, jsonify, g
from botocore.exceptions import ClientError, NoCredentialsError

# ─── Configuration ────────────────────────────────────────────────────────────

from config import Config

# ─── Structured Logging Setup ─────────────────────────────────────────────────

def add_correlation_id(logger, method, event_dict):
    """Inject correlation ID from Flask request context."""
    try:
        event_dict["correlation_id"] = g.get("correlation_id", "no-request-context")
    except RuntimeError:
        event_dict["correlation_id"] = "no-request-context"
    return event_dict

def add_service_info(logger, method, event_dict):
    event_dict["service"] = Config.SERVICE_NAME
    event_dict["environment"] = Config.ENVIRONMENT
    event_dict["version"] = Config.SERVICE_VERSION
    return event_dict

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        add_service_info,
        add_correlation_id,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

log = structlog.get_logger()

# ─── CloudWatch Metrics Client ────────────────────────────────────────────────

cloudwatch = boto3.client("cloudwatch", region_name=Config.AWS_REGION)

def publish_metric(metric_name, value, unit="Count", dimensions=None):
    """Publish a single metric to CloudWatch."""
    if dimensions is None:
        dimensions = [{"Name": "Service", "Value": Config.SERVICE_NAME},
                      {"Name": "Environment", "Value": Config.ENVIRONMENT}]
    try:
        cloudwatch.put_metric_data(
            Namespace=Config.METRICS_NAMESPACE,
            MetricData=[{
                "MetricName": metric_name,
                "Value": value,
                "Unit": unit,
                "Timestamp": datetime.now(timezone.utc),
                "Dimensions": dimensions,
            }]
        )
    except (ClientError, NoCredentialsError) as e:
        log.warning("cloudwatch_publish_failed", metric=metric_name, error=str(e))


def publish_metrics_batch(metric_data):
    """Publish multiple metrics to CloudWatch in one call (max 20)."""
    try:
        cloudwatch.put_metric_data(
            Namespace=Config.METRICS_NAMESPACE,
            MetricData=metric_data,
        )
    except (ClientError, NoCredentialsError) as e:
        log.warning("cloudwatch_batch_publish_failed", error=str(e))

# ─── In-Memory State (simulates a database) ───────────────────────────────────

orders: dict = {}
_lock = threading.Lock()

# Rolling counters (reset each minute by background thread)
_stats = {
    "requests_total": 0,
    "requests_success": 0,
    "requests_error": 0,
    "orders_created": 0,
    "total_order_value": 0.0,
    "latencies": [],          # list of floats (seconds)
    "active_sessions": 0,
}

def _increment(key, amount=1):
    with _lock:
        _stats[key] += amount

def _append_latency(val):
    with _lock:
        _stats["latencies"].append(val)

# ─── Flask Application ────────────────────────────────────────────────────────

app = Flask(__name__)

# ─── Middleware: Correlation ID & Request Logging ─────────────────────────────

@app.before_request
def before_request():
    g.start_time = time.time()
    g.correlation_id = (
        request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
    )
    _increment("requests_total")
    _increment("active_sessions")
    log.info(
        "request_started",
        method=request.method,
        path=request.path,
        remote_addr=request.remote_addr,
    )


@app.after_request
def after_request(response):
    duration = time.time() - g.start_time
    _append_latency(duration)

    status_class = response.status_code // 100
    if status_class == 2:
        _increment("requests_success")
    elif status_class >= 4:
        _increment("requests_error")

    _increment("active_sessions", -1)

    log.info(
        "request_completed",
        method=request.method,
        path=request.path,
        status_code=response.status_code,
        duration_ms=round(duration * 1000, 2),
    )

    response.headers["X-Correlation-ID"] = g.correlation_id
    return response


@app.errorhandler(Exception)
def handle_exception(exc):
    log.error(
        "unhandled_exception",
        error=str(exc),
        error_type=type(exc).__name__,
        exc_info=True,
    )
    publish_metric("UnhandledErrors", 1)
    return jsonify({"error": "Internal server error",
                    "correlation_id": g.get("correlation_id")}), 500

# ─── Routes ──────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint — used by load balancers and monitoring."""
    with _lock:
        order_count = len(orders)
    return jsonify({
        "status": "healthy",
        "service": Config.SERVICE_NAME,
        "version": Config.SERVICE_VERSION,
        "order_count": order_count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }), 200


@app.route("/orders", methods=["POST"])
def create_order():
    """
    Create a new order.
    Body: { "customer_id": str, "items": [{"sku": str, "qty": int, "price": float}] }
    """
    body = request.get_json(silent=True)
    if not body:
        log.warning("create_order_bad_request", reason="missing_body")
        publish_metric("ValidationErrors", 1)
        return jsonify({"error": "Request body required",
                        "correlation_id": g.correlation_id}), 400

    customer_id = body.get("customer_id")
    items = body.get("items", [])

    if not customer_id or not items:
        log.warning("create_order_bad_request",
                    reason="missing_fields",
                    customer_id=customer_id)
        publish_metric("ValidationErrors", 1)
        return jsonify({"error": "customer_id and items required",
                        "correlation_id": g.correlation_id}), 400

    # Validate items
    for item in items:
        if not all(k in item for k in ("sku", "qty", "price")):
            return jsonify({"error": "Each item needs sku, qty, price",
                            "correlation_id": g.correlation_id}), 400
        if item["qty"] <= 0 or item["price"] < 0:
            return jsonify({"error": "Invalid qty or price",
                            "correlation_id": g.correlation_id}), 400

    # Simulate occasional processing delay (for latency metrics)
    if Config.SIMULATE_LATENCY:
        time.sleep(random.uniform(0.01, 0.1))

    order_id = str(uuid.uuid4())
    total = sum(i["qty"] * i["price"] for i in items)
    order = {
        "order_id": order_id,
        "customer_id": customer_id,
        "items": items,
        "total": round(total, 2),
        "status": "confirmed",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "correlation_id": g.correlation_id,
    }

    with _lock:
        orders[order_id] = order
        _stats["orders_created"] += 1
        _stats["total_order_value"] += total

    log.info(
        "order_created",
        order_id=order_id,
        customer_id=customer_id,
        item_count=len(items),
        total=total,
    )

    # Publish business metrics immediately
    publish_metrics_batch([
        {
            "MetricName": "OrdersCreated",
            "Value": 1,
            "Unit": "Count",
            "Dimensions": [{"Name": "Service", "Value": Config.SERVICE_NAME},
                           {"Name": "Environment", "Value": Config.ENVIRONMENT}],
        },
        {
            "MetricName": "OrderValue",
            "Value": total,
            "Unit": "None",
            "Dimensions": [{"Name": "Service", "Value": Config.SERVICE_NAME},
                           {"Name": "Environment", "Value": Config.ENVIRONMENT}],
        },
    ])

    return jsonify(order), 201


@app.route("/orders/<order_id>", methods=["GET"])
def get_order(order_id):
    """Retrieve an existing order by ID."""
    with _lock:
        order = orders.get(order_id)

    if not order:
        log.warning("order_not_found", order_id=order_id)
        publish_metric("OrderNotFound", 1)
        return jsonify({"error": "Order not found",
                        "correlation_id": g.correlation_id}), 404

    log.info("order_retrieved", order_id=order_id,
             customer_id=order["customer_id"])
    return jsonify(order), 200


@app.route("/orders/<order_id>", methods=["DELETE"])
def cancel_order(order_id):
    """Cancel (delete) an order."""
    with _lock:
        order = orders.pop(order_id, None)

    if not order:
        log.warning("order_cancel_not_found", order_id=order_id)
        return jsonify({"error": "Order not found",
                        "correlation_id": g.correlation_id}), 404

    log.info("order_cancelled", order_id=order_id,
             customer_id=order["customer_id"], total=order["total"])
    publish_metric("OrdersCancelled", 1)
    return jsonify({"message": "Order cancelled", "order_id": order_id}), 200


@app.route("/orders", methods=["GET"])
def list_orders():
    """List all orders (optionally filter by customer_id)."""
    customer_id = request.args.get("customer_id")
    with _lock:
        result = list(orders.values())

    if customer_id:
        result = [o for o in result if o["customer_id"] == customer_id]

    log.info("orders_listed", count=len(result), customer_filter=customer_id)
    return jsonify({"orders": result, "count": len(result)}), 200


@app.route("/metrics", methods=["GET"])
def internal_metrics():
    """Expose internal counters for debugging (not a replacement for CloudWatch)."""
    with _lock:
        snapshot = dict(_stats)
        snapshot["order_count"] = len(orders)
        lats = snapshot.pop("latencies", [])

    if lats:
        sorted_lats = sorted(lats)
        n = len(sorted_lats)
        snapshot["p50_latency_ms"] = round(sorted_lats[int(n * 0.50)] * 1000, 2)
        snapshot["p95_latency_ms"] = round(sorted_lats[int(n * 0.95)] * 1000, 2)
        snapshot["p99_latency_ms"] = round(sorted_lats[min(int(n * 0.99), n - 1)] * 1000, 2)
    else:
        snapshot["p50_latency_ms"] = 0
        snapshot["p95_latency_ms"] = 0
        snapshot["p99_latency_ms"] = 0

    error_rate = (
        snapshot["requests_error"] / snapshot["requests_total"] * 100
        if snapshot["requests_total"] > 0 else 0
    )
    snapshot["error_rate_pct"] = round(error_rate, 2)
    return jsonify(snapshot), 200


# ─── Failure Injection Endpoints (for incident simulation) ────────────────────

@app.route("/chaos/latency", methods=["POST"])
def inject_latency():
    """Inject artificial latency (seconds) for incident simulation."""
    body = request.get_json(silent=True) or {}
    seconds = float(body.get("seconds", 2))
    log.warning("chaos_latency_injected", seconds=seconds)
    time.sleep(seconds)
    return jsonify({"message": f"Slept {seconds}s", "correlation_id": g.correlation_id}), 200


@app.route("/chaos/error", methods=["POST"])
def inject_error():
    """Force a 500 error for incident simulation."""
    log.error("chaos_error_injected", reason="manual_trigger")
    publish_metric("ChaosErrors", 1)
    return jsonify({"error": "Chaos error injected",
                    "correlation_id": g.correlation_id}), 500


@app.route("/chaos/memory", methods=["POST"])
def inject_memory():
    """Allocate a chunk of memory for saturation testing."""
    body = request.get_json(silent=True) or {}
    mb = int(body.get("mb", 100))
    log.warning("chaos_memory_injected", mb=mb)
    # Hold reference in app context so it isn't GC'd immediately
    app._chaos_memory = bytearray(mb * 1024 * 1024)
    return jsonify({"message": f"Allocated {mb}MB",
                    "correlation_id": g.correlation_id}), 200


# ─── Background Metrics Publisher ─────────────────────────────────────────────

def _publish_aggregate_metrics():
    """
    Publishes aggregate metrics to CloudWatch every 60 seconds:
      - RequestRate (requests/min)
      - ErrorRate (%)
      - P95Latency (ms)
      - ActiveSessions
      - OrdersPerMinute
      - AverageOrderValue
    """
    while True:
        time.sleep(60)
        with _lock:
            total = _stats["requests_total"]
            errors = _stats["requests_error"]
            orders_created = _stats["orders_created"]
            order_value = _stats["total_order_value"]
            active = _stats["active_sessions"]
            lats = list(_stats["latencies"])

            # Reset rolling counters
            _stats["requests_total"] = 0
            _stats["requests_success"] = 0
            _stats["requests_error"] = 0
            _stats["orders_created"] = 0
            _stats["total_order_value"] = 0.0
            _stats["latencies"] = []

        error_rate = (errors / total * 100) if total > 0 else 0
        avg_order_value = (order_value / orders_created) if orders_created > 0 else 0

        if lats:
            sorted_lats = sorted(lats)
            n = len(sorted_lats)
            p95 = sorted_lats[int(n * 0.95)] * 1000
        else:
            p95 = 0

        dims = [{"Name": "Service", "Value": Config.SERVICE_NAME},
                {"Name": "Environment", "Value": Config.ENVIRONMENT}]

        metrics = [
            {"MetricName": "RequestRate",     "Value": total,            "Unit": "Count/Minute", "Dimensions": dims},
            {"MetricName": "ErrorRate",       "Value": error_rate,       "Unit": "Percent",       "Dimensions": dims},
            {"MetricName": "P95LatencyMs",    "Value": p95,              "Unit": "Milliseconds",  "Dimensions": dims},
            {"MetricName": "ActiveSessions",  "Value": active,           "Unit": "Count",         "Dimensions": dims},
            {"MetricName": "OrdersPerMinute", "Value": orders_created,   "Unit": "Count",         "Dimensions": dims},
            {"MetricName": "AvgOrderValue",   "Value": avg_order_value,  "Unit": "None",          "Dimensions": dims},
        ]

        publish_metrics_batch(metrics)

        log.info(
            "aggregate_metrics_published",
            request_rate=total,
            error_rate_pct=round(error_rate, 2),
            p95_latency_ms=round(p95, 2),
            orders_per_minute=orders_created,
            avg_order_value=round(avg_order_value, 2),
        )


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("service_starting",
             service=Config.SERVICE_NAME,
             port=Config.PORT,
             environment=Config.ENVIRONMENT)

    # Start background metrics thread
    t = threading.Thread(target=_publish_aggregate_metrics, daemon=True)
    t.start()
    log.info("background_metrics_thread_started")

    app.run(host="0.0.0.0", port=Config.PORT, threaded=True)
