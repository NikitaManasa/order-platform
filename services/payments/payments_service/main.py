import asyncio
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from common.db import make_engine
from common.events import Event
from common.logging_config import setup_logging
from common.messaging import EventBus, Publisher
from payments_service.config import Settings
from payments_service.handlers import SUBSCRIBES_TO, handle_event
from payments_service.models import Base, Payment


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    order_id: str
    customer_id: str
    amount_cents: int
    status: str
    reason: str | None
    created_at: datetime


def create_app(settings: Settings | None = None, publisher: Publisher | None = None) -> FastAPI:
    """App factory. Pass a `publisher` (tests) to run without RabbitMQ."""
    settings = settings or Settings.from_env()
    setup_logging("payments")

    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.publisher is not None:
            yield
            return

        bus = EventBus(settings.rabbitmq_url, queue_name="payments", routing_keys=SUBSCRIBES_TO)

        async def on_event(event: Event) -> None:
            outgoing = await asyncio.to_thread(handle_event, session_factory, event, settings.payment_limit_cents)
            for e in outgoing:
                await bus.publisher.publish(e)

        await bus.start(on_event)
        app.state.publisher = bus.publisher
        try:
            yield
        finally:
            await bus.stop()

    app = FastAPI(title="payments-service", lifespan=lifespan)
    app.state.publisher = publisher

    def _get_payment(order_id: str) -> PaymentOut | None:
        with session_factory() as session:
            payment = session.scalar(select(Payment).where(Payment.order_id == order_id))
            return PaymentOut.model_validate(payment) if payment else None

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/payments/{order_id}", response_model=PaymentOut)
    async def get_payment(order_id: str) -> PaymentOut:
        payment = await asyncio.to_thread(_get_payment, order_id)
        if payment is None:
            raise HTTPException(status_code=404, detail="payment not found")
        return payment

    return app
