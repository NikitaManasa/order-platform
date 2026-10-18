import os
from dataclasses import dataclass

from common.secrets import database_url, read_secret


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./identity.db"
    # PEM private key used to sign tokens; a throwaway key is generated when unset (local dev only).
    jwt_private_key: str | None = None
    token_ttl_seconds: int = 3600
    # Bootstrap admin account, created on startup if it does not exist yet.
    admin_email: str | None = None
    admin_password: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=database_url(cls.database_url),
            jwt_private_key=read_secret("JWT_PRIVATE_KEY"),
            token_ttl_seconds=int(os.getenv("TOKEN_TTL_SECONDS", cls.token_ttl_seconds)),
            admin_email=os.getenv("ADMIN_EMAIL"),
            admin_password=read_secret("ADMIN_PASSWORD"),
        )
