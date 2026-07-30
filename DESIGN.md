# EvidWell — Architecture & Design

Evidence-checked wellness content. A Pinterest-style public feed of short articles that
state what a product or trend claims, what the research actually shows, and a plain-language
verdict — every factual sentence carrying a citation to a real paper, every article approved
by a human before publication.

**Status:** implemented and verified against a live Postgres 16 + pgvector — 61 tests pass with
zero skips, the migration applies cleanly, and every endpoint has been exercised end to end.
One caveat, detailed in README → *Verification status*: no live PubMed, Claude or Voyage call
has been made, so provider parsing and the prompts have not met real responses.

Deliberately deferred, per §11 of the brief: full-text retrieval and chunking, the agentic
query-refinement loop, and the AWS deployment. `LLMQueryStrategy` is a declared seam that
raises — the template strategy is what runs.

---

## 1. The four invariants

These are not conventions — each is enforced by a specific mechanism, named here so a reviewer
can check the mechanism rather than trust the prose.

| # | Invariant | Enforced by |
|---|---|---|
| 1 | Nothing publishes without a human | No code path from the pipeline reaches `published`. The pipeline's terminal write is `pending_review`; `published` is set only by `ReviewService.approve()`, which requires an authenticated `reviewer_id`. DB `CHECK` constraint requires `reviewed_by`/`reviewed_at` to be non-null when status is `published`. |
| 2 | Every citation is real and was in the prompt | `evidence/validation.py` runs after synthesis, before persistence. It checks each emitted `S`-handle against the exact handle set built for that prompt, then that each maps to a source row with a resolvable PMID or DOI. A draft that fails is written as `validation_failed`, not `pending_review`, and never enters the queue. |
| 3 | Evidence grade caps verdict confidence | `evidence/grading.py::max_verdict_for_grade()`. A `supported` verdict backed only by in-vitro/animal sources is a **validation failure**, not a style note. The cap is applied twice: as an instruction in the synthesis prompt, and as a hard post-check the model cannot talk its way past. |
| 4 | The AI draft is immutable | `articles.original_content` is written once by the pipeline and never updated (enforced by a DB trigger). Human edits write to `edited_content`. The public feed renders `COALESCE(edited_content, original_content)`. |

A fifth, structural rather than ethical: **the feed card is derived, never generated.**
`services/card.py::derive_card()` computes headline + verdict badge + first sentence of beat 1
from the article body. There is no separate card generation call, so card and article cannot
contradict each other.

**Where to check each one:**

| Invariant | Implementation | Test |
|---|---|---|
| #1 human-in-the-loop | `services/review.py::approve` — the only assignment of `published` in the codebase | `test_publish_path.py` (AST guard), `test_db_invariants.py` (CHECK constraint) |
| #2 grounded | `evidence/validation.py::validate_draft` | `test_invariants.py` — hallucinated handle, unresolvable source, uncited beat |
| #3 evidence caps verdict | `evidence/grading.py::verdict_exceeds_grade` | `test_invariants.py` — including that an *uncited* strong source cannot raise the ceiling |
| #4 immutable draft | DB trigger `articles_original_content_immutable` | `test_db_invariants.py` |
| card derived | `services/card.py::derive_card` | `test_content.py` — card verdict always equals article verdict |

---

## 2. System shape

```
                    ┌──────────────────────────────┐
                    │  Public feed (Vite SPA)      │  read-only, unauthenticated
                    │  masonic + TanStack Query    │
                    └───────────────┬──────────────┘
                                    │  GET /api/feed, /api/articles/{slug}
                    ┌───────────────▼──────────────┐
                    │        FastAPI backend       │
                    │  api/public   api/console    │
                    └───────┬──────────────┬───────┘
                            │              │  authenticated
        ┌───────────────────▼───┐   ┌──────▼─────────────────────┐
        │  Postgres + pgvector  │   │  Editorial console (SPA)   │
        │  articles / sources / │   │  review queue + TipTap +   │
        │  article_sources      │   │  sources panel             │
        └───────────▲───────────┘   └────────────────────────────┘
                    │
        ┌───────────┴────────────────────────────────────┐
        │  Pipeline worker (local process)               │
        │  extract → retrieve → rank → synthesize        │
        │          → validate → persist                  │
        └───────────┬────────────────────────────────────┘
                    │
     ┌──────────────┴───────────────┐
     │ PubMed · Europe PMC ·        │   Claude (extraction, synthesis)
     │ Semantic Scholar · OpenAlex  │   Voyage (embeddings)
     └──────────────────────────────┘
```

Two surfaces, one backend, one database. The console and the feed share nothing but the
`articles` table and the API server; they are separate route trees with separate auth posture.

