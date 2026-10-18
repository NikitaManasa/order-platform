"""Attacks the token verification every service relies on."""

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from common.auth import ALGORITHM, AUDIENCE, ISSUER, Role, TokenIssuer, TokenVerifier, generate_private_key_pem
from common.secrets import database_url, read_secret
from common.testing import fake_issuer, fake_verifier


def claims(**overrides):
    now = datetime.now(UTC)
    base = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "alice",
        "role": Role.CUSTOMER,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    return {**base, **overrides}


def sign(payload, issuer=None):
    issuer = issuer or fake_issuer()
    return jwt.encode(payload, issuer._private_key, algorithm=ALGORITHM, headers={"kid": issuer.kid})


def test_valid_token_round_trips():
    principal = fake_verifier().verify(fake_issuer().issue("alice", "alice@example.com", Role.ADMIN))

    assert (principal.user_id, principal.email, principal.is_admin) == ("alice", "alice@example.com", True)


@pytest.mark.parametrize(
    "bad_claims",
    [
        {"exp": datetime.now(UTC) - timedelta(seconds=1)},  # expired
        {"aud": "some-other-app"},  # token minted for another system
        {"iss": "evil-issuer"},
    ],
)
def test_rejects_bad_claims(bad_claims):
    with pytest.raises(jwt.InvalidTokenError):
        fake_verifier().verify(sign(claims(**bad_claims)))


def test_rejects_missing_expiry():
    payload = claims()
    del payload["exp"]

    with pytest.raises(jwt.InvalidTokenError):
        fake_verifier().verify(sign(payload))


def test_rejects_token_signed_by_another_key():
    attacker = TokenIssuer(generate_private_key_pem())

    with pytest.raises(jwt.InvalidTokenError):
        fake_verifier().verify(attacker.issue("alice", "a@example.com", Role.ADMIN))


def test_rejects_alg_none_and_hs256_confusion():
    unsigned = jwt.encode(claims(role=Role.ADMIN), key=None, algorithm="none")
    # Classic attack: sign with HS256 using the *public* key as the HMAC secret.
    public_pem = fake_issuer().public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    for token in (unsigned, _hs256(claims(role=Role.ADMIN), public_pem)):
        with pytest.raises(jwt.InvalidTokenError):
            fake_verifier().verify(token)


def _hs256(payload, secret: bytes) -> str:
    # PyJWT refuses to HMAC-sign with a PEM key, so build the token by hand like an attacker would.
    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(json.dumps(payload, default=lambda d: int(d.timestamp())).encode())
    signature = b64(hmac.new(secret, f"{header}.{body}".encode(), hashlib.sha256).digest())
    return f"{header}.{body}.{signature}"


def test_verifier_needs_exactly_one_key_source():
    with pytest.raises(ValueError):
        TokenVerifier()


def test_read_secret_prefers_file(tmp_path, monkeypatch):
    secret_file = tmp_path / "db_password"
    secret_file.write_text("from-file\n")
    monkeypatch.setenv("DB_PASSWORD", "from-env")
    monkeypatch.setenv("DB_PASSWORD_FILE", str(secret_file))

    assert read_secret("DB_PASSWORD") == "from-file"


def test_database_url_injects_password(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://app@postgres:5432/orders")
    monkeypatch.setenv("DB_PASSWORD", "p@ss/word")
    monkeypatch.delenv("DB_PASSWORD_FILE", raising=False)

    assert database_url("unused") == "postgresql+psycopg://app:p%40ss%2Fword@postgres:5432/orders"
