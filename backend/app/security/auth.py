"""Authentication for the editorial console.

Deliberately minimal — one admin, JWT bearer, no invite flow — but not a shared
password. ``reviewed_by`` is a real foreign key to ``users``, so "who approved
this article" stays answerable indefinitely. That is the part that must not be
compromised for convenience; everything else here can be replaced later without
touching the audit trail.

Argon2id rather than bcrypt: no 72-byte silent truncation, and the memory-hard
parameters are current best practice.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.domain.enums import UserRole

logger = logging.getLogger(__name__)

ACCESS_TOKEN_TTL = timedelta(hours=12)
JWT_ALGORITHM = "HS256"

_hasher = PasswordHasher()

#: A real Argon2 hash of a throwaway value. Verified when a login names an
#: unknown email, so the response takes the same time as a wrong password —
#: otherwise timing enumerates valid reviewer accounts.
DUMMY_HASH = _hasher.hash("timing-equalisation-placeholder")


class AuthError(Exception):
    """Authentication failed.

    Always surfaces as 401 with a generic message. Never distinguishes expired
    from malformed from unknown-user to the client.
    """


@dataclass(frozen=True, slots=True)
class AuthenticatedReviewer:
    """Resolved from a bearer token. Passed into every review mutation."""

    id: str
    email: str
    display_name: str
    role: UserRole


def hash_password(plaintext: str) -> str:
    return _hasher.hash(plaintext)


def verify_password(plaintext: str, hashed: str) -> bool:
    """Constant-time verification.

    Returns False rather than raising on a mismatch, so callers cannot
    accidentally distinguish "wrong password" from "malformed hash" in a
    response.
    """
    try:
        return _hasher.verify(hashed, plaintext)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def equalise_timing() -> None:
    """Burn one hash verification for an unknown email.

    Call this on the not-found branch of login so both branches cost the same.
    """
    verify_password("wrong", DUMMY_HASH)


def create_access_token(reviewer: AuthenticatedReviewer, secret: str) -> tuple[str, int]:
    """Issue a JWT. Returns (token, expires_in_seconds).

    Claims are ``sub``, ``role``, ``exp``, ``iat`` — deliberately no email or
    display name. Those change, and a token asserting a stale one is worse than
    a token that requires a lookup.
    """
    now = datetime.now(UTC)
    expires = now + ACCESS_TOKEN_TTL
    payload = {
        "sub": reviewer.id,
        "role": str(reviewer.role),
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    token = jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)
    return token, int(ACCESS_TOKEN_TTL.total_seconds())


def decode_token_subject(token: str, secret: str) -> str:
    """Verify the signature and return the user id.

    Deliberately does **not** trust the token's claims beyond identity. The
    caller re-loads the user to confirm they still exist and are active — a
    signature-valid token for a deactivated reviewer must not authenticate, and
    a claims-only check would allow exactly that for the token's full 12-hour
    life.

    Raises:
        AuthError: expired, malformed, or bad signature.
    """
    try:
        payload = jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise AuthError("invalid token") from exc

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise AuthError("invalid token")
    return subject
