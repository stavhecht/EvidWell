"""Claim → scholarly search query. A deliberately swappable step.

The MVP ships the template strategy because it is deterministic, free, and
debuggable: when retrieval returns nothing useful you can read the query and
see why. An LLM strategy implementing the same Protocol can replace it without
the pipeline noticing.

**Retrieval is biased toward reviews at the query layer, not only at the
ranking layer.** Every claim produces two queries: a review-filtered one and a
general one. Ranking bias alone is not enough — a systematic review that never
enters the candidate pool cannot be promoted by any re-ranker, and against
fifty topically-tighter primary studies it frequently loses the raw relevance
race. Running the filtered query separately guarantees reviews are present to
be ranked.
"""

from __future__ import annotations

import re
from typing import Protocol

from app.retrieval.base import SearchQuery

#: Hand-mapped MeSH terms for the ingredients we expect to see early. Not a
#: general solution — a real MeSH lookup against NCBI is Phase 4 — but it makes
#: the Phase 1 topic work correctly and shows the shape.
MESH_HINTS: dict[str, str] = {
    "ashwagandha": "Withania",
    "withania somnifera": "Withania",
    "withania": "Withania",
    "turmeric": "Curcuma",
    "curcumin": "Curcumin",
    "melatonin": "Melatonin",
    "magnesium": "Magnesium",
    "creatine": "Creatine",
    "rhodiola": "Rhodiola",
    "valerian": "Valerian",
    "l-theanine": "Theanine",
    "theanine": "Theanine",
    "omega-3": '"Fatty Acids, Omega-3"',
    "fish oil": '"Fish Oils"',
    "probiotics": "Probiotics",
    "collagen": "Collagen",
    "vitamin d": '"Vitamin D"',
    "zinc": "Zinc",
    "ginseng": "Panax",
}

#: Claim language → the outcome vocabulary papers actually use. "reduces
#: stress" retrieves poorly; "stress psychological" and "cortisol" retrieve
#: well. This gap between marketing language and indexed terminology is the
#: main reason naive claim-as-query retrieval underperforms.
OUTCOME_HINTS: dict[str, str] = {
    "stress": '("stress, psychological"[MeSH] OR cortisol OR anxiety)',
    "anxiety": '("anxiety"[MeSH] OR anxiolytic)',
    "sleep": '("sleep"[MeSH] OR insomnia OR "sleep quality")',
    "energy": '(fatigue OR vitality OR "physical endurance")',
    "fatigue": '("fatigue"[MeSH] OR vitality)',
    "focus": '("cognition"[MeSH] OR attention OR "cognitive performance")',
    "memory": '("memory"[MeSH] OR "cognitive function")',
    "cognition": '("cognition"[MeSH] OR "cognitive performance")',
    "inflammation": '("inflammation"[MeSH] OR "c-reactive protein")',
    "immune": '("immune system"[MeSH] OR immunity)',
    "muscle": '("muscle strength"[MeSH] OR hypertrophy OR "lean mass")',
    "strength": '("muscle strength"[MeSH] OR "resistance training")',
    "weight": '("weight loss"[MeSH] OR "body composition" OR obesity)',
    "skin": '("skin aging"[MeSH] OR "skin elasticity" OR hydration)',
    "hair": '("hair"[MeSH] OR alopecia)',
    "joint": '("arthralgia"[MeSH] OR "joint pain" OR osteoarthritis)',
    "gut": '("gastrointestinal microbiome"[MeSH] OR "digestive health")',
    "digestion": '("digestion"[MeSH] OR bloating)',
    "mood": '("affect"[MeSH] OR depression OR "mood")',
    "testosterone": "(testosterone OR androgen)",
    "blood sugar": '("blood glucose"[MeSH] OR "insulin resistance")',
    "cholesterol": '("cholesterol"[MeSH] OR "lipid profile")',
}

