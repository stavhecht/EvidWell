"""Stage 4 — the RAG generation (LLM call 2)."""

from __future__ import annotations

import logging

from app.domain.contracts import PromptSource, SynthesisInput, SynthesisOutput
from app.llm.base import LLMError, LLMResult, SynthesisClient
from app.pipeline.stages import PipelineContext, StageError, StageName
from app.services.no_evidence import no_evidence_draft

logger = logging.getLogger(__name__)

#: Fraction of the offered sources a draft must actually cite before it is left
#: alone. Below this it is re-prompted once, naming the handles it skipped.
#:
#: **This is not a length rule, and it deliberately stopped being one.** It was
#: briefly a word-count floor aimed at a three-minute read, which was the wrong
#: target twice over: it fired on essentially every run because no local model
#: this machine can hold writes 700 words, and length was never the defect
#: anyway. The defect is a draft that was handed 24 systematic reviews and wrote
#: about three of them — measured, repeatedly — and the one re-prompt that
#: clearly worked took a draft from 3 cited sources to 24.
#:
#: Aiming at coverage instead keeps DESIGN.md §6 intact rather than bending it:
#: there is no floor on how long an article may be, and a thoroughly-cited short
#: article is now a correct output that is never re-prompted. It also restates a
#: rule the synthesis prompt already gives — "every source bearing on a claim
#: deserves a sentence" — rather than adding a competing one.
MIN_CITED_FRACTION = 0.5

#: Sources below which a thinly-cited draft is left alone. The prompt already
#: says "six or more usable sources supports 3 to 5 sections"; reusing its number
#: keeps this a nudge toward a rule the model was given.
#:
#: This is the anti-padding guarantee. A draft citing one of two sources has
#: nothing more to say, and asking it for more can only invent it. Only a draft
#: sitting on plenty of unused evidence is worth a second ask.
SECTIONS_EXPECTED_FROM = 6


