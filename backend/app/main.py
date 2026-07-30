"""FastAPI application factory.

Two surfaces mounted on one app, separated by prefix and by auth posture:

    /api/*                 public, unauthenticated, read-only
    /api/console/auth/*    unauthenticated (login must be reachable)
    /api/console/*         JWT required, enforced router-level

The separation is structural rather than conventional — public handlers live in
``api/public`` and cannot acquire an authenticated reviewer, console handlers
live behind a router-level dependency and cannot lose one.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.console import routes as console_routes
from app.api.public import feed as public_feed
from app.config import get_settings
from app.db import dispose_engine
from app.security.auth import AuthError
from app.services.review import ReviewError

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    settings = get_settings()

    # Fail fast on an embedding-width mismatch rather than at the first
    # pgvector write. Skipped when no key is configured, so the API can serve
    # the public feed without embedding credentials.
    if settings.voyage_api_key or settings.openai_api_key:
        from app.llm.embeddings.factory import build_embedding_provider

        build_embedding_provider(settings)
    else:
        logger.warning(
            "no embedding provider key configured; the pipeline will not run "
            "(the public feed and console are unaffected)"
        )

    yield
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    app = FastAPI(title="EvidWell API", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(public_feed.router, prefix="/api")
    app.include_router(console_routes.auth_router, prefix="/api/console")
    app.include_router(console_routes.router, prefix="/api/console")

    @app.exception_handler(ReviewError)
    async def _review_error(_: Request, exc: ReviewError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)}
        )

    @app.exception_handler(AuthError)
    async def _auth_error(_request: Request, _exc: AuthError) -> JSONResponse:
        # Deliberately generic: never tell the client which half of the
        # credential was wrong.
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Not authenticated"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    return app


app = create_app()
