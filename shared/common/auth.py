"""JWT authentication shared by all services.

The identity service signs tokens with an RSA private key (RS256). Every other service only gets
the *public* key, fetched from the identity service's JWKS endpoint, so a compromised order or
payment service can verify tokens but can never mint them.
"""

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.algorithms import RSAAlgorithm
from jwt.exceptions import InvalidTokenError, PyJWKClientConnectionError, PyJWKClientError
from pydantic import BaseModel

ISSUER = "order-platform-identity"
AUDIENCE = "order-platform"
ALGORITHM = "RS256"


class Role:
    CUSTOMER = "customer"
    ADMIN = "admin"


class Principal(BaseModel):
    """The authenticated caller."""

    user_id: str
    email: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN


def generate_private_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


class TokenIssuer:
    def __init__(self, private_key_pem: str, ttl_seconds: int = 3600):
        self._private_key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
        public_der = self.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        # Key id lets verifiers pick the right key once keys are rotated.
        self.kid = hashlib.sha256(public_der).hexdigest()[:16]
        self.ttl_seconds = ttl_seconds

    def public_key(self) -> rsa.RSAPublicKey:
        return self._private_key.public_key()

    def issue(self, user_id: str, email: str, role: str) -> str:
        now = datetime.now(UTC)
        claims = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": user_id,
            "email": email,
            "role": role,
            "iat": now,
            "exp": now + timedelta(seconds=self.ttl_seconds),
        }
        return jwt.encode(claims, self._private_key, algorithm=ALGORITHM, headers={"kid": self.kid})

    def jwks(self) -> dict:
        jwk = RSAAlgorithm.to_jwk(self.public_key(), as_dict=True)
        return {"keys": [{**jwk, "kid": self.kid, "use": "sig", "alg": ALGORITHM}]}


class TokenVerifier:
    """Verifies tokens with either a JWKS URL (services) or a public key (tests, identity itself)."""

    def __init__(self, *, jwks_url: str | None = None, public_key: rsa.RSAPublicKey | None = None):
        if (jwks_url is None) == (public_key is None):
            raise ValueError("pass exactly one of jwks_url or public_key")
        self._jwks = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=300) if jwks_url else None
        self._public_key = public_key

    def verify(self, token: str) -> Principal:
        key = self._jwks.get_signing_key_from_jwt(token).key if self._jwks else self._public_key
        claims = jwt.decode(
            token,
            key,
            algorithms=[ALGORITHM],  # pinned: never let the token choose its own algorithm
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
        return Principal(user_id=claims["sub"], email=claims.get("email", ""), role=claims.get("role", Role.CUSTOMER))


_bearer = HTTPBearer(auto_error=False)
_UNAUTHORIZED = {"WWW-Authenticate": "Bearer"}


def auth_dependencies(verifier: TokenVerifier) -> tuple[Callable[..., Principal], Callable[..., Principal]]:
    """FastAPI dependencies: (any logged-in user, admin only)."""

    def current_user(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    ) -> Principal:
        if credentials is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token", headers=_UNAUTHORIZED)
        try:
            return verifier.verify(credentials.credentials)
        except PyJWKClientConnectionError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "auth keys unavailable") from exc
        except (InvalidTokenError, PyJWKClientError) as exc:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "invalid or expired token", headers=_UNAUTHORIZED
            ) from exc

    def admin_user(user: Annotated[Principal, Depends(current_user)]) -> Principal:
        if not user.is_admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "admin role required")
        return user

    return current_user, admin_user
