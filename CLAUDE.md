# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

All backend commands run from `backend/` with the venv active (`source .venv/bin/activate`).

```bash
# setup
docker compose up -d db                  # pgvector/pgvector:pg16 on :5432
pip install -e ".[dev]"
python -m scripts.migrate                # applies migrations via asyncpg; no psql needed
python -m scripts.migrate --status
python -m scripts.seed_admin --email you@example.com --name "Your Name"

# run
uvicorn app.main:app --reload            # API :8000, OpenAPI at /docs
python -m app.pipeline.runner            # generation worker, separate terminal

# check
pytest tests/ -q
pytest tests/test_invariants.py::test_name -q     # single test
ruff check app tests scripts             # clean; treat as a gate
mypy app                                 # NOT clean — see below

# frontend, from frontend/
npm run dev            # :5173, console at /console
npm run typecheck
npm run build          # tsc -b && vite build
```

Two declared checks do not currently pass, so don't read a failure as something you broke:

- `npm run lint` is in [package.json](frontend/package.json) but eslint is neither installed
  nor configured — the script fails outright. Use `npm run typecheck`.
- `mypy` is configured `strict = true` in [pyproject.toml](backend/pyproject.toml) but reports
  ~30 pre-existing errors across 12 files (mostly untyped-dict returns from service methods
  flowing into response models, plus `voyageai` stubs). It is not in the verified set. `ruff`
  and `pytest` are the real gates.

## Testing gotchas

`tests/test_db_invariants.py` **skips silently when no database is reachable**. It covers the
CHECK constraint and the immutability trigger — exactly the guarantees a fake session cannot
verify. A green `pytest` run with the DB down does not verify invariants #1 and #4. Bring the
DB up before trusting any change to the schema, `services/review.py`, or the persist stage.

The other suites use `FakeSession` from `tests/conftest.py` and need no database.
`asyncio_mode = "auto"`, so async tests need no decorator.

## Architecture

Read [DESIGN.md](./DESIGN.md) before non-trivial work — it carries the reasoning behind each
decision. The short version:

**`domain/contracts.py` is the spec.** Every pipeline stage's input and output is a Pydantic
model there, and `ExtractionOutput`/`SynthesisOutput` double as the structured-output schemas
the LLM generates under. If a stage's shape isn't in that file, it isn't defined. Field bounds
(sentence counts, claim caps) are validators there rather than a global word count, so thin
evidence yields a short article instead of padding.

**The pipeline is six ordered stages** — extract → retrieve → rank → synthesize → validate →
persist — in `pipeline/steps/`, driven by `pipeline/orchestrator.py`. Each stage is
`(input, ctx) -> output` and maps 1:1 onto a future Step Functions state, so keep stages free
of transport concerns. The orchestrator writes `pipeline_runs` / `pipeline_stage_runs` through
a **separate session factory**, so bookkeeping survives a rolled-back article write.

**Retrieval: the scholarly APIs are the index; pgvector is re-rank plus cache.** Pass 1 fans
out per claim across PubMed, Europe PMC, Semantic Scholar and OpenAlex, normalising into
`CandidatePaper` (dedup by DOI → PMID → title hash). Pass 2 upserts into `sources`, embeds new
abstracts whole (no chunking), and ranks by cosine + grade bonus + recency bonus. Those bonus
constants in `retrieval/rerank.py` are explicitly tunable starting values, not settled numbers.

**Article body is TipTap JSON, not markdown.** The model emits plain text with `[S1]` markers;
`services/tiptap.py::body_text_to_doc()` parses them into typed citation nodes at assembly.
Parse failure is a validation failure. Validation walks the tree rather than regexing prose.

**Frontend is a Vite SPA shaped for a cheap Next.js port.** Keep all data access in
`src/lib/api/*` framework-agnostic (plain fetch + typed contracts), keep `src/routes/`
mirroring a Next `app/` tree one-for-one, and keep `window` out of render paths above the
route level.

## Invariants — do not route around these

The four are stated with their enforcement mechanisms in [DESIGN.md](./DESIGN.md) §1. What
matters when editing:

1. `ReviewService.approve()` is the **only** assignment of `published` in the codebase.
   `tests/test_publish_path.py` greps the AST and fails when a second one appears. If a change
   seems to need another publish path, that is a design conversation, not a fix.
2. Citation validation runs after synthesis, before persistence. A failing draft is written as
   `validation_failed` and never enters the review queue — do not soften it to a warning.
3. Evidence grade caps verdict confidence (`evidence/grading.py`). Exceeding the cap is a
   validation failure, not a style note.
4. `articles.original_content` is written once and trigger-protected. Human edits go to
   `edited_content`; the feed renders `COALESCE(edited_content, original_content)`.

Plus one structural: the feed card is **derived** by `services/card.py::derive_card()`, never
generated by a separate model call, so card and article cannot contradict each other.

## Config traps

- **`EMBEDDING_DIM` is load-bearing.** The migration templates the vector column width from it.
  Changing the provider or the dimension after the cache has rows needs a re-embed *and* a
  migration, not a config edit.
- **`claude-opus-5` rejects `temperature` / `top_p` / `top_k`.** Steer behaviour by prompt only.
  Thinking is on by default and `max_tokens` caps thinking **plus** output, which is why
  synthesis is sized at 8K for a ~300-word article. The synthesis system prompt is stable and
  marked `cache_control`; the per-article source block goes after it.
- Password hashing is Argon2id via `argon2-cffi` — chosen over bcrypt to avoid 72-byte
  truncation.

## Deliberately out of scope

Full-text retrieval and chunking, the agentic query-refinement loop, and the AWS deployment are
deferred (DESIGN.md §11). `LLMQueryStrategy` is a declared seam that raises —
`TemplateQueryStrategy` is what runs. A `source_passages` table sits commented in the migration
so the FK direction is already settled.

No live PubMed, Claude or Voyage call has been made yet, so provider parsing and the prompts
have not met real responses. Treat that code as unexercised rather than proven.
