"""The keywords a research run asks Google Trends about, per category.

A seed is a broad term whose *rising related queries* are the candidates:
"creatine" surfaces "creatine before bed" and "creatine for women". Seeds are
deliberately few — each one is a rate-limited Trends request, and Trends
throttles readily — and deliberately broad, because a narrow seed can only
return its own variations.

Changing this list changes which corners of the internet a run can see. It is
an editorial decision, not tuning.
"""

from __future__ import annotations

from app.research.contracts import Category

#: ``OTHER`` has no seeds of its own: it is where triage files a wellness topic
#: that fits none of the rest, and such a topic can surface under any seed.
SEEDS: dict[Category, tuple[str, ...]] = {
    Category.FITNESS: (
        "workout", "fitness", "exercise", "running", "strength training",
    ),
    Category.NUTRITION: ("diet", "protein", "nutrition"),
    Category.SUPPLEMENTS: ("supplement", "creatine", "magnesium"),
    Category.SLEEP_RECOVERY: ("sleep", "muscle recovery", "sauna"),
    Category.LIFESTYLE: ("stress", "fasting"),
    Category.PREVENTIVE_HEALTH: ("blood pressure", "cholesterol"),
    Category.GENERAL_HEALTH: ("gut health", "inflammation"),
    Category.WELLNESS: ("wellness", "meditation"),
}


def seeds_for(categories: list[Category]) -> list[tuple[str, Category]]:
    """(seed, category) pairs for the requested categories, deduplicated.

    Asking for ``OTHER`` alone searches every seed — there is nowhere else for
    an uncategorised topic to come from — and triage then keeps only what it
    files under ``OTHER``.
    """
    if list(categories) == [Category.OTHER]:
        categories = list(SEEDS)
    seen: set[str] = set()
    pairs: list[tuple[str, Category]] = []
    for category in categories:
        for seed in SEEDS.get(category, ()):
            if seed not in seen:
                seen.add(seed)
                pairs.append((seed, category))
    return pairs
