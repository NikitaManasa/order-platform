"""Reserves stock for new orders and releases it when payment fails (saga compensation)."""

import logging
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from common.events import Event, EventType
from inventory_service.models import ProcessedEvent, Product, Reservation

log = logging.getLogger(__name__)

SUBSCRIBES_TO = [EventType.ORDER_CREATED, EventType.PAYMENT_FAILED]


def handle_event(session_factory: sessionmaker, event: Event) -> list[Event]:
    with session_factory.begin() as session:
        if session.get(ProcessedEvent, event.event_id) is not None:
            log.info("duplicate event skipped", extra={"event_id": event.event_id})
            return []
        session.add(ProcessedEvent(event_id=event.event_id))

        if event.type == EventType.ORDER_CREATED:
            return _reserve(session, event)
        if event.type == EventType.PAYMENT_FAILED:
            _release(session, event.order_id)
        return []


def _reserve(session: Session, event: Event) -> list[Event]:
    customer_id = event.payload["customer_id"]
    requested: dict[str, int] = defaultdict(int)
    for item in event.payload["items"]:
        requested[item["sku"]] += item["quantity"]

    products: dict[str, Product] = {}
    problems: list[str] = []
    # Lock rows in a fixed order so two concurrent orders can't deadlock each other.
    for sku in sorted(requested):
        product = session.get(Product, sku, with_for_update=True)
        if product is None:
            problems.append(f"unknown sku {sku}")
            continue
        if product.stock < requested[sku]:
            problems.append(f"insufficient stock for {sku} (requested {requested[sku]}, available {product.stock})")
        products[sku] = product

    # Never trust prices sent by the client.
    for item in event.payload["items"]:
        product = products.get(item["sku"])
        if product is not None and item["unit_price_cents"] != product.price_cents:
            problems.append(f"price mismatch for {item['sku']}")

    if problems:
        reason = "; ".join(dict.fromkeys(problems))
        log.info("reservation rejected: %s", reason, extra={"order_id": event.order_id})
        return [
            Event(
                type=EventType.INVENTORY_REJECTED,
                order_id=event.order_id,
                payload={"customer_id": customer_id, "reason": reason},
            )
        ]

    for sku, quantity in requested.items():
        products[sku].stock -= quantity
        session.add(Reservation(order_id=event.order_id, sku=sku, quantity=quantity))

    log.info("stock reserved", extra={"order_id": event.order_id})
    return [
        Event(
            type=EventType.INVENTORY_RESERVED,
            order_id=event.order_id,
            payload={"customer_id": customer_id, "total_cents": event.payload["total_cents"]},
        )
    ]


def _release(session: Session, order_id: str) -> None:
    reservations = session.scalars(select(Reservation).where(Reservation.order_id == order_id)).all()
    for reservation in reservations:
        product = session.get(Product, reservation.sku, with_for_update=True)
        product.stock += reservation.quantity
        session.delete(reservation)
    if reservations:
        log.info("stock released", extra={"order_id": order_id})
