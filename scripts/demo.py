"""Places three orders against the running stack and shows how each saga ends.

Usage (after `docker compose up -d --build`):  python scripts/demo.py
"""

import json
import time
import urllib.error
import urllib.request

ORDERS_URL = "http://localhost:8001"
INVENTORY_URL = "http://localhost:8002"
NOTIFICATIONS_URL = "http://localhost:8004"

# name -> (items, expected final status)
SCENARIOS = {
    "happy path": ([{"sku": "SKU-MOUSE", "quantity": 2, "unit_price_cents": 2999}], "CONFIRMED"),
    "out of stock": ([{"sku": "SKU-MONITOR", "quantity": 100, "unit_price_cents": 24999}], "CANCELLED"),
    "payment declined": ([{"sku": "SKU-LAPTOP", "quantity": 1, "unit_price_cents": 129999}], "CANCELLED"),
}


def request(method: str, url: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"{method} {url} -> {exc.code}: {exc.read().decode()}") from exc


def wait_for_final_state(order_id: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        order = request("GET", f"{ORDERS_URL}/orders/{order_id}")
        if order["status"] != "PENDING":
            return order
        time.sleep(0.3)
    raise TimeoutError(f"order {order_id} still PENDING after {timeout}s")


def main() -> None:
    failures = []
    for name, (items, expected) in SCENARIOS.items():
        order = request("POST", f"{ORDERS_URL}/orders", {"customer_id": "demo-user", "items": items})
        final = wait_for_final_state(order["id"])
        notes = request("GET", f"{NOTIFICATIONS_URL}/notifications?order_id={order['id']}")
        print(f"\n== {name} ==")
        print(f"  order   {final['id']}  ->  {final['status']}  (expected {expected})")
        if final["cancel_reason"]:
            print(f"  reason  {final['cancel_reason']}")
        if notes:
            print(f"  email   {notes[0]['message']}")
        if final["status"] != expected:
            failures.append(name)

    print("\n== stock after demo ==")
    for p in request("GET", f"{INVENTORY_URL}/products"):
        print(f"  {p['sku']:<14} {p['stock']}")

    if failures:
        raise SystemExit(f"\nFAILED scenarios: {', '.join(failures)}")
    print("\nAll scenarios ended as expected.")


if __name__ == "__main__":
    main()
