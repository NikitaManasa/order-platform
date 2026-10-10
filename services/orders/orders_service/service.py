from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from common.events import Event, EventType
from orders_service.models import Order, OrderItem
from orders_service.schemas import OrderCreate, OrderOut


def create_order(session_factory: sessionmaker, data: OrderCreate) -> tuple[OrderOut, Event]:
    """Persist a PENDING order and build the `order.created` event that kicks off the saga."""
    with session_factory.begin() as session:
        order = Order(
            customer_id=data.customer_id,
            total_cents=sum(i.quantity * i.unit_price_cents for i in data.items),
            items=[OrderItem(**i.model_dump()) for i in data.items],
        )
        session.add(order)
        session.flush()
        result = OrderOut.model_validate(order)

    event = Event(
        type=EventType.ORDER_CREATED,
        order_id=result.id,
        payload={
            "customer_id": result.customer_id,
            "total_cents": result.total_cents,
            "items": [i.model_dump() for i in data.items],
        },
    )
    return result, event


def get_order(session_factory: sessionmaker, order_id: str) -> OrderOut | None:
    with session_factory() as session:
        order = session.get(Order, order_id)
        return OrderOut.model_validate(order) if order else None


def list_orders(
    session_factory: sessionmaker,
    customer_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[OrderOut]:
    query = select(Order).order_by(Order.created_at.desc()).limit(limit)
    if customer_id:
        query = query.where(Order.customer_id == customer_id)
    if status:
        query = query.where(Order.status == status)
    with session_factory() as session:
        return [OrderOut.model_validate(o) for o in session.scalars(query)]
