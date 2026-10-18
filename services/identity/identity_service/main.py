import asyncio
import logging
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy.orm import sessionmaker

from common.auth import Principal, TokenIssuer, TokenVerifier, auth_dependencies, generate_private_key_pem
from common.db import make_engine
from common.logging_config import setup_logging
from identity_service import service
from identity_service.config import Settings
from identity_service.models import Base
from identity_service.schemas import Credentials, TokenOut, UserOut

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    setup_logging("identity")

    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(engine, expire_on_commit=False)

    private_key = settings.jwt_private_key
    if not private_key:
        log.warning("JWT_PRIVATE_KEY not set; using a throwaway key (tokens die on restart)")
        private_key = generate_private_key_pem()
    issuer = TokenIssuer(private_key, ttl_seconds=settings.token_ttl_seconds)
    current_user, _ = auth_dependencies(TokenVerifier(public_key=issuer.public_key()))

    if settings.admin_email and settings.admin_password:
        service.ensure_admin(session_factory, settings.admin_email.strip().lower(), settings.admin_password)

    app = FastAPI(title="identity-service")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/.well-known/jwks.json")
    async def jwks() -> dict:
        """Public keys other services use to verify tokens."""
        return issuer.jwks()

    @app.post("/auth/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
    async def register(body: Credentials) -> UserOut:
        try:
            return await asyncio.to_thread(service.register, session_factory, body.email, body.password)
        except service.EmailAlreadyRegistered as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, "email already registered") from exc

    @app.post("/auth/token", response_model=TokenOut)
    async def login(body: Credentials) -> TokenOut:
        user = await asyncio.to_thread(service.authenticate, session_factory, body.email, body.password)
        if user is None:
            # Same message for unknown email and wrong password.
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "invalid email or password", headers={"WWW-Authenticate": "Bearer"}
            )
        return TokenOut(access_token=issuer.issue(user.id, user.email, user.role), expires_in=issuer.ttl_seconds)

    @app.get("/auth/me", response_model=UserOut)
    async def me(principal: Annotated[Principal, Depends(current_user)]) -> UserOut:
        user = await asyncio.to_thread(service.get_user, session_factory, principal.user_id)
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
        return user

    return app