---

## 3. Decisions made, with reasoning

### 3.1 Vite SPA, shaped for a Next.js port (your call, recorded)

You chose Vite. Consequence to keep in view: **article pages will not be indexed well by
search engines.** If discoverability becomes a goal, the port is real work — but I have kept
it mechanical rather than a rewrite:

- All data access lives in `frontend/src/lib/api/*` and is framework-agnostic (plain `fetch`
  + typed response contracts). No React Router APIs leak into fetch logic.
- Route components live in `src/routes/` mirroring a Next `app/` tree one-for-one
  (`routes/feed.tsx` → `app/(public)/page.tsx`, `routes/article.$slug.tsx` →
  `app/(public)/a/[slug]/page.tsx`).
- Nothing above the route level touches `window` during render, so the components are
  server-renderable as-is.

The port would then be: replace the router, add `generateMetadata` + JSON-LD to the article
route, and mark the console route group `noindex`. `masonic` is client-only and would need a
`dynamic(..., { ssr: false })` wrapper or a static-grid fallback for the first paint.

### 3.2 Auth: minimal, but with a real audit trail

A `users` table (email, password hash, role) and a JWT bearer dependency guarding
`/api/console/*`. Seeded with one admin. This is deliberately small, but it is *not* a shared
password — `reviewed_by` is a real foreign key, so "who approved this" is answerable forever.
Adding reviewers later is inserting rows; adding an invite flow is additive.

Password hashing is Argon2id (`argon2-cffi`), not bcrypt — no 72-byte truncation surprise.

### 3.3 Pipeline: local worker with a `pipeline_runs` table

Slightly more than `BackgroundTasks`, and worth it for one specific reason: **when a draft
fails citation validation, you need to know why.** `pipeline_runs` + `pipeline_stage_runs`
record per-stage status, timing, token cost, and the error payload. Without it, invariant #2
fails silently and looks like "the pipeline didn't produce anything today."

Each stage is a pure-ish function `(input, ctx) -> output` registered in an ordered list. That
shape is deliberate: each stage maps 1:1 onto a future Step Functions state, so the AWS
migration is a transport swap (the orchestrator calls Lambda instead of a local function)
rather than a redesign. The agentic query-refinement loop, when it comes, is a loop *around*
stages 3–4 in the orchestrator — no stage needs to change to accommodate it.

### 3.4 Article body as TipTap JSON, not markdown

`original_content` and `edited_content` are `JSONB` holding a TipTap document. Citations are a
first-class inline node (`{type: "citation", attrs: {sourceIds: ["S1","S3"]}}`), not a `[S1]`
string in prose. Three payoffs: the editor can render a citation as a clickable chip that
opens the sources panel; validation walks a typed tree instead of regexing prose; and
`original` vs `edited` diffs structurally.

The model still *emits* `[S1]` markers in a plain-text body — asking it for TipTap JSON would
burn tokens and invite malformed output. `services/tiptap.py::body_text_to_doc()` parses the
marker syntax into the document tree at the assembly step. Parsing failure is a validation
failure.

### 3.5 Embeddings: Voyage, behind an interface

`voyage-3` tier: strong on scientific text, cheap enough to embed thousands of cached
abstracts. `EmbeddingProvider` is a Protocol with one method, so OpenAI is a config swap.

**Vector width is a single config constant** (`settings.embedding_dim`, default 1024) because
changing it is a table migration and an index rebuild, not a config change. The migration
templates the dimension.

### 3.6 LLM: Claude Opus 5, structured outputs, no sampling params

Both generative calls use `claude-opus-5` via `client.messages.parse()` with a Pydantic
`output_format`, which constrains the response to the schema and hands back a validated model
instance. This removes a whole class of failure (malformed JSON, missing keys) before our own
validation runs.

Notes that matter for implementation:
- `temperature` / `top_p` / `top_k` are **rejected** on this model. Behaviour is steered by
  prompt only.
- Thinking is on by default. `max_tokens` caps thinking **plus** output, so synthesis is sized
  generously (8K) even though the article is ~300 words.
- The synthesis system prompt is stable across calls and marked with `cache_control`; the
  per-article source block is volatile and goes after it. Minimum cacheable prefix on this
  model is 512 tokens, which the system prompt clears comfortably.

---

## 4. Retrieval design

**The scholarly APIs are the search index. pgvector is a re-ranking and caching layer.**
No attempt is made to pre-index PubMed.

### Pass 1 — keyword recall (external APIs)