class SynthesizeStage:
    name = StageName.SYNTHESIZE

    def __init__(self, client: SynthesisClient) -> None:
        self._client = client

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        """Write the article, grounded strictly in the ranked abstracts.

        The ``SynthesisInput`` built here is carried forward on the context
        unchanged, because VALIDATE needs the *exact* handle set rendered into
        the prompt. Rebuilding it in the validate stage would look equivalent
        and would quietly break invariant #2.

        This stage does not check its own output. Schema conformance is
        guaranteed by structured outputs; grounding is VALIDATE's job. Keeping
        them apart is what stops the generator from also being the approver.

        **Zero sources takes the template branch, not an error.** RETRIEVE
        deliberately lets an empty candidate set through so that a fringe trend
        can be reported honestly; failing here would discard that at the last
        step and turn the most useful thing this product can say into a
        ``draft_failed`` run. See ``services/no_evidence.py``.
        """
        if ctx.extraction is None:
            raise StageError(self.name, "extraction stage did not run")

        payload = self._build_input(ctx)

        if not payload.sources:
            return self._no_evidence(ctx, payload)

        try:
            result = await self._client.synthesize(payload)
        except LLMError as exc:
            # Synthesis carries the pipeline's largest token budget, so a
            # failure here is the most expensive thing that can happen and the
            # one least worth reporting as free. See LLMError.usage.
            ctx.record_usage(self.name, exc.model, exc.usage)
            raise StageError(self.name, str(exc)) from exc

        ctx.record_usage(self.name, result.model, result.usage)

        first_cited = len(result.output.body.cited_handles())
        if _is_thin(result.output, payload):
            retried = True
            result = await self._use_more_sources(ctx, payload, result)
        else:
            retried = False

        ctx.record_metrics(
            self.name,
            {
                "verdict": str(result.output.verdict),
                "sources_in_prompt": len(payload.sources),
                "coverage_retry": retried,
                "first_draft_handles_cited": first_cited,
                # Body handles, matching ``was_cited`` in PERSIST. Counting
                # the union with the ``citations`` list reported sources the
                # article never refers to as cited, which is the one direction
                # this metric must not err in.
                "handles_cited": len(result.output.body.cited_handles()),
                "handles_listed_not_written": len(
                    result.output.all_cited_handles()
                    - result.output.body.cited_handles()
                ),
                "body_words": _body_words(result.output),
            },
        )

        return ctx.model_copy(
            update={"synthesis_input": payload, "draft": result.output}
        )

    async def _use_more_sources(
        self,
        ctx: PipelineContext,
        payload: SynthesisInput,
        result: LLMResult[SynthesisOutput],
    ) -> LLMResult[SynthesisOutput]:
        """Ask once for the skipped sources, and keep the first draft if refused.

        **This can only improve the outcome, never fail the run.** A draft is
        already in hand and it is publishable — it passed the same contract the
        second one has to. So a re-prompt that raises is logged and discarded
        rather than propagated: turning a usable article into a failed run would
        be strictly worse than shipping it, and would hand this nudge a veto over
        the pipeline that it has no business having. Its tokens are still
        recorded, because they were still spent.

        **The second draft is kept only if it cites more sources than the
        first**, which is the same question the re-prompt asked. Keeping it
        unconditionally was tried and is wrong: live, a draft citing 3 of 13 came
        back citing 1, and separately a 152-word ``mixed`` draft came back at
        ``no_evidence`` while citing everything. Nothing downstream catches the
        latter — ``no_evidence`` is exempt from the cited-beat rule and sits
        below every ceiling, so it validates cleanly and publishes.

        Note the test is coverage, not length. A second draft that says more
        about fewer papers is not what was asked for, and a shorter draft that
        genuinely works through more of the evidence is.
        """
        cited = len(result.output.body.cited_handles())
        unused = sorted(
            payload.handle_set - result.output.body.cited_handles(),
            key=lambda handle: int(handle[1:]),
        )
        logger.info(
            "draft cites %d of %d sources (%d unused); asking once for the rest",
            cited,
            len(payload.sources),
            len(unused),
        )

        try:
            retry = await self._client.synthesize(
                payload, feedback=_coverage_feedback(cited, unused)
            )
        except LLMError as exc:
            ctx.record_usage(self.name, exc.model, exc.usage)
            logger.warning(
                "coverage re-prompt failed (%s); keeping the first draft (%d cited)",
                exc,
                cited,
            )
            return result

        ctx.record_usage(self.name, retry.model, retry.usage)
        improved = len(retry.output.body.cited_handles())
        if improved <= cited:
            logger.info(
                "coverage re-prompt cited %d of %d (was %d); keeping the first draft",
                improved,
                len(payload.sources),
                cited,
            )
            return result

        logger.info(
            "coverage re-prompt cited %d of %d (was %d)",
            improved,
            len(payload.sources),
            cited,
        )
        return retry

    def _no_evidence(
        self, ctx: PipelineContext, payload: SynthesisInput
    ) -> PipelineContext:
        """Take the deterministic branch: no model call, no tokens, no cost.

        Records *why* the source list was empty. The two causes are the same
        sentence to a reader and different problems to us: nothing published on
        the topic is the product working as intended, whereas candidates
        retrieved and then filtered away is a signal that ``min_year`` or the
        grade floor is tighter than the corpus can satisfy. Distinguishing them
        after the fact means reading two stages of metrics, so it is recorded
        here where both numbers are in hand.
        """
        retrieved = sum(len(papers) for papers in ctx.candidates.values())
        cause = "no_candidates_retrieved" if retrieved == 0 else "all_candidates_filtered"

        draft = no_evidence_draft(payload.product, payload.target_claims)

        logger.info(
            "no usable sources for %r (%s, %d retrieved); "
            "writing the deterministic no-evidence article",
            payload.product,
            cause,
            retrieved,
        )

        ctx.record_metrics(
            self.name,
            {
                "verdict": str(draft.verdict),
                "sources_in_prompt": 0,
                "handles_cited": 0,
                "handles_listed_not_written": 0,
                "body_words": _body_words(draft),
                "model_called": False,
                # Recorded as False rather than omitted so that "how often does
                # the coverage nudge fire?" is one query over every run, not one
                # that has to know which branch each row came from.
                "coverage_retry": False,
                "no_evidence_cause": cause,
                "candidates_retrieved": retrieved,
            },
        )

        return ctx.model_copy(update={"synthesis_input": payload, "draft": draft})

    @staticmethod
    def _build_input(ctx: PipelineContext) -> SynthesisInput:
        """Flatten per-claim ranked sources into one handle-ordered list.

        Deduplicated by handle: a source backing two claims appears once in the
        prompt. Presenting it twice would invite the model to cite it as two
        separate findings.
        """
        assert ctx.extraction is not None

        by_handle: dict[str, PromptSource] = {}
        for claim, ranked in ctx.ranked.items():
            for entry in ranked:
                if (existing := by_handle.get(entry.citation_handle)) is not None:
                    # Still one entry in the prompt — but it now records every
                    # claim it answers, which is what the per-claim quorum
                    # counts against. Dropping the second claim outright, as
                    # this did, made a shared source invisible to one of them.
                    if claim not in existing.claims:
                        existing.claims.append(claim)
                    continue
                by_handle[entry.citation_handle] = PromptSource(
                    handle=entry.citation_handle,
                    title=entry.paper.title,
                    abstract=entry.paper.abstract,
                    journal=entry.paper.journal,
                    year=entry.paper.year,
                    study_type=entry.paper.study_type,
                    source_id=entry.source_id,
                    claims=[claim],
                )

        ordered = sorted(by_handle.values(), key=lambda s: int(s.handle[1:]))
        return SynthesisInput(
            product=ctx.extraction.product,
            target_claims=ctx.extraction.target_claims,
            sources=ordered,
        )


