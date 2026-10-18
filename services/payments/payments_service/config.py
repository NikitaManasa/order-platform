import os
from dataclasses import dataclass

from common.secrets import database_url


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./payments.db"
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    jwks_url: str = "http://localhost:8005/.well-known/jwks.json"
    # Simulated card limit: charges above this are declined, which exercises the compensation path.
    payment_limit_cents: int = 50_000

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=database_url(cls.database_url),
            rabbitmq_url=os.getenv("RABBITMQ_URL", cls.rabbitmq_url),
            jwks_url=os.getenv("JWKS_URL", cls.jwks_url),
            payment_limit_cents=int(os.getenv("PAYMENT_LIMIT_CENTS", cls.payment_limit_cents)),
        )
