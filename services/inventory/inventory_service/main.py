import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from common.db import make_engine
from common.events import Event
from common.logging_config import setup_logging
from common.messaging import EventBus, Publisher
from inventory_service.config import Settings
from inventory_service.handlers import SUBSCRIBES_TO, handle_event
from inventory_service.models import Base, Product, seed_products


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sku: str
    name: str
    price_cents: int
    stock: int


def create_app(settings: Settings | None = None, publisher: Publisher | None = None) -> FastAPI:
    """App factory. Pass a `publisher` (tests) to run without RabbitMQ."""
    settings = settings or Settings.from_env()
    setup_logging("inventory")

    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(engine, expire_on_commit=False)
    seed_products(session_factory)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.publisher is not None:
            yield
            return

        bus = EventBus(settings.rabbitmq_url, queue_name="inventory", routing_keys=SUBSCRIBES_TO)

        async def on_event(event: Event) -> None:
            for outgoing in await asyncio.to_thread(handle_event, session_factory, event):
                await bus.publisher.publish(outgoing)

        await bus.start(on_event)
        app.state.publisher = bus.publisher
        try:
            yield
        finally:
            await bus.stop()

    app = FastAPI(title="inventory-service", lifespan=lifespan)
    app.state.publisher = publisher

    def _list_products() -> list[ProductOut]:
        with session_factory() as session:
            return [ProductOut.model_validate(p) for p in session.scalars(select(Product).order_by(Product.sku))]

    def _get_product(sku: str) -> ProductOut | None:
        with session_factory() as session:
            product = session.get(Product, sku)
            return ProductOut.model_validate(product) if product else None

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/products", response_model=list[ProductOut])
    async def list_products() -> list[ProductOut]:
        return await asyncio.to_thread(_list_products)

    @app.get("/products/{sku}", response_model=ProductOut)
    async def get_product(sku: str) -> ProductOut:
        product = await asyncio.to_thread(_get_product, sku)
        if product is None:
            raise HTTPException(status_code=404, detail="product not found")
        return product

    return app
