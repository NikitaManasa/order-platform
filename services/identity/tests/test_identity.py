import pytest
from fastapi.testclient import TestClient

from common.auth import Role, TokenVerifier
from identity_service.config import Settings
from identity_service.main import create_app
from identity_service.passwords import hash_password, verify_password

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "admin-password-123"
ALICE = {"email": "alice@example.com", "password": "correct-horse-battery"}


@pytest.fixture
def client():
    app = create_app(Settings(database_url="sqlite://", admin_email=ADMIN_EMAIL, admin_password=ADMIN_PASSWORD))
    with TestClient(app) as c:
        yield c


def login(client, email, password):
    return client.post("/auth/token", json={"email": email, "password": password})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_register_creates_customer_without_leaking_hash(client):
    resp = client.post("/auth/register", json=ALICE)

    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == ALICE["email"]
    assert body["role"] == Role.CUSTOMER
    assert "password" not in str(body)


def test_email_is_normalised_and_unique(client):
    client.post("/auth/register", json=ALICE)

    resp = client.post("/auth/register", json={**ALICE, "email": "  ALICE@Example.com "})

    assert resp.status_code == 409


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not-an-email", "password": "long-enough-pw"},
        {"email": "a@example.com", "password": "short"},
    ],
)
def test_register_validates_input(client, payload):
    assert client.post("/auth/register", json=payload).status_code == 422


def test_login_returns_token_that_verifies_against_jwks(client):
    client.post("/auth/register", json=ALICE)

    resp = login(client, **ALICE)

    assert resp.status_code == 200
    token = resp.json()["access_token"]
    # Verify exactly the way other services do: with only the published public key.
    import jwt

    jwk = client.get("/.well-known/jwks.json").json()["keys"][0]
    assert "d" not in jwk  # private exponent must never be published
    verifier = TokenVerifier(public_key=jwt.PyJWK(jwk).key)
    principal = verifier.verify(token)
    assert principal.email == ALICE["email"]
    assert principal.role == Role.CUSTOMER


@pytest.mark.parametrize(
    ("email", "password"),
    [(ALICE["email"], "wrong-password"), ("nobody@example.com", "whatever-pw")],
)
def test_login_failures_look_identical(client, email, password):
    client.post("/auth/register", json=ALICE)

    resp = login(client, email, password)

    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid email or password"


def test_me_returns_current_user(client):
    client.post("/auth/register", json=ALICE)
    token = login(client, **ALICE).json()["access_token"]

    assert client.get("/auth/me", headers=bearer(token)).json()["email"] == ALICE["email"]
    assert client.get("/auth/me").status_code == 401


def test_bootstrap_admin_can_log_in_with_admin_role(client):
    token = login(client, ADMIN_EMAIL, ADMIN_PASSWORD).json()["access_token"]

    assert client.get("/auth/me", headers=bearer(token)).json()["role"] == Role.ADMIN


def test_nobody_can_register_as_admin(client):
    resp = client.post("/auth/register", json={**ALICE, "role": "admin"})

    assert resp.json()["role"] == Role.CUSTOMER


def test_password_hashing():
    stored = hash_password("s3cret-password")

    assert "s3cret-password" not in stored
    assert stored != hash_password("s3cret-password")  # random salt
    assert verify_password("s3cret-password", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("anything", "garbage")
