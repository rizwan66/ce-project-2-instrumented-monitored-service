"""
Simple load generator — creates realistic traffic to populate CloudWatch metrics.
Run: python load_test.py --url http://localhost:5000 --rps 10 --duration 120
"""
import argparse
import random
import time
import uuid
import threading
import http.client
import json
from urllib.parse import urlparse

SKUS = ["SKU-001", "SKU-002", "SKU-003", "SKU-004", "SKU-005"]
PRICES = {"SKU-001": 19.99, "SKU-002": 49.99, "SKU-003": 9.99,
          "SKU-004": 99.99, "SKU-005": 5.99}

_results = {"success": 0, "error": 0, "latencies": []}
_lock = threading.Lock()


def _do_request(base_url: str):
    parsed = urlparse(base_url)
    host = parsed.hostname
    port = parsed.port or 80
    path_prefix = parsed.path.rstrip("/")

    try:
        conn = http.client.HTTPConnection(host, port, timeout=10)
        action = random.choices(
            ["create", "get", "list", "health"],
            weights=[50, 30, 15, 5],
        )[0]

        start = time.time()
        if action == "create":
            customer_id = f"cust-{random.randint(1, 100)}"
            num_items = random.randint(1, 4)
            items = [
                {"sku": random.choice(SKUS),
                 "qty": random.randint(1, 5),
                 "price": PRICES[random.choice(SKUS)]}
                for _ in range(num_items)
            ]
            body = json.dumps({"customer_id": customer_id, "items": items}).encode()
            headers = {
                "Content-Type": "application/json",
                "X-Correlation-ID": str(uuid.uuid4()),
            }
            conn.request("POST", f"{path_prefix}/orders", body=body, headers=headers)

        elif action == "get":
            order_id = str(uuid.uuid4())  # random, mostly 404s — realistic!
            conn.request("GET", f"{path_prefix}/orders/{order_id}")

        elif action == "list":
            conn.request("GET", f"{path_prefix}/orders")

        else:
            conn.request("GET", f"{path_prefix}/health")

        resp = conn.getresponse()
        resp.read()
        latency = time.time() - start

        with _lock:
            if resp.status < 500:
                _results["success"] += 1
            else:
                _results["error"] += 1
            _results["latencies"].append(latency)

    except Exception as e:
        with _lock:
            _results["error"] += 1
    finally:
        conn.close()


def _worker(base_url, interval, stop_event):
    while not stop_event.is_set():
        _do_request(base_url)
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Load test for Order API")
    parser.add_argument("--url", default="http://localhost:5000")
    parser.add_argument("--rps", type=int, default=5, help="Requests per second")
    parser.add_argument("--duration", type=int, default=60, help="Duration in seconds")
    args = parser.parse_args()

    interval = 1.0 / args.rps
    stop_event = threading.Event()

    threads = []
    for _ in range(args.rps):
        t = threading.Thread(target=_worker, args=(args.url, 1.0, stop_event), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(interval)

    print(f"Load testing {args.url} at ~{args.rps} RPS for {args.duration}s...")
    time.sleep(args.duration)
    stop_event.set()

    for t in threads:
        t.join(timeout=5)

    with _lock:
        total = _results["success"] + _results["error"]
        lats = sorted(_results["latencies"])
        n = len(lats)

    print("\n=== Results ===")
    print(f"Total requests : {total}")
    print(f"Success        : {_results['success']}")
    print(f"Errors         : {_results['error']}")
    if n:
        print(f"P50 latency    : {lats[int(n*0.50)]*1000:.1f}ms")
        print(f"P95 latency    : {lats[int(n*0.95)]*1000:.1f}ms")
        print(f"P99 latency    : {lats[min(int(n*0.99), n-1)]*1000:.1f}ms")


if __name__ == "__main__":
    main()
