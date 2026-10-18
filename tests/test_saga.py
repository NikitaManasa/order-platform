"""End-to-end saga tests: every service's real handler, wired together by an in-memory bus.

This proves the choreography works without needing RabbitMQ or Postgres running.
"""

from collections import defaultdict, deque

import pytest
from sqlalchemy.orm import sessionmaker

from common.db import make_engine
from common.events import Event
from inventory_service import handlers as inventory
from inventory_service.models import Base as InventoryBase
from inventory_service.models import Product, seed_products
from notifications_service import handlers as notifications
from orders_service import handlers as orders
from orders_service import service as order_service
from orders_service.models import Base as OrdersBase
from orders_service.schemas import OrderCreate
from payments_service import handlers as payments
from payments_service.models import Base as PaymentsBase

PAYMENT_LIMIT = 50_000


def _db(base):
    engine = make_engine("sqlite://")
    base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


class InMemoryBus:
    def __init__(self):
        self.subscribers = defaultdict(list)
        self.history: list[Event] = []

    def subscribe(self, event_types, handler):
        for t in event_types:
            self.subscribers[t].append(handler)

    def publish(self, event: Event):
        pending = deque([event])
        while pending:
            current = pending.popleft()
            self.history.append(current)
            for handler in self.subscribers[current.type]:
                pending.extend(handler(current) or [])


class Platform:
    def __init__(self):
        self.orders_db = _db(OrdersBase)
        self.inventory_db = _db(InventoryBase)
        self.payments_db = _db(PaymentsBase)
        seed_products(self.inventory_db)
        self.outbox = deque()

        self.bus = InMemoryBus()
        self.bus.subscribe(orders.SUBSCRIBES_TO, lambda e: orders.handle_event(self.orders_db, e))
        self.bus.subscribe(inventory.SUBSCRIBES_TO, lambda e: inventory.handle_event(self.inventory_db, e))
        self.bus.subscribe(payments.SUBSCRIBES_TO, lambda e: payments.handle_event(self.payments_db, e, PAYMENT_LIMIT))
        self.bus.subscribe(notifications.SUBSCRIBES_TO, lambda e: notifications.handle_event(e, self.outbox))

    def place_order(self, items):
        order, event = order_service.create_order(self.orders_db, "cust-1", OrderCreate(items=items))
        self.bus.publish(event)
        return order_service.get_order(self.orders_db, order.id)

    def stock(self, sku):
        with self.inventory_db() as s:
            return s.get(Product, sku).stock

    def event_types(self):
        return [e.type for e in self.bus.history]


@pytest.fixture
def platform():
    return Platform()


def test_happy_path_confirms_order(platform):
    order = platform.place_order([{"sku": "SKU-MOUSE", "quantity": 2, "unit_price_cents": 2999}])

    assert order.status == "CONFIRMED"
    assert platform.stock("SKU-MOUSE") == 98
    assert platform.event_types() == [
        "order.created",
        "inventory.reserved",
        "payment.succeeded",
        "order.confirmed",
    ]
    assert "is confirmed" in platform.outbox[-1]["message"]


def test_out_of_stock_cancels_order(platform):
    order = platform.place_order([{"sku": "SKU-MONITOR", "quantity": 11, "unit_price_cents": 24999}])

    assert order.status == "CANCELLED"
    assert "insufficient stock" in order.cancel_reason
    assert platform.stock("SKU-MONITOR") == 10
    assert "payment.succeeded" not in platform.event_types()


def test_declined_payment_cancels_order_and_releases_stock(platform):
    order = platform.place_order([{"sku": "SKU-LAPTOP", "quantity": 1, "unit_price_cents": 129999}])

    assert order.status == "CANCELLED"
    assert "exceeds card limit" in order.cancel_reason
    assert platform.stock("SKU-LAPTOP") == 5  # compensation put the reserved laptop back
    assert platform.event_types() == [
        "order.created",
        "inventory.reserved",
        "payment.failed",
        "order.cancelled",
    ]
    assert "was cancelled" in platform.outbox[-1]["message"]


def test_redelivered_order_created_does_not_double_reserve(platform):
    order, event = order_service.create_order(
        platform.orders_db,
        "cust-1",
        OrderCreate(items=[{"sku": "SKU-KEYBOARD", "quantity": 5, "unit_price_cents": 8999}]),
    )

    platform.bus.publish(event)
    platform.bus.publish(event)  # broker redelivery

    assert order_service.get_order(platform.orders_db, order.id).status == "CONFIRMED"
    assert platform.stock("SKU-KEYBOARD") == 45
    assert len(platform.outbox) == 1
