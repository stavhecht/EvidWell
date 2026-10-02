"""What one case's run did, recorded so it can be scored — and re-scored — later.

Execution and scoring are separate on purpose. A case costs minutes of retrieval
and local-model time; scoring it costs seconds. Keeping every fact the scorer
needs in the trace means a metric can be fixed or added and a whole suite
re-scored from disk (``--rescore``) without running the pipeline again.

Recorded: every tool call (HTTP request, model call, embedding batch) with its
sanitised inputs, status and latency; every stage span with the stage's own
metrics; the candidate pool and ranked list of each retrieval round; every draft
the synthesis model returned; each validation report.

Not recorded: model reasoning. Both pipeline calls are structured-output calls
that return a JSON object and nothing else, so there is no hidden reasoning to
leak — the trace holds outputs, never a chain of thought.
"""

from __future__ import annotations

import time
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

#: The pipeline stage currently executing, so a tool call made deep inside a
#: provider can be attributed to the stage that caused it.
CURRENT_STAGE: ContextVar[str] = ContextVar("eval_current_stage", default="harness")

#: Search providers, as tool names. Everything the article pipeline can search.
SEARCH_TOOLS = frozenset({"pubmed", "europe_pmc", "openalex", "semantic_scholar"})


class ToolCall(BaseModel):
    seq: int
    stage: str
    tool: str
    operation: str
    request: dict[str, Any] = Field(default_factory=dict)
    #: ok | error | fault | cassette_miss
    status: str = "ok"
    http_status: int | None = None
    cached: bool = False
    #: Wall clock as this run experienced it.
    latency_ms: float = 0.0
    #: What the call cost when it was made live — the recorded value for a
    #: cassette hit. Performance metrics read this one.
    live_latency_ms: float | None = None
    result_count: int | None = None
    error: str | None = None
    fault: str | None = None
    model: str | None = None
    usage: dict[str, int] | None = None


class StageSpan(BaseModel):
    stage: str
    ordinal: int
    refine_round: int
    revise_round: int
    started_ms: float
    duration_ms: float = 0.0
    status: str = "running"
    error: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None


class PaperRecord(BaseModel):
    source_id: str
    pmid: str | None = None
    doi: str | None = None
    title: str
    abstract: str
    journal: str | None = None
    year: int | None = None
    study_type: str
    raw_study_type: str | None = None
    url: str | None = None
    source_api: str
    injected: bool = False


class RankedEntry(BaseModel):
    source_id: str
    handle: str
    cosine: float
    score: float


class RetrievalRound(BaseModel):
    round: int
    #: claim -> source ids, in the pool's order (post-dedup, pre-rank).
    candidates: dict[str, list[str]] = Field(default_factory=dict)
    #: claim -> ranked top-k, best first.
    ranked: dict[str, list[RankedEntry]] = Field(default_factory=dict)
    thin_claims: list[str] = Field(default_factory=list)


class Trace(BaseModel):
    case_id: str
    query: str
    started_at: str
    finished_at: str | None = None
    duration_ms: float = 0.0
    mode: str
    environment: dict[str, Any] = Field(default_factory=dict)

    outcome: str = "running"
    skip_reason: str | None = None
    error: dict[str, Any] | None = None

    extraction: dict[str, Any] | None = None
    papers: dict[str, PaperRecord] = Field(default_factory=dict)
    rounds: list[RetrievalRound] = Field(default_factory=list)
    excerpts: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    synthesis_input: dict[str, Any] | None = None
    #: Every synthesis call's output, in call order, tagged first / coverage /
    #: revision. The stage decides which one it keeps; ``final_draft`` is that.
    drafts: list[dict[str, Any]] = Field(default_factory=list)
    final_draft: dict[str, Any] | None = None
    validations: list[dict[str, Any]] = Field(default_factory=list)
    final_validation: dict[str, Any] | None = None
    #: Routing decisions the graph took, derived from the context each stage
    #: returned. The "agent action" half of the trace.
    decisions: list[dict[str, Any]] = Field(default_factory=list)

    stages: list[StageSpan] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stage_metrics: dict[str, Any] = Field(default_factory=dict)
    usage_by_stage: dict[str, Any] = Field(default_factory=dict)
    cassette_misses: int = 0
    notes: list[str] = Field(default_factory=list)

    #: Research-agent scenarios only.
    research: dict[str, Any] | None = None


class TraceRecorder:
    """Owns one ``Trace`` while its case runs; hands out sequence numbers."""

    def __init__(self, trace: Trace) -> None:
        self.trace = trace
        self._origin = time.monotonic()
        self._seq = 0

    @classmethod
    def start(
        cls, case_id: str, query: str, mode: str, environment: dict[str, Any]
    ) -> TraceRecorder:
        return cls(
            Trace(
                case_id=case_id,
                query=query,
                started_at=datetime.now(UTC).isoformat(timespec="seconds"),
                mode=mode,
                environment=environment,
            )
        )

    def now_ms(self) -> float:
        return (time.monotonic() - self._origin) * 1000

    def record(self, **fields: Any) -> ToolCall:
        self._seq += 1
        call = ToolCall(seq=self._seq, stage=fields.pop("stage", CURRENT_STAGE.get()), **fields)
        self.trace.tool_calls.append(call)
        if call.status == "cassette_miss":
            self.trace.cassette_misses += 1
        return call

    def begin_stage(
        self, stage: str, ordinal: int, refine_round: int, revise_round: int
    ) -> StageSpan:
        span = StageSpan(
            stage=stage,
            ordinal=ordinal,
            refine_round=refine_round,
            revise_round=revise_round,
            started_ms=self.now_ms(),
        )
        self.trace.stages.append(span)
        return span

    def end_stage(
        self,
        span: StageSpan,
        *,
        error: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        span.duration_ms = self.now_ms() - span.started_ms
        span.status = "failed" if error else "ok"
        span.error = error
        span.metrics = metrics

    def finish(self, outcome: str) -> Trace:
        self.trace.outcome = outcome
        self.trace.duration_ms = self.now_ms()
        self.trace.finished_at = datetime.now(UTC).isoformat(timespec="seconds")
        return self.trace
