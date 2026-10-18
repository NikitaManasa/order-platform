import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from common.auth import Role
from common.db import make_engine
from common.events import Event, EventType
from common.testing import InMemoryPublisher, auth_header, fake_verifier
from inventory_service.config import Settings
from inventory_service.handlers import handle_event
from inventory_service.main import create_app
from inventory_service.models import Base, Product, Reservation, seed_products


@pytest.fixture
def session_factory():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    seed_products(factory)
    return factory


def stock(session_factory, sku):
    with session_factory() as s:
        return s.get(Product, sku).stock


def order_created(items, order_id="order-1"):
    return Event(
        type=EventType.ORDER_CREATED,
        order_id=order_id,
        payload={
            "customer_id": "cust-1",
            "total_cents": sum(i["quantity"] * i["unit_price_cents"] for i in items),
            "items": items,
        },
    )


KEYBOARDS = [{"sku": "SKU-KEYBOARD", "quantity": 3, "unit_price_cents": 8999}]


def test_reserves_stock(session_factory):
    out = handle_event(session_factory, order_created(KEYBOARDS))

    assert [e.type for e in out] == [EventType.INVENTORY_RESERVED]
    assert out[0].payload["total_cents"] == 3 * 8999
    assert stock(session_factory, "SKU-KEYBOARD") == 47


def test_same_sku_on_multiple_lines_is_summed(session_factory):
    items = KEYBOARDS + [{"sku": "SKU-KEYBOARD", "quantity": 2, "unit_price_cents": 8999}]

    handle_event(session_factory, order_created(items))

    assert stock(session_factory, "SKU-KEYBOARD") == 45


def test_rejects_insufficient_stock_without_touching_anything(session_factory):
    items = KEYBOARDS + [{"sku": "SKU-LAPTOP", "quantity": 6, "unit_price_cents": 129999}]

    out = handle_event(session_factory, order_created(items))

    assert out[0].type == EventType.INVENTORY_REJECTED
    assert "insufficient stock for SKU-LAPTOP" in out[0].payload["reason"]
    assert stock(session_factory, "SKU-KEYBOARD") == 50
    assert stock(session_factory, "SKU-LAPTOP") == 5


def test_rejects_unknown_sku(session_factory):
    out = handle_event(session_factory, order_created([{"sku": "SKU-NOPE", "quantity": 1, "unit_price_cents": 1}]))

    assert out[0].type == EventType.INVENTORY_REJECTED
    assert "unknown sku SKU-NOPE" in out[0].payload["reason"]


def test_rejects_tampered_price(session_factory):
    out = handle_event(session_factory, order_created([{"sku": "SKU-LAPTOP", "quantity": 1, "unit_price_cents": 1}]))

    assert out[0].type == EventType.INVENTORY_REJECTED
    assert "price mismatch for SKU-LAPTOP" in out[0].payload["reason"]
    assert stock(session_factory, "SKU-LAPTOP") == 5


def test_payment_failed_releases_reservation(session_factory):
    handle_event(session_factory, order_created(KEYBOARDS))

    handle_event(session_factory, Event(type=EventType.PAYMENT_FAILED, order_id="order-1"))

    assert stock(session_factory, "SKU-KEYBOARD") == 50
    with session_factory() as s:
        assert s.scalars(select(Reservation)).all() == []


def test_duplicate_order_created_reserves_once(session_factory):
    event = order_created(KEYBOARDS)

    handle_event(session_factory, event)
    assert handle_event(session_factory, event) == []

    assert stock(session_factory, "SKU-KEYBOARD") == 47


@pytest.fixture
def client():
    app = create_app(Settings(database_url="sqlite://"), publisher=InMemoryPublisher(), verifier=fake_verifier())
    with TestClient(app) as c:
        yield c


def test_catalog_is_public(client):
    products = client.get("/products").json()
    assert {p["sku"] for p in products} == {"SKU-KEYBOARD", "SKU-MOUSE", "SKU-MONITOR", "SKU-LAPTOP"}
    assert client.get("/products/SKU-MOUSE").json()["stock"] == 100
    assert client.get("/products/SKU-NOPE").status_code == 404


def test_admin_can_restock(client):
    resp = client.post("/products/SKU-MONITOR/restock", json={"quantity": 5}, headers=auth_header("a", Role.ADMIN))

    assert resp.status_code == 200
    assert resp.json()["stock"] == 15


@pytest.mark.parametrize(("headers", "expected"), [({}, 401), (auth_header("alice"), 403)])
def test_restock_requires_admin(client, headers, expected):
    resp = client.post("/products/SKU-MONITOR/restock", json={"quantity": 5}, headers=headers)

    assert resp.status_code == expected
    assert client.get("/products/SKU-MONITOR").json()["stock"] == 10
