# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

All backend commands run from `backend/` with the venv active (`source .venv/bin/activate`).

```bash
# setup
docker compose up -d db                  # pgvector/pgvector:pg16 on :5432
pip install -e ".[dev]"
python -m scripts.migrate                # applies migrations via asyncpg; no psql needed
                                         # 0002 adds readers, folders, the contact inbox,
                                         # articles.subject and articles.card_image
python -m scripts.migrate --status
python -m scripts.seed_admin --email you@example.com --name "Your Name"

# maintenance (both dry by default; --apply to write)
python -m scripts.reclassify_sources          # after any classifier change
python -m scripts.check_retractions           # re-check cited sources
python -m scripts.check_retractions --scope all

# run
uvicorn app.main:app --reload            # API :8000, OpenAPI at /docs
python -m app.pipeline.runner            # generation worker, separate terminal

# check
pytest tests/ -q
pytest tests/test_invariants.py::test_name -q     # single test
ruff check app tests scripts             # clean; treat as a gate
mypy app                                 # NOT clean — see below

# frontend, from frontend/
npm run dev            # :5173, public site at /, review desk at /review
npm run typecheck
npm run build          # tsc -b && vite build
```

There is also a container path — `docker compose up backend` builds
[backend/Dockerfile](backend/Dockerfile) and serves the API on :8000 against the `db` service.
Two things it deliberately does not do, so don't expect them:

- **It cannot migrate or seed.** `scripts/` is in [.dockerignore](backend/.dockerignore), so
  `python -m scripts.migrate` has no entry point inside the image even though `migrations/` is
  copied in. Run migrate and seed from the host venv; the container starts happily against an
  unmigrated database and fails at the first query.
- **It does not run the worker.** Compose defines the API only, so `docker compose up backend`
  gives you a service that accepts `POST /pipeline/runs` and never executes them. Run
  `python -m app.pipeline.runner` on the host alongside it.

Two declared checks do not currently pass, so don't read a failure as something you broke:

- `npm run lint` is in [package.json](frontend/package.json) but eslint is neither installed
  nor configured — the script fails outright. Use `npm run typecheck`.
- `mypy` is configured `strict = true` in [pyproject.toml](backend/pyproject.toml) but reports
  ~30 pre-existing errors across 12 files (mostly untyped-dict returns from service methods
  flowing into response models, plus `voyageai` stubs). It is not in the verified set. `ruff`
  and `pytest` are the real gates.

## Testing gotchas

**Seven suites skip silently when no database is reachable**, and they are the ones covering
guarantees a fake session cannot express — SQL semantics, transaction boundaries, and the
query planner. A green `pytest` with the DB down is a much weaker signal than it looks:

| Suite | What goes unverified when skipped |
|---|---|
| `test_db_invariants.py` | the CHECK constraint and the immutability trigger — invariants #1 and #4 |
| `test_source_cache.py` | resolve-then-update, the `SAVEPOINT` retry, split-row reporting |
| `test_orchestrator_transactions.py` | per-stage commit, and persist+completion being atomic |
| `test_worker_claim.py` | the claim `UPDATE`, and `attempts` counted at claim time |
| `test_stale_recovery.py` | heartbeat sweep, requeue-vs-fail on a spent budget |
| `test_rerank_plan.py` | the `EXPLAIN` assertion that no approximate scan is chosen |
| `test_reader_accounts.py` | the composite FK on `reader_saves`, the save-is-a-move primary key, folder-name uniqueness, and the contact `CHECK` |

Point them elsewhere with `TEST_DATABASE_URL`; they otherwise use `database_url` from
settings. `test_worker_claim.py` and `test_stale_recovery.py` also skip when the queue is
already non-empty — they need to see the whole table, so a leftover `queued` or `running` row
from a manual run turns them into passes. Clear it before trusting them.

The remaining suites use `FakeSession` from `tests/conftest.py` and need no database.
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

