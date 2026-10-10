"""RabbitMQ publishing and consuming.

Topology:
  - `events`      topic exchange; every service publishes here with routing key = event type
  - `<service>`   durable queue per service, bound to the event types it cares about
  - `events.dlx`  direct exchange; failed messages land in `<service>.dlq` after N retries
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

import aio_pika
from aio_pika import DeliveryMode, ExchangeType, Message
from aio_pika.abc import AbstractChannel, AbstractIncomingMessage, AbstractRobustConnection
from pydantic import ValidationError

from common.events import Event

log = logging.getLogger(__name__)

EXCHANGE = "events"
DEAD_LETTER_EXCHANGE = "events.dlx"
RETRY_HEADER = "x-retry-count"

Handler = Callable[[Event], Awaitable[None]]


class Publisher(Protocol):
    async def publish(self, event: Event) -> None: ...


def _log_context(event: Event) -> dict:
    return {"event_type": event.type, "event_id": event.event_id, "order_id": event.order_id}


def _to_message(event: Event, headers: dict | None = None) -> Message:
    return Message(
        body=event.model_dump_json().encode(),
        content_type="application/json",
        delivery_mode=DeliveryMode.PERSISTENT,
        message_id=event.event_id,
        correlation_id=event.order_id,
        headers=headers or {},
    )


async def connect(url: str, attempts: int = 30, delay: float = 2.0) -> AbstractRobustConnection:
    """Containers start in any order, so wait for the broker instead of crashing."""
    for attempt in range(1, attempts + 1):
        try:
            return await aio_pika.connect_robust(url)
        except (OSError, aio_pika.exceptions.AMQPConnectionError) as exc:
            if attempt == attempts:
                raise
            log.warning("broker not ready (%s), retry %d/%d in %.0fs", exc, attempt, attempts, delay)
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")


class RabbitPublisher:
    def __init__(self, url: str):
        self._url = url
        self._connection: AbstractRobustConnection | None = None
        self._exchange: aio_pika.abc.AbstractExchange | None = None

    async def connect(self) -> None:
        self._connection = await connect(self._url)
        channel = await self._connection.channel(publisher_confirms=True)
        self._exchange = await channel.declare_exchange(EXCHANGE, ExchangeType.TOPIC, durable=True)

    async def publish(self, event: Event) -> None:
        if self._exchange is None:
            raise RuntimeError("publisher not connected")
        await self._exchange.publish(_to_message(event), routing_key=event.type)
        log.info("published event", extra=_log_context(event))

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()


async def consume(
    url: str,
    queue_name: str,
    routing_keys: list[str],
    handler: Handler,
    max_retries: int = 3,
) -> None:
    connection = await connect(url)
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=10)

        exchange = await channel.declare_exchange(EXCHANGE, ExchangeType.TOPIC, durable=True)
        dlx = await channel.declare_exchange(DEAD_LETTER_EXCHANGE, ExchangeType.DIRECT, durable=True)
        dlq = await channel.declare_queue(f"{queue_name}.dlq", durable=True)
        await dlq.bind(dlx, routing_key=queue_name)

        queue = await channel.declare_queue(
            queue_name,
            durable=True,
            arguments={
                "x-dead-letter-exchange": DEAD_LETTER_EXCHANGE,
                "x-dead-letter-routing-key": queue_name,
            },
        )
        for key in routing_keys:
            await queue.bind(exchange, routing_key=key)

        log.info("consuming %s on queue %s", routing_keys, queue_name)
        async with queue.iterator() as messages:
            async for message in messages:
                await _handle_message(channel, queue_name, message, handler, max_retries)


async def _handle_message(
    channel: AbstractChannel,
    queue_name: str,
    message: AbstractIncomingMessage,
    handler: Handler,
    max_retries: int,
) -> None:
    try:
        event = Event.model_validate_json(message.body)
    except ValidationError:
        log.error("malformed message on %s, dead-lettering", queue_name)
        await message.reject(requeue=False)
        return

    try:
        await handler(event)
    except Exception:
        retries = int((message.headers or {}).get(RETRY_HEADER, 0))
        if retries < max_retries:
            log.exception("handler failed, retry %d/%d", retries + 1, max_retries, extra=_log_context(event))
            # Re-publish straight to our own queue with a bumped counter, then ack the original.
            await channel.default_exchange.publish(
                _to_message(event, {RETRY_HEADER: retries + 1}), routing_key=queue_name
            )
            await message.ack()
        else:
            log.exception("handler failed after %d retries, dead-lettering", max_retries, extra=_log_context(event))
            await message.reject(requeue=False)
        return

    await message.ack()


class EventBus:
    """The publisher plus the background consumer task for one service."""

    def __init__(self, url: str, queue_name: str, routing_keys: list[str]):
        self._url = url
        self._queue_name = queue_name
        self._routing_keys = routing_keys
        self.publisher = RabbitPublisher(url)
        self._task: asyncio.Task | None = None

    async def start(self, handler: Handler) -> None:
        await self.publisher.connect()
        self._task = asyncio.create_task(consume(self._url, self._queue_name, self._routing_keys, handler))
        self._task.add_done_callback(_log_consumer_exit)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self.publisher.close()


def _log_consumer_exit(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        log.error("consumer stopped unexpectedly", exc_info=task.exception())
