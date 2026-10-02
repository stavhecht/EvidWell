"""How much a cited source should be trusted, by kind.

Health content must not treat every citation as equal: a meta-analysis, a mouse
study and a blog post are three different things even when all three "support"
a sentence. This assigns each source a tier and a score.

The article pipeline only retrieves from scholarly indexes, so in practice the
tiers that occur are the study designs — taken from the pipeline's own
``StudyType`` classification, so this file and the verdict cap agree on what a
paper is — plus guidelines and preprints, which that classification does not
separate. The web tiers (government, news, blog) exist for completeness: the
research agent's web results are classified the same way, and a future
web-search tool in the pipeline would be scored on the same scale rather than
on a new one.

Scores are ordinal judgements written down, not measurements. They are only
ever averaged into ``citation_quality``; nothing gates on one source's number.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

TIER_SCORES: dict[str, float] = {
    "systematic_review_or_meta_analysis": 1.0,
    "clinical_guideline": 0.95,
    "randomised_trial": 0.85,
    "government_or_institutional": 0.8,
    "observational_study": 0.6,
    "narrative_review": 0.45,
    "unclassified_scholarly": 0.4,
    "case_report": 0.3,
    "animal_or_in_vitro": 0.3,
    "preprint": 0.25,
    "news": 0.15,
    "blog": 0.1,
    "generic_website": 0.1,
}

#: Tiers a reader should take as peer-reviewed research.
PEER_REVIEWED = frozenset(
    {
        "systematic_review_or_meta_analysis",
        "clinical_guideline",
        "randomised_trial",
        "observational_study",
        "narrative_review",
        "unclassified_scholarly",
        "case_report",
        "animal_or_in_vitro",
    }
)

_STUDY_TYPE_TIER = {
    "meta_analysis": "systematic_review_or_meta_analysis",
    "systematic_review": "systematic_review_or_meta_analysis",
    "rct": "randomised_trial",
    "observational": "observational_study",
    "narrative_review": "narrative_review",
    "case_report": "case_report",
    "animal": "animal_or_in_vitro",
    "in_vitro": "animal_or_in_vitro",
    "unknown": "unclassified_scholarly",
}

#: Registrant prefixes of the preprint servers that turn up in OpenAlex.
PREPRINT_DOI_PREFIXES = (
    "10.1101/",  # bioRxiv, medRxiv
    "10.21203/",  # Research Square
    "10.2139/",  # SSRN
    "10.20944/",  # Preprints.org
    "10.48550/",  # arXiv
    "10.31219/",  # OSF Preprints
    "10.31234/",  # PsyArXiv
    "10.22541/",  # Authorea
)

_GUIDELINE = re.compile(
    r"\b(practice guidelines?|clinical guidelines?|guidelines? for|position stand|"
    r"position statement|consensus statement|recommendations? (?:of|from) the)\b",
    re.I,
)
_GUIDELINE_TYPES = {"practice guideline", "guideline", "consensus development conference"}

_GOV_SUFFIXES = (".gov", ".gov.uk", ".nhs.uk", ".edu", ".ac.uk", "who.int", "europa.eu")
_NEWS_HOSTS = (
    "nytimes.com",
    "bbc.co.uk",
    "bbc.com",
    "cnn.com",
    "theguardian.com",
    "reuters.com",
    "apnews.com",
    "washingtonpost.com",
    "nbcnews.com",
    "foxnews.com",
    "usatoday.com",
    "independent.co.uk",
    "dailymail.co.uk",
    "news.",
)
_BLOG_HINTS = ("blog", "medium.com", "substack.com", "wordpress.com", "blogspot.")


def scholarly_tier(
    study_type: str,
    *,
    raw_study_type: str | None = None,
    title: str = "",
    doi: str | None = None,
    journal: str | None = None,
) -> str:
    if doi and doi.lower().startswith(PREPRINT_DOI_PREFIXES):
        return "preprint"
    if journal and re.search(r"\b(bioRxiv|medRxiv|preprint)\b", journal, re.I):
        return "preprint"
    raw = {part.strip().lower() for part in (raw_study_type or "").split(";") if part.strip()}
    if raw & _GUIDELINE_TYPES or _GUIDELINE.search(title):
        return "clinical_guideline"
    return _STUDY_TYPE_TIER.get(study_type, "unclassified_scholarly")


def web_tier(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    if any(host.endswith(suffix) for suffix in _GOV_SUFFIXES):
        return "government_or_institutional"
    if any(hint in host for hint in _NEWS_HOSTS):
        return "news"
    if any(hint in host or hint in url.lower() for hint in _BLOG_HINTS):
        return "blog"
    return "generic_website"


def tier_score(tier: str) -> float:
    return TIER_SCORES.get(tier, 0.1)