**The orchestrator owns every transaction boundary — do not commit in a stage or in the
worker.** It commits after each successful stage and rolls back a failing one, so a synthesis
failure no longer discards the `sources` rows and embeddings retrieval paid for, and no
transaction is held open across a model call. The one exception is the last stage: its write
commits together with the run's completion row, which is what stops a killed worker from
leaving an article whose run still says `running` (it would be requeued as stale and written
twice). A retryable `StageError` requeues the run with a backoff up to
`PIPELINE_MAX_ATTEMPTS`; anything else fails it permanently.

**A `running` run proves nothing; its heartbeat does.** The worker pings
`pipeline_runs.heartbeat_at` on a timer while a run is in flight, and a periodic sweep in the
poll loop recovers rows that have gone quiet for `WORKER_STALE_AFTER_SECONDS`. Staleness is
deliberately *not* measured from `started_at`: a duration cutoff cannot tell a slow run from a
dead one, and requeueing a live run starts a second concurrent execution of it — the same
double-write the persist/completion atomicity above prevents. Two rules follow. `_beat` must
never raise (a dead heartbeat under a live run *is* the double-execution bug, so a failed ping
is logged and retried), and the sweep must stay in the poll loop rather than becoming a
background task, so a worker can never sweep its own run. Recovery consumes the run's attempt
budget — which is why `attempts` is incremented at claim time — and fails the run rather than
requeueing it once the budget is gone.

**Retrieval: the scholarly APIs are the index; pgvector is re-rank plus cache.** Pass 1 fans
out per claim across PubMed, Europe PMC, Semantic Scholar and OpenAlex, normalising into
`CandidatePaper`. Pass 2 upserts into `sources`, embeds new abstracts whole (no chunking), and
ranks by cosine + grade bonus + recency bonus. Those bonus constants in `retrieval/rerank.py`
are explicitly tunable starting values, not settled numbers.

**Every query must name a substance, and failing to is a stage failure — not a warning.**
`QueryStrategy.build()` takes `product` and `ingredients` separately: ingredients name the
actives but are optional, `product` is required, so the product is the fallback anchor when a
model returns `ingredients: []` (local models do this constantly). If neither yields a subject
term, `TemplateQueryStrategy` raises `UnanchoredQuery` and `RetrieveStage` converts it to a
**non-retryable** `StageError` before any provider is called — nothing varies between attempts,
so a retry just recomposes the identical unusable query. This replaced a keyword fallback that
logged a warning and continued, and the difference is not academic: a run on creatine with no
ingredients searched `(workout AND performance)`, retrieved 50 real papers about CrossFit and
pre-workout blends, cited two of them, passed citation validation, and produced a `supported`
verdict at `systematic_review` grade on a product none of the sources mention. Every downstream
signal reads as success — this stage is the last place the mistake is visible. Do not soften it
back to a warning, and do not merge `product` into `ingredients` at the call site.

**Identity is a union-find over every identifier a record carries** (`retrieval/dedup.py`), not
one preferred key per record. Providers report different identifier *subsets* for the same
paper, so a DOI-only and a PMID-only record only unify when some third record carrying both
bridges them — a single-key scheme leaves two candidates, two `sources` rows, and two handles
the model cites as independent corroboration. Titles are an identity key only for a record
carrying no identifier at all: errata and conference abstracts repeat their parent's title, and
over-merging silently deletes a study while under-merging only costs a prompt slot. This is
invisible with PubMed alone (its records carry both identifiers) and appears the day the other
providers come online — watch `retrieve.duplicates_merged`.

`RetrieveStage` is the only stage that writes to the cache; it carries each candidate's row id
forward as a `CachedCandidate` on `ctx.candidates`, so **`RankStage` takes no `SourceCache` and
reads only**. Don't reintroduce a lookup there — it was a full re-upsert of the candidate set
to recover ids the previous stage already had.

