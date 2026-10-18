"""Secrets come from mounted files, never from source code.

`read_secret("DB_PASSWORD")` checks `DB_PASSWORD_FILE` first (Docker/Kubernetes secrets, or AWS
Secrets Manager synced into a file by the External Secrets / CSI driver), then the plain env var.
"""

import os
from pathlib import Path


def read_secret(name: str, default: str | None = None) -> str | None:
    path = os.getenv(f"{name}_FILE")
    if path:
        return Path(path).read_text(encoding="utf-8").strip()
    return os.getenv(name, default)


def database_url(default: str) -> str:
    """DATABASE_URL without a password, plus the password injected from DB_PASSWORD(_FILE)."""
    url = os.getenv("DATABASE_URL", default)
    password = read_secret("DB_PASSWORD")
    if password:
        from sqlalchemy.engine import make_url

        url = make_url(url).set(password=password).render_as_string(hide_password=False)
    return url
