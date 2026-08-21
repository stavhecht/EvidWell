# EvidWell — Architecture & Design

Evidence-checked wellness content. A Pinterest-style public feed of short articles that
state what a product or trend claims, what the research actually shows, and a plain-language
verdict — every factual sentence carrying a citation to a real paper, every article approved
by a human before publication.

**Status:** implemented and verified against a live Postgres 16 + pgvector — 365 tests pass with
zero skips, the migration applies cleanly, and every endpoint has been exercised end to end.
The pipeline has run end to end against **live PubMed and OpenAlex** with local Ollama models,
producing two articles from 92 real cached papers. The caveat is now narrower and detailed in
README → *Verification status*: the **hosted** providers (Claude, Voyage) have never been
called, so their clients and the token accounting remain written-against-documentation.

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
| 3 | Evidence grade **and quantity** cap verdict confidence | `evidence/grading.py::max_verdict_for_claims()`. Two questions: how good the best cited source is, and how many there are. A `supported` verdict backed only by in-vitro/animal sources is a **validation failure**, not a style note — and so is one resting on a single trial, since `supported` needs two sources at a supported-tier grade, **per claim**. The cap is applied twice: as an instruction in the synthesis prompt, and as a hard post-check the model cannot talk its way past. |
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
| #3 evidence caps verdict | `evidence/grading.py::max_verdict_for_claims` | `test_invariants.py` — including that an *uncited* strong source cannot raise the ceiling — and `test_evidence_quorum.py` for the two-source rule and per-claim scoping |
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

**Login is rate limited, and the limit runs before the hash.** Argon2id being memory-hard is
the point of choosing it and also a liability on an unauthenticated endpoint: measured here, a
single junk login costs the server **31ms of CPU**, so **32 requests per second saturates a
core**. The `equalise_timing()` dummy verification that defeats account enumeration means
nonsense input costs exactly as much as a real attempt. So `/auth/login` was a CPU exhaustion
vector before it was a password oracle, and a limiter placed after the user lookup would have
fixed the second and not the first. Refusing costs 0.35µs — **~88,000× cheaper than the hash it
avoids**, which is the asymmetry the whole mechanism depends on.

`security/login_throttle.py` counts failures against two keys: a tight per-IP budget (10 per 5
min) that stops spraying and the CPU flood, and a looser per-account one (20 per 15 min) that
is the only defence against a distributed attack on a known reviewer address. Both are needed
— per-IP alone misses the patient attacker, per-account alone lets one source spray many
addresses and does nothing about junk emails, which hit no account at all. Failures are counted
for an unknown email exactly as for a wrong password, or the 429 becomes the enumeration oracle
the equal timing was there to prevent.

Three shapes worth defending, because the obvious alternative is wrong in each case:

- **It rejects rather than delaying.** A progressive delay only slows a client that waits for
  the response; an attacker firing 500 concurrent requests waits four seconds *in total*, while
  the reviewer who mistyped waits four seconds for real. It puts the friction on the honest
  user. Note this is the opposite of `retrieval/throttle.py`, which sleeps — correct for pacing
  our own outbound calls, backwards for a server.
- **Blocks escalate but always expire** (60s → 5min → 30min, capped; tier resets after a quiet
  hour). A flat window is a rate, not a deterrent — 10 guesses a minute forever is 14,000 a day
  at no cost. The cap and the expiry are what keep this from being a lockout: there is **no
  user-management endpoint in this API**, so a block needing an admin to clear it would require
  a shell on the server, and anyone who knows a reviewer's email could halt publishing at will
  (`approve()` is the only path to `published`).
- **The client IP is the socket peer, never `X-Forwarded-For`.** Nothing sits in front of
  uvicorn today, so the header is attacker-controlled: honouring it would let one client mint a
  fresh identity per request and bypass the IP budget entirely — worse than no limit, because
  the endpoint would look protected. Behind a load balancer this becomes
  `uvicorn --proxy-headers --forwarded-allow-ips=<balancer>` (§10), not a code change here.

The state is an in-process dict behind a `LoginThrottle` Protocol. The honest limitation: N
uvicorn workers means N× the budget and a restart clears it. Neither matters with one local
process, and the Protocol is where a Redis implementation goes when it does. The table is
capped and evicts only *spent* entries — never blocked ones, or flooding it from fresh
addresses would release your own block. That holds because reaching a block costs ten Argon2
verifications, so the memory attack is bounded by the CPU attack it has to pay for first.

What this cannot do: with a password alone, blocking distributed guessing against a known
account and guaranteeing that account's owner is never blocked are not simultaneously
achievable. MFA or a trusted-device cookie is the real answer; until then the account budget is
set loose enough to fire on an attack rather than an afternoon.

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

**The orchestrator owns every transaction boundary, and commits per stage.** No stage commits
and the worker does not commit; a successful stage is committed by the orchestrator and a
failing one is rolled back by it.

One transaction per run was the obvious first shape and it was wrong in three ways. A synthesis
failure discarded the `sources` rows and the embeddings RetrieveStage had just paid for, so the
retry re-fetched and re-embedded everything — the cache defeated at exactly the moment it
exists for. The connection sat `idle in transaction` for the length of every model call
(measured: `xact_age` tracks the call second for second), holding the row locks the cache takes
on matched sources, so a second worker on an overlapping topic would block behind them for
minutes. And each in-flight run pinned a pooled connection regardless of whether it was using
one.

Per-stage commit is safe because `sources` is an idempotent cache with no invariant attached,
and the only stage writing article data is PersistStage — one stage, so still atomic in itself.
It is also what the Step Functions target does anyway: one Lambda, one connection, one
transaction per state. This narrows the gap between local and deployed rather than widening it.

**The final stage is the exception**: its write commits together with the run's completion row.
Separating them opens a window where an article is committed but its run still reads `running`,
so a worker killed there has the run requeued as stale and writes a *second* article for the
same topic. Persist is last, so nothing long-running follows it and the atomicity costs
nothing.

**Retryable failures requeue, up to `PIPELINE_MAX_ATTEMPTS`.** `StageError.retryable` was
declared from the beginning and read by nothing; the cases that set it — a throttled provider,
a claim that went unsearched (§4) — are exactly the ones a second attempt fixes, and they are
what makes the surviving cache pay for itself. A requeued run gets `next_attempt_at` set,
because the poll interval is five seconds and a run requeued without a delay spends its whole
budget on the same 429 inside fifteen seconds. `attempts` is incremented when a worker
*claims* the run, not when it finishes, so a run that reliably kills its worker cannot cycle
forever. Stage rows are kept per attempt rather than overwritten — diagnosing a run that
succeeded on its third try means seeing what the first two did.

