import pytest
from fastapi.testclient import TestClient

from common.events import EventType
from common.testing import InMemoryPublisher
from orders_service.config import Settings
from orders_service.main import create_app


@pytest.fixture
def publisher():
    return InMemoryPublisher()


@pytest.fixture
def client(publisher):
    app = create_app(Settings(database_url="sqlite://"), publisher=publisher)
    with TestClient(app) as c:
        yield c


ORDER = {
    "customer_id": "cust-1",
    "items": [
        {"sku": "SKU-KEYBOARD", "quantity": 2, "unit_price_cents": 8999},
        {"sku": "SKU-MOUSE", "quantity": 1, "unit_price_cents": 2999},
    ],
}


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_create_order_returns_pending_and_publishes_event(client, publisher):
    resp = client.post("/orders", json=ORDER)

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "PENDING"
    assert body["total_cents"] == 2 * 8999 + 2999
    assert len(body["items"]) == 2

    assert len(publisher.published) == 1
    event = publisher.published[0]
    assert event.type == EventType.ORDER_CREATED
    assert event.order_id == body["id"]
    assert event.payload["total_cents"] == body["total_cents"]


@pytest.mark.parametrize(
    "payload",
    [
        {"customer_id": "cust-1", "items": []},
        {"customer_id": "", "items": ORDER["items"]},
        {"customer_id": "cust-1", "items": [{"sku": "SKU-MOUSE", "quantity": 0, "unit_price_cents": 2999}]},
        {"customer_id": "cust-1", "items": [{"sku": "SKU-MOUSE", "quantity": 1, "unit_price_cents": -1}]},
    ],
)
def test_create_order_rejects_invalid_input(client, publisher, payload):
    assert client.post("/orders", json=payload).status_code == 422
    assert publisher.published == []


def test_get_order(client):
    order_id = client.post("/orders", json=ORDER).json()["id"]

    resp = client.get(f"/orders/{order_id}")

    assert resp.status_code == 200
    assert resp.json()["id"] == order_id


def test_get_unknown_order_returns_404(client):
    assert client.get("/orders/does-not-exist").status_code == 404


def test_list_orders_filters_by_customer(client):
    client.post("/orders", json=ORDER)
    client.post("/orders", json={**ORDER, "customer_id": "cust-2"})

    resp = client.get("/orders", params={"customer_id": "cust-2"})

    assert [o["customer_id"] for o in resp.json()] == ["cust-2"]


def test_list_orders_rejects_unknown_status(client):
    assert client.get("/orders", params={"status": "SHIPPED"}).status_code == 422
