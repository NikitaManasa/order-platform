import functools

from common.auth import Role, TokenIssuer, TokenVerifier, generate_private_key_pem
from common.events import Event


class InMemoryPublisher:
    """Drop-in replacement for RabbitPublisher in tests."""

    def __init__(self) -> None:
        self.published: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.published.append(event)


@functools.cache
def fake_issuer() -> TokenIssuer:
    """One RSA key per test session; generating keys is slow."""
    return TokenIssuer(generate_private_key_pem())


def fake_verifier() -> TokenVerifier:
    return TokenVerifier(public_key=fake_issuer().public_key())


def auth_header(user_id: str = "cust-1", role: str = Role.CUSTOMER) -> dict[str, str]:
    token = fake_issuer().issue(user_id, f"{user_id}@example.com", role)
    return {"Authorization": f"Bearer {token}"}
