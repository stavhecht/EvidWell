"""Trend discovery — what should we write about next?

A periodic scan of newly-indexed PubMed literature that ranks emerging
substances by how fast their evidence base is growing, and proposes the top ones
to a reviewer. Nothing here enqueues a run or spends a token: the output is rows
in ``discovery_candidates``, and a human turns one into an article.

Deliberately separate from ``app/retrieval``. That package answers "what does the
literature say about *this* substance" for an article already being written;
this one answers "which substance" — a question with no product, no claim, and
therefore no ``SearchQuery`` (``QueryStrategy.build`` would raise
``UnanchoredQuery`` on every query this module makes). The two share a throttle,
a study-type classifier and a vocabulary, and nothing else.
"""
