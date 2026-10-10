from common.events import Event


class InMemoryPublisher:
    """Drop-in replacement for RabbitPublisher in tests."""

    def __init__(self) -> None:
        self.published: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.published.append(event)
