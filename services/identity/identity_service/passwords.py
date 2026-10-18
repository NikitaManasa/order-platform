"""Password hashing with scrypt (memory-hard, in the standard library).

Stored format: scrypt$<n>$<r>$<p>$<salt b64>$<hash b64>, so cost parameters can be raised later
without breaking existing hashes.
"""

import base64
import hashlib
import hmac
import secrets

N, R, P = 2**14, 8, 1
_KEY_LEN = 32


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=_KEY_LEN)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(base64.b64decode(expected)),
        )
    except ValueError:
        return False
    return hmac.compare_digest(digest, base64.b64decode(expected))


# Hashed once so a login for an unknown email costs the same time as a wrong password,
# which stops attackers from discovering which emails are registered.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))
