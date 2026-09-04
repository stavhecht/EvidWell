"""Application settings, loaded from the environment.

``embedding_dim`` is the one to be careful with: it must match both the active
embedding provider's output width and the ``vector(N)`` column in the applied
migration. ``validate_embedding_dim`` asserts the first two agree at startup,
because a mismatch otherwise surfaces as an opaque pgvector error at the first
write — or worse, as silently degraded retrieval.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), extra="ignore", case_sensitive=False
    )

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
    cors_origins: list[str] = ["http://localhost:5173"]

    # --- generative provider ---
    #: 'ollama' | 'anthropic'. Ollama is the default while the pipeline is being
    #: built: every generative call runs locally, costs nothing, and needs no
    #: key. Set LLM_PROVIDER=anthropic for real deployment — the hosted client
    #: is fully wired and nothing else changes. See llm/factory.py.
    llm_provider: str = "ollama"          # "ollama" | "anthropic"

    # --- Ollama (local, free) ---
    ollama_base_url: str = "http://localhost:11434"
    #: Minutes, not seconds: a cold model is loaded into memory before the first
    #: token, and a CPU-only synthesis turn over eight abstracts is slow.
    ollama_timeout_seconds: float = 600.0
    #: Both default to the same small model. Extraction is mechanical enough to
    #: survive it; synthesis is where a bigger local model
    #: (qwen2.5:14b-instruct, llama3.3:70b) actually shows, since that call
    #: carries invariants #2 and #3. Pull whatever you set here first —
    #: `ollama pull llama3.1:8b`.
    ollama_extraction_model: str = "llama3.1:8b"
    ollama_synthesis_model: str = "llama3.1:8b"

    # --- Claude (hosted; used when llm_provider='anthropic') ---
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
    embedding_provider: str = "ollama"  # 'ollama' | 'voyage' | 'openai'
    voyage_api_key: str = ""
    openai_api_key: str = ""
    #: mxbai-embed-large is the local default because it is 1024-d, the same
    #: width as voyage-4 — so the move from local to hosted embeddings is a
    #: re-embed but not a migration. A 768-d model is both.
    ollama_embedding_model: str = "mxbai-embed-large"
    #: Only consulted for a model absent from
    #: ``llm/embeddings/ollama.py::KNOWN_DIMENSIONS``; prefer adding it there,
    #: so startup checks the width instead of taking your word for it.
    ollama_embedding_dim: int = 1024
    #: Must equal the provider's dimension AND the migration's vector(N).
    embedding_dim: int = 1024

    # --- scholarly providers ---
    pubmed_api_key: str = ""
    semantic_scholar_api_key: str = ""
    openalex_mailto: str = ""
    #: Providers enabled for retrieval. Phase 1 runs PubMed alone by design —
    #: one API learned properly beats four half-integrated.
    enabled_providers: list[str] = ["pubmed"]
    http_timeout_seconds: float = 30.0
    #: How many times a throttled provider call is retried before it is
    #: recorded as a failure. Two rides out a burst; more would let one
    #: throttled provider hold a run open indefinitely, and the worst case is
    #: already this many waits of up to the cap below. Per-provider request
    #: rates are constants in ``retrieval/factory.py``, not settings — they are
    #: facts about each API rather than things to tune.
    provider_max_retries: int = 2
    provider_max_retry_wait_seconds: float = 10.0

    # --- retrieval tuning ---
    #: Ranked sources kept **per claim**, deduplicated across claims when the
    #: synthesis prompt is assembled. This is the one knob that decides how many
    #: sources an article *can* cite, so it moved 8 -> 12 when articles grew
    #: from three beats to a three-to-five minute read.
    #:
    #: Raising it further means raising `SYNTHESIS_NUM_CTX` in
    #: `llm/ollama_client.py` with it. Ollama's context overflow is silent: the
    #: front of the prompt is dropped, so sources vanish while the instruction
    #: to cite them survives, and the run fails as `hallucinated_handle` with
    #: nothing pointing at the real cause.
    retrieval_top_k: int = 12
    retrieval_max_candidates_per_claim: int = 50
    retrieval_min_year: int | None = None

    # --- pipeline ---
    worker_poll_interval_seconds: float = 5.0
    #: How many times a run may be *started* before a retryable failure is
    #: treated as permanent. Only failures that set ``StageError.retryable``
    #: consume it — a malformed draft is not retried at any budget. Backoff
    #: between attempts is ``orchestrator.RETRY_DELAYS``. A run whose worker was
    #: killed spends the same budget: the attempt is counted when it is claimed.
    pipeline_max_attempts: int = 3
    #: How often the worker reports that its in-flight run is still alive.
    worker_heartbeat_seconds: float = 15.0
    #: Silence after which a `running` run is treated as abandoned. Must stay
    #: comfortably above the heartbeat interval — see the validator below.
    worker_stale_after_seconds: float = 120.0
    #: How often the worker looks for abandoned runs. Checked between polls, so
    #: a worker busy with its own run does not sweep; with one worker that is
    #: harmless, since the only run it could recover is the one it is running.
    worker_sweep_interval_seconds: float = 60.0

    # --- article media ---
    #: Per-file ceiling. Generous for a photo, small enough that a stray upload
    #: cannot bloat the database or the request buffer. The reviewer sees the
    #: limit in the error, so raising it is a config change, not a code one.
    #:
    #: There is no `media_root` any more: image bytes live in `media_objects`,
    #: so the store needs a session rather than a path and there is nothing
    #: left to configure. See migrations/0004_media_objects.sql.
    media_max_bytes: int = 8 * 1024 * 1024

    # --- article imagery (generated) ---
    #: 'huggingface' | 'none'. Set to 'none' to draft text-only articles
    #: without touching the provider at all.
    image_provider: str = "huggingface"
    #: The Hugging Face token, from IMAGE_GEN_KEY. It needs the "Make calls to
    #: Inference Providers" permission — a plain read token authenticates and
    #: then 403s on the first render. **An empty key is not a startup error**:
    #: ``imagery/factory.py`` returns no client and drafts come out text-only,
    #: because a picture is decorative and the API must still boot for review,
    #: publishing and the public feed.
    image_gen_key: str = ""
    image_model: str = "black-forest-labs/FLUX.1-schnell"
    #: Which inference provider serves the model. 'auto' picks the fastest
    #: available and fails over; name one (nscale, fal-ai, replicate, together)
    #: for consistent latency and billing.
    image_inference_provider: str = "auto"
    #: Generous: two renders run back to back and a cold provider can take a
    #: while to answer the first one.
    image_timeout_seconds: float = 120.0
    #: FLUX.1-schnell is distilled for 1-4 steps at guidance 0.0 — these are the
    #: model card's numbers, not tuning knobs. Raising steps costs credits and
    #: buys nothing on a *schnell* checkpoint.
    image_steps: int = 4
    image_guidance_scale: float = 0.0
    #: The article's lead image: landscape, for a 760px prose column.
    image_lead_width: int = 1216
    image_lead_height: int = 832
    #: The feed tile's cover: portrait. 3:4 on purpose — ``tileRatio()`` in
    #: ``frontend/src/features/feed/ArticleCard.tsx`` hashes a slug into 3/4,
    #: 1/1, 4/5 or 2/3, and 3:4 sits nearest the middle of that spread, so
    #: ``object-cover`` takes a modest centre crop on every tile rather than a
    #: severe one on half of them.
    image_cover_width: int = 864
    image_cover_height: int = 1152

    @field_validator("cors_origins", "enabled_providers", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Allow comma-separated env values as well as JSON lists."""
        if isinstance(value, str) and not value.strip().startswith("["):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator(
        "image_lead_width",
        "image_lead_height",
        "image_cover_width",
        "image_cover_height",
    )
    @classmethod
    def _dimension_is_a_multiple_of_16(cls, value: int, info: ValidationInfo) -> int:
        """Diffusion latents are 1/8 scale over a patch grid; 16 is the safe step.

        Providers disagree about what to do with a size that does not divide:
        some 400, some silently round and return an image that is not the shape
        that was asked for — which reaches the feed as a tile cropped wrong,
        with nothing anywhere saying why. Refusing at startup names the number.
        """
        if value <= 0 or value % 16:
            raise ValueError(
                f"{info.field_name} must be a positive multiple of 16, got {value}"
            )
        return value

    @model_validator(mode="after")
    def _heartbeat_leaves_room_to_miss_one(self) -> Settings:
        """The staleness window must survive a missed beat or two.

        Set too close together, one slow UPDATE — a checkpoint, a brief lock
        wait — declares a live run abandoned, and the sweep requeues work that
        is still executing. Three intervals means it takes three consecutive
        misses, which is a dead process rather than a hiccup.
        """
        floor = self.worker_heartbeat_seconds * 3
        if self.worker_stale_after_seconds < floor:
            raise ValueError(
                f"worker_stale_after_seconds ({self.worker_stale_after_seconds}) must be "
                f"at least 3x worker_heartbeat_seconds ({self.worker_heartbeat_seconds}) "
                f"= {floor}s, or a single missed heartbeat requeues a live run."
            )
        return self

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
