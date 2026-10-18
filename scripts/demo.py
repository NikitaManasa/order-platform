"""End-to-end check against the running stack: auth, the three saga outcomes, and access control.

Usage (after `python scripts/dev_secrets.py` and `docker compose up -d --build`):
    python scripts/demo.py
Exits non-zero if anything is not as expected, so CI uses it as the integration test.
"""

import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ORDERS_URL = "http://localhost:8001"
INVENTORY_URL = "http://localhost:8002"
NOTIFICATIONS_URL = "http://localhost:8004"
IDENTITY_URL = "http://localhost:8005"
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD_FILE = Path(__file__).resolve().parent.parent / "secrets" / "admin_password.txt"

# name -> (items, expected final status)
SCENARIOS = {
    "happy path": ([{"sku": "SKU-MOUSE", "quantity": 2, "unit_price_cents": 2999}], "CONFIRMED"),
    "out of stock": ([{"sku": "SKU-MONITOR", "quantity": 100, "unit_price_cents": 24999}], "CANCELLED"),
    "payment declined": ([{"sku": "SKU-LAPTOP", "quantity": 1, "unit_price_cents": 129999}], "CANCELLED"),
}

failures: list[str] = []


def call(method: str, url: str, body: dict | None = None, token: str | None = None) -> tuple[int, object]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def ok(method: str, url: str, body: dict | None = None, token: str | None = None):
    status, data = call(method, url, body, token)
    if status >= 300:
        raise SystemExit(f"{method} {url} -> {status}: {data}")
    return data


def check(label: str, condition: bool) -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        failures.append(label)


def new_customer() -> str:
    creds = {"email": f"demo-{uuid.uuid4().hex[:8]}@example.com", "password": "demo-password-123"}
    ok("POST", f"{IDENTITY_URL}/auth/register", creds)
    return ok("POST", f"{IDENTITY_URL}/auth/token", creds)["access_token"]


def wait_for_final_state(order_id: str, token: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        order = ok("GET", f"{ORDERS_URL}/orders/{order_id}", token=token)
        if order["status"] != "PENDING":
            return order
        time.sleep(0.3)
    raise SystemExit(f"order {order_id} still PENDING after {timeout}s")


def main() -> None:
    alice = new_customer()
    bob = new_customer()
    admin_creds = {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD_FILE.read_text(encoding="utf-8").strip()}
    admin = ok("POST", f"{IDENTITY_URL}/auth/token", admin_creds)["access_token"]

    order_ids = []
    for name, (items, expected) in SCENARIOS.items():
        order = ok("POST", f"{ORDERS_URL}/orders", {"items": items}, token=alice)
        order_ids.append(order["id"])
        final = wait_for_final_state(order["id"], alice)
        print(f"\n== saga: {name} ==")
        print(f"  order   {final['id']}  ->  {final['status']}")
        if final["cancel_reason"]:
            print(f"  reason  {final['cancel_reason']}")
        check(f"{name} ends {expected}", final["status"] == expected)

    print("\n== access control ==")
    status, _ = call("POST", f"{ORDERS_URL}/orders", {"items": SCENARIOS["happy path"][0]})
    check("ordering without a token -> 401", status == 401)
    status, _ = call("POST", f"{ORDERS_URL}/orders", {"items": SCENARIOS["happy path"][0]}, token="forged.token.x")
    check("ordering with a forged token -> 401", status == 401)
    status, _ = call("GET", f"{ORDERS_URL}/orders/{order_ids[0]}", token=bob)
    check("another customer reading the order -> 404", status == 404)
    _, bobs_orders = call("GET", f"{ORDERS_URL}/orders", token=bob)
    check("another customer's order list is empty", bobs_orders == [])
    status, _ = call("GET", f"{NOTIFICATIONS_URL}/notifications", token=alice)
    check("customer reading notifications -> 403", status == 403)
    status, _ = call("POST", f"{INVENTORY_URL}/products/SKU-MOUSE/restock", {"quantity": 1}, token=alice)
    check("customer restocking -> 403", status == 403)
    status, _ = call("GET", f"{ORDERS_URL}/orders/{order_ids[0]}", token=admin)
    check("admin reading any order -> 200", status == 200)

    print("\n== notifications (admin view) ==")
    time.sleep(1)  # notifications are the last hop of the saga
    for order_id in order_ids:
        notes = ok("GET", f"{NOTIFICATIONS_URL}/notifications?order_id={order_id}", token=admin)
        if notes:
            print(f"  {notes[0]['message']}")

    print("\n== stock after demo ==")
    for p in ok("GET", f"{INVENTORY_URL}/products"):
        print(f"  {p['sku']:<14} {p['stock']}")

    if failures:
        raise SystemExit(f"\nFAILED: {', '.join(failures)}")
    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