Per claim, query providers in parallel; each returns candidate papers normalised to a common
`CandidatePaper` shape (title, abstract, year, journal, study type, citation count, PMID, DOI,
URL, source API). Target 20–50 candidates per claim after dedup.

Dedup key is DOI (normalised, lowercased) falling back to PMID, falling back to a normalised
title hash — the same paper routinely arrives from three providers with three different
identifier subsets.

Provider roles:

| Provider | Role | Notes |
|---|---|---|
| PubMed E-utilities | Primary recall for clinical evidence | MeSH terms + publication-type filters give the cleanest study-type signal |
| Europe PMC | Breadth + open-access full text (v2) | Also the fallback when PubMed rate-limits |
| Semantic Scholar | Citation counts, cross-domain | |
| OpenAlex | Broad coverage backstop | Used to fill gaps, not lead |

Explicitly excluded: Google Scholar and any scraping. SerpApi is reserved as a later fallback
only if genuine coverage gaps appear, and is not in the MVP.

**Retrieval is biased toward reviews at the query layer, not just the ranking layer.** The
PubMed query builder issues a review-filtered query first (`systematic[sb]` /
`meta-analysis[pt]`) and a general query second, so reviews enter the candidate pool even when
they would lose a raw relevance race against fifty primary studies.

### Pass 2 — semantic re-rank (pgvector)

1. Upsert each candidate into `sources` (cache hit → reuse the stored embedding).
2. Embed any new abstracts. **Whole abstract, one vector, no chunking** — abstracts are short.
3. Embed the claim text.
4. Cosine-rank candidates against the claim embedding.
5. Apply metadata filters: evidence grade floor, recency window, minimum abstract length.
6. Apply the review bonus, then take top-k (default k=8).

The final score is deliberately not pure cosine similarity:

```
score = cosine_similarity
      + grade_bonus[study_type]     # meta-analysis/SR: +0.15, RCT: +0.08, obs: 0, in-vitro: -0.10
      + recency_bonus               # ≤5y: +0.03, tapering to 0 at 15y
```

The magnitudes are a starting point to be tuned against real output, not a claim of
correctness — they are constants in `retrieval/rerank.py` for exactly that reason. The intent
is that a single recent systematic review outranks several topically-tighter primary studies,
per the brief.

### The `sources` table as a growing library

Once fetched and embedded, a paper is persisted and reused for every future article. The store
becomes a reusable library of cited literature: the tenth ashwagandha article costs almost no
embedding calls. `sources.last_seen_at` supports a later staleness sweep (citation counts drift).

### v2, designed for and deliberately deferred

Full text for the 2–3 load-bearing sources a verdict rests on, via Europe PMC open access,
chunked into ~500-token passages linked back to the paper. The schema anticipates this: a
`source_passages` table is sketched in the migration as a commented block so the FK direction
is settled now. Abstracts for breadth, full text for the pivotal few. **Not in the MVP.**

### Provenance

`article_sources(article_id, source_id, claim, citation_handle)` records which paper backed
which claim under which `S`-handle. This is what makes the review UI fast: the sources panel
is one indexed query, and each claim in the editor renders with its backing citations already
attached rather than resolved client-side.

---

## 5. The AI contracts

Both calls are typed end to end: a Pydantic input model, a Pydantic output model used as the
structured-output schema, and a deterministic validator downstream.

### Call 1 — claim + ingredient extraction

Input: product/trend name + optional marketing blurb.
Output (`ExtractionOutput`): `product`, `target_claims[]`, `ingredients[]`.

Cheap, low-risk, no retrieval involved. The only failure mode worth guarding is the model
inventing claims the blurb never made, so the prompt forbids inferring unstated claims and the
schema caps `target_claims` at 6.

### Query generation (swappable step)

`retrieval/query_builder.py` exposes a `QueryStrategy` protocol. The MVP ships
`TemplateQueryStrategy` (claim + ingredient → MeSH-aware boolean query) because it is
deterministic, free, and debuggable. An `LLMQueryStrategy` implementing the same protocol can
be dropped in without touching the pipeline.

### Call 2 — synthesis (the RAG generation)

Input: product + claims, top-k abstracts each tagged `S1..Sn` with year and study type, and a
strict system prompt. Output (`SynthesisOutput`): `headline`, `verdict`, `summary`,
`body` (three beats), `citations[]`.

The system prompt enforces, in order of importance: use only the provided sources; attach a
source id to every factual claim; say plainly when evidence is weak or absent; never use
outside knowledge; let study-type grade cap verdict confidence; never name or attack brands;
never give medical advice.

**Grounding rule, enforced in code.** After generation, `validate_draft()` checks:

1. Every `S`-handle in `body` and in `citations[]` exists in the prompt's handle set.
2. Every referenced source resolves to a row with a non-null PMID or DOI.
3. Every one of the three beats contains at least one citation, unless the verdict is
   `no evidence`.
4. The verdict does not exceed `max_verdict_for_grade(best_grade_among_cited)`.
5. Field bounds (§6) hold.

Any failure → `validation_failed` with a structured reason. The draft does not enter the queue.
Failures are visible in the console under a separate tab, because a persistent validation
failure is a prompt bug you want to see, not silence.

---

## 6. Article format

Primary output is the on-tap article: ~120–250 words, up to ~300 for a rich evidence base.
Three beats: (1) what it claims → (2) what the research shows → (3) bottom line / caveat.

**Length is a ceiling, not a floor.** Thin evidence should produce a short, honest card
("one small trial suggests X; not enough to conclude"). Nothing in the system pads to length.

Bounds are enforced structurally, per field, not as a global word count:

| Field | Bound | Enforced |
|---|---|---|
| `headline` | ≤ 12 words | Pydantic validator |
| `summary` | ≤ 2 sentences | Pydantic validator |
| `body.beat_1/2/3` | ≤ 3 sentences each | Pydantic validator |
| `verdict` | one label + optional one-clause qualifier | enum + ≤ 15-word qualifier |

Every article renders with an "informational, not medical advice" disclaimer. It is a
render-time constant in a shared layout component, not model output — the model cannot forget
it, reword it, or drop it.

---

## 7. Human-in-the-loop review

States: `pending_review` → `published` | `rejected`, plus `validation_failed` as a terminal
pre-queue state and `draft_failed` for pipeline errors.

The review screen is the editor and the sources panel side by side:

- **Sources panel** lists each paper's title, journal, year, study type, and a direct DOI/PMID
  link, grouped by the claim it backs. Weak study types are flagged inline, so "this confident
  verdict rests on two cell-culture studies" is visible without opening anything.
- **Validation badge** shows `4/4 citations resolve` — computed at draft time and stored on
  the article, not recomputed in the browser.
- **Editor** is TipTap with autosave (debounced 800ms) writing to `edited_content`.
  `original_content` is never touched, giving a clean audit trail of what the model wrote
  versus what went live.

Publishing sets status, `reviewed_by`, `reviewed_at`, `published_at`, and materialises the
derived card fields in the same transaction. The public feed only ever queries
`status = 'published'`.

---

## 8. API surface

Full request/response models are in `backend/app/api/*/schemas.py`. Summary:

### Public (unauthenticated, read-only)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/feed` | Cursor-paginated published cards. `?cursor=&limit=&verdict=` |
| `GET` | `/api/articles/{slug}` | Full article + citations + sources |
| `GET` | `/api/healthz` | Liveness |

### Console (JWT required)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/console/auth/login` | Email + password → access token |
| `GET` | `/api/console/auth/me` | Current reviewer |
| `GET` | `/api/console/articles` | Queue. `?status=pending_review&cursor=&limit=` |
| `GET` | `/api/console/articles/{id}` | Draft + sources + validation report |
| `PATCH` | `/api/console/articles/{id}/content` | Autosave into `edited_content` |
| `POST` | `/api/console/articles/{id}/approve` | → `published` |
| `POST` | `/api/console/articles/{id}/reject` | → `rejected` (reason required) |
| `POST` | `/api/console/pipeline/runs` | Enqueue a topic |
| `GET` | `/api/console/pipeline/runs` | Run history + per-stage status |
| `GET` | `/api/console/pipeline/runs/{id}` | One run, all stages, errors, token cost |

Two deliberate omissions: no `DELETE` on articles (rejection is a state, and the audit trail is
the point), and no endpoint that can set `status = published` other than `approve`.

---

## 9. Data model

Full DDL: `backend/migrations/0001_initial.sql`. Shape:

- **`users`** — reviewers. `role` in (`admin`, `reviewer`).
- **`articles`** — `status`, `slug`, `topic`, `original_content` (JSONB, immutable),
  `edited_content` (JSONB), `verdict`, `verdict_qualifier`, `evidence_grade`, derived card
  fields, `validation_report` (JSONB), `reviewed_by`, `reviewed_at`, `published_at`.
- **`sources`** — `pmid`, `doi`, `title`, `abstract`, `journal`, `year`, `study_type`,
  `citation_count`, `url`, `source_api`, `embedding vector(N)`, `last_seen_at`.
- **`article_sources`** — `(article_id, source_id, claim, citation_handle)`. Provenance.
- **`pipeline_runs`** / **`pipeline_stage_runs`** — observability.