**A crash is recovered by heartbeat, not by a duration cutoff.** A run row says `running`
because a worker said so, and nothing retracts that if the worker is killed — the console then
reports work no process is doing, indefinitely. The first version of this compared `started_at`
against a 30-minute cutoff and swept once, at worker boot, which was wrong in three separate
directions:

- **Stuck forever.** A worker killed at 14:00 and restarted at 14:01 saw a one-minute-old run,
  judged it healthy, and never swept again. The row stayed `running` for the life of the
  process — the failure mode the sweep existed to prevent, reached by the sweep itself.
- **Requeued while alive.** A duration cutoff cannot distinguish slow from dead. A cold local
  model plus a provider backoff puts a legitimate run past any fixed number, and requeueing one
  starts a *second concurrent execution* — the double-article write the paragraph above makes
  `_finish_run` atomic to prevent, arriving through a different door.
- **Unbounded lives.** Recovery ignored `attempts`, so a run that kills every worker claiming
  it was resurrected indefinitely.

`pipeline_runs.heartbeat_at` is pinged on a timer by the worker for as long as
a run is in flight, so staleness means *no sign of life for two minutes* rather than *started a
long time ago*. That detects a kill in ~2 minutes instead of 30 and cannot fire on a live run
at any duration. On a timer rather than between stages because a single synthesis call against
a local model runs for minutes, so a per-stage ping would need a window longer than the slowest
stage — which is the coarse timeout being replaced. The ping never raises: a heartbeat that
dies while its run continues makes the run *look* abandoned, which is the double-execution case
again, so a failed beat is logged and retried.

The sweep runs between polls, never during a run, so a worker cannot sweep its own in-flight
run whatever its heartbeat is doing — a property of where the call sits, and the reason it is
not a background task. Recovery spends the same budget as a retryable failure (which is what
counting `attempts` at claim time is *for*): budget left requeues behind the same
`retry_delay` ladder, budget spent fails the run with `attempts_exhausted`. The stage rows the
dead worker left open are closed out in the same pass, both because a stage claiming `running`
under a queued run is a worse lie than either alone, and because the stage name is the one
piece of diagnosis a hard kill otherwise destroys — "died during synthesize" points at a model
timeout, "died during retrieve" at a provider.

`WORKER_STALE_AFTER_SECONDS` must stay at least 3× `WORKER_HEARTBEAT_SECONDS`, checked at
startup: set closer, a single slow `UPDATE` declares a live run dead, and the cost of that is
a duplicate article rather than a log line.

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

**Multi-source citations are accepted in both spellings — `[S1][S5]` and `[S1, S5]` — and the
marker pattern is defined once, in `domain/contracts.py`.** The comma form was originally
rejected, and the cost of that was a whole draft: the prompt's only example was a single handle,
so llama3.1:8b wrote `[S1, S5, S8]`, the body failed to render, and the article was written
`validation_failed`. That status never enters the review queue, so the run *looked* like nothing
had happened. Strictness at the parser is not free when the thing being parsed is generated:
rejecting a spelling nobody documented discards work rather than catching an error.

The single definition matters more than the widening. `body_text_to_doc()` and
`extract_handles()` both read markers, and they feed different things — the document tree and
`all_cited_handles()` respectively. If the extractor cannot read a form the parser renders,
`was_cited` is false for a source the article visibly cites and `check_beats_are_cited` reports
an uncited beat that is cited on the page. Validation would be measuring a document nobody is
looking at.

Malformedness is checked by *elimination*: strip every well-formed marker, and any bracket still
standing is a parse failure. The earlier "not a valid marker" pattern had to enumerate the ways
a marker can be wrong, and missed unterminated runs like `[S1, S5` — which matched nothing and
fell through as literal text, printing a broken marker into the finished article.

**Every nullable JSONB column uses `NullableJSONB`, not bare `JSONB`.** SQLAlchemy's JSON types
default to `none_as_null=False`, which persists a Python `None` as the JSON literal `null`
rather than as SQL `NULL`. That breaks the `COALESCE` above — a never-edited article renders as
JSON `null` instead of falling through to the draft — and it breaks `WHERE error IS NULL` for
every stage the orchestrator records as succeeded. Both were live: the first article written
after the schema reset had `jsonb_typeof(edited_content) = 'null'` and all six of its stage rows
had the same in `error`.

It stayed invisible because `json.loads('null')` is `None`, so every Python reader
(`display_content`, `ReviewService.approve`) took the correct branch through `or`. The defect
only appears in the SQL this document and `services/card.py` both name as the mechanism, which
is the worst place for it to first surface — enforcement that is documented but not actually in
force reads exactly like enforcement that works.

### 3.4b Reviewer media: uploaded, never linked

A reviewer can add a picture from their own machine and a YouTube video to a draft. Both are
block-level TipTap nodes beside the beat paragraphs, so neither disturbs the three beats —
those are addressed by `attrs.beat`, and a picture above beat 1 does not change which
paragraph the feed card is derived from.

Two rules, enforced in `services/media.py` and re-checked at approve:

**Images are uploaded, never linked.** `POST /api/console/media` stores the bytes and returns
the only `src` shape the server will accept back. A linked image breaks when the other site
reorganises and tracks the reader until it does, so the image node's `parseHTML` declines any
other `src` — a picture copied out of a web page is dropped, while the same picture *dropped
as a file* is uploaded and kept. What a file is is decided by its first bytes; the upload's
filename and `Content-Type` are read and discarded. **SVG is not on the allowlist**: it is a
script-bearing document, and one served from our own origin inside a published article is
stored XSS against every reader of it. PNG, JPEG, GIF and WebP cannot carry script.

**A video is an id, not a URL.** The document stores the eleven-character YouTube id and the
renderer builds the embed src from it, so no string anyone typed reaches an iframe intact. The
public page renders a still until it is pressed — an embedded player is about a megabyte of
Google's JavaScript executed on every reader of every article that has one, and mounting the
iframe on the click makes being tracked by YouTube something the reader chose.

Storage is content-addressed local disk — filename is the SHA-256 of the bytes, so uploads
dedupe, cannot collide, and nothing client-supplied reaches the filesystem. Served at
`/api/media/…` rather than a prefix of its own because the dev proxy and any deployment
already route `/api`. The S3 swap is that one module; orphan collection (an image dropped from
a draft leaves its file) is deferred with the rest of §11.

