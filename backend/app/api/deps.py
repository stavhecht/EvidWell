"""FastAPI dependencies: database sessions and the auth gate."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_session_factory
from app.domain.enums import UserRole
from app.domain.models import User
from app.security.auth import AuthenticatedReviewer, AuthError, decode_token_subject
from app.security.login_throttle import InMemoryLoginThrottle, LoginThrottle

bearer_scheme = HTTPBearer(auto_error=False)

#: One generic message for every authentication failure. Distinguishing
#: "expired" from "malformed" from "unknown user" tells an attacker which half
#: of their guess was right.
_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_session() -> AsyncGenerator[AsyncSession]:
    """Request-scoped session, committed on success and rolled back on error."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

#: Process-wide, because the counters *are* the state — a per-request instance
#: would forget every failure the moment it answered. Injected as a dependency
#: rather than imported at the call site so a test can override it and a Redis
#: implementation can replace it without touching the route.
_login_throttle: LoginThrottle = InMemoryLoginThrottle()


def get_login_throttle() -> LoginThrottle:
    return _login_throttle


LoginThrottleDep = Annotated[LoginThrottle, Depends(get_login_throttle)]


def client_ip(request: Request) -> str:
    """The socket peer, and deliberately nothing else.

    ``X-Forwarded-For`` is attacker-controlled until a trusted proxy overwrites
    it. Reading it here would let anyone mint a fresh identity per request and
    bypass the IP budget entirely — worse than no limit, because the endpoint
    would look protected. Nothing sits in front of uvicorn today, so the socket
    peer is the truth.

    Behind a load balancer this must become the forwarded address, via
    ``uvicorn --proxy-headers --forwarded-allow-ips=<balancer>`` so Starlette
    trusts the header only from the balancer (DESIGN.md §10). Until then, one
    bucket for anything with no peer address, which no real request has.
    """
    return request.client.host if request.client else "unknown"


ClientIpDep = Annotated[str, Depends(client_ip)]


async def require_reviewer(
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> AuthenticatedReviewer:
    """Auth gate for every console route.

    Applied at the router level in ``api/console/routes.py``, not per handler —
    a router-level dependency cannot be forgotten when someone adds a route.

    Re-loads the user rather than trusting the token's claims, so a deactivated
    reviewer stops authenticating immediately rather than at token expiry.
    """
    if credentials is None or not credentials.credentials:
        raise _UNAUTHORIZED

    try:
        user_id = decode_token_subject(credentials.credentials, settings.jwt_secret)
    except AuthError:
        raise _UNAUTHORIZED from None

    result = await session.execute(
        select(User).where(User.id == user_id, User.is_active.is_(True))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise _UNAUTHORIZED

    return AuthenticatedReviewer(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=UserRole(user.role),
    )


ReviewerDep = Annotated[AuthenticatedReviewer, Depends(require_reviewer)]
