import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from common.db import make_engine
from common.events import Event, EventType
from common.testing import InMemoryPublisher
from payments_service.config import Settings
from payments_service.handlers import handle_event
from payments_service.main import create_app
from payments_service.models import Base

LIMIT = 50_000


@pytest.fixture
def session_factory():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def reserved(total_cents, order_id="order-1"):
    return Event(
        type=EventType.INVENTORY_RESERVED,
        order_id=order_id,
        payload={"customer_id": "cust-1", "total_cents": total_cents},
    )


def test_charge_within_limit_succeeds(session_factory):
    out = handle_event(session_factory, reserved(LIMIT), LIMIT)

    assert [e.type for e in out] == [EventType.PAYMENT_SUCCEEDED]
    assert out[0].payload["amount_cents"] == LIMIT


def test_charge_over_limit_is_declined(session_factory):
    out = handle_event(session_factory, reserved(LIMIT + 1), LIMIT)

    assert [e.type for e in out] == [EventType.PAYMENT_FAILED]
    assert "exceeds card limit" in out[0].payload["reason"]


def test_order_is_never_charged_twice(session_factory):
    handle_event(session_factory, reserved(100), LIMIT)

    assert handle_event(session_factory, reserved(100), LIMIT) == []


def test_ignores_other_events(session_factory):
    assert handle_event(session_factory, Event(type=EventType.ORDER_CREATED, order_id="x"), LIMIT) == []


def test_payments_api():
    app = create_app(Settings(database_url="sqlite://"), publisher=InMemoryPublisher())
    with TestClient(app) as client:
        assert client.get("/payments/order-1").status_code == 404
        assert client.get("/health").json() == {"status": "ok"}
