"""Application settings, loaded from the environment.

``embedding_dim`` is the one to be careful with: it must match both the active
embedding provider's output width and the ``vector(N)`` column in the applied
migration. ``validate_embedding_dim`` asserts the first two agree at startup,
because a mismatch otherwise surfaces as an opaque pgvector error at the first
write — or worse, as silently degraded retrieval.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

#: A list field that accepts `a,b,c` as well as a JSON array.
#:
#: ``NoDecode`` is load-bearing, not decoration. pydantic-settings treats any
#: complex-typed field as JSON and calls ``json.loads`` inside the dotenv
#: *source*, which is upstream of every validator — so a plain
#: ``mode="before"`` validator never sees the value, and `FOO=a,b` raises
#: SettingsError before it can be split. ``NoDecode`` suppresses that decode and
#: hands the raw string to `_split_csv` below.
CsvList = Annotated[list[str], NoDecode]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), extra="ignore", case_sensitive=False
    )

    environment: str = "local"
    debug: bool = False

    # --- database ---
    database_url: str = "postgresql+asyncpg://evidwell:evidwell@localhost:5433/evidwell"
    db_echo: bool = False

    # --- auth ---
    jwt_secret: str = Field(
        default="dev-only-insecure-secret-change-me-32-chars",
        min_length=32,
        description="HS256 signing key",
    )
    cors_origins: CsvList = ["http://localhost:5173"]

    # --- Claude ---
    anthropic_api_key: str = ""
    #: Split by job, not by tier-for-its-own-sake. Extraction is a short,
    #: mechanical structured response. Synthesis is where invariants #2 and #3
    #: are produced, so it keeps the stronger model — raise it back to
    #: "claude-opus-5" if drafts start failing citation or grade validation.
    #: Measured, not assumed: claude-haiku-4-5 left `ingredients` empty in 9 of
    #: 10 samples, and an empty ingredient list silently unanchors the PubMed
    #: query (query_builder._compose falls back to claim keywords, turning
    #: "ashwagandha for stress" into a search for stress in general).
    #: claude-sonnet-5 was 0/10 on the same test. Do not lower this without
    #: re-running that check.
    extraction_model: str = "claude-sonnet-5"
    synthesis_model: str = "claude-sonnet-5"

    # --- embeddings ---
    embedding_provider: str = "voyage"  # 'voyage' | 'openai'
    voyage_api_key: str = ""
    openai_api_key: str = ""
    #: Must equal the provider's dimension AND the migration's vector(N).
    embedding_dim: int = 1024

    # --- scholarly providers ---
    pubmed_api_key: str = ""
    semantic_scholar_api_key: str = ""
    openalex_mailto: str = ""
    #: Providers enabled for retrieval. Phase 1 runs PubMed alone by design —
    #: one API learned properly beats four half-integrated.
    enabled_providers: CsvList = ["pubmed"]
    http_timeout_seconds: float = 30.0

    # --- retrieval tuning ---
    retrieval_top_k: int = 8
    retrieval_max_candidates_per_claim: int = 50
    retrieval_min_year: int | None = None

    # --- pipeline ---
    worker_poll_interval_seconds: float = 5.0
    worker_concurrency: int = 1

    @field_validator("cors_origins", "enabled_providers", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Allow comma-separated env values as well as JSON lists.

        These fields are ``CsvList``, so pydantic-settings hands the raw string
        over undecoded and both forms have to be handled here — including the
        JSON one, which nothing upstream will parse any more.
        """
        if not isinstance(value, str):
            return value

        text = value.strip()
        if text.startswith("["):
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"not valid JSON: {value!r}") from exc
        return [item.strip() for item in text.split(",") if item.strip()]

    def validate_embedding_dim(self, provider_dimension: int) -> None:
        """Fail fast when the configured width disagrees with the provider.

        Raises:
            RuntimeError: naming both numbers, because the fix depends on which
                one is wrong — re-embedding the cache or editing the migration.
        """
        if provider_dimension != self.embedding_dim:
            raise RuntimeError(
                f"embedding_dim mismatch: settings say {self.embedding_dim}, "
                f"provider '{self.embedding_provider}' emits {provider_dimension}. "
                "The vector(N) column must match the provider; changing it "
                "requires a migration and a full re-embed of `sources`."
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached so the environment is read once per process."""
    return Settings()