**The cache upsert is deliberately not a single `ON CONFLICT`.** A paper's identity spans two
partial unique indexes (`doi`, `pmid`) and Postgres takes one inference clause per statement,
so inferring against whichever identifier the incoming record happens to carry breaks as soon
as a record arrives *more complete* than the row it matches — PMID-only row, DOI-bearing
record, infers on the DOI index, finds nothing, inserts, violates `sources_pmid_key`. Union-find
makes that the common path, not the edge case. `SourceCache` therefore resolves first (one read
matching every identifier at once), updates matched rows **by primary key**, and inserts only
new papers; the read-then-write race surfaces as an `IntegrityError` inside a `begin_nested()`
`SAVEPOINT` and is retried once. The savepoint is load-bearing — the pipeline session is
long-lived and a bare rollback would discard the rest of the run. Matched-row refreshes are one
`UPDATE ... FROM (VALUES ...)` per batch, not per row; warm is the normal case, and the per-row
loop made it the most expensive path instead of the cheapest.

**The re-rank is an exact sort and there is no HNSW index.** Top-k
is applied in Python after the grade bonus, so no `LIMIT` reaches SQL and the planner cannot
choose an approximate scan — `tests/test_rerank_plan.py` pins this with `EXPLAIN`. Recreating
the index means revisiting `rank_for_claim` first; `0001_initial.sql` records the statement and
the condition that would justify it, commented beside the index it declines to create.

**A throttled search must never look like an empty one.** Providers sit behind
`retrieval/throttle.py` (per-provider token bucket + bounded retries honouring `Retry-After`),
and `RetrieveStage` fails the stage when *any single claim* had every provider call fail.
Recall lost to a 429 produces a more cautious verdict, which reads as a correct answer — so the
degradation is invisible unless it is caught here. Rates are per process and the APIs limit per
IP; the constants in `retrieval/factory.py` are deliberately under the published ceilings. A
related consequence: Semantic Scholar without an API key is skipped **at construction** rather
than returning `[]` from `search()`, because a provider that answers without making a request
counts as a successful search and would mask a claim whose only real provider was throttled.

**Zero usable sources means no generative call at all.** `SynthesizeStage` branches to
`services/no_evidence.py`, which assembles the `SynthesisOutput` deterministically. A model
handed a product name and no abstracts is the ungrounded generation the pipeline exists to
prevent, and it would emit no citations — so invariant #2 would pass by vacuum rather than by
verification. The draft still takes the ordinary path and passes validation on its merits (empty
handle set, `no_evidence` exempt from the cited-beat rule, `best_grade([])` is `UNKNOWN`) and a
human still approves it. This is the pair to the throttle rule above: "we searched and found
nothing" is publishable, "we could not search" is not, and they reach synthesis as the same
empty list. `metrics.no_evidence_cause` separates nothing-retrieved from filtered-away.

**Nullable JSONB columns use `NullableJSONB` from `domain/models.py`, never bare `JSONB`.**
SQLAlchemy defaults to `none_as_null=False`, so a Python `None` persists as the JSON literal
`null`, not SQL `NULL`. `COALESCE(edited_content, original_content)` then returns JSON `null`
for an unedited article, and `WHERE error IS NULL` matches no succeeded stage. Python readers
hide it — `json.loads('null')` is `None`, so `edited_content or original_content` is correct —
which means it only breaks in the SQL that DESIGN.md §3.4 and `services/card.py` document as
the enforcement of invariant #4. Both columns were shipping JSON `null` before this was caught.

