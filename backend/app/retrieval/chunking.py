"""Split a text into overlapping chunks before embedding it.

Each chunk gets its own vector, and a paper is scored by its best-matching
chunk (``retrieval/rerank.py``).

The chunk size is set by what the embedding model can read, not chosen to force
a split. Most abstracts fit in one chunk and are embedded whole. A longer one
is split rather than silently cut off at the model's limit — which would drop
its end, usually the results and conclusions.

Neighbouring chunks share ``OVERLAP_WORDS`` words. A sentence of up to that
length that straddles a chunk boundary therefore still appears whole in the
next chunk, instead of being cut in half in both.
"""

from __future__ import annotations

#: About 420 tokens: inside the 512-token window of ``mxbai-embed-large``, the
#: local default and the smallest window of the configured embedding models.
#: The median cached abstract is ~250 words, so three in four are one chunk.
CHUNK_WORDS = 300
OVERLAP_WORDS = 30

#: Recorded on each source as ``sources.chunk_settings`` when its chunks are
#: written. Derived from the two numbers above, so changing either one marks
#: every stored paper as out of date and it is re-chunked.
CHUNK_SETTINGS = f"{CHUNK_WORDS}/{OVERLAP_WORDS} words"


def chunk_text(
    text: str, size: int = CHUNK_WORDS, overlap: int = OVERLAP_WORDS
) -> list[str]:
    """Split ``text`` into chunks of ``size`` words, each repeating the last
    ``overlap`` words of the chunk before it.

    Text no longer than ``size`` comes back as a single chunk; empty text as none.
    """
    if not 0 <= overlap < size:
        raise ValueError(f"overlap must be in [0, size); got {overlap=} {size=}")

    words = text.split()
    step = size - overlap
    chunks: list[str] = []
    for start in range(0, len(words), step):
        chunks.append(" ".join(words[start : start + size]))
        if start + size >= len(words):
            break
    return chunks
