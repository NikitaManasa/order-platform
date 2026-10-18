import pytest
from fastapi.testclient import TestClient

from common.auth import Role
from common.events import EventType
from common.testing import InMemoryPublisher, auth_header, fake_verifier
from orders_service.config import Settings
from orders_service.main import create_app

ALICE = auth_header("alice")
BOB = auth_header("bob")
ADMIN = auth_header("admin-1", Role.ADMIN)


@pytest.fixture
def publisher():
    return InMemoryPublisher()


@pytest.fixture
def client(publisher):
    app = create_app(Settings(database_url="sqlite://"), publisher=publisher, verifier=fake_verifier())
    with TestClient(app) as c:
        yield c


ORDER = {
    "items": [
        {"sku": "SKU-KEYBOARD", "quantity": 2, "unit_price_cents": 8999},
        {"sku": "SKU-MOUSE", "quantity": 1, "unit_price_cents": 2999},
    ],
}


def test_health_needs_no_auth(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_create_order_returns_pending_and_publishes_event(client, publisher):
    resp = client.post("/orders", json=ORDER, headers=ALICE)

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "PENDING"
    assert body["customer_id"] == "alice"
    assert body["total_cents"] == 2 * 8999 + 2999
    assert len(body["items"]) == 2

    assert len(publisher.published) == 1
    event = publisher.published[0]
    assert event.type == EventType.ORDER_CREATED
    assert event.order_id == body["id"]
    assert event.payload["customer_id"] == "alice"


def test_customer_id_comes_from_token_not_body(client):
    resp = client.post("/orders", json={**ORDER, "customer_id": "bob"}, headers=ALICE)

    assert resp.json()["customer_id"] == "alice"


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer not-a-jwt"}, {"Authorization": "Basic YWxpY2U6cGFzcw=="}],
)
def test_create_order_requires_valid_token(client, publisher, headers):
    resp = client.post("/orders", json=ORDER, headers=headers)

    assert resp.status_code == 401
    assert publisher.published == []


@pytest.mark.parametrize(
    "payload",
    [
        {"items": []},
        {"items": [{"sku": "SKU-MOUSE", "quantity": 0, "unit_price_cents": 2999}]},
        {"items": [{"sku": "SKU-MOUSE", "quantity": 101, "unit_price_cents": 2999}]},
        {"items": [{"sku": "SKU-MOUSE", "quantity": 1, "unit_price_cents": -1}]},
    ],
)
def test_create_order_rejects_invalid_input(client, publisher, payload):
    assert client.post("/orders", json=payload, headers=ALICE).status_code == 422
    assert publisher.published == []


def test_owner_can_read_order(client):
    order_id = client.post("/orders", json=ORDER, headers=ALICE).json()["id"]

    resp = client.get(f"/orders/{order_id}", headers=ALICE)

    assert resp.status_code == 200
    assert resp.json()["id"] == order_id


def test_other_customer_gets_404_not_403(client):
    order_id = client.post("/orders", json=ORDER, headers=ALICE).json()["id"]

    assert client.get(f"/orders/{order_id}", headers=BOB).status_code == 404


def test_admin_can_read_any_order(client):
    order_id = client.post("/orders", json=ORDER, headers=ALICE).json()["id"]

    assert client.get(f"/orders/{order_id}", headers=ADMIN).status_code == 200


def test_get_unknown_order_returns_404(client):
    assert client.get("/orders/does-not-exist", headers=ALICE).status_code == 404


def test_customers_only_list_their_own_orders(client):
    client.post("/orders", json=ORDER, headers=ALICE)
    client.post("/orders", json=ORDER, headers=BOB)

    # Bob tries to peek at Alice's orders via the filter; it is ignored for non-admins.
    resp = client.get("/orders", params={"customer_id": "alice"}, headers=BOB)

    assert [o["customer_id"] for o in resp.json()] == ["bob"]


def test_admin_can_filter_by_customer(client):
    client.post("/orders", json=ORDER, headers=ALICE)
    client.post("/orders", json=ORDER, headers=BOB)

    resp = client.get("/orders", params={"customer_id": "alice"}, headers=ADMIN)

    assert [o["customer_id"] for o in resp.json()] == ["alice"]
    assert len(client.get("/orders", headers=ADMIN).json()) == 2


def test_list_orders_rejects_unknown_status(client):
    assert client.get("/orders", params={"status": "SHIPPED"}, headers=ALICE).status_code == 422
