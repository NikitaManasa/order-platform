from collections import deque

from fastapi.testclient import TestClient

from common.events import Event, EventType
from notifications_service.handlers import build_message, handle_event
from notifications_service.main import create_app


def test_confirmed_message():
    event = Event(type=EventType.ORDER_CONFIRMED, order_id="o-1", payload={"customer_id": "cust-1"})
    assert build_message(event) == "Hi cust-1, your order o-1 is confirmed."


def test_cancelled_message_includes_reason():
    event = Event(
        type=EventType.ORDER_CANCELLED, order_id="o-1", payload={"customer_id": "c", "reason": "out of stock"}
    )
    assert "cancelled: out of stock" in build_message(event)


def test_irrelevant_events_are_ignored():
    outbox = deque()
    handle_event(Event(type=EventType.PAYMENT_SUCCEEDED, order_id="o-1"), outbox)
    assert not outbox


def test_notifications_endpoint_filters_by_order():
    app = create_app(start_consumer=False)
    for order_id in ("o-1", "o-2"):
        handle_event(Event(type=EventType.ORDER_CONFIRMED, order_id=order_id), app.state.outbox)

    with TestClient(app) as client:
        assert len(client.get("/notifications").json()) == 2
        assert [n["order_id"] for n in client.get("/notifications", params={"order_id": "o-2"}).json()] == ["o-2"]