**Article body is TipTap JSON, not markdown.** The model emits plain text with `[S1]` markers;
`services/tiptap.py::body_text_to_doc()` parses them into typed citation nodes at assembly.
Parse failure is a validation failure. **`CITATION_MARKER_PATTERN` in `domain/contracts.py` is
the only definition of a marker** — `tiptap.py` builds its run regex from it, and
`extract_handles()` uses it directly. Do not restate the pattern in either place: the parser and
the extractor disagreeing means `was_cited` is false for a source the article visibly cites, and
`check_beats_are_cited` flags a beat that is cited on the page. Both `[S1][S5]` and `[S1, S5]`
are accepted and collapse into one citation node; ranges (`[S1-S8]`) are not, inline. Anything
bracketed that is not a marker is a parse failure — checked by stripping valid markers and
looking for leftover brackets, because the old "not a valid marker" pattern missed unterminated
runs like `[S1, S5` and let them through as literal text. Validation walks the tree rather than regexing prose.
Reviewers can add two more block nodes — `image` and `youtube` (DESIGN.md §3.4b). Both are
siblings of the beat paragraphs, and beats stay addressable because `beat_text()` uses
`attrs.beat`, never position.

**A retracted paper is refused, not downgraded — and `retraction_checked_at IS NULL` means
nobody asked, not "clean".** Three mechanisms, and mixing them up is how this breaks:
`is_retracted()` drops the paper in `RetrieveStage` before the cache; `classify_study_type`
returns `UNKNOWN` for one already cached; `scripts/check_retractions.py` re-checks cited sources
against PubMed + Crossref on a schedule, because retractions land *after* publication and the
ingest screen only ever catches what was already withdrawn when we first saw it.

Two rules with teeth. **Retractions are checked *before* the publication-type map**, unlike
every other entry in `NEGATIVE_PUBLICATION_TYPES` — a retracted trial also carries `Randomized
Controlled Trial`, so under the normal "positive identification wins" rule it graded `rct`,
ceiling `supported`, and the cap existed only for papers it did not matter for. Don't move it
back. And **`RetractionVerdict` has three states**: a provider that failed, 500'd, or cannot
resolve an id leaves the paper *unchecked*, never clean. NCBI in particular returns an `error`
record rather than omitting an unknown id, and reading that as an empty `pubtype` list marked
unlookuppable papers verified clean — then wrote a timestamp that suppressed the next sweep.
Same principle as `retrieval/throttle.py`: a search that could not run must never look like one
that found nothing.

**A flagged article stays `published`.** The sweep sets `retraction_flagged_at`, which raises a
reader-facing banner and a console row. It does not withdraw. That is invariant #1 pointed the
other way — a human decides what the public sees, in both directions — and one retracted source
among several need not invalidate a conclusion. Nothing is deleted either: the source row may be
referenced by `article_sources`, and provenance outranks tidiness.

**Tokens are stored; cost is computed at read time and never written down.** The ledger is
`model` + four token columns on `pipeline_stage_runs` — that table is already one row per run ×
attempt × stage, which is the grain cost surprises actually have. `pipeline_runs` keeps a
rollup, written as an **increment** because `ctx` is rebuilt per attempt and assigning would
report the cheapest attempt as the whole run. Prices live in `llm/pricing.py` keyed by
**provider-namespaced** model id (`anthropic/claude-sonnet-5`), so a local model — genuinely
free — is never confused with a hosted one nobody has priced. Two rules that look like
politeness and are not: an unpriced model returns **`None`, never `0`** (they are both falsy
and mean opposite things, and the zero would be believed on the first run after someone edits
`SYNTHESIS_MODEL`), and a run total is all-or-nothing, because extraction alone is ~1% of a run
and would read as a plausible answer. Do not add a `cost` column — a stored price freezes
whichever number was current that day, with no record of which one it was.

**Tokens burned by a *failed* call are recorded too, and that write outlives the rollback.**
`LLMError` carries `usage`; the extract and synthesize stages record it before converting to a
`StageError`; `_abandon` writes it while rolling back everything else the stage did. The
rollback undoes our writes, not the provider's charge — and the case that matters is truncation,
which spends the entire 8K synthesis budget and produces nothing, so a zero here makes the
pipeline look cheapest exactly when it is burning the most. This is also why
`PipelineContext.record_usage` mutates in place instead of returning a copy: a raising stage
returns no context. `ctx.usage` is a **derived property** over `usage_by_stage`, not a field —
don't reintroduce a separately-summed one, the two drift the first time a call is added and
only one side is updated.

