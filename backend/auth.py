"""Password hashing, session tokens, roles and API keys for AXON Verify.

Stdlib only: PBKDF2-HMAC-SHA256 for passwords and a store-persisted HMAC
secret for signed session tokens.  No token leaves the server without the
matching session row, so a leaked signature cannot outlive a revocation.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

PBKDF2_ITERATIONS = 390_000
TOKEN_VERSION = "v1"

ROLES = ("owner", "admin", "manager", "technician", "viewer", "customer")

# role -> granted permissions
PERMISSIONS: dict[str, set[str]] = {
    "owner": {"read", "write", "verify", "certify", "manage", "audit"},
    "admin": {"read", "write", "verify", "certify", "manage", "audit"},
    "manager": {"read", "write", "verify", "certify", "audit"},
    "technician": {"read", "write"},
    "viewer": {"read"},
    "customer": {"read"},
    # API keys are machine principals: they can move evidence but cannot
    # change the organisation, issue certificates or read the audit log.
    "api_key": {"read", "write"},
}


class AuthError(ValueError):
    """Raised for authentication or authorisation failures."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    if len(password) < 8:
        raise AuthError("password must be at least 8 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), _unb64(salt), int(iterations))
        return hmac.compare_digest(candidate, _unb64(digest))
    except (ValueError, TypeError):
        return False


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_api_key(org_id: str) -> str:
    # Readable prefix helps operators identify a leaked key; the secret is random.
    return f"axv1_{org_id[-6:]}_{secrets.token_urlsafe(24)}"


def new_secret() -> str:
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class Principal:
    """An authenticated caller bound to exactly one organisation."""
    user_id: str
    org_id: str
    role: str
    email: str
    name: str
    auth_type: str  # "session" | "api_key"

    @property
    def permissions(self) -> set[str]:
        return PERMISSIONS.get(self.role, set())

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    @property
    def actor(self) -> str:
        return self.email if self.auth_type == "session" else f"api-key:{self.user_id}"


def issue_token(secret: str, user_id: str, org_id: str, ttl_seconds: int = 60 * 60 * 12,
                now: int | None = None) -> str:
    payload = {
        "v": TOKEN_VERSION,
        "sub": user_id,
        "org": org_id,
        "exp": int(now if now is not None else time.time()) + ttl_seconds,
        "iat": int(now if now is not None else time.time()),
        # Without a nonce, two logins inside the same second produce the same
        # token and collide on the session table.
        "nonce": secrets.token_urlsafe(9),
    }
    body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = _b64(hmac.new(secret.encode("utf-8"), body.encode("ascii"),
                              hashlib.sha256).digest())
    return f"{TOKEN_VERSION}.{body}.{signature}"


def read_token(secret: str, token: str, now: int | None = None) -> dict | None:
    try:
        version, body, signature = token.split(".")
        if version != TOKEN_VERSION:
            return None
        expected = _b64(hmac.new(secret.encode("utf-8"), body.encode("ascii"),
                                 hashlib.sha256).digest())
        if not hmac.compare_digest(expected, signature):
            return None
        payload = json.loads(_unb64(body))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    current = int(now if now is not None else time.time())
    if int(payload.get("exp", 0)) < current:
        return None
    return payload