def _body_words(draft: SynthesisOutput) -> int:
    """Words of article body, recorded as a metric and never used as a target.

    Nothing branches on this. It is here so that "how long are articles coming
    out?" is answerable from ``pipeline_stage_runs`` without re-parsing the
    documents — which is how the 66-to-266-word problem was found in the first
    place. A *threshold* on it was tried and removed: see ``MIN_CITED_FRACTION``.

    ``as_text()`` is beats plus sections, which is every block
    ``body_text_to_doc`` emits, and it still counts the ``[S1]`` markers that
    the frontend's own word count skips. Close enough for a trend line, which is
    all it is.
    """
    return len(draft.body.as_text().split())


def _is_thin(draft: SynthesisOutput, payload: SynthesisInput) -> bool:
    """Ignoring most of its sources, *and* given plenty. Both halves required."""
    if len(payload.sources) < SECTIONS_EXPECTED_FROM:
        return False
    cited = len(draft.body.cited_handles())
    return cited < len(payload.sources) * MIN_CITED_FRACTION


def _coverage_feedback(cited: int, unused: list[str]) -> str:
    """The note appended to the user turn on a re-prompt.

    Every sentence points at the *unused sources*, and the explicit refusal of
    padding is load-bearing: an instruction to write more, handed to a model with
    nothing left to say, produces exactly the manufactured nuance DESIGN.md §6
    refuses. Nothing here names a word count, because nothing here wants one.
    """
    return (
        f"That draft cited {cited} of the sources above and left "
        f"{len(unused)} unused: {', '.join(unused)}.\n\n"
        "Go back through those and give each one a sentence: what it measured, "
        "in whom, and what it found, with its handle. Take every claim in turn "
        "and check it has sources cited against it.\n\n"
        "Do not pad. Every sentence you add must state a specific finding from "
        "one of the sources above and carry its handle. If a source does not "
        "bear on any claim, say so and cite it there. If a source genuinely has "
        "nothing to add, leave it out: an article that works through the "
        "evidence honestly is the goal, not a longer one."
    )