Embeddings are **outside** the ledger on purpose (~1% of a run, and widening
`EmbeddingProvider` changes its return type and every caller). Say "not measured", not "free".

The two pre-existing runs were backfilled from the old `metrics` JSONB, and their stage rows sum
to ~328 fewer input tokens than the run rollup: extraction's *input* count was never in
`metrics` (only `"tokens"`, its output). Expected, not drift — don't "fix" it. Their `model` is
NULL, so they report an unknown cost rather than a guessed one.

**The theme is `styles/youth.css`; `styles/design-system.css` is vendored and read-only.**
The second is Modernist, copied from the Claude Design project — retune it *there* and re-copy,
because hand-edits are lost on the next sync and that project is what the readme and the
component pages render from. `youth.css` declares the `--ew-*` tokens components speak and
re-points Modernist's `--color-*` roles at them, so the focus ring and `::selection` follow the
active theme. Every Tailwind value resolves to a custom property: a hex in a component is a bug,
because it cannot follow the theme and is invisible to anyone retuning the system. Two
deliberate exceptions, both because the surface underneath is a photograph nobody controls —
feed-tile text and `.ew-tile-scrim` are fixed white on fixed dark, and the verdict *mark* is
dropped on an image tile because ink is invisible on a scrim and a white version would be a
second mark meaning the same thing. `features/console/controls.ts` carries the same pill-and-
rounded-field shapes as the public side, on purpose: a reviewer crossing between the two
surfaces should not have to relearn what a button looks like, and that file's own rule is that
those three controls staying consistent is a safety property.

**Frontend is a Vite SPA shaped for a cheap Next.js port.** Keep all data access in
`src/lib/api/*` framework-agnostic (plain fetch + typed contracts), keep `src/routes/`
mirroring a Next `app/` tree one-for-one, and keep `window` out of render paths above the
route level.

**Two route groups that share only `App.tsx`.** The public site (`/`, `/a/:slug`, `/about`,
`/join`, `/you`, `/contact`) renders under `PublicLayout` — site bar, category drawer, mobile
tab bar. The review desk is at **`/review`**, is lazy-loaded, and renders `ReviewHeader`
instead. Nothing public links to it. That is chrome and discoverability, *not* access control:
a URL is not a secret, and what actually keeps readers out is `RequireAuth` plus the
router-level bearer check on every `/api/console/*` route. Public routes must never import
from `features/console`, and the desk must never render public chrome.

**Two accounts, two tables, two tokens.** `users` is the reviewer roster and
`articles.reviewed_by` is a foreign key into it, so a row there is a claim about who is
answerable for a published article. `readers` are self-service. One table with a role column
would put a correctly-set enum between a public signup and the review queue; two tables make
that unrepresentable. The tokens carry a `typ` claim (`reviewer` / `reader`) checked in
`decode_token_subject` **before** the account lookup, so a reader token presented to the
console is rejected as malformed rather than merely failing to resolve — the separation
survives a future change to either query. `lib/api/client.ts` picks which token to send from
the path, never from the caller.

**The browse drawer offers subject only.** It listed the four verdicts for a while and they
were removed deliberately: a standing menu of Supported / Mixed / Weak / No evidence is a
scoreboard, and offering it as a primary way into the feed invites browsing by score rather
than by subject — the framing the content rules exist to avoid. `?verdict=` is still a real
narrowing and still works from a shared link; the navigation just does not propose it.

**A reader account orders the feed; it never narrows it.** `FeedService.page` gains a leading
sort tier — 0 for a subject the reader picked, 1 for everything else — and the rest of the
feed follows below. Filtering instead would let an account quietly shrink the world, and it
would make every *unclassified* article (`subject IS NULL`) invisible to anyone with an
interest set. The cost is that the tiered sort cannot use `articles_feed_idx`; the tier is
therefore skipped entirely for a reader with no interests, which is every anonymous request.
Free-text search is client-side over loaded pages — there is no search endpoint — and the
empty state says so rather than claiming nothing exists.

