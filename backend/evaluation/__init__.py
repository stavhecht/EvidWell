"""Evaluation framework for the article pipeline and the research agent.

Run from ``backend/`` with the venv active::

    python -m evaluation.run                 # every quality suite
    python -m evaluation.run --suite retrieval
    python -m evaluation.run --test basic_001

See ``evaluation/README.md`` for what is measured and how to add cases.
"""

"""
Answers


behavior: did the run end the way the case expects (answered, no_evidence, refused, controlled failure)?
verdict: is the verdict inside the case's acceptable range?
validation: did the draft pass the pipeline's own validation?
groundedness: are the cited sentences actually supported by their sources? This uses the judge, or word overlap when no judge verdict exists.

no_hallucination: no invented [S…] handle, no number missing from the cited source, no contradicted or unsupported claim, no forbidden claim, no citation of a nonexistent paper.
required_concepts / expected_claims / forbidden_claims: the case's expected content is present and its forbidden content is absent.
uncertainty: is the confidence proportionate to the evidence (hedged verdict and wording where the evidence is thin or mixed)?
Citations

citation_existence: does it cite anything when it should?
citation_precision: are the cited papers real and on topic?
citation_correctness: does each cited source support its sentence? The judge scores supported as 1 and partially supported as 0.5; the case fails below 0.75, or on any "contradicted".
citation_completeness: do at least 85% of the finding sentences in the evidence beat and sections carry a citation?
source_types / min_citations: expected study types (e.g. meta-analysis, RCT) and a minimum citation count.
Sources

sources_exist: the cited PMIDs and DOIs are verified in PubMed/Crossref/doi.org, with matching titles and no retraction.
Retrieval

retrieval: non-empty when evidence is expected; precision@5 ≥ 0.6; gold-paper recall@10 ≥ 0.5 where gold papers exist.
recency: for "latest" queries, at least half the cited papers are from the last 5 years.
Query and agent

query_understanding: extraction named the expected subject and outcome.
context_carryover: for follow-ups only, the output stayed on the conversation's subject.
tool_selection: expected tools called, forbidden ones not.
decisions: the pipeline took the branches its design requires.
termination / no_loop: the run ended cleanly, within its loop and call limits.
Failure cases only: failure_recovery, fallback, retry_classification.

So for latest_007, the article some sources only partly supported (citation_correctness), it had finding sentences with no citation (citation_completeness), and fewer than 85% of its cited sentences were grounded (groundedness).

This run uses the old judge prompt, which sometimes misread an article's claim restatement as an assertion. That mostly affected expected_claims, forbidden_claims and uncertainty. All of those will be re-scored with the fixed prompt once the run finishes. The full reason behind each FAIL is in that case's debug block in report.md.
"""