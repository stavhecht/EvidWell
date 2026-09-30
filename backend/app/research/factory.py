"""Builds the research agent's providers from settings.

The only place that knows which implementation stands behind each protocol —
swapping Google Trends or DuckDuckGo for something else is a change here and
nowhere else. A provider that cannot be built is passed as ``None`` and shows
up as ``unavailable`` in the run's provider status; it never fails the build.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

import httpx

from app.config import Settings
from app.llm.base import LLMError
from app.llm.factory import build_generative_clients
from app.llm.ollama_client import build_ollama_client
from app.research.contracts import PastTopic, ResearchConfig
from app.research.graph import ResearchDeps
from app.research.providers.base import NewsProvider
from app.research.providers.google_trends import GoogleTrendsProvider
from app.research.providers.news import (
    DuckDuckGoNewsProvider,
    FallbackNewsProvider,
    NewsApiProvider,
)
from app.research.providers.scientific import (
    EuropePMCEvidenceProvider,
    PubMedEvidenceProvider,
)
from app.research.providers.web_search import SimpleWebSearchProvider, ddgs_region
from app.research.triage import OllamaTriageClient
from app.retrieval.factory import (
    build_europe_pmc_provider,
    build_pubmed_provider,
    throttled_client,
)

logger = logging.getLogger(__name__)

#: News API's free plan allows 100 requests a day; one a second is plenty.
NEWS_API_RPS = 1.0


def build_news_provider(
    settings: Settings, http: httpx.AsyncClient, region: str
) -> NewsProvider:
    """News API with DuckDuckGo news behind it, or DuckDuckGo news alone."""
    ddg = DuckDuckGoNewsProvider(region=region)
    if not settings.news_api_key:
        return ddg
    news_api = NewsApiProvider(
        throttled_client(settings, http, "news_api", NEWS_API_RPS), settings.news_api_key
    )
    return FallbackNewsProvider(news_api, ddg)


def build_research_deps(
    settings: Settings,
    http: httpx.AsyncClient,
    config: ResearchConfig,
    history: Callable[[datetime], Awaitable[list[PastTopic]]],
) -> ResearchDeps:
    region = ddgs_region(config.geo, config.language)
    pubmed = build_pubmed_provider(settings, http)
    pubmed_evidence = PubMedEvidenceProvider(pubmed)

    extraction = None
    try:
        extraction, _ = build_generative_clients(settings)
    except LLMError as exc:
        logger.warning("research: no extraction client, deep research is skipped: %s", exc)

    # Triage runs on the local synthesis model (qwen2.5 by default). On the
    # hosted path it is skipped and the raw queries are used — one small call
    # is not worth a second hosted integration.
    triage = None
    if settings.llm_provider.lower() == "ollama":
        triage = OllamaTriageClient(
            build_ollama_client(settings.ollama_base_url, settings.ollama_timeout_seconds),
            settings.ollama_synthesis_model,
        )

    embedder = None
    try:
        from app.llm.embeddings.factory import build_embedding_provider

        embedder = build_embedding_provider(settings)
    except Exception as exc:
        logger.warning(
            "research: no embedding provider; clustering and novelty skipped: %s", exc
        )

    return ResearchDeps(
        trends=GoogleTrendsProvider(
            min_interval=settings.research_trends_request_interval_seconds
        ),
        web=SimpleWebSearchProvider(),
        news=build_news_provider(settings, http, region),
        science=pubmed_evidence,
        cross_check=EuropePMCEvidenceProvider(build_europe_pmc_provider(settings, http)),
        deep_counts=pubmed_evidence,
        papers=pubmed,
        extraction=extraction,
        triage=triage,
        embedder=embedder,
        history=history,
        web_region=region,
    )
