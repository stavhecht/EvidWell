"""The research agent: internet trend signals -> evidence-scored topic proposals.

What people are becoming interested in (Google Trends, web search, news) and
what can responsibly be said about it (PubMed, Europe PMC) are separate
questions, answered by separate providers and kept apart in separate scores.
A topic being popular is never evidence that a health claim is true.

A research run **proposes**. It writes ``research_candidates`` and nothing
else; a reviewer promoting one is what creates a pipeline run, exactly as with
the MeSH literature scan in ``app/discovery``. See DESIGN.md "Research agent".

Layout:

    contracts.py   state, signals, scores (the spec)
    providers/     one protocol per signal, implementations behind it
    normalize.py   deterministic query normalisation and clustering
    triage.py      the one model call: what each trend query means
    scoring.py     pure scoring and selection
    graph.py       the LangGraph workflow
    service.py     database reads and writes
    runner.py      executes one claimed run inside the worker
    factory.py     builds the providers from settings
"""