Indexes that matter:

```sql
CREATE INDEX sources_embedding_hnsw
  ON sources USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

CREATE UNIQUE INDEX sources_doi_key  ON sources (lower(doi)) WHERE doi IS NOT NULL;
CREATE UNIQUE INDEX sources_pmid_key ON sources (pmid)       WHERE pmid IS NOT NULL;
CREATE INDEX articles_feed_idx ON articles (published_at DESC) WHERE status = 'published';
```

The partial unique indexes are load-bearing: they make the cache upsert a single
`ON CONFLICT` rather than a read-then-write race.

---

## 10. Infrastructure: local now, AWS later

**Now:** Docker Compose (Postgres 16 + pgvector), FastAPI via uvicorn, worker as a separate
process, Vite dev server. Everything runs on one machine with two API keys.

**Target, designed for but not provisioned:**

| Local | Production |
|---|---|
| Worker process | Step Functions state machine, one state per pipeline stage |
| Manual `POST /pipeline/runs` | EventBridge schedule → Step Functions |
| Stage function call | Lambda invoke (each stage is already a self-contained function) |
| Local Postgres | RDS or Neon, pgvector extension enabled |
| Local filesystem | S3 for card images and assets |
| `.env` | Secrets Manager |

The migration is a transport change because stages already communicate through a serialisable
`PipelineContext` rather than shared memory. Retrieval fan-out becomes a Step Functions `Map`
state. Long-running synthesis stays under Lambda's 15-minute ceiling comfortably.

Not provisioned in this task. No Terraform, no CDK, no AWS account touched.

---

## 11. Phased roadmap

The organising principle: **one topic end-to-end before any breadth.** Each phase ends with
something demonstrable.

### Phase 0 — foundations (½ day)
Compose file, Postgres + pgvector up, migration applied, FastAPI boots, `/healthz` green,
Vite dev server renders an empty feed. No AI.

### Phase 1 — the vertical slice, one topic, hand-driven
Target: `"ashwagandha for stress"` produces one validated draft.
1. `sources` upsert + cache, embedding provider wired.
2. PubMed provider only — one API, learn its shape properly.
3. Extraction call (LLM 1).
4. Semantic re-rank against the claim.
5. Synthesis call (LLM 2) with the real prompt.
6. Citation validation. **Deliberately try to make it fail** — hand-edit a source id in a
   fixture and confirm the draft is rejected. Invariant #2 is untested until you have watched
   it reject something.
7. Persist as `pending_review`.

Exit criterion: one row in `articles` with `status = pending_review` and `4/4` citations
resolving, produced end to end.

### Phase 2 — the console
Auth, review queue, article detail with sources panel, TipTap editor with autosave, approve
and reject. Exit criterion: that draft is human-approved and reaches `published`.

### Phase 3 — the public feed
Masonry feed of published articles, card → article route, citation rendering, disclaimer.
Exit criterion: the approved article is visible and readable by someone who isn't you.

**At this point the whole loop works for one topic. Only now scale breadth.**

### Phase 4 — retrieval breadth
Europe PMC, Semantic Scholar, OpenAlex. Cross-provider dedup. Study-type classification
tuning. Review-bias tuning against real output. Exit criterion: five topics of varying
evidence strength produce appropriately different verdicts — and at least one correctly says
"not enough evidence."

### Phase 5 — pipeline hardening
`pipeline_runs` UI, retries, rate-limit handling, cost tracking per run, batch topic
submission.

### Phase 6 — production
AWS migration per §10, S3 assets, managed Postgres, EventBridge schedule.

### Later, explicitly deferred
Full-text retrieval + chunking (v2 §4); agentic query refinement; multi-reviewer roles and
invites; SerpApi fallback; Next.js port if SEO becomes a priority.

---

## 12. Known risks

| Risk | Mitigation |
|---|---|
| Study-type metadata is inconsistent across providers | Classify from publication type + title/abstract heuristics in `evidence/grading.py`; store both raw and normalised. Unknown grades are treated as the *weakest* tier, so uncertainty caps confidence rather than inflating it. |
| A grounded draft can still be misleading by omission | Human review is the backstop. The sources panel shows what was retrieved, not just what was cited, so a reviewer can see when something relevant was left out. |
| Abstracts overstate findings relative to full text | Known limitation of an abstract-only MVP. Grade caps mitigate; full text for pivotal sources is the v2 answer. |
| Retrieval finds nothing for a fringe trend | `no evidence` is a first-class verdict with its own short-article path, not an error. |
| PubMed rate limits | API key raises to 10 req/s; Europe PMC is the failover; `sources` cache absorbs repeat topics. |
