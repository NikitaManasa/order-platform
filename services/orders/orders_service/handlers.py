"""Reacts to the outcome of the saga and moves the order to its final state."""

import logging

from sqlalchemy.orm import sessionmaker

from common.events import Event, EventType
from orders_service.models import Order, OrderStatus, ProcessedEvent

log = logging.getLogger(__name__)

SUBSCRIBES_TO = [EventType.INVENTORY_REJECTED, EventType.PAYMENT_SUCCEEDED, EventType.PAYMENT_FAILED]


def handle_event(session_factory: sessionmaker, event: Event) -> list[Event]:
    """Returns the events to publish once the DB transaction has committed."""
    with session_factory.begin() as session:
        if session.get(ProcessedEvent, event.event_id) is not None:
            log.info("duplicate event skipped", extra={"event_id": event.event_id})
            return []
        session.add(ProcessedEvent(event_id=event.event_id))

        order = session.get(Order, event.order_id)
        if order is None:
            log.warning("event for unknown order", extra={"order_id": event.order_id})
            return []
        if order.status != OrderStatus.PENDING.value:
            return []

        if event.type == EventType.PAYMENT_SUCCEEDED:
            order.status = OrderStatus.CONFIRMED.value
            outgoing_type = EventType.ORDER_CONFIRMED
        elif event.type in (EventType.INVENTORY_REJECTED, EventType.PAYMENT_FAILED):
            order.status = OrderStatus.CANCELLED.value
            order.cancel_reason = event.payload.get("reason")
            outgoing_type = EventType.ORDER_CANCELLED
        else:
            return []

        log.info("order %s", order.status.lower(), extra={"order_id": order.id})
        return [
            Event(
                type=outgoing_type,
                order_id=order.id,
                payload={"customer_id": order.customer_id, "reason": order.cancel_reason},
            )
        ]
