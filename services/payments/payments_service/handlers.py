"""Charges the customer once stock is reserved (simulated payment provider)."""

import logging

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from common.events import Event, EventType
from payments_service.models import Payment

log = logging.getLogger(__name__)

SUBSCRIBES_TO = [EventType.INVENTORY_RESERVED]


def handle_event(session_factory: sessionmaker, event: Event, limit_cents: int) -> list[Event]:
    if event.type != EventType.INVENTORY_RESERVED:
        return []

    customer_id = event.payload["customer_id"]
    amount = int(event.payload["total_cents"])
    approved = amount <= limit_cents
    reason = None if approved else f"amount {amount} exceeds card limit {limit_cents}"

    with session_factory.begin() as session:
        if session.scalar(select(Payment).where(Payment.order_id == event.order_id)) is not None:
            log.info("order already charged, skipping", extra={"order_id": event.order_id})
            return []
        session.add(
            Payment(
                order_id=event.order_id,
                customer_id=customer_id,
                amount_cents=amount,
                status="SUCCEEDED" if approved else "DECLINED",
                reason=reason,
            )
        )

    log.info("payment %s", "succeeded" if approved else "declined", extra={"order_id": event.order_id})
    if approved:
        return [
            Event(
                type=EventType.PAYMENT_SUCCEEDED,
                order_id=event.order_id,
                payload={"customer_id": customer_id, "amount_cents": amount},
            )
        ]
    return [
        Event(
            type=EventType.PAYMENT_FAILED,
            order_id=event.order_id,
            payload={"customer_id": customer_id, "reason": reason},
        )
    ]