**Layout is two attributes, never CSS.** A reviewer can resize a picture or a video and wrap
prose around it — Word's "Square" — and the whole model is `width` (20–100, a percentage) and
`align` (`none` / `left` / `right`). Both are validated server-side beside the `src` check, on
autosave and again at approve.

The three things that follow from storing data rather than a stylesheet:

- **The same classes render both sides.** `mediaWrapClass()` in `lib/media.ts` produces the
  `.ew-media` classes that `styles/evidwell.css` defines, and the console editor and the
  published article both use them. That identity is the only way "the reviewer approves what
  publishes" is true of layout and not just of words.
- **It collapses on a phone.** The float and the width live inside one `@media (min-width:
  640px)` block, so under that every picture is a full-width block — a 40% float in a 320px
  column leaves about fifteen characters a line. An inline `style` could not express this,
  which is the concrete reason the document does not store one.
- **Nothing reviewer-authored reaches a reader as style.** The renderer turns one of three
  words into a class it already ships. There is no string a reviewer can supply that becomes
  CSS on a published page.

A percentage rather than pixels because the editor's column (~850px) and the article's measure
(64ch) differ, and a percentage is the only unit that means the same in both. No x/y
coordinates: media stay siblings of the beat paragraphs and are placed by being dragged
between them, which is what keeps `attrs.beat` — and therefore the derived card — meaningful.

In the editor the two nodes are React node views (`console/MediaNodeView.tsx`). Worth knowing
before editing it: TipTap's React renderer wraps a node view in an element of *its own*, and
that element is the one in the editor's flow, so it is the one that has to float — hence the
`attrs` option on `addNodeView()` rather than styling the component's own root. Dragging the
corner writes the width straight to that element's custom property and commits a single
attribute on release, so one gesture costs one undo step and one autosave rather than sixty.

### 3.5 Embeddings: Voyage, behind an interface

`voyage-3` tier: strong on scientific text, cheap enough to embed thousands of cached
abstracts. `EmbeddingProvider` is a Protocol with one method, so OpenAI is a config swap.

**Vector width is a single config constant** (`settings.embedding_dim`, default 1024) because
changing it is a table migration and an index rebuild, not a config change. The migration
templates the dimension.

### 3.6 LLM: a model setting per job, structured outputs, no sampling params

Both generative calls go through `client.messages.parse()` with a Pydantic `output_format`,
which constrains the response to the schema and hands back a validated model instance. This
removes a whole class of failure (malformed JSON, missing keys) before our own validation runs.

**The two calls carry separate model settings, because they are different jobs — though both
currently resolve to the same model:**

