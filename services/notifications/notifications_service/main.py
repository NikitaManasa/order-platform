import asyncio
import contextlib
import os
from collections import deque
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI

from common.auth import Principal, TokenVerifier, auth_dependencies
from common.events import Event
from common.logging_config import setup_logging
from common.messaging import consume
from notifications_service.handlers import SUBSCRIBES_TO, handle_event


def create_app(start_consumer: bool = True, verifier: TokenVerifier | None = None) -> FastAPI:
    setup_logging("notifications")
    rabbitmq_url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
    jwks_url = os.getenv("JWKS_URL", "http://localhost:8005/.well-known/jwks.json")
    _, admin_user = auth_dependencies(verifier or TokenVerifier(jwks_url=jwks_url))
    outbox: deque = deque(maxlen=100)  # recent notifications, exposed for the demo

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not start_consumer:
            yield
            return

        async def on_event(event: Event) -> None:
            handle_event(event, outbox)

        task = asyncio.create_task(consume(rabbitmq_url, "notifications", SUBSCRIBES_TO, on_event))
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="notifications-service", lifespan=lifespan)
    app.state.outbox = outbox

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/notifications")
    async def list_notifications(
        _: Annotated[Principal, Depends(admin_user)], order_id: str | None = None
    ) -> list[dict]:
        items = list(reversed(outbox))
        return [n for n in items if n["order_id"] == order_id] if order_id else items

    return app
