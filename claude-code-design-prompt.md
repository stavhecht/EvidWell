# Claude Code brief — Initial design: evidence-checked wellness content app

## Your task

Produce the **initial design and scaffold** for the project described below — architecture, data models, interface/contract definitions, the AI prompt templates, and a phased build roadmap, plus a repository skeleton with clearly separated modules and stubbed key functions.

**This is a design task, not a full implementation.** Do not build the whole system. Lay solid foundations I can review before we implement. If anything below is ambiguous or underspecified, ask me before making irreversible structural choices.

---

## 1. What the product is

A Pinterest-style (masonry) web app that publishes short, **evidence-checked** articles about wellness news, products, and trends. For a given product or trend, each article states:

- what it claims to target / do,
- whether its ingredients or the trend are actually supported by real research,
- a plain-language verdict, backed by citations to real scientific papers.

The backend generates these articles by querying scholarly literature APIs and using an LLM under **retrieval-augmented generation (RAG)** to write a grounded draft with citations. Every draft is reviewed and approved by a human before it goes live.

## 2. Non-negotiable principles (design everything around these)

1. **Human-in-the-loop.** The AI pipeline only ever produces a *draft* in a `pending_review` state. Nothing is published automatically. A human approves (and may edit) every article before it reaches the public feed.
2. **Grounded, never hallucinated.** The LLM may only cite sources that were actually retrieved and passed into its prompt. Before a draft enters the review queue, programmatically verify that every citation in the output maps to a real source in the input and resolves to a valid PMID/DOI. Reject drafts that fail.
3. **Honest about evidence strength.** Evidence quality is graded (see §5). A confident-sounding abstract must not produce a confident verdict if the underlying studies are weak. Length and certainty scale with the strength of evidence.
4. **Not medical advice.** Every article carries an "informational, not medical advice" disclaimer. Frame content around ingredients/claims and the evidence for them — never around attacking specific brands.

---

## 3. Tech stack

- **Frontend:** React + Vite, TypeScript, Tailwind CSS, shadcn/ui. Masonry feed via `masonic` (virtualized). Data fetching with TanStack Query, routing with React Router.
  - *Decision to flag to me:* plain Vite SPA is the default. If SEO / public discoverability is a priority for the article feed, Next.js (SSR/SSG) would be the better choice. Note the tradeoff in your design and let me decide.
