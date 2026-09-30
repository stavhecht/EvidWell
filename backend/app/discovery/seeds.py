"""The eight PubMed queries that define "wellness and sports nutrition" here.

These are facts about the literature, not tuning knobs, so they are constants
rather than settings — the same call ``retrieval/factory.py`` makes about
per-provider request rates. ``discovery_seeds`` in config selects *among* them
for debugging; it cannot define a new one.

Two decisions worth keeping:

**No publication-type filter.** Restricting to RCTs would hide the observational
wave that precedes them, which is exactly the emergence signal — by the time a
substance has randomised trials, the trend is old news and somebody has already
written about it.

**``humans[MeSH]`` on every seed**, which restricts to MEDLINE-indexed records.
That is not a loss: MeSH tags *are* the signal, so an unindexed record carries
nothing this module can count. It does mean the clock is the indexing clock,
which is why the window is measured on the Entrez date and re-read with an
overlap — see ``harvest.py``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SeedQuery:
    """One broad net over a region of the literature.

    ``anchors`` are the MeSH descriptors the query itself names. They are listed
    beside the terms rather than in a stoplist so the two cannot drift: editing
    a seed's terms without updating its anchors is a visible inconsistency in
    one place, while a stoplist entry three files away would silently stop
    matching and put the seed's own subject at the top of its own ranking.

    Measured, not theoretical — a scan on 2026-09-04 ranked "Plant Extracts"
    first among botanicals with 146 of 665 papers, which is the botanicals seed
    describing itself.
    """

    name: str
    terms: str
    #: MeSH UIs the terms above name. Descriptors only, not their descendants:
    #: "Vitamins" (D014815) is an anchor of the micronutrients net, while
    #: "Vitamin D" (D014807) is a real finding inside it.
    anchors: frozenset[str] = frozenset()

    def esearch_term(self) -> str:
        """The full ``term`` parameter, human filter included."""
        return f'({self.terms}) AND "humans"[MeSH Terms]'


SEED_QUERIES: tuple[SeedQuery, ...] = (
    SeedQuery("supplements", '"Dietary Supplements"[MeSH]', frozenset({"D019587"})),
    SeedQuery(
        "sports_nutrition",
        '"Sports Nutritional Physiological Phenomena"[MeSH]'
        ' OR "Athletic Performance"[MeSH]',
        frozenset({"D064133", "D054874"}),
    ),
    SeedQuery(
        "botanicals",
        '"Plant Preparations"[MeSH] OR "Plant Extracts"[MeSH] OR "Phytotherapy"[MeSH]',
        frozenset({"D028321", "D010936", "D008517"}),
    ),
    SeedQuery(
        "microbiome",
        '"Probiotics"[MeSH] OR "Prebiotics"[MeSH] OR "Synbiotics"[MeSH]',
        frozenset({"D019936", "D056692", "D058616"}),
    ),
    SeedQuery(
        "micronutrients",
        '"Micronutrients"[MeSH] OR "Vitamins"[MeSH] OR "Trace Elements"[MeSH]',
        frozenset({"D018977", "D014815", "D014131"}),
    ),
    SeedQuery(
        "nootropics",
        '"Nootropic Agents"[MeSH] OR "Plants, Medicinal"[MeSH]',
        frozenset({"D018697", "D010946"}),
    ),
    SeedQuery(
        "amino_acids",
        '"Amino Acids"[MeSH] AND ("Exercise"[MeSH] OR "Resistance Training"[MeSH])',
        frozenset({"D000596", "D015444", "D055070"}),
    ),
    SeedQuery(
        "functional_food",
        '"Functional Food"[MeSH] OR "Food, Fortified"[MeSH]',
        frozenset({"D055951", "D005527"}),
    ),
)

#: Every descriptor any seed names. Derived, so adding a seed cannot leave its
#: own subject competing in its own ranking.
SEED_ANCHOR_UIS: frozenset[str] = frozenset().union(
    *(seed.anchors for seed in SEED_QUERIES)
)

SEEDS_BY_NAME: dict[str, SeedQuery] = {seed.name: seed for seed in SEED_QUERIES}


def select_seeds(names: list[str]) -> tuple[SeedQuery, ...]:
    """Resolve seed names to queries; empty means all of them.

    Raises:
        ValueError: an unknown name. Loudly, rather than silently querying
            fewer nets than asked for — a typo would otherwise present as "that
            corner of the literature has no trends", which is indistinguishable
            from a true answer.
    """
    if not names:
        return SEED_QUERIES
    unknown = [name for name in names if name not in SEEDS_BY_NAME]
    if unknown:
        raise ValueError(
            f"unknown seed(s) {', '.join(sorted(unknown))}; expected "
            f"{', '.join(SEEDS_BY_NAME)}"
        )
    return tuple(SEEDS_BY_NAME[name] for name in names)