**`subject` is reviewer-set and stays optional.** It is the product's one chromatic axis and
it cannot be derived: `product` is free text, and guessing would put a confident colour on an
unchecked classification. `PATCH /console/articles/{id}/subject` is deliberately separate from
the content autosave and legal *after* publication, because it drives a colour and a browse
listing rather than a word the reader was shown. An unclassified article renders in ink and
sits under "Everything" — the design's resting state, not a broken cell — so it must never
block publishing.

**The feed tile's picture is derived, like its headline and excerpt.** `derive_card()` takes
the first `image` node from the approved body into `articles.card_image` at publish time, so a
tile cannot show a picture the article does not contain. `youtube` nodes are excluded on
purpose: a video thumbnail lives on a third-party host, and deriving one would put a YouTube
request on every feed render — the same tracking the article page's click-to-load facade
exists to avoid. `NULL` is the normal case and the feed draws a typographic tile.

## Invariants — do not route around these

The four are stated with their enforcement mechanisms in [DESIGN.md](./DESIGN.md) §1. What
matters when editing:

1. `ReviewService.approve()` is the **only** assignment of `published` in the codebase.
   `tests/test_publish_path.py` greps the AST and fails when a second one appears. If a change
   seems to need another publish path, that is a design conversation, not a fix.
2. Citation validation runs after synthesis, before persistence. A failing draft is written as
   `validation_failed` and never enters the review queue — do not soften it to a warning.
3. Evidence grade **and quantity** cap verdict confidence (`evidence/grading.py`). Exceeding
   the cap is a validation failure, not a style note. Two rules, both **per claim**, with the
   article inheriting its weakest claim's ceiling: the study-type ceiling, and a quorum of
   `QUORUM_FOR_SUPPORTED` sources at a supported-tier grade before `supported` is reachable —
   one study is a finding, not a conclusion. Use `max_verdict_for_claims`, not
   `max_verdict_for_grade`, which answers only the first question. The quorum lowers the
   *ceiling*; `best_evidence_grade` still records the best grade cited, because the console
   needs to say what the article actually rests on.
4. `articles.original_content` is written once and trigger-protected. Human edits go to
   `edited_content`; the feed renders `COALESCE(edited_content, original_content)`.

Plus one structural: the feed card is **derived** by `services/card.py::derive_card()`, never
generated by a separate model call, so card and article cannot contradict each other.

## Config traps

- **Model calls run locally by default.** `LLM_PROVIDER=ollama` and
  `EMBEDDING_PROVIDER=ollama` (see [.env.example](backend/.env.example)) — free, no key,
  needs `ollama serve` plus `ollama pull llama3.1:8b mxbai-embed-large`. The hosted clients
  are fully wired and one setting away: `LLM_PROVIDER=anthropic`, `EMBEDDING_PROVIDER=voyage`.
  Providers are selected in [llm/factory.py](backend/app/llm/factory.py) and
  [llm/embeddings/factory.py](backend/app/llm/embeddings/factory.py); nothing else knows
  which one is live.
- **Ollama's `format` is a decoding grammar, not a validator.** It guarantees JSON of the
  right shape and nothing else — the Pydantic validators in `domain/contracts.py` (sentence
  ceilings, 12-word headline) are still checked in Python, and small local models break them
  often, so `ollama_client.py` retries once with the errors fed back. Also: `num_ctx` is set
  explicitly on both calls because the server default is 4K and overflow silently drops the
  front of the prompt — for synthesis that means sources vanish while the instruction to cite
  them survives.
- **`EMBEDDING_DIM` is load-bearing.** The migration templates the vector column width from it.
  Changing the provider or the dimension after the cache has rows needs a re-embed *and* a
  migration, not a config edit.
