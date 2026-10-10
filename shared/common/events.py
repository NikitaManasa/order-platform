"""Event envelope shared by every service.

Every message on the bus has the same shape, so any service can log, trace or
dead-letter a message without knowing what is inside the payload.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class EventType:
    """Routing keys used on the `events` topic exchange."""

    ORDER_CREATED = "order.created"
    ORDER_CONFIRMED = "order.confirmed"
    ORDER_CANCELLED = "order.cancelled"
    INVENTORY_RESERVED = "inventory.reserved"
    INVENTORY_REJECTED = "inventory.rejected"
    PAYMENT_SUCCEEDED = "payment.succeeded"
    PAYMENT_FAILED = "payment.failed"


class Event(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    type: str
    order_id: str  # every event in this system belongs to an order; doubles as correlation id
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = Field(default_factory=dict)
