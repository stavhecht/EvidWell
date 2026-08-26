"""Stage 5 — draw the article's pictures, or don't.

**This stage cannot fail a run, and that is the whole of its error handling.**
The picture is decorative. An article without one renders as a typographic
tile, which is the design's resting state and which a reader cannot tell from
an editorial choice — so a provider outage that failed the run would trade a
complete, citable article for no article at all, in exchange for a decoration.

That is the deliberate *opposite* of ``retrieval/throttle.py``'s rule, and the
two are worth holding side by side, because the difference is not "how
important is this stage". Recall lost to a 429 produces a more cautious verdict,
which reads as a correct answer: it is invisible unless the stage fails loudly.
A missing picture is visible on the reviewer's very next screen and on every
tile. The rule is *fail on the degradations nobody can see*, and this one
everybody can.

So it is nearer to ``services/no_evidence.py``: record why, in the metrics, and
carry on. ``cause`` is what separates "no key is configured" from "the provider
503'd" — the same blank tile, and different problems.

**Nothing here enters the token ledger.** ``record_usage`` is never called. An
image is billed per image by a provider nobody has priced, while
``pipeline_stage_runs`` prices four token columns; writing a model id against
four zeros is exactly the "unpriced model reads as free" mistake
``llm/pricing.py`` exists to refuse. Count, bytes, latency and model go into
``metrics`` instead. Say "not measured", not "free" — the standing embeddings
already have.

**It writes files, not rows.** Nothing it does sits inside the pipeline
transaction, so the orchestrator's rollback cannot un-write it. That is already
the accepted position: the store is content-addressed and orphan collection is
deferred (``services/media.py``), so a file from a run that later failed is the
same orphan as an image a reviewer dropped from a draft.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.imagery.base import ImageClient, ImageError
from app.pipeline.stages import PipelineContext, StageError, StageName
from app.services.illustration import generate_illustration, seed_for_run

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IllustrationConfig:
    """Where the pictures go and how big they are.

    A narrow config rather than the whole ``Settings`` object, the same shape
    as ``RerankConfig``: a stage handed Settings can reach anything, and a
    stage that can reach anything eventually does.
    """

    media_root: Path
    max_bytes: int
    lead_size: tuple[int, int]
    cover_size: tuple[int, int]
    #: Whether imagery was asked for at all. Read only when the client is
    #: ``None``, to tell "switched off" from "no key configured" — two states
    #: that produce an identical article and want opposite responses.
    enabled: bool


class IllustrateStage:
    """Stage 5 of 7. Never raises for an image failure."""

    name = StageName.ILLUSTRATE

    def __init__(self, client: ImageClient | None, config: IllustrationConfig) -> None:
        self._client = client
        self._config = config

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        """Generate both frames and attach them to the context.

        The one exception to "never raises": a missing draft or extraction
        means SYNTHESIZE did not run, which is a broken pipeline rather than a
        failed image. Reporting that as ``cause: "unexpected"`` and continuing
        would hide an assembly bug behind a decorative feature.
        """
        if ctx.draft is None or ctx.extraction is None:
            raise StageError(self.name, "synthesis stage did not run")

        if self._client is None:
            return self._skip(ctx, "disabled" if not self._config.enabled else "not_configured")

        started = time.monotonic()
        seed = seed_for_run(ctx.run_id)
        try:
            illustration = await generate_illustration(
                self._client,
                product=ctx.extraction.product,
                topic=ctx.topic,
                # None, always, on this path: `subject` is reviewer-set and the
                # article row does not exist yet. The prompt builder infers
                # which objects to photograph from the text instead — which is
                # why the actives are worth passing. See imagery/prompt.py.
                subject=None,
                ingredients=" ".join(ctx.extraction.ingredients),
                lead_size=self._config.lead_size,
                cover_size=self._config.cover_size,
                media_root=self._config.media_root,
                max_bytes=self._config.max_bytes,
                seed=seed,
            )
        except ImageError as exc:
            logger.warning("illustration failed for run %s: %s", ctx.run_id, exc)
            return self._skip(
                ctx, "provider_error", detail=str(exc), seed=seed, started=started
            )
        except Exception as exc:
            # Broader than this codebase's usual instinct, and deliberate: a bug
            # in our own prompt builder or encoder must not fail every run for
            # a decoration. The traceback still reaches the log and the type
            # still reaches the metrics, so it is recorded rather than
            # swallowed.
            logger.exception("illustration raised unexpectedly for run %s", ctx.run_id)
            return self._skip(
                ctx,
                "unexpected",
                detail=f"{type(exc).__name__}: {exc}",
                seed=seed,
                started=started,
            )

        ctx.record_metrics(
            self.name,
            {
                # 0 or 2, never 1 — this stage always asks for both frames, and
                # generate_illustration writes nothing until both are stored.
                # A reviewer can later redraw one alone; that is the console's
                # doing and does not touch this run's metrics.
                "generated": 2,
                "cause": None,
                # Read off the lead because provenance lives on the frames now.
                # On this path the two are identical by construction: one call,
                # one prompt, one seed.
                "model": illustration.lead.model,
                "seed": seed,
                "lead_src": illustration.lead.src,
                "cover_src": illustration.cover.src,
                "prompt": illustration.lead.prompt,
                "duration_ms": _elapsed_ms(started),
            },
        )
        return ctx.model_copy(update={"illustration": illustration})

    def _skip(
        self,
        ctx: PipelineContext,
        cause: str,
        *,
        detail: str | None = None,
        seed: int | None = None,
        started: float | None = None,
    ) -> PipelineContext:
        """Record why there is no picture and hand the context back untouched.

        Untouched, not copied: nothing changed. ``ctx.illustration`` stays
        ``None`` and PersistStage writes the article without an image node.
        """
        metrics: dict[str, Any] = {
            "generated": 0,
            "cause": cause,
            "detail": detail,
            "model": None,
            "seed": seed,
        }
        if started is not None:
            metrics["duration_ms"] = _elapsed_ms(started)
        ctx.record_metrics(self.name, metrics)
        return ctx


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