_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "has", "have", "help", "helps", "improve", "improves", "in", "is",
        "it", "its", "of", "on", "or", "reduce", "reduces", "support",
        "supports", "that", "the", "this", "to", "with", "your",
    }
)  # fmt: skip

#: Reviews are scarce and each is worth more, so a smaller cap costs nothing.
#: The general pass carries the breadth.
REVIEW_PASS_MAX_RESULTS = 15
GENERAL_PASS_MAX_RESULTS = 35


class QueryStrategy(Protocol):
    def build(self, claim: str, ingredients: list[str]) -> list[SearchQuery]:
        """Produce the queries for one claim. Returns reviews-first, then general."""
        ...


class TemplateQueryStrategy:
    """MeSH-aware boolean query construction. No model call."""

    def __init__(self, min_year: int | None = None) -> None:
        self._min_year = min_year

    def build(self, claim: str, ingredients: list[str]) -> list[SearchQuery]:
        terms = self._compose(claim, ingredients)
        return [
            SearchQuery(
                claim=claim,
                terms=terms,
                reviews_only=True,
                max_results=REVIEW_PASS_MAX_RESULTS,
                min_year=self._min_year,
            ),
            SearchQuery(
                claim=claim,
                terms=terms,
                reviews_only=False,
                max_results=GENERAL_PASS_MAX_RESULTS,
                min_year=self._min_year,
            ),
        ]

    def _compose(self, claim: str, ingredients: list[str]) -> str:
        subject = self._subject_group(ingredients) or self._keyword_group(claim)
        outcome = self._outcome_group(claim)
        if subject and outcome:
            return f"{subject} AND {outcome}"
        return subject or outcome or claim

    @staticmethod
    def _subject_group(ingredients: list[str]) -> str:
        """OR the ingredients, mapping known ones through MeSH."""
        terms: list[str] = []
        for ingredient in ingredients:
            normalised = ingredient.strip().lower()
            # Extract the botanical name if the model supplied one in parens:
            # "ashwagandha (Withania somnifera)" -> also try "withania somnifera".
            candidates = [normalised]
            if match := re.search(r"\(([^)]+)\)", normalised):
                candidates.append(match.group(1).strip())
                candidates.append(re.sub(r"\s*\([^)]*\)", "", normalised).strip())

            mapped = next(
                (MESH_HINTS[candidate] for candidate in candidates if candidate in MESH_HINTS),
                None,
            )
            terms.append(mapped or f'"{candidates[-1]}"')

        unique = list(dict.fromkeys(term for term in terms if term))
        if not unique:
            return ""
        return f"({' OR '.join(unique)})"

    @staticmethod
    def _outcome_group(claim: str) -> str:
        """Map claim language onto the vocabulary papers are indexed under."""
        lowered = claim.lower()
        matches = [hint for keyword, hint in OUTCOME_HINTS.items() if keyword in lowered]
        if matches:
            # Several hints can match ("improves sleep and reduces stress");
            # OR them rather than picking one, or half the claim is unsearched.
            unique = list(dict.fromkeys(matches))
            return f"({' OR '.join(unique)})" if len(unique) > 1 else unique[0]
        return TemplateQueryStrategy._keyword_group(claim)

    @staticmethod
    def _keyword_group(text: str) -> str:
        """Fallback: the claim with stopwords and punctuation stripped."""
        words = [
            word
            for word in re.findall(r"[a-z0-9\-]+", text.lower())
            if word not in _STOPWORDS and len(word) > 2
        ]
        return f"({' AND '.join(words)})" if words else ""


class LLMQueryStrategy:
    """Model-generated queries. Not wired up; Phase 4+ at the earliest.

    Only worth building if the template strategy demonstrably fails on real
    topics — and if it does, the fix may be better hints rather than a model
    call. Recorded here to fix the seam, not to endorse the approach.
    """

    def build(self, claim: str, ingredients: list[str]) -> list[SearchQuery]:
        raise NotImplementedError("LLM query generation is deferred; see DESIGN.md §5")