- **`claude-opus-5` rejects `temperature` / `top_p` / `top_k`.** Steer behaviour by prompt only.
  `max_tokens` caps thinking **plus** output, which is why synthesis is sized at 8K for a
  ~300-word article; `_check_truncation` names that cause explicitly, because a truncated
  structured response has no parsed output and otherwise reads as a schema failure.
- **`thinking` is passed explicitly on both Anthropic calls, and must stay that way.** Whether
  an omitted `thinking` means "think" varies across the range (Sonnet 5 / Opus 5 do, Opus 4.8 /
  4.7 do not), and the model is config — leaving it implicit lets `EXTRACTION_MODEL` silently
  decide whether the token budget is spent on thinking. It also means **extraction cannot run on
  `claude-haiku-4-5`**: it is pre-adaptive-thinking, so the argument 400s. Both models therefore
  default to `claude-sonnet-5` — the other reason being that Haiku left `ingredients` empty in 9
  of 10 samples, which silently unanchors the PubMed query.
- **The `cache_control` breakpoint is a no-op on extraction.** The minimum cacheable prefix is
  per-model and not monotonic (512 on Opus 5, 1024 on Sonnet 5, 4096 on Haiku 4.5), and a prompt
  under it fails to cache silently — `cache_creation_input_tokens: 0`, no error. Measured with
  `messages.count_tokens`: extraction is **306 tokens** and caches on nothing; synthesis is
  **1285**, so it caches on Sonnet 5 with ~260 tokens of headroom. Trimming the synthesis system
  prompt would break caching without saying so. Confirm against `usage.cache_read_input_tokens`
  on a real call rather than assuming.
- **Pointing a model setting at something new needs a price-table entry.** Nothing breaks
  without one — the run just reports `estimatedCostUsd: null` forever, which is honest and
  easy to miss. `test_cost_accounting.py` asserts the clients' four default models are all in
  `PRICES`, so the defaults are covered; a value set only in `.env` is not.
- **Both LLM clients log full prompts at `INFO`.** Useful while the prompts are unexercised,
  but it puts every system prompt and source block in the log stream — reconsider the level
  before anything ships where logs are retained.
- **`WORKER_STALE_AFTER_SECONDS` must be ≥ 3× `WORKER_HEARTBEAT_SECONDS`**, enforced by a
  `model_validator` in [config.py](backend/app/config.py) that refuses to start otherwise. Set
  closer, one slow `UPDATE` declares a live run abandoned and the sweep starts a second
  concurrent execution of it. Per-provider request *rates* are not settings — they are
  constants in `retrieval/factory.py`, being facts about each API rather than knobs; only the
  retry counts (`PROVIDER_MAX_RETRIES`, `PROVIDER_MAX_RETRY_WAIT_SECONDS`) are configurable.
- Password hashing is Argon2id via `argon2-cffi` — chosen over bcrypt to avoid 72-byte
  truncation. **The login throttle must stay ahead of the hash.** Argon2 costs 31ms per
  attempt, so `/auth/login` saturates a core at ~32 req/s of junk, and `equalise_timing()`
  makes nonsense input cost the same as a real try. `security/login_throttle.py` is checked
  before the user lookup for that reason; moving it after would still pass every behavioural
  test and leave the process exposed. **There are three separate instances** of it —
  reviewer login, reader login, and the contact form (`api/deps.py`). One shared counter
  would make the endpoints each other's denial of service: readers and reviewers arrive from
  the same office NAT, and enough failed reader logins would lock the console out of its own
  IP budget. Failures are counted for unknown emails too, or the 429
  becomes the account-enumeration oracle the equal timing exists to prevent. Unlike
  `retrieval/throttle.py` it rejects instead of sleeping — a delay only slows a client that
  chooses to wait, which is the honest user and not the attacker.
