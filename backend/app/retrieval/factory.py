"""Builds the enabled scholarly providers from config.

``settings.enabled_providers`` defaults to PubMed alone. That is Phase 1's
design, not an oversight: one API learned properly beats four half-integrated,
and PubMed is the one carrying study-type metadata.
"""

from __future__ import annotations

import logging

import httpx

from app.config import Settings
from app.retrieval.base import ScholarlyProvider
from app.retrieval.providers import (
    EuropePMCProvider,
    OpenAlexProvider,
    SemanticScholarProvider,
)
from app.retrieval.pubmed import PubMedProvider

logger = logging.getLogger(__name__)

USER_AGENT = "EvidWell/0.1 (evidence-checked wellness content)"


def build_http_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=settings.http_timeout_seconds,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )


def build_providers(
    settings: Settings, http: httpx.AsyncClient
) -> list[ScholarlyProvider]:
    """Instantiate each enabled provider.

    Raises:
        ValueError: an unknown provider name — surfaced loudly rather than
            silently skipped, because a typo in config would otherwise look
            like "that source has no literature".
    """
    providers: list[ScholarlyProvider] = []
    for name in settings.enabled_providers:
        match name.strip().lower():
            case "pubmed":
                providers.append(PubMedProvider(http, settings.pubmed_api_key or None))
            case "europe_pmc":
                providers.append(EuropePMCProvider(http))
            case "semantic_scholar":
                providers.append(
                    SemanticScholarProvider(http, settings.semantic_scholar_api_key or None)
                )
            case "openalex":
                providers.append(OpenAlexProvider(http, settings.openalex_mailto or None))
            case unknown:
                raise ValueError(
                    f"unknown provider {unknown!r} in enabled_providers; expected "
                    "pubmed, europe_pmc, semantic_scholar or openalex"
                )

    if not providers:
        raise ValueError("enabled_providers is empty; retrieval would find nothing")

    logger.info("retrieval providers: %s", ", ".join(p.source_api for p in providers))
    return providers
