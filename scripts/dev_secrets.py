"""Generates random local-dev secrets into ./secrets (git-ignored). Safe to re-run; never overwrites.

In the cloud these same files are provided by AWS Secrets Manager instead (see README).
Usage:  python scripts/dev_secrets.py
"""

import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

SECRETS_DIR = Path(__file__).resolve().parent.parent / "secrets"


def _private_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


GENERATORS = {
    "db_password.txt": lambda: secrets.token_urlsafe(24),
    "admin_password.txt": lambda: secrets.token_urlsafe(18),
    "jwt_private_key.pem": _private_key_pem,
}


def main() -> None:
    SECRETS_DIR.mkdir(exist_ok=True)
    for name, generate in GENERATORS.items():
        path = SECRETS_DIR / name
        if path.exists():
            print(f"exists   {path.name}")
            continue
        path.write_text(generate(), encoding="utf-8")
        # Compose bind-mounts these files as-is, and the containers run as non-root users,
        # so they must be readable by others. Fine for local dev; never do this in production.
        os.chmod(path, 0o644)
        print(f"created  {path.name}")


if __name__ == "__main__":
    main()
