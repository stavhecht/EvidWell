"""Shared fixtures.

The validation tests need an ``AsyncSession`` only so ``check_sources_resolve``
can look up rows. Rather than require a live Postgres for what is otherwise
pure logic, ``FakeSession`` serves ``select(Source)`` from an in-memory list.

The DB-backed invariants (the CHECK constraint, the immutability trigger) are
in ``test_db_invariants.py`` and *do* need a real database — those are exactly
the guarantees a fake cannot verify, since the whole point is that Postgres
enforces them independently of application code.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-that-is-at-least-32-characters-long")

from app.domain.contracts import (
    ArticleBody,
    CitationGroup,
    PromptSource,
    SynthesisInput,
    SynthesisOutput,
)
from app.domain.enums import StudyType, Verdict


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def __iter__(self) -> Any:
        return iter(self._rows)


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._rows)

    def all(self) -> list[Any]:
        return self._rows

    def scalar_one_or_none(self) -> Any | None:
        return self._rows[0] if self._rows else None


class FakeSource:
    """Stands in for a ``sources`` row."""

    def __init__(
        self,
        id: str,
        pmid: str | None = "12345678",
        doi: str | None = "10.1000/example",
        study_type: StudyType = StudyType.RCT,
    ) -> None:
        self.id = id
        self.pmid = pmid
        self.doi = doi
        self.study_type = study_type


class FakeSession:
    """Serves ``select(Source).where(Source.id.in_(...))`` from memory."""

    def __init__(self, sources: list[FakeSource]) -> None:
        self._by_id = {source.id: source for source in sources}

    async def execute(self, statement: Any) -> _FakeResult:
        wanted = self._extract_ids(statement)
        if wanted is None:
            return _FakeResult(list(self._by_id.values()))
        return _FakeResult([self._by_id[i] for i in wanted if i in self._by_id])

    @staticmethod
    def _extract_ids(statement: Any) -> list[str] | None:
        """Pull the id list out of the compiled ``IN`` clause."""
        try:
            params = statement.compile().params
        except Exception:
            return None
        for value in params.values():
            if isinstance(value, (list, tuple)):
                return list(value)
        return None


def make_prompt_source(handle: str, source_id: str) -> PromptSource:
    return PromptSource(
        handle=handle,
        title=f"Study {handle}",
        abstract="An abstract long enough to be plausible for testing purposes.",
        journal="Journal of Testing",
        year=2022,
        study_type=StudyType.RCT,
        source_id=source_id,
    )


@pytest.fixture
def payload() -> SynthesisInput:
    """A prompt payload offering exactly S1 and S2."""
    return SynthesisInput(
        product="Ashwagandha KSM-66",
        target_claims=["reduces stress"],
        sources=[
            make_prompt_source("S1", "src-1"),
            make_prompt_source("S2", "src-2"),
        ],
    )


def make_draft(
    *,
    verdict: Verdict = Verdict.MIXED,
    beat_2: str = "One randomised trial reported lower evening cortisol [S1].",
    citations: list[CitationGroup] | None = None,
) -> SynthesisOutput:
    return SynthesisOutput(
        headline="Ashwagandha and stress: what the evidence shows",
        verdict=verdict,
        summary="A small number of trials suggest a modest effect.",
        body=ArticleBody(
            beat_1_claim="The product claims to reduce day-to-day stress.",
            beat_2_evidence=beat_2,
            beat_3_bottom_line="The evidence is early and the trials are small.",
        ),
        citations=citations
        if citations is not None
        else [CitationGroup(claim="reduces cortisol", source_ids=["S1"])],
    )
