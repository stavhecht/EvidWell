"""FastAPI dependencies: database sessions and the auth gate."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_session_factory
from app.domain.enums import UserRole
from app.domain.models import User
from app.security.auth import AuthenticatedReviewer, AuthError, decode_token_subject

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


async def require_admin(reviewer: ReviewerDep) -> AuthenticatedReviewer:
    """Admin-only gate. Unused in the MVP; the seam exists for user management."""
    if reviewer.role is not UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required"
        )
    return reviewer
