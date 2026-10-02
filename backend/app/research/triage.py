"""The research agent's one model call: what each rising query is about.

Deciding that "creatine before bed" and "taking creatine at night" ask the same
thing, that "creatine" is the subject and "sleep" the outcome, and that
"taylor swift workout" is not a health question, is semantic work — the one
place in topic discovery where a model earns its cost (one call per run, on
the already-clustered queries).

**The output is checked, never trusted.** Every id must be one we sent, each
at most once; anything the model dropped falls back to the deterministic
reading of its cluster. And if the call fails outright, the whole run falls
back to that reading and says so in its notes. The model decides what a query
*means*; it never decides whether a claim is true or how strong the evidence
is — that is PubMed's job, in ``graph.py``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Protocol

from ollama import AsyncClient
from pydantic import BaseModel, ConfigDict, Field

from app.llm.base import LLMError
from app.llm.ollama_client import structured_chat
from app.research.contracts import CandidateTopic, Category
from app.research.normalize import QueryCluster, fallback_subject, query_key

logger = logging.getLogger(__name__)

TRIAGE_NUM_CTX = 8_192
TRIAGE_MAX_TOKENS = 3_000
#: Query groups per call. Measured 2026-09-30: handed 75 at once, qwen2.5:7b
#: skipped 25 and lumped 58 queries into one topic called "General Wellness and
#: Health". Small batches are what a 7B model can actually keep track of.
TRIAGE_BATCH = 25
#: A topic covering more groups than this is a model lumping unrelated queries
#: together, not a topic; its groups fall back to their raw queries.
MAX_GROUPS_PER_TOPIC = 6
#: Subjects that name no subject.
_VAGUE_SUBJECTS = frozenset({"various", "general", "multiple", "mixed", "misc", "other"})


class TriageTopic(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"description": "One article topic covering one or more queries."}
    )

    ids: list[int] = Field(min_length=1, description="Ids of the queries this topic covers.")
    canonical_topic: str = Field(
        min_length=3,
        max_length=80,
        description='Short plain-words topic, e.g. "Creatine before bed".',
    )
    category: Category
    is_wellness_topic: bool = Field(
        description="True only for a wellness, fitness, nutrition or health question a "
        "science-based article could address."
    )
    subject: str = Field(
        min_length=1,
        max_length=60,
        description='The substance, food, activity or practice, e.g. "creatine".',
    )
    outcome: str = Field(
        default="",
        max_length=60,
        description='What it is sought for, e.g. "sleep". Empty if none.',
    )
    reader_question: str = Field(
        default="", max_length=160, description="The reader's question, one sentence."
    )


class TriageOutput(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"description": "Topics for the given queries."}
    )

    topics: list[TriageTopic]


TRIAGE_SYSTEM_PROMPT = f"""\
You sort search queries that are rising on Google into article topics for a \
wellness, fitness and health publication.

Group only queries that ask about the same specific thing into one topic; most \
topics cover one or two queries. Never make a catch-all topic. For each topic \
return:
- ids: the ids of the queries it covers. Every id belongs to exactly one topic.
- canonical_topic: a short topic in plain words, like "Creatine before bed".
- category: one of {", ".join(c.value for c in Category)}. Use other \
only for a wellness topic that fits none of the rest.
- is_wellness_topic: true only when a science-based article on wellness, \
fitness, nutrition, sleep, recovery or health could address it. False for \
celebrities, brands, products for sale, shopping queries ("best running \
shoes", "cheap protein powder"), shops, news events, sports results, games, and \
recipes with no health question.
- subject: the substance, food, activity or practice the topic is about, like \
"creatine" or "zone 2 training". Use the common name.
- outcome: what people want from it, like "sleep" or "muscle growth". Leave it \
empty when the query names no outcome.
- reader_question: the reader's question in one sentence.

