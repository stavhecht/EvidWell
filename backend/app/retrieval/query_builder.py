"""Turn a claim into search queries. No model call, so a bad result can be
debugged by reading the query.

Every claim gets two queries: one restricted to reviews and meta-analyses, and
one general. Without the review-only pass, a few reviews lose the relevance
race against fifty primary studies and never reach ranking at all.

Every query must name the substance: ``<substance> AND <outcome>``. The
substance comes from the extracted ingredients, or from the product name when
there are none (small local models often return no ingredients). If neither
names anything, ``UnanchoredQuery`` is raised instead of searching the outcome
alone — an outcome-only query ("improves workout performance") returns real
papers about the wrong subject, and nothing downstream can tell.
"""

from __future__ import annotations

import re
from typing import Protocol

from app.retrieval.base import SearchQuery

#: Substance name -> the MeSH term papers are indexed under. Hand-made, not a
#: general MeSH lookup.
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

#: Claim word -> the vocabulary papers actually use. "reduces stress" searches
#: poorly; "stress, psychological" and "cortisol" search well. Also read by
#: ``discovery/topics.py``, which only proposes outcomes listed here.
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
    "heart": '("cardiovascular diseases"[MeSH] OR "heart" OR "cardiovascular health")',
    "cardiovascular": '("cardiovascular diseases"[MeSH] OR "cardiovascular health")',
    "blood pressure": '("blood pressure"[MeSH] OR hypertension)',
    "bone": '("bone density"[MeSH] OR osteoporosis OR "bone health")',
    "recovery": '("muscle soreness" OR "exercise recovery" OR "delayed onset")',
    "endurance": '("physical endurance"[MeSH] OR "aerobic capacity" OR VO2)',
}

_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "has", "have", "help", "helps", "improve", "improves", "in", "is",
        "it", "its", "of", "on", "or", "reduce", "reduces", "support",
        "supports", "that", "the", "this", "to", "with", "your",
    }
)  # fmt: skip

#: Reviews are scarce, so their pass needs fewer results; the general pass
#: carries the breadth.
REVIEW_PASS_MAX_RESULTS = 15
GENERAL_PASS_MAX_RESULTS = 35


class UnanchoredQuery(RuntimeError):
    """Neither the ingredients nor the product name a substance to search for."""

    def __init__(self, claim: str, product: str, ingredients: list[str]) -> None:
        super().__init__(
            f"no searchable substance for claim {claim!r}: product {product!r} and "
            f"ingredients {ingredients!r} yielded no subject terms. Retrieval would "
            f"search the outcome only and return off-topic literature."
        )
        self.claim = claim
        self.product = product
        self.ingredients = ingredients


class QueryStrategy(Protocol):
    def build(self, claim: str, product: str, ingredients: list[str]) -> list[SearchQuery]:
        """The queries for one claim, reviews first. Raises ``UnanchoredQuery``."""
        ...

    def broaden(self, claim: str, product: str, ingredients: list[str]) -> list[SearchQuery]:
        """Wider queries for a claim that came back thin. Raises ``UnanchoredQuery``.

        Broadening drops the *outcome* and keeps the substance, never the other
        way round: relaxing the substance is exactly the off-topic query
        ``UnanchoredQuery`` exists to refuse.
        """
        ...


class TemplateQueryStrategy:
    """MeSH-aware boolean queries built from fixed templates."""

    def __init__(self, min_year: int | None = None) -> None:
        self._min_year = min_year

    def build(self, claim: str, product: str, ingredients: list[str]) -> list[SearchQuery]:
        """``<substance> AND <outcome>``, or just the substance if the claim has no keywords."""
        subject = self._subject(claim, product, ingredients)
        outcome = _outcome_group(claim)
        terms = f"{subject} AND {outcome}" if outcome else subject
        return _review_and_general(claim, terms, self._min_year)

    def broaden(self, claim: str, product: str, ingredients: list[str]) -> list[SearchQuery]:
        """The substance alone, with no year limit: a claim short on recent
        work is exactly where an older trial is worth having."""
        return _review_and_general(claim, self._subject(claim, product, ingredients), None)

    @staticmethod
    def _subject(claim: str, product: str, ingredients: list[str]) -> str:
        # Ingredients first: they name the actives, while a multi-ingredient
        # product's name is a brand with no literature behind it.
        subject = _subject_group(ingredients or [product])
        if not subject:
            raise UnanchoredQuery(claim, product, ingredients)
        return subject


class LLMQueryStrategy:
    """Model-written queries. Not wired up; kept only to mark the seam."""

    def build(self, claim: str, product: str, ingredients: list[str]) -> list[SearchQuery]:
        raise NotImplementedError("LLM query generation is deferred; see DESIGN.md §5")

    def broaden(self, claim: str, product: str, ingredients: list[str]) -> list[SearchQuery]:
        raise NotImplementedError("LLM query generation is deferred; see DESIGN.md §5")


def _review_and_general(claim: str, terms: str, min_year: int | None) -> list[SearchQuery]:
    """The two passes every claim gets: reviews only, then everything."""
    return [
        SearchQuery(
            claim=claim,
            terms=terms,
            reviews_only=True,
            max_results=REVIEW_PASS_MAX_RESULTS,
            min_year=min_year,
        ),
        SearchQuery(
            claim=claim,
            terms=terms,
            reviews_only=False,
            max_results=GENERAL_PASS_MAX_RESULTS,
            min_year=min_year,
        ),
    ]


def _subject_group(substances: list[str]) -> str:
    """OR the substances together, mapping known ones to MeSH terms."""
    terms: list[str] = []
    for substance in substances:
        name = substance.strip().lower()
        if not name:
            continue
        # "ashwagandha (withania somnifera)" -> also try both halves.
        names = [name]
        if match := re.search(r"\(([^)]+)\)", name):
            names.append(match.group(1).strip())
            names.append(re.sub(r"\s*\([^)]*\)", "", name).strip())

        mapped = next((MESH_HINTS[n] for n in names if n in MESH_HINTS), None)
        terms.append(mapped or _mesh_within(name) or f'"{names[-1]}"')

    unique = list(dict.fromkeys(term for term in terms if term))
    return f"({' OR '.join(unique)})" if unique else ""


def _mesh_within(phrase: str) -> str | None:
    """A known substance inside a longer name ("Creatine Monohydrate Powder").

    Longest match wins, so "withania somnifera" beats "withania"; whole words
    only, so "zinc" does not match "zincate".
    """
    matches = [
        (key, term)
        for key, term in MESH_HINTS.items()
        if re.search(rf"\b{re.escape(key)}\b", phrase)
    ]
    if not matches:
        return None
    return max(matches, key=lambda pair: len(pair[0]))[1]


def _outcome_group(claim: str) -> str:
    """The claim's outcome in indexed vocabulary, or its keywords as a fallback."""
    lowered = claim.lower()
    # Several can match ("improves sleep and reduces stress"): OR them all.
    hints = list(
        dict.fromkeys(hint for keyword, hint in OUTCOME_HINTS.items() if keyword in lowered)
    )
    if len(hints) > 1:
        return f"({' OR '.join(hints)})"
    if hints:
        return hints[0]

    words = [
        word
        for word in re.findall(r"[a-z0-9\-]+", lowered)
        if word not in _STOPWORDS and len(word) > 2
    ]
    return f"({' AND '.join(words)})" if words else ""
