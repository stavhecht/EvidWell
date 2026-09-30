"""Generated article imagery. A sibling of ``app/llm/``, shaped like it.

One Protocol (``base.ImageClient``), one implementation
(``huggingface.HuggingFaceImageClient``), one factory that reads config, and a
prompt builder that is deliberately not a model call. Nothing outside this
package knows which provider is live.

**Why this is a sibling of ``app/llm/`` rather than a subpackage of it**, since
the obvious answer is wrong and was written here for a while: it is *not*
because an image carries no tokens. ``llm/embeddings/`` carries none either and
lives inside ``llm/`` quite happily. The ledger is a consequence, not the
boundary.

The boundary is the call itself. An ``LLMClient`` sends messages and gets back
structured output validated against a schema in ``domain/contracts.py``;
embeddings are a near relative of that — text in, vector out, same providers,
same keys. An image client sends one prompt string to a different vendor
through a different SDK and gets back pixels, which leave through the media
store rather than through the pipeline context. Two packages because those are
two shapes, not because one of them is cheaper to account for.

``imagery/prompt.py`` stays here for the same reason and not in
``llm/prompts/``: those are static templates with a cache-control boundary in
them, while this one is a builder — it infers a motif from the article's text
and lets the seed pick a composition. It is nearer kin to ``retrieval/rerank.py``
than to ``prompts/synthesis.py``, and it feeds only the client beside it.

What follows from the tokens, separately: ``RenderedImage`` has no
``TokenUsage`` and ``IllustrateStage`` never calls ``ctx.record_usage``, because
``pipeline_stage_runs`` has four token columns and ``llm/pricing.py`` prices per
million tokens — so a model id written against four zeros would report a paid
call as free. Say "not measured", not "free".
"""
