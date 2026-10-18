import asyncio
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy.orm import sessionmaker

from common.auth import Principal, TokenVerifier, auth_dependencies
from common.db import make_engine
from common.events import Event
from common.logging_config import setup_logging
from common.messaging import EventBus, Publisher
from orders_service import service
from orders_service.config import Settings
from orders_service.handlers import SUBSCRIBES_TO, handle_event
from orders_service.models import Base, OrderStatus
from orders_service.schemas import OrderCreate, OrderOut


def create_app(
    settings: Settings | None = None,
    publisher: Publisher | None = None,
    verifier: TokenVerifier | None = None,
) -> FastAPI:
    """App factory. Pass a `publisher` (tests) to run without RabbitMQ."""
    settings = settings or Settings.from_env()
    setup_logging("orders")

    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(engine, expire_on_commit=False)
    current_user, _ = auth_dependencies(verifier or TokenVerifier(jwks_url=settings.jwks_url))
    User = Annotated[Principal, Depends(current_user)]

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.publisher is not None:
            yield
            return

        bus = EventBus(settings.rabbitmq_url, queue_name="orders", routing_keys=SUBSCRIBES_TO)

        async def on_event(event: Event) -> None:
            for outgoing in await asyncio.to_thread(handle_event, session_factory, event):
                await bus.publisher.publish(outgoing)

        await bus.start(on_event)
        app.state.publisher = bus.publisher
        try:
            yield
        finally:
            await bus.stop()

    app = FastAPI(title="orders-service", lifespan=lifespan)
    app.state.publisher = publisher

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.post("/orders", response_model=OrderOut, status_code=201)
    async def create_order(body: OrderCreate, user: User) -> OrderOut:
        order, event = await asyncio.to_thread(service.create_order, session_factory, user.user_id, body)
        await app.state.publisher.publish(event)
        return order

    @app.get("/orders/{order_id}", response_model=OrderOut)
    async def get_order(order_id: str, user: User) -> OrderOut:
        order = await asyncio.to_thread(service.get_order, session_factory, order_id)
        # 404 rather than 403 for other people's orders, so ids can't be probed for existence.
        if order is None or (order.customer_id != user.user_id and not user.is_admin):
            raise HTTPException(status_code=404, detail="order not found")
        return order

    @app.get("/orders", response_model=list[OrderOut])
    async def list_orders(
        user: User,
        customer_id: str | None = None,
        status: OrderStatus | None = None,
        limit: int = Query(50, ge=1, le=200),
    ) -> list[OrderOut]:
        """Customers only ever see their own orders; admins can filter by any customer."""
        if not user.is_admin:
            customer_id = user.user_id
        return await asyncio.to_thread(
            service.list_orders, session_factory, customer_id, status.value if status else None, limit
        )

    return app
