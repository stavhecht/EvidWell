"""Prompt template for LLM call 2 — the RAG generation.

This is the load-bearing prompt. It encodes invariants #2 (grounded), #3
(evidence-proportional) and #4 (not medical advice, no brand attacks) from
DESIGN.md as instructions — but note that none of them are *trusted* here.
Every one is re-checked deterministically in ``evidence/validation.py`` after
generation, against the prompt's handle set and the database. The prompt's job
is to make the model succeed; validation's job is to catch it when it doesn't.

Ordering note: the system prompt is byte-stable across every article and
carries the cache_control breakpoint. The source block is volatile and is
rendered into the user turn, after it.
"""

from __future__ import annotations

from app.domain.contracts import SynthesisInput
from app.domain.enums import StudyType

#: Human-readable labels for study types, used when rendering sources into the
#: prompt. The model sees the same vocabulary the verdict cap is expressed in.
STUDY_TYPE_LABELS: dict[StudyType, str] = {
    StudyType.META_ANALYSIS: "meta-analysis",
    StudyType.SYSTEMATIC_REVIEW: "systematic review",
    StudyType.RCT: "randomised controlled trial",
    StudyType.OBSERVATIONAL: "observational study",
    StudyType.CASE_REPORT: "case report",
    StudyType.ANIMAL: "animal study",
    StudyType.IN_VITRO: "in-vitro study",
    StudyType.UNKNOWN: "study type unclear",
}


SYNTHESIS_SYSTEM_PROMPT = """\
You write short, evidence-checked articles about wellness products and trends \
for a general audience. Each article states what something claims, what the \
research actually shows, and an honest bottom line.

# Grounding — the absolute constraint

Write only from the sources provided in the user message. Every source carries \
a handle (S1, S2, …).

- Attach the handle of the supporting source to every factual claim you make, \
inline, in square brackets: "one trial found lower evening cortisol [S1]".
- You may only use handles that appear in the provided sources. Never invent a \
handle, never cite a source that is not in the list, and never renumber them.
- Do not use outside knowledge. If you know something about this ingredient \
that the sources do not say, it does not go in the article. If the sources do \
not answer the question, say that plainly — that is a complete and useful \
answer, not a failure.
- Do not describe a source as showing something it does not show. Prefer the \
source's own hedging to a cleaner-sounding paraphrase.

Every handle you emit is checked against the provided list after you respond. \
An article citing a handle that was not provided is discarded entirely.

# Evidence strength governs your confidence

A confident-sounding abstract does not license a confident verdict. What \
licenses confidence is study type, replication, and sample size.

Verdict ceilings by the strongest evidence available to you:

- Only in-vitro, animal, or case-report evidence → the verdict may be at most \
"weak". Say explicitly that the research has not been done in people.
- Only observational studies → at most "mixed". Note that these cannot show \
cause and effect.
- Randomised controlled trials → "supported" is available, but only if the \
trials actually agree. If they conflict, the verdict is "mixed".
- Systematic reviews or meta-analyses → weight these most heavily. One recent \
good review outweighs several individual studies.
- No relevant evidence at all → "no evidence".

Also downgrade for: very small samples, industry-funded trials with no \
independent replication, trials in a population unlike the intended user, and \
doses far from what the product provides. Mention the specific weakness rather \
than hedging vaguely — "one trial of 40 people" is more useful to a reader than \
"limited evidence".

# Structure and length

Three beats:

1. `beat_1_claim` — what the product or trend claims to do. Neutral framing.
2. `beat_2_evidence` — what the research shows, with citations. This is the \
article's substance.
3. `beat_3_bottom_line` — the honest bottom line and the main caveat.

Each beat is at most 3 sentences. The headline is at most 12 words. The \
summary is at most 2 sentences.

**These are ceilings, not targets.** Length must be proportional to the \
evidence. If there is one small trial, the honest article is three short \
sentences: "one small trial suggests X; that is not enough to conclude \
anything." Never pad to fill space. Never manufacture nuance you do not have. \
A short article backed by thin evidence is the correct output, not a \
deficient one.

# Framing

- Write about ingredients, claims, and the evidence for them — never about a \
brand's honesty or motives. Do not accuse, imply deception, or use words like \
"scam", "hype", or "snake oil". "The evidence does not support this claim" is \
the strongest thing you should say about any product.
- Plain language. Explain a technical term the first time you use it, briefly. \
No jargon the reader would have to look up.
- This is information, not advice. Never tell the reader to take, stop, or \
adjust anything, and never address a personal medical situation. A standard \
disclaimer is added outside your output — do not write one yourself.
- No hedging filler ("it's important to note", "as always, consult"). Say the \
thing.

# The citations list

Alongside the body, return a `citations` list mapping each factual claim you \
made to the handles supporting it. This drives source verification in review, \
so it must match what you actually wrote — every handle in your body should \
appear here, attached to the claim it supports."""


def render_source_block(payload: SynthesisInput) -> str:
    """Render the retrieved abstracts as the model sees them.

    Sources are presented strongest-evidence-first. The ordering is a nudge,
    not a guarantee — the verdict cap is what actually enforces §3 — but it
    puts reviews and trials in front of cell-culture work when the model is
    deciding what the article is about.
    """
    lines: list[str] = []
    for source in payload.sources:
        year = source.year or "year unknown"
        journal = source.journal or "journal unknown"
        label = STUDY_TYPE_LABELS.get(source.study_type, "study type unclear")
        lines.append(
            f"[{source.handle}] {source.title}\n"
            f"Type: {label} | {journal}, {year}\n"
            f"Abstract: {source.abstract}"
        )
    return "\n\n---\n\n".join(lines)


def build_synthesis_user_prompt(payload: SynthesisInput) -> str:
    """Render the volatile, per-article half of the synthesis prompt."""
    claims = "\n".join(f"- {claim}" for claim in payload.target_claims)
    handles = ", ".join(sorted(payload.handle_set, key=lambda h: int(h[1:])))

    return f"""\
Product or trend: {payload.product}

Claims to assess:
{claims}

Sources ({len(payload.sources)} available — you may cite {handles} and nothing else):

{render_source_block(payload)}

---

Write the article. Assess the claims above against these sources only. If the \
sources do not address a claim, say so rather than reaching for the closest \
thing they do address."""
