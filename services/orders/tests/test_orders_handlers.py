import pytest
from sqlalchemy.orm import sessionmaker

from common.db import make_engine
from common.events import Event, EventType
from orders_service import service
from orders_service.handlers import handle_event
from orders_service.models import Base
from orders_service.schemas import OrderCreate


@pytest.fixture
def session_factory():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def order_id(session_factory):
    data = OrderCreate(items=[{"sku": "SKU-MOUSE", "quantity": 1, "unit_price_cents": 2999}])
    order, _ = service.create_order(session_factory, "cust-1", data)
    return order.id


def test_payment_succeeded_confirms_order(session_factory, order_id):
    out = handle_event(session_factory, Event(type=EventType.PAYMENT_SUCCEEDED, order_id=order_id))

    assert service.get_order(session_factory, order_id).status == "CONFIRMED"
    assert [e.type for e in out] == [EventType.ORDER_CONFIRMED]


@pytest.mark.parametrize("event_type", [EventType.INVENTORY_REJECTED, EventType.PAYMENT_FAILED])
def test_failures_cancel_order_with_reason(session_factory, order_id, event_type):
    event = Event(type=event_type, order_id=order_id, payload={"reason": "nope"})

    out = handle_event(session_factory, event)

    order = service.get_order(session_factory, order_id)
    assert order.status == "CANCELLED"
    assert order.cancel_reason == "nope"
    assert out[0].type == EventType.ORDER_CANCELLED
    assert out[0].payload["reason"] == "nope"


def test_duplicate_event_is_processed_once(session_factory, order_id):
    event = Event(type=EventType.PAYMENT_SUCCEEDED, order_id=order_id)

    assert len(handle_event(session_factory, event)) == 1
    assert handle_event(session_factory, event) == []


def test_final_state_is_not_overwritten(session_factory, order_id):
    handle_event(session_factory, Event(type=EventType.PAYMENT_SUCCEEDED, order_id=order_id))

    out = handle_event(session_factory, Event(type=EventType.PAYMENT_FAILED, order_id=order_id))

    assert out == []
    assert service.get_order(session_factory, order_id).status == "CONFIRMED"


def test_unknown_order_is_ignored(session_factory):
    assert handle_event(session_factory, Event(type=EventType.PAYMENT_SUCCEEDED, order_id="missing")) == []