- **Backend:** Python + FastAPI.
- **Database:** PostgreSQL with the `pgvector` extension (HNSW index for vector search).
- **LLM:** Anthropic Claude API for extraction and synthesis.
- **Embeddings:** an embedding-model API (Voyage AI or OpenAI) — text in, vector out. Keep the provider behind an interface so it's swappable.
- **Scholarly data sources:** PubMed (E-utilities API), Europe PMC, Semantic Scholar; OpenAlex for broad coverage. **Do NOT use Google Scholar / scraping.** Reserve SerpApi only as a later fallback if genuinely needed.
- **Editor:** TipTap or Lexical for the in-app article editor.
- **Infra (design for, don't build now):** design the pipeline to run locally first (a Python worker / FastAPI background task), with a clear migration path to AWS Step Functions + Lambda for orchestration and EventBridge for scheduling. S3 for image/assets, managed Postgres (RDS/Neon) in production. Describe this target; don't provision it in this task.

---

## 4. Two surfaces

Design the app as two cleanly separated surfaces sharing one backend:

- **Public feed** — read-only masonry feed of `published` articles. Card → tap → full article with citations.
- **Editorial console** — authenticated. A review queue of `pending_review` drafts, each opening into an editor with a sources panel (see §7).

---

## 5. Content-generation pipeline

A staged pipeline that turns a product/trend into a reviewable draft:

1. **Input** — a product or trend (e.g. "ashwagandha for stress"), optionally with marketing blurb.
2. **Extract claims + ingredients** (LLM call 1) → structured JSON.
3. **Retrieve evidence** — query the scholarly APIs per claim (keyword recall).
4. **Rank + filter** — semantic re-rank of retrieved abstracts against the specific claim (pgvector), then filter/grade by evidence quality.
5. **Synthesize** (LLM call 2, the RAG generation) — write the article grounded strictly in the retrieved abstracts, with inline citations.
6. **Output draft** — assemble article + verdict + citations, run citation validation, store as `pending_review`.

**Evidence-quality hierarchy** (used in step 4 grading, and to cap verdict confidence):
`systematic review / meta-analysis > randomized controlled trial > observational study > in-vitro / animal study`.
Bias retrieval and ranking **toward systematic reviews and meta-analyses** — a single recent review abstract is worth more than many primary-study abstracts.

---

## 6. RAG / retrieval design (important — get this right)

**The scholarly APIs are the primary search index. The vector store is a re-ranking + caching layer, NOT the primary search.** Do not attempt to pre-index all of PubMed.

**Hybrid retrieval, two passes:**
1. **Keyword recall (APIs):** query PubMed / Europe PMC / Semantic Scholar for each claim → 20–50 candidate papers (title, abstract, year, journal, study type, citation count, PMID/DOI, url, source).
2. **Semantic re-rank (pgvector):** embed candidate abstracts, cosine-rank against the claim's embedding, then apply metadata filters (study type, recency) → take top-k. Those top-k abstracts become the LLM's grounding context.

**What the vector store holds** — a `sources` table:
- `id, pmid, doi, title, abstract, journal, year, study_type, citation_count, url, source_api, embedding vector(N)`
- Embed the **whole abstract** as one vector (abstracts are short — no chunking needed for the MVP). Metadata columns are stored but not embedded; they drive the evidence-grade filter.
- HNSW index on `embedding` for cosine similarity.
- **Cache aggressively:** once a paper is fetched and embedded, persist it and reuse it for future articles. The store grows into a reusable library of cited literature.
- *v2 (design for, flag as later):* pull **full text** only for the 2–3 load-bearing sources a verdict rests on (Europe PMC open access), chunked into ~500-token passages linked back to the paper. Abstracts for breadth, full text for the pivotal few.

**Provenance:** an `article_sources(article_id, source_id, claim)` link table records which papers backed which claims — this powers fast source verification in the review UI.

---

## 7. The AI calls — input / output contracts

Design these as strict, typed contracts. There are two generative LLM calls (plus a non-generative embedding call).

### LLM call 1 — Claim + ingredient extraction
- **Input:** product/trend name + optional blurb.
- **Output (JSON only):**
```json
{
  "product": "Ashwagandha KSM-66",
  "target_claims": ["reduces stress", "improves sleep quality"],
  "ingredients": ["ashwagandha (Withania somnifera)"]
}
```

### (Optional) query generation
- Turn each claim/ingredient into a scholarly search query. May be an LLM call or a template + MeSH terms. Design it as a swappable step.

### LLM call 2 — Synthesis (RAG generation, the core)
- **Input:**
  1. product + extracted claims,
  2. the top-k retrieved abstracts, each tagged with a citation handle (`S1`, `S2`, …), year, and study type,
  3. a strict system prompt: *write only from the provided sources; attach `[source_id]` to every factual claim; if evidence is weak or absent, say so plainly; do not use outside knowledge; let study-type grade cap the verdict's confidence.*
- **Output (JSON only):**
```json
{
  "headline": "…",
  "verdict": "supported | mixed | weak | no evidence",
  "body": "…[S1]…[S3]…",
  "citations": [{ "claim": "reduces cortisol", "source_ids": ["S1", "S3"] }]
}
```

### Non-generative — embeddings
- Text (abstract or claim) in → vector out. Used only for the semantic re-rank in §6, behind a swappable provider interface.

**Grounding rule (enforce in code):** the LLM may only emit `source_id`s present in its input. After generation, validate that every cited `source_id` exists in the input set and resolves to a real PMID/DOI. A draft failing validation does not enter the review queue.

---

## 8. Article format & length

- **Primary output = the expanded "on-tap" article:** ~120–250 words, up to ~300 for a rich evidence base.
- **Structured in three beats:** (1) what it claims → (2) what the research shows → (3) bottom line / caveat. Plus citations.
- **Length is a ceiling, not a floor — evidence-proportional.** Thin evidence → a short, honest card (e.g. "one small trial suggests X; not enough to conclude"). Never pad to length.
- **Field bounds** (enforce structurally, not with a global word count):
  - `headline` ≤ 12 words
  - `summary` ≤ 2 sentences
  - `body` = the three beats, each ≤ ~3 sentences
  - `verdict` = single label + optional one-clause qualifier
- **The feed card is derived from the article, not generated separately** — headline + verdict badge + first sentence of beat 1. One AI output, two renderings, so card and article can never contradict.

---

## 9. Human-in-the-loop review

- **Article states:** `pending_review` → `published` | `rejected`.
- **Article fields:** `article_status`, `original_content` (immutable AI draft), `edited_content` (the human-approved version that gets published), `verdict`, `reviewed_by`, `reviewed_at`, plus the derived card fields.
- **Review dashboard (authenticated):** queue of `pending_review` drafts; opening one shows the editor + a sources panel side by side.
- **Fast source access (a core requirement):** using the `article_sources` provenance, render each claim with its backing citations attached. The sources panel lists each paper's title, journal, year, study type, and a direct link (DOI/PMID). Surface the evidence grade inline (flag when a verdict rests on weak study types), and show a citation-validation badge (e.g. "4/4 citations resolve").
- **Inline editing (a core requirement):** a TipTap/Lexical editor on the draft with autosave. Keep `original_content` immutable and save edits to `edited_content` — this gives a clean audit trail of "what the model wrote vs. what went live."
- **Publishing:** the public feed only ever queries `status = published`.

---

## 10. Deliverables for this task

Produce:
1. A concise **architecture / design document** (`DESIGN.md`) covering the above and any decisions you make.
2. A **repository skeleton** — frontend and backend directory structure with clearly separated modules (pipeline stages, retrieval, LLM clients, review/editorial, public feed), key functions and interfaces stubbed with type signatures and docstrings (not full implementations).
3. The **database schema** (SQL or migrations) for `articles`, `sources`, `article_sources`, and any auth/user tables, including the pgvector column and index.
4. The **API surface** — a list of FastAPI endpoints (public feed + editorial console) with request/response models.
5. The **AI prompt templates** for the extraction and synthesis calls, written to enforce §7 and §8.
6. A **phased build roadmap** (what to implement first → last), matching the "one topic end-to-end before scaling breadth" approach.

## 11. Explicitly out of scope for now
- Full implementation of any stage (stubs + contracts only).
- The AWS deployment (design the migration path; don't provision).
- The agentic query-refinement loop (design the pipeline so it can be added later; don't build it).
- Full-text retrieval and chunking (abstract-only for the MVP; note full text as v2).

Before you start, briefly restate your understanding and list any clarifying questions.