- **`MEDIA_ROOT` holds live article assets, not a cache.** Published articles link into it,
  so it belongs in the backup set with the database. `services/media.py` decides what a file
  is from its first bytes (never the filename or Content-Type) and **SVG is refused** — it is
  a script host, and this directory is served from the app's own origin. A document may only
  reference media the store wrote; that is checked on autosave *and* again in
  `ReviewService.approve()`. Do not relax either check to make a paste work.

## Deliberately out of scope

Full-text retrieval and chunking, the agentic query-refinement loop, and the AWS deployment are
deferred (DESIGN.md §11). `LLMQueryStrategy` is a declared seam that raises —
`TemplateQueryStrategy` is what runs. A `source_passages` table sits commented in the migration
so the FK direction is already settled.

**What has and has not met a real response** (checked 2026-08-21; keep this honest, it is what
tells you which code to trust):

- **Exercised.** PubMed and OpenAlex parsing, and the full six-stage pipeline — two runs on
  2026-08-20 produced 92 cached sources and two articles.
- **Exercised, contrary to what this file said until 2026-08-21: Voyage.** All 92 rows carry
  `sources.embedding_model = 'voyage-4'` with populated 1024-d vectors, created inside those
  two runs. That string is written from `VoyageEmbeddingProvider.model_id` and from nowhere
  else; the Ollama provider namespaces its own as `ollama/…`. So a real Voyage call was made,
  with a real key, and the note claiming otherwise was wrong.
- **Unresolved: which generative provider those runs used.** Both defaults said `ollama` by
  2026-08-20, but the embedding default said `ollama` too and was plainly overridden, so the
  defaults prove nothing about the environment. Each run finished in ~56s including retrieval
  and 73 embeddings, which is fast for local synthesis of a 10k-token prompt but is not
  evidence. Nothing in the database records it — which is precisely the gap the token ledger
  closes going forward, since `pipeline_stage_runs.model` now names the model per call. The
  two historical runs are backfilled with tokens and a **NULL model**, so they report
  `estimatedCostUsd: null`: inventing an id would have priced them against a model they may
  never have run on.
- **Not exercised.** Anthropic: the client and its token accounting are written against
  documented response shapes only. Europe PMC and Semantic Scholar are wired but have not been
  enabled in a run (`ENABLED_PROVIDERS=pubmed`).

That data corrected the classifier twice (DESIGN.md §4) and left one thing open.

**Abstract heuristics must never run on a paper whose tags already say something.** A trial
protocol and a narrative review both describe randomised trials at length — that is what they
*are* — so falling through to prose scored three protocols as `rct` and one plainly-tagged
`Review` as `systematic_review`, and the latter set a real article's evidence grade.
`NEGATIVE_PUBLICATION_TYPES` now ends classification at `unknown` for those tags, checked
*after* the map so a positive identification still wins. Protocols are refused in
`RetrieveStage` before the cache sees them. Watch both directions when editing this: mapping
`Review` straight to `NARRATIVE_REVIEW` was tried and reverted, because it demoted genuine
systematic reviews PubMed had tagged only with the supertype. `Review` is a floor the text may
raise **within the review family only**.

Classification is Python, so no migration can backfill it — run
`python -m scripts.reclassify_sources` (dry by default, `--apply` to write) after touching
`PUBLICATION_TYPE_MAP`, `NEGATIVE_PUBLICATION_TYPES`, `RETRACTION_PUBLICATION_TYPES` or
`TEXT_PATTERNS`. Skipping it means the fix applies only to papers nobody has fetched yet.

**Closed 2026-08-21:** a `supported` verdict could rest on a single cited systematic review,
because `check_verdict_within_grade` read the strongest cited study and never counted them.
`max_verdict_for_claims` now requires `QUORUM_FOR_SUPPORTED` sources per claim — see invariant
#3 above. The queued article that was in exactly that state still passes: it cites an RCT *and*
a systematic review for its one claim.