| Call | Model | Why |
|---|---|---|
| Extraction | `claude-sonnet-5` | Topic → product + claims. Mechanical and schema-constrained, and *intended* for a small model — see below for why it isn't on one. |
| Synthesis | `claude-sonnet-5` | Where the guarantees are produced. Must cite only supplied `S`-handles (#2) and keep the verdict under the evidence ceiling (#3). A failure here is the expensive kind. |

Both are config (`settings.extraction_model` / `settings.synthesis_model`), not constants.
**If drafts start landing in `validation_failed`, raise the synthesis model first** — that is
the signal a downgrade was too aggressive, and `pipeline_runs` records it per stage.

The asymmetry the split was built on is still the right one: validation is a hard post-check
that no model talks its way past, so a weaker *synthesis* model degrades **yield** (more
rejected drafts), not **correctness**. That is what makes downgrading safe to try at all.

**Extraction is the case where it did not hold, which is worth recording.** `claude-haiku-4-5`
was the original choice and was reverted after sampling: it left `ingredients` empty in 9 of 10
runs. That is not a schema failure — the field is optional, the response validates, the stage
reports success — and the effect lands two stages away, where the query is built from the
ingredient list and quietly loses its anchor. So the cheap-model failure mode here is not "loud
and cheap" as first assumed; it is the same silent-degradation shape this document keeps
returning to, and it is why extraction sits on Sonnet.

**The consequence was worse than this section originally claimed, and the correction is the
interesting part.** The prediction here was that an unanchored query searches the product's
*brand name*, returns nothing, and routes to the `no evidence` path — a wrong article, but a
*cautious* one. That is not what the code did. `_compose` fell back to **claim keywords**, so a
run on creatine with `ingredients: []` searched `(workout AND performance)` and got 50 real
papers about pre-workout blends, CrossFit programming and probiotics — none of them about
creatine. Synthesis cited two of them, citation validation resolved both handles, and the
article shipped a `supported` verdict at `systematic_review` grade. Failing safe and failing
loud are different properties, and assuming the first gave the second is what let this run.

Two changes follow. The subject group now falls back to `product` (required, always present)
before it falls back to anything else, so the empty-`ingredients` case is anchored rather than
merely detected. And when nothing yields a substance, `TemplateQueryStrategy` raises
`UnanchoredQuery` and `RetrieveStage` fails the run — it does not warn. A warning was the
original design here, and a warning on a worker's stdout is not a control: every downstream
signal still read as success. This is the same rule as the throttle one in §4 — "we searched
and found nothing" is publishable, "we searched for the wrong thing" is not — and the two
arrive at synthesis looking identical, so they have to be separated where the query is still
visible.

A second, harder constraint now pins it there: both calls pass `thinking` explicitly (below),
and Haiku 4.5 predates adaptive thinking, so that argument **400s** on it. Reaching for a small
extraction model again means handling that difference, not just changing the setting.

Notes that matter for implementation:
- `temperature` / `top_p` / `top_k` are **rejected** on Opus 5 and Sonnet 5 — a request
  carrying them returns a 400. We send none on either call, so the code is safe across the
  whole model range. Behaviour is steered by prompt only.
- `max_tokens` caps thinking **plus** output — so synthesis is sized generously (8K) even
  though the article is ~300 words, and extraction gets 4K for a response of a few hundred.
  Every call passes an explicit cap; there is no unbounded request in the codebase.
- **`thinking` is passed explicitly rather than left to the model default**, because that
  default is not stable across the range: Sonnet 5 and Opus 5 think when it is omitted, Opus
  4.8 and 4.7 do not. The model is a *setting*, so an implicit default would let
  `EXTRACTION_MODEL` silently decide whether the budget above is spent on thinking or on
  output — config changing behaviour the stages are supposed to be insulated from. The cost of
  being explicit is the Haiku incompatibility noted above; it is the better trade.
- **Hitting the cap is diagnosed, not inferred.** A truncated structured response has no
  parsed output at all, so it arrives looking exactly like a schema failure and sends you to
  `domain/contracts.py` instead of to `max_tokens`. `_check_truncation` reads `stop_reason`
  and names the real cause. This is a thinking-budget symptom specifically: the two share one
  allowance, so a thinking-heavy turn on a tight cap produces it while the schema is fine.
- The synthesis system prompt is stable across calls and marked with `cache_control`; the
  per-article source block is volatile and goes after it. **The minimum cacheable prefix is
  per-model and not monotonic** — 512 tokens on Opus 5, 1024 on Sonnet 5, 4096 on Haiku 4.5 —
  and a prompt below it silently fails to cache, with no error. Measured with
  `messages.count_tokens`: the synthesis prompt is **1285 tokens**, so it still caches on
  Sonnet 5, but with only ~260 tokens of headroom — trimming it would silently break caching.
  The extraction prompt is **306 tokens** and caches on no model; it never did on Opus 5
  either (512 minimum), so that is not a regression, and at 306 tokens it does not matter.

### 3.7 Cost: store tokens, price at read time

Every number in the paragraph above was a claim nothing could check. `TokenUsage` had carried
`cache_read_tokens` and `cache_write_tokens` since the Anthropic client was written, and the
persistence layer dropped both — so the `cache_control` breakpoint that section justifies at
length was, by construction, unmeasurable. `pipeline_runs` stored one summed input/output pair
and no model id, which is not "imprecise" but **unpriceable in principle**: extraction and
synthesis are separately configurable and may be different models at different rates.

Three decisions, each with a cheaper alternative that fails quietly.

**The ledger sits on `pipeline_stage_runs`, not on the run.** That table is already at the
grain the question needs — one row per run × attempt × stage, kept per attempt rather than
overwritten. Cost surprises come from retries, and a run-level rollup cannot show them. The
run keeps a rollup so "what did this consume" stays one row read, and that rollup is written
as an **increment**, not an assignment: `ctx` is rebuilt per attempt, so assigning would
overwrite the first attempt's tokens with the second's and report the cheapest attempt as the
run's whole cost.

**Four token columns, not two.** The classes are priced an order of magnitude apart in both
directions — a cache read is a tenth of a fresh input token, a cache write a quarter more than
one. Folding them into `input_tokens` would make caching look free and the first call look
cheap, in opposite directions, so the errors would not even cancel.

**No cost column anywhere.** Tokens are a fact about a call that happened; a price is an
external number that changes. `llm/pricing.py` computes cost at read time, so a price
correction fixes history instead of leaving it silently wrong. The trade is the mirror image —
past runs re-quote at today's rates — and that is right while these figures inform a build
decision rather than an invoice. If a run's cost ever has to be *owed* rather than
*estimated*, this becomes a table with effective dates and the choice inverts.

Two things follow that are easy to get backwards:

- **An unpriced model reports `null`, never `0`.** They are both falsy and mean opposite
  things, and the silent zero would appear exactly when someone points `SYNTHESIS_MODEL` at
  something new — the one moment "this run cost nothing" would be believed. A run total is
  all-or-nothing for the same reason: extraction alone is ~1% of a run, so reporting it when
  synthesis is unpriced yields a number both plausible and wrong by two orders of magnitude.
  Model ids are provider-namespaced (`anthropic/claude-sonnet-5`) so a *local* model, which is
  genuinely free, is never confused with an unpriced hosted one.
- **Tokens spent by a call that failed are still recorded**, and this is the case worth having.
  Truncation spends the entire 8K synthesis budget and produces nothing, so a run reported at
  zero would make the pipeline look cheapest at the moment it is burning the most. `LLMError`
  therefore carries `usage`, the stage records it before converting to a `StageError`, and
  `_abandon` writes it *through* the rollback that discards the stage's own writes — the
  rollback undoes ours, not the provider's charge. This is also why `record_usage` mutates the
  context in place rather than returning a copy: a raising stage returns no context.

**Embeddings are outside the ledger, deliberately and visibly.** `EmbeddingProvider` returns
bare vectors, and threading usage back through it would change the Protocol's return type and
every caller. The gap is about 28k tokens per cold run (~$0.002 at voyage-4 rates), roughly 1%
of a synthesis call. The number that would justify the refactor is not a run — it is a full
re-embed of the cache, and that is a script, not a pipeline stage.

---

## 4. Retrieval design

**The scholarly APIs are the search index. pgvector is a re-ranking and caching layer.**
No attempt is made to pre-index PubMed.

### Pass 1 — keyword recall (external APIs)

Per claim, query providers in parallel; each returns candidate papers normalised to a common
`CandidatePaper` shape (title, abstract, year, journal, study type, citation count, PMID, DOI,
URL, source API). Target 20–50 candidates per claim after dedup.

**Identity is a union-find over every identifier a record carries**, not one key per record
(`retrieval/dedup.py`). The same paper routinely arrives from three providers with three
different identifier *subsets* — Europe PMC knows the PMID, Semantic Scholar the DOI, PubMed
both — and a single preferred key cannot unify those: the DOI-only and PMID-only records
produce different keys, survive as two candidates, become two `sources` rows, and reach the
model under two handles, which it cites as two independent findings. One record carrying both
identifiers joins the groups, so a single PubMed hit bridges the other two.

That failure is invisible with PubMed alone (its records carry both), which is exactly why it
is guarded now: it appears the day the other three providers come online, and it appears as
*more* apparent corroboration rather than as an error. `retrieve.duplicates_merged` in the
stage metrics is the number to watch — near-zero cross-provider overlap means matching has
stopped working.

Titles never join two records that carry identifiers. A normalised title is an identity key
only for a record with no identifier at all. Erratum notices and conference abstracts repeat
their parent's title verbatim, and a wrong merge is invisible afterwards because the result
simply looks like one paper: under-merging costs a prompt slot, over-merging silently deletes a
study.

On merge the group keeps the union of identifiers, the strongest study-type classification, the
highest citation count — and the abstract from the **most trusted provider**, not the longest
one. OpenAlex ships an inverted index rather than text, so its reconstruction is frequently
longer than the clean original while carrying tokenisation damage, and that text is both what
gets embedded and what the model reads as evidence.

Provider roles:

| Provider | Role | Notes |
|---|---|---|
| PubMed E-utilities | Primary recall for clinical evidence | MeSH terms + publication-type filters give the cleanest study-type signal |
| Europe PMC | Breadth + open-access full text (v2) | Also the fallback when PubMed rate-limits |
| Semantic Scholar | Citation counts, cross-domain | |
| OpenAlex | Broad coverage backstop | Used to fill gaps, not lead |

Explicitly excluded: Google Scholar and any scraping. SerpApi is reserved as a later fallback
only if genuine coverage gaps appear, and is not in the MVP.

**Pacing and throttles (`retrieval/throttle.py`).** Every provider is built behind a
`ThrottledClient`: a per-provider token bucket, plus bounded retries that honour `Retry-After`.
Both halves are load-bearing for correctness, not just for politeness.

The limiter sits at the *HTTP-call* level rather than around `search()`, because a PubMed
search is two requests — esearch then efetch — and the published ceiling counts requests. It is
per provider per **process**, while every documented limit here is per IP, so the configured
rates in `retrieval/factory.py` sit under the published ones to leave room for a second worker
on the same host. NCBI answers sustained overage by blocking the address, which no retry
recovers from.

The retry half exists because of what a 429 turns into downstream. A throttled provider that
merely fails degrades recall, a thinner evidence base yields a more cautious verdict, and a
cautious verdict is indistinguishable in the finished article from a correct one — the failure
has no visible symptom. PubMed makes this worse by signalling some throttles with **200 OK and
an error string in the body**, which parses cleanly as zero results; `pubmed.detect_throttle`
is passed to the client so those are retried rather than believed.

**A claim where every provider call failed fails the stage** (`StageError`, retryable). This is
the pairing with the zero-source branch in §5: "we searched and found nothing" is publishable,
"we could not search" is not, and the two arrive at synthesis as the same empty list. Coverage
is tracked per claim, not per run — two claims searching cleanly does not license a verdict on
a third that was throttled. `retrieve.rate_limited` is recorded separately from
`provider_failures` because the remedies differ: throttling wants slower pacing, failure wants
a look at the provider.

A consequence worth knowing: Semantic Scholar without an API key is **skipped at construction**
rather than returning an empty list from `search()`. A provider that answers without making a
request counts as a successful search, which would mask a claim whose only real provider was
throttled.

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

Step 1 happens in RetrieveStage, and **its row ids are carried forward on the context**
(`CachedCandidate` in `domain/contracts.py`) rather than looked up again. RankStage used to
re-upsert the entire candidate set purely to recover them — roughly a hundred statements
against rows written seconds earlier — so it now takes no `SourceCache` at all and reads only.
A model rather than a parallel `dedup_key -> source_id` map, because the map version drops any
candidate missing from it, and a source lost between retrieval and ranking is invisible
downstream: it reads as thinner evidence, and thinner evidence reads as a more careful verdict.

Refreshing matched rows is one `UPDATE ... FROM (VALUES ...)` per batch, not one per row. Warm
is the normal case — the cache exists so a repeat topic re-matches everything — and the
per-row loop made that the most expensive path instead of the cheapest. Measured on 100
candidates: **206 statements per warm run before, 4 after.**

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

### Classification: what the first live runs corrected

Everything above was designed before a real PubMed response existed. Two of the three defects
the first 92 cached sources exposed were in classification, and both came from one mistake —
`classify_study_type` treated a publication type that was *informative but unmapped* exactly
like *no metadata at all*, and fell through to reading the abstract.

That fallback is actively misleading for precisely the papers that reach it. A **trial
protocol** describes the randomised trial it intends to run, in that vocabulary; three scored
`rct`, whose ceiling is `supported`, on the strength of a study that has reported nothing. A
**narrative review** discusses the trials it surveys; one plainly tagged `Review` scored
`systematic_review`, was cited in a real draft, and set that article's evidence grade. The
function's own docstring promises to prefer the weaker classification when torn. It did the
opposite, and no amount of reasoning about the code surfaced that — only real data did.

Three changes, each narrow on purpose:

- **`NEGATIVE_PUBLICATION_TYPES` suppresses the text fallback.** Tags like `Clinical Trial
  Protocol`, `Editorial` and `Comment` say something real without naming a design, so they end
  classification at `unknown` rather than handing it to prose. Checked *after* the map, because
  a genuine RCT can carry `Comment` alongside its own type and a positive identification must
  outrank a negative one.
- **Protocols are refused at retrieval, not graded down.** A protocol reports no findings, so
  no verdict can honestly rest on it; admitting one can only cost a top-k slot. Dropped in
  `RetrieveStage` before dedup and before the cache, and counted in `protocols_dropped` — a
  number that climbs means the queries are drifting toward planned work.
- **`NARRATIVE_REVIEW` is a real tier**, below observational and above case report, ceiling
  `weak`. Sixteen of the 92 belonged there. Folding them into `UNKNOWN` would give the same
  ceiling, so the value is diagnostic rather than protective: "we retrieved sixteen narrative
  reviews" and "we retrieved sixteen unreadable records" call for opposite responses, and one
  number cannot say both.

The reverse mistake is just as easy and was made once in passing: mapping `Review` straight to
`NARRATIVE_REVIEW` demoted genuine systematic reviews that PubMed had only tagged with the
supertype while their titles said otherwise. So `Review` is a **floor** — narrative unless the
text says systematic review or meta-analysis, and never upgradeable into a trial. Over-trusting
a tag and over-trusting prose are the same failure pointed in different directions.

`scripts/reclassify_sources.py` re-derives grades for rows already cached, because
classification is Python and no migration can express it. Without it a classifier fix applies
only to papers nobody has fetched yet — the opposite of the ones that need it, since the cached
set is what recent articles were built from. It reports protocols already in the cache and
never deletes them: a row may be referenced by `article_sources`, and a published article's
provenance outranks tidying up a grade.

### The evidence quorum

Invariant #3 asked one question for most of this project's life: is the strongest cited source
good enough for this verdict? A single randomised trial answers it perfectly and still leaves
the article overstating, because **one study is a finding, not a conclusion**. Nothing stopped a
lone trial from carrying `supported`, the strongest thing the system can say.

So `supported` now additionally requires **two distinct cited sources at a supported-tier
grade** — RCT, systematic review, or meta-analysis. Two rather than one, and the reason is
local rather than clinical: convention holds that a good meta-analysis suffices on its own, but
that assumes a grade you can trust, and ours is a heuristic that has already been wrong in both
directions on real data (§4 above). A second source means no single misgrading can license a
confident verdict by itself.

Falling short drops the ceiling exactly one step, to `mixed` — "there is real evidence and it
is not settled", which is the honest description of one good trial. Dropping to `weak` would
understate an RCT as badly as `supported` overstates it. Below the top tier nothing changes:
those ceilings already say "provisional", and a quorum there would penalise thin evidence that
is being reported as thin.

**Counted per claim, and the article inherits its weakest claim's ceiling.** Counted
article-wide, two claims each backed by a single trial satisfy a quorum of two between them
while neither is corroborated — and the finished article shows no trace of which source backed
which claim. A claim with no cited source at all caps the article at `weak`; that is the
aggressive reading and it is deliberate, since asserting `supported` about a product while one
of its claims turned up nothing is exactly the overstatement this system exists to prevent.
`no_evidence` remains permissible everywhere: an article may always report finding nothing.

Two consequences worth stating plainly. `PromptSource.claims` exists only to make this
countable, and is **required and non-empty** — defaulted to `[]` it made every draft fail the
quorum while the report pointed at the verdict rather than at the missing attribution.
And `best_evidence_grade`, stored on the row and shown in the console, still records the best
grade cited: the quorum lowers the *ceiling*, not the description of what the article rests on.

What this cannot see: a meta-analysis and a trial it already includes are not independent
sources, and detecting that needs reference lists the abstract-only MVP does not fetch. Sources
are counted after cross-provider dedup, so one paper arriving from three APIs is one source —
that part is solid.

### Retractions: refused at ingest, re-checked on a schedule

A citation is a promise that a study says what we claim it says. A retraction withdraws the
study, and nothing in a cached row notices.

**Measured before anything was written** (2026-08-21), because the alternative was building a
fire brigade for a fire that might not exist: 90/90 cached PMIDs resolved against PubMed
`esummary`, 92/92 DOIs against Crossref `update-to`, **zero flagged**. Verified with a positive
control — PMID 9500320, retracted by *The Lancet* in 2010 — since "none found" from an untested
checker is indistinguishable from a broken one. Base rates say ~0.04 expected in 92 papers, so
this is a smoke alarm, and it was built as one.

**The protection that was assumed to exist did not.** `"retracted publication"` had been in
`NEGATIVE_PUBLICATION_TYPES` since the classifier was corrected, which was believed to cap such
a paper at `unknown`. It did not: `classify_study_type` consults the positive publication-type
map *first* and reaches the negative set only when nothing matched — correct reasoning for
`Comment`, which describes a different aspect of a paper that really is a trial. A retraction is
not another aspect. A retracted trial nearly always also carries `Randomized Controlled Trial`,
so it graded `rct`, ceiling `supported`, and the cap applied to exactly the papers it did not
matter for. This was found by a test written to assert the cap, which found the opposite.
Retractions are now checked **before** the map.

**Three mechanisms, because each covers what the others cannot.**

1. **Refused at ingest.** `RetrieveStage` drops a retracted paper before dedup and before the
   cache, alongside the protocol filter, and for the stronger version of the same argument: a
   protocol reports nothing so it cannot support a claim; a retracted paper *does* report
   something, and the record has repudiated it. A grade cap was never sufficient here — it
   governs how confident the article may sound and leaves the citation in place, so the article
   still says "one trial found X [S3]" pointing at a withdrawn study.
2. **A scheduled sweep** (`scripts/check_retractions.py`, dry by default). This is the half that
   earns the feature. Retractions land *after* publication, so the ingest screen only catches
   papers already retracted when first seen. Default scope is sources cited by a published or
   pending article — a few dozen papers, one batched call — with `--scope all` for the whole
   cache.
3. **Two providers**, because neither covers the corpus. PubMed batches 200 ids per request and
   is the cheap path but indexes only what it indexes; two cached rows have no PMID at all.
   Crossref costs one request per DOI and covers every paper that has one. A positive from
   either wins: two bibliographic databases disagreeing is not a tie to break by precedence,
   because one of them has newer data and it is the one saying yes.

**An unchecked paper is not a clean paper**, and this is the reliability story. `RetractionVerdict`
has three states, not two, and `sources.retraction_checked_at` is written **only** when a
provider actually answered — never on a transport error, a 500, or an id the provider cannot
resolve. Collapsing them records a check that did not happen, which then suppresses the *next*
sweep too, so the error compounds instead of being retried. This is the same failure
`retrieval/throttle.py` exists to prevent one layer down: a search that could not run must never
look like a search that found nothing.

That principle caught a live bug. NCBI does not omit an unresolvable id — it returns a record
carrying `{"error": "cannot get document summary"}` and no `pubtype`, which the first version
read as an empty type list and reported as **verified clean**. Found by a deliberately
unresolvable control in the live probe, not by review.

**A flagged article stays published.** The sweep sets `articles.retraction_flagged_at`, which
raises a banner on the public page and puts the row in front of a reviewer. It does not
withdraw anything. This is invariant #1 read in the direction it actually points:
`ReviewService.approve()` is the only path to `published` because a *human* decides what the
public sees, and an automatic withdrawal is the same machine making the same call in the other
direction. One retracted source among several does not necessarily invalidate a conclusion. A
reader is told before they read; a human decides what to do about it. Anything stronger is a
conversation about invariant #1, not a feature.

Nothing is deleted, either. A retracted source keeps its row: it may be referenced by
`article_sources`, and breaking a published article's provenance to tidy up a citation trades a
real guarantee for a cosmetic one.

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
`TemplateQueryStrategy` (claim + substance → MeSH-aware boolean query) because it is
deterministic, free, and debuggable. An `LLMQueryStrategy` implementing the same protocol can
be dropped in without touching the pipeline.

`build()` takes `product` and `ingredients` as **separate** arguments rather than one merged
list, because they are not interchangeable: `ingredients` names the actives and is optional,
`product` is required and may be a brand. A strategy prefers the ingredient list when it has
one and falls back to the product when it does not — merging them would OR a dead brand term
into every query that already names its actives. Product names carry packaging ("Creatine
Monohydrate Powder"), so the MeSH lookup also matches a known substance appearing as a whole
word inside a longer name; without that the fallback quotes the whole string and retrieves
nothing, which reads as `no_evidence` and is the *more* dangerous failure, since that verdict
is publishable.

Raising `UnanchoredQuery` is part of the protocol, not an implementation detail —
`LLMQueryStrategy` will need the same guarantee.

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

### Call 2, zero-source branch — the `no evidence` article

When ranking yields no usable sources, **no generative call is made at all**. `SynthesizeStage`
branches to `services/no_evidence.py`, which assembles the `SynthesisOutput` deterministically
from the extraction output: what the marketing claims, that a search found nothing meeting our
evidence criteria, and that absent evidence is not contrary evidence.

A model handed a product name and zero abstracts is exactly the ungrounded generation the rest
of the pipeline exists to prevent — and it would emit no citations, so `validate_draft()` would
have nothing to check and invariant #2 would pass by vacuum rather than by verification. The
template cannot hallucinate, costs nothing, and is the one article shape where determinism
loses no quality.

The draft then takes the ordinary path and passes validation **on its merits, not by
exemption**: the emitted handle set is empty so checks 1–2 have nothing to reject,
`no_evidence` is exempt from the cited-beat rule, and `best_grade([])` is `UNKNOWN`, whose
ceiling of `weak` sits above `no_evidence`. It reaches the queue as `0/0 citations resolve` and
a human still approves it.

Two causes reach this branch and are identical to a reader but not to us — nothing retrieved
(a genuinely unstudied trend) versus candidates retrieved and then filtered away by `min_year`
or the grade floor. `pipeline_stage_runs.metrics.no_evidence_cause` distinguishes them, because
the second is a signal that the filters are tighter than the corpus can satisfy.

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

`validation_failed` → `rejected` is also allowed, and it is the console's "discard" action.
Since there is no delete, rejection is the only way a draft leaves a queue tab; without this
edge, failed drafts have no available action at all and accumulate forever in the tab that
exists to make prompt regressions visible. They are still never approvable — the discard path
records a reason, it does not route around invariant #2.

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
| `POST` | `/api/console/articles/{id}/reject` | → `rejected` (reason required); also the discard path from `validation_failed` |
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
  `citation_count`, `url`, `source_api`, `embedding vector(N)`, `last_seen_at`, plus the
  retraction record: `retracted_at`, `concern_at`, `retraction_checked_at`, `retraction_note`
  (§4 — a NULL `retraction_checked_at` means *nobody successfully asked*, not "clean").
- **`article_sources`** — `(article_id, source_id, claim, citation_handle)`. Provenance.
- **`pipeline_runs`** / **`pipeline_stage_runs`** — observability, plus the retry state
  (`attempts`, `next_attempt_at` on the run; `attempt` on each stage row, so a retry adds a
  second set rather than colliding with the first). See §3.3. The stage row also carries the
  token ledger — `model` plus four token counts, priced at read time and never stored as
  cost (§3.7); the run's four counters are the lifetime rollup across attempts.

Indexes that matter:

```sql
CREATE UNIQUE INDEX sources_doi_key  ON sources (lower(doi)) WHERE doi IS NOT NULL;
CREATE UNIQUE INDEX sources_pmid_key ON sources (pmid)       WHERE pmid IS NOT NULL;
CREATE INDEX articles_feed_idx ON articles (published_at DESC) WHERE status = 'published';
```

**There is deliberately no HNSW index on `sources.embedding`.** An earlier draft of the schema
created one and nothing could ever use it. The only vector query is
`rank_for_claim`, whose score is cosine + grade + recency, so top-k is applied in Python and no
`LIMIT` reaches SQL; with a restrictive `id = ANY(...)` filter over one run's ~100 candidates,
the planner picks a bitmap scan on `sources_pkey` and an exact quicksort. `EXPLAIN` confirms
it, and the exact plan is *faster* at that size (0.53ms against 1.44ms over 3000 rows) as well
as being exact rather than approximate.

The index was meanwhile the dominant cost of writing to the table designed to grow forever:
**5.26s versus 0.11s to insert 1500 embedded rows, and 27 MB of index** — roughly 48× on write,
for zero reads. `rerank.py` also used to `SET LOCAL hnsw.ef_search` before each query, which
read as tuning that index scan; the statement worked and the plan never consulted it, which is
the general shape of the problem this file keeps returning to — a mechanism that runs cleanly
and affects nothing.

`0001_initial.sql` carries the `CREATE INDEX CONCURRENTLY` statement to bring it back, commented
beside the index it declines to create, along with the
condition that would justify it: a query issuing `ORDER BY embedding <=> $1 LIMIT k` without a
restrictive filter. The v2 passage search and a related-articles feature are the two candidates.
Neither exists.

The partial unique indexes are load-bearing, and they are also why the cache **cannot** be a
single `ON CONFLICT`. A paper's identity spans both of them, Postgres accepts one inference
clause per statement, and routing by whichever identifier the incoming record happens to carry
breaks the moment a record arrives *more complete* than the row it matches: a paper first seen
PMID-only, met again with a DOI, infers against the DOI index, finds nothing, inserts, and
violates `sources_pmid_key`. Union-find makes newly-complete records the normal case, so this
is the common path.

`SourceCache` therefore resolves first — one indexed read matching every identifier at once —
then updates matched rows **by primary key**, against which no conflict target exists, and
inserts only genuinely new papers. The race that reintroduces is two workers inserting the same
new paper between the read and the write; it surfaces as an `IntegrityError` inside a
`SAVEPOINT` and is retried once, by which point the row exists and resolves as an ordinary
match. The savepoint matters: the pipeline session is long-lived, and a bare rollback would
discard the rest of the run.

One paper matching two *different* rows means the cache split it under the old keying. That is
reported and resolved to the DOI row — never repaired inline, because repair means re-pointing
`article_sources` and deleting a row `ON DELETE RESTRICT` exists to protect, and a cache refresh
must not rewrite the provenance of published articles as a side effect of fetching abstracts.

---

## 10. Infrastructure: local now, AWS later

**Now:** Docker Compose (Postgres 16 + pgvector), FastAPI via uvicorn, worker as a separate
process, Vite dev server. Everything runs on one machine, and with the Ollama defaults, with
no API keys at all.

**The API is containerised; the worker and the migrations are not.** `backend/Dockerfile` is a
two-stage build — toolchain and a venv in the builder, `libpq5` and a non-root `evidwell` user
in the runtime — and `docker compose up backend` serves it on :8000 against the `db` service.
What it deliberately leaves out is as much of the design as what it includes:

- **`scripts/` is excluded from the image** (`.dockerignore`), so nothing in the container can
  apply a migration, while `migrations/` *is* copied in for reference. Schema changes are a
  deliberate act against a specific database, not something a container does to whatever it
  finds at boot — an image that migrates on start will eventually be scaled to two replicas and
  race itself, and in the target topology the thing being migrated is RDS. Migrate and seed
  from the host venv.
- **Compose runs no worker service.** A container that both serves and generates would tie the
  two lifecycles together, and they are already separate processes precisely because the
  pipeline is going to become Step Functions rather than a scaled copy of the API. The API in
  Docker with `python -m app.pipeline.runner` on the host is the honest shape of the target.
- **One uvicorn worker, scaled by replicas rather than pre-fork.** Each worker holds its own
  asyncpg pool, so pre-fork multiplies connections against the database's fixed budget while
  replicas at least make that count visible. It also keeps the `LoginThrottle` caveat (§3.2)
  from getting quietly worse: in-process state means N processes is N× the budget, and one
  replica per container keeps that ratio legible.
- `MEDIA_ROOT` is a named volume, not a bind mount or container-local storage — §3.4b's point
  that it holds live article assets rather than a cache, so losing it breaks published pages.

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
Retries, rate-limit handling, per-stage transaction boundaries and heartbeat-based crash
recovery are done (§3.3, §4) — they were pulled forward because each of them fails *silently*
rather than loudly, and a silent failure in this system reads as a confident answer. What
remains: `pipeline_runs` UI (including `attempts`, `nextAttemptAt` and `heartbeatAt`, so a run
in backoff does not read as a stalled worker), batch topic submission, and putting
`scripts/check_retractions.py` on a schedule — it exists and is dry-by-default, but a sweep
nobody runs is the same as no sweep, and that is the whole feature (§4). Cost tracking is
done (§3.7) — tokens are recorded per stage per attempt with the model that spent them, and
the API returns an estimate. It was pulled forward ahead of the hosted-provider switch on the
same reasoning as the rest of this phase: instrumentation added *after* the switch has no
before-picture to compare against, and the saving from prompt caching is exactly the kind of
quantity that is assumed rather than measured.

### Phase 6 — production
AWS migration per §10, S3 assets, managed Postgres, EventBridge schedule. The API image exists
(§10) and is the one piece pulled forward, because it is also what pins down the runtime
contract — non-root, no migration on boot, one worker per container. Still outstanding before
anything is deployable: a migration step that runs as its own task rather than at container
start, `--proxy-headers --forwarded-allow-ips` once a balancer is in front (§3.2 — until then
the login throttle must keep reading the socket peer), and secrets from somewhere other than an
`.env` file mounted into compose.

### Later, explicitly deferred
Full-text retrieval + chunking (v2 §4); agentic query refinement; multi-reviewer roles and
invites; SerpApi fallback; Next.js port if SEO becomes a priority.

---

## 12. Known risks

| Risk | Mitigation |
|---|---|
| Study-type metadata is inconsistent across providers | Classify from publication type + title/abstract heuristics in `evidence/grading.py`; store both raw and normalised. Unknown grades are treated as the *weakest* tier, so uncertainty caps confidence rather than inflating it. |
| Abstract heuristics read the *surveyed* literature as the paper's own design | Measured on real data, not anticipated: a trial protocol and a narrative review both describe randomised trials at length, and three protocols scored `rct` (ceiling `supported`) on that basis. The text fallback now runs only when the publication types are genuinely uninformative — `NEGATIVE_PUBLICATION_TYPES` suppresses it — and a bare `Review` may be sharpened only within the review family, never into a trial. |
| A grounded draft can still be misleading by omission | Human review is the backstop. The sources panel shows what was retrieved, not just what was cited, so a reviewer can see when something relevant was left out. |
| Abstracts overstate findings relative to full text | Known limitation of an abstract-only MVP. Grade caps mitigate; full text for pivotal sources is the v2 answer. |
| Retrieval finds nothing for a fringe trend | `no evidence` is a first-class verdict with its own short-article path, not an error. |
| PubMed rate limits | Per-provider token bucket keeps us under the ceiling; bounded retries honour `Retry-After`; the `sources` cache absorbs repeat topics; an API key raises the ceiling to 10 req/s. Europe PMC is available as a failover but is *not* used automatically — its weaker study-type metadata would quietly lower the verdict ceiling, so recovered recall would cost grade. |
| A throttled search reads as "no literature exists" | The failure with no visible symptom, and the reason §4's pacing exists. Throttles are retried rather than parsed (including PubMed's 200-with-error-body form), and a claim whose every provider call failed fails the stage instead of reaching the `no evidence` path. |
| A killed worker leaves a run claiming to be `running` | `heartbeat_at` is pinged on a timer while the run is in flight, and a periodic sweep recovers rows that have gone quiet (§3.3). Recovery spends the run's attempt budget, so a run that kills every worker terminates instead of cycling. |
| An unauthenticated endpoint that runs Argon2 is a CPU exhaustion vector | Measured: 31ms per junk login, so ~32 req/s saturates a core. `login_throttle` rejects before the lookup and before any hashing, at 0.35µs — 88,000× cheaper than the hash avoided (§3.2). |
| Rate limiting the login locks out the only reviewers | Blocks escalate but cap at 30 minutes and always expire on their own; a successful login clears both counters. Nothing requires an admin to clear, which matters because the API has no user-management endpoint. |
| A cited paper is retracted after we publish | Refused at ingest, and re-checked on a schedule against PubMed and Crossref (§4). A flagged article raises a reader-facing banner and a console row rather than being withdrawn automatically — one retracted source among several need not invalidate a conclusion, and the human decides. |
| A retraction check that could not run reads as a clean bill of health | `RetractionVerdict` has three states and `retraction_checked_at` is written only when a provider actually answered. This caught a real bug: NCBI returns an `error` record rather than omitting an unresolvable id, and reading it as an empty publication-type list marked unlookuppable papers verified clean. |
| A model call that fails is billed and reported as free | Truncation spends the whole 8K synthesis budget and returns nothing, so the most expensive failure would look like the cheapest run. `LLMError` carries its usage, the stage records it before raising, and the ledger write survives the rollback that discards the stage's own writes (§3.7). |
| An unpriced model reads as a free one | A price lookup miss returns `null`, never `0`, and a run total is `null` if any consuming stage is unpriced. Model ids are provider-namespaced so a genuinely free local model stays distinguishable from one nobody has priced. A test asserts the clients' default models are all in the table, which is the likeliest way it goes stale. |
| Recovery requeues a run that is merely slow, and it executes twice | Why staleness is measured from the heartbeat rather than from `started_at`: a live run is never quiet, at any duration. The heartbeat never raises, the staleness window is validated at 3× the ping interval, and the sweep runs between polls so a worker cannot sweep its own run. |
