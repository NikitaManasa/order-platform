import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./orders.db"
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            rabbitmq_url=os.getenv("RABBITMQ_URL", cls.rabbitmq_url),
        )
