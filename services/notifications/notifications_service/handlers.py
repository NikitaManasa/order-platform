"""Tells the customer how their order ended. "Sending" is a log line; swap in SES/SendGrid later."""

import logging
from collections import deque
from datetime import UTC, datetime

from common.events import Event, EventType

log = logging.getLogger(__name__)

SUBSCRIBES_TO = [EventType.ORDER_CONFIRMED, EventType.ORDER_CANCELLED]


def build_message(event: Event) -> str | None:
    customer = event.payload.get("customer_id", "customer")
    if event.type == EventType.ORDER_CONFIRMED:
        return f"Hi {customer}, your order {event.order_id} is confirmed."
    if event.type == EventType.ORDER_CANCELLED:
        return f"Hi {customer}, your order {event.order_id} was cancelled: {event.payload.get('reason')}."
    return None


def handle_event(event: Event, outbox: deque) -> None:
    message = build_message(event)
    if message is None:
        return
    outbox.append(
        {
            "order_id": event.order_id,
            "customer_id": event.payload.get("customer_id"),
            "type": event.type,
            "message": message,
            "sent_at": datetime.now(UTC).isoformat(),
        }
    )
    log.info("notification sent: %s", message, extra={"order_id": event.order_id})