Only use the queries given. Do not judge whether any claim is true."""


class TriageClient(Protocol):
    async def triage(self, clusters: list[QueryCluster]) -> TriageOutput: ...


class OllamaTriageClient:
    def __init__(self, client: AsyncClient, model: str) -> None:
        self._client = client
        self._model = model

    async def triage(self, clusters: list[QueryCluster]) -> TriageOutput:
        topics: list[TriageTopic] = []
        for start in range(0, len(clusters), TRIAGE_BATCH):
            result = await structured_chat(
                self._client,
                call="research triage",
                model=self._model,
                system=TRIAGE_SYSTEM_PROMPT,
                user=build_triage_user_prompt(clusters[start : start + TRIAGE_BATCH]),
                output_format=TriageOutput,
                num_ctx=TRIAGE_NUM_CTX,
                max_tokens=TRIAGE_MAX_TOKENS,
            )
            batch: TriageOutput = result.output
            topics.extend(batch.topics)
        return TriageOutput(topics=topics)


def build_triage_user_prompt(clusters: list[QueryCluster]) -> str:
    lines = []
    for cluster in clusters:
        others = [query for query in cluster.queries if query != cluster.representative.query]
        also = f"; also: {', '.join(others[:4])}" if others else ""
        lines.append(
            f"{cluster.cluster_id}: {cluster.representative.query} "
            f"(seed: {cluster.representative.seed}{also})"
        )
    return "Queries:\n" + "\n".join(lines)


def apply_triage(
    clusters: list[QueryCluster],
    output: TriageOutput | None,
    *,
    allowed: list[Category],
) -> tuple[list[CandidateTopic], int]:
    """Candidates from the model's grouping, checked against what was sent.

    Returns the candidates and how many clusters fell back to the deterministic
    reading because the model skipped them (all of them when ``output`` is
    None). Non-wellness and out-of-category topics come back *discarded with a
    reason*, not dropped, so the desk can show what was considered.
    """
    by_id = {cluster.cluster_id: cluster for cluster in clusters}
    claimed: set[int] = set()
    candidates: list[CandidateTopic] = []

    for topic in output.topics if output is not None else []:
        ids = [i for i in dict.fromkeys(topic.ids) if i in by_id and i not in claimed]
        if (
            not ids
            or len(ids) > MAX_GROUPS_PER_TOPIC
            or topic.subject.strip().lower() in _VAGUE_SUBJECTS
        ):
            continue
        claimed.update(ids)
        members = [by_id[i] for i in ids]
        candidate = _candidate(
            members,
            canonical=topic.canonical_topic.strip(),
            category=topic.category,
            subject=topic.subject.strip(),
            outcome=topic.outcome.strip(),
            reader_question=topic.reader_question.strip() or None,
        )
        if not topic.is_wellness_topic:
            candidate.discard("not a wellness, fitness or health question (triage)")
        candidates.append(candidate)

    skipped = [cluster for cluster in clusters if cluster.cluster_id not in claimed]
    for cluster in skipped:
        candidates.append(
            _candidate(
                [cluster],
                canonical=cluster.representative.query,
                category=cluster.category,
                subject=fallback_subject(cluster.representative.query),
                outcome="",
                reader_question=None,
            )
        )

    candidates = merge_duplicates(candidates)
    for candidate in candidates:
        if candidate.live and candidate.category not in allowed:
            candidate.discard(f"category {candidate.category.value} was not requested")
    return candidates, len(skipped)


def merge_duplicates(candidates: list[CandidateTopic]) -> list[CandidateTopic]:
    """Fold candidates that name the same topic into the first of them.

    Triage runs in batches and cannot merge across them, and a group it skipped
    keeps its raw query — so one run produced "gut health" and "Gut Health",
    and "Intermittent Fasting" beside "Fasting intermittently". Same key (word
    order, case and plurals ignored), or the same subject and outcome, is one
    topic. The first keeps its name, since candidates arrive strongest first.
    """
    kept: list[CandidateTopic] = []
    index: dict[str, CandidateTopic] = {}
    for candidate in candidates:
        keys = [
            f"topic:{query_key(candidate.canonical_topic)}",
            f"pair:{query_key(candidate.subject)}|{query_key(candidate.outcome)}",
        ]
        existing = next((index[key] for key in keys if key in index), None)
        if existing is None or existing.live != candidate.live:
            kept.append(candidate)
            for key in keys:
                index.setdefault(key, candidate)
            continue
        existing.queries = list(dict.fromkeys(existing.queries + candidate.queries))
        existing.seeds = list(dict.fromkeys(existing.seeds + candidate.seeds))
    return kept


def _candidate(
    members: list[QueryCluster],
    *,
    canonical: str,
    category: Category,
    subject: str,
    outcome: str,
    reader_question: str | None,
) -> CandidateTopic:
    trends = [trend for member in members for trend in member.trends]
    trends.sort(key=lambda t: (not t.is_breakout, -(t.rising_percent or 0), t.depth))
    queries = list(dict.fromkeys(trend.query for trend in trends))
    return CandidateTopic(
        topic_id=str(uuid.uuid4()),
        canonical_topic=canonical,
        category=category,
        subject=subject or fallback_subject(queries[0]),
        outcome=outcome,
        reader_question=reader_question,
        queries=queries,
        seeds=list(dict.fromkeys(trend.seed for trend in trends)),
    )


async def triage_or_fallback(
    client: TriageClient | None,
    clusters: list[QueryCluster],
    *,
    allowed: list[Category],
) -> tuple[list[CandidateTopic], str | None]:
    """Run triage, degrading to the deterministic reading on any failure.

    Returns the candidates and a note for the run when it degraded.
    """
    if client is None:
        candidates, _ = apply_triage(clusters, None, allowed=allowed)
        return (
            candidates,
            "triage skipped (no local model configured); topics are the raw queries",
        )
    try:
        output = await client.triage(clusters)
    except LLMError as exc:
        logger.warning("research triage failed, using raw queries: %s", exc)
        candidates, _ = apply_triage(clusters, None, allowed=allowed)
        return candidates, f"triage failed ({exc}); topics are the raw queries"
    candidates, skipped = apply_triage(clusters, output, allowed=allowed)
    note = f"triage skipped {skipped} query groups; they use the raw query" if skipped else None
    return candidates, note
