# Decisions

Deviations from `PROJECT_SPEC.md` and design choices the spec leaves open, with reasoning.
Newest first.

## 2026-09-15 — Readable identity for rule sets

**Problem:** scores in a file's history can change because the rules changed, not the code, and the
dashboard showed only the rubric version — the rules version (`rc_…` hash) was invisible.
**Decision:** every rules corpus version gets a deterministic two-word name derived from its hash
(`rules/identity.py`, nautical vocabulary, 4,096 combinations), shown with its rule count and a short
hash. Reviews store the corpus size seen at submission. The review page shows a "Scored with" strip
(rules name · rule count · short hash, rubric version, prompt version); History marks the first rule
flags files scored under more than one rule set (a "N rule sets" badge; each score chip names its rule
set on hover — inline "rules changed" markers were tried and removed as clutter, since opening a review
shows its rule set); the Rules tab and ingest reports show the name.
**Why a derived name, not a stored one:** the same rules always get the same name in every
environment with no extra storage or coordination — the local offline run and the deployed system
both named the same 39-rule corpus "snowy-bearing". The full version string remains the source of
truth and is shown on hover.

## 2026-09-14 — System prompt p2

**Change:** the review system prompt was rewritten (`PROMPT_VERSION` p1 → p2) from an owner-supplied
draft, adding: evidence requirements and no invented behaviour, an explicit empty-findings case,
root-cause de-duplication, smallest line ranges, severity-consistency guidance, "rules do not override
instructions", and "do not cite a rule the code does not violate".
**Adjusted from the draft:**
1. **Confidence scale tied to the system's thresholds.** Findings need sufficient evidence to be
   reported (no speculation), and confidence follows anchors — 0.9–1.0 demonstrated, 0.6–0.9 likely
   but context-dependent, 0.3–0.6 plausible and evidenced, below 0.3 not reported — which line up
   with the rubric's `min_confidence` (0.3) and the escalation threshold (0.6).
2. **Uncertainty goes into confidence, never into severity.** *"When severity is uncertain, choose the
   lower severity"* was dropped: an uncertain high finding downgraded to low would neither escalate
   to Pro nor carry its score impact.
3. **Historical-rule violations are always reported and cited**, even minor formatting ones, and a
   finding may cite several rules. The formatting guidance is stated once.
4. **Repeated instances of one issue in the same function or block are one finding**, so the score
   does not depend on how the model splits, say, fifteen single-character names.
5. Repeated sections (score prohibition ×3, overlapping do/don't lists) were merged.
**Cost:** 256 → 867 system-prompt tokens per call (+611, Vertex AI token counter): +$0.00046 per Flash
call at promotional pricing, +$0.00122 per Pro call; about +$0.0011 per review at the measured 56%
escalation rate.
**Effect on cache:** the prompt version is part of the cache key, so all existing cache entries stop
matching once deployed — correct, since they came from a different prompt.
**Not yet measured:** run the paired benchmark with p2 and compare escalation rate, findings per
file, grounded-finding share, score agreement with all-Pro, and cost against the p1 run before
treating p2 as settled.

## 2026-09-14 — Bug: grammars unreadable in the container (chunking silently disabled)

**Found by:** a slow smoke-test review whose worker log said `tree-sitter parse failed; using
whole-file chunk`, followed by a new `/health/parsers` check on the worker.
**Cause:** the grammar cache creates owner-only (0700) directories. The image fetched grammars as
root and ran the app as `plimsoll`, which got `Permission denied`. Every deployed review since the
first release fell back to one whole-file chunk: scores, caching and grounding still worked, but
retrieval was per file rather than per function. Local tests run as a single user and could not see
it; the benchmark ran locally and was unaffected.
**Fix:** the image creates the app user first and fetches grammars as that user; the build runs
`parser_health()` and fails if any grammar does not load; the worker exposes `GET /health/parsers`
(IAM-protected) for post-deploy verification. All 15 grammars verified in production.

## 2026-09-14 — Cold start: lazy SDKs and a 1-second startup probe

**Measured (Cloud Run `container/startup_latencies`):** API ~10.0 s, worker 7.6–9.6 s.
**Causes:** (1) every service built every Google Cloud client at startup, each resolving credentials
separately (0.6–1.1 s each locally), although the API never calls a model and the worker never
verifies Firebase tokens; (2) the default startup probe polls every 10 s, so a container that was
ready after 3 s still waited for the next probe.
**Fix:** credentials resolved once and shared; each client built on first use; adapters wired as
`LazyProxy` stand-ins so their SDKs (genai, BigQuery, Firestore, Cloud Tasks, Cloud Run, Firebase,
tree-sitter) import only when used; startup probes every 1 s; startup CPU boost on.
**Result:** local startup 13.4 s → 0.27 s with no SDK loaded; on Cloud Run API 10.1 s → 4.6 s and
worker 9.1 s → 5.1 s. The remainder is the Python runtime and web framework on a cold 1-vCPU
instance; `api_min_instances = 1` removes it for live demos at the cost of an idle instance.

## 2026-09-14 — Escalation model thinking: `medium`, 16k output limit (bug found by benchmark)

**Found by:** the first baseline benchmark — 4 of 16 all-Pro reviews failed as unparseable JSON.
**Cause (probed live):** Gemini 3 thinking tokens count against `max_output_tokens`. With default
(high) thinking, `gemini-3.1-pro-preview` spent 7,860 of 8,192 tokens thinking and stopped with
`finish_reason=MAX_TOKENS`, cutting the JSON at ~1,100 characters. In production the same failure
was silent: an unparseable escalation kept the triage findings with only a log line.
**Measured on the same 400-line file:**
| Setting | Finish | Thinking tokens | Result | Pro cost |
|---|---|---|---|---|
| default thinking, 8k limit | MAX_TOKENS | 7,860 | unparseable | — |
| default thinking, 32k limit | STOP | 19,435 | 3 findings | ~$0.23 |
| `medium`, 16k limit | STOP | 6,300 | 2 findings | ~$0.09 |
| `low`, 8k limit | STOP | 2,263 | 1 finding | ~$0.03 |
**Decision:** escalation uses `thinking_level=medium` and a 16,384-token limit; triage keeps `low` and
8,192. A `MAX_TOKENS` finish is reported explicitly as truncation. A failed escalation is recorded
on the review (`escalations[].escalation_error`) instead of disappearing into logs.
**Why medium:** high thinking roughly doubles-to-triples Pro cost for one extra finding on this
file; low under-reports. Medium keeps the escalation tier meaningfully stronger than triage.

## 2026-09-14 — Benchmarks are paired and versioned

**What happened:** the first benchmark compared 16 two-tier reviews against 12 baseline reviews
(four baseline runs failed), so the savings figure mixed different file sets.
**Decision:** `plimsoll benchmark` writes rows only for files where **both** modes produced a review,
tags every row with a `run_id`, and `/v1/stats` reads only the most recent run (production traffic
still honours the time window). The `reviews` table gained `run_id` via an additive
`ALTER TABLE … ADD COLUMN IF NOT EXISTS`, applied automatically when an insert reports an unknown
field.

## 2026-09-14 — Known quality trade-off of two-tier triage

**Measured:** on files where Flash found nothing high-severity (no escalation), Flash-only scores
were often higher than Pro's for the same file (e.g. 9.0 vs 3.7 on one cloud storage policy checker) — Pro
finds issues Flash misses, and escalation is triggered by Flash's own judgement.
**Status:** accepted for now and stated in the README. The two-tier design is a locked decision;
the cost panel shows the savings, and this entry records their quality price. Mitigations if needed:
route a small random sample of Flash-only reviews to Pro as a calibration audit (and report the
score gap on the cost panel), or escalate by file size/complexity as well as severity.

## 2026-09-14 — Dashboard served from Firebase Hosting on the API's origin

**Decision:** React + Vite + Tailwind SPA on Firebase Hosting with rewrites of `/v1/**`, `/admin/**`
and `/health` to the `plimsoll-api` Cloud Run service. The browser only ever talks to its own origin,
so the API needs no CORS configuration. Firebase web config (API key, auth domain, project) is served
at runtime by `GET /v1/config` from Terraform variables, so no key is committed or baked into the
bundle. `GET /v1/me` tells the UI whether the user is an admin, because admin can come from a custom
claim or from `admin_uids`. Charts are hand-rolled SVG (no chart library): one accent hue, the
reserved status colours with icon + label for severity, crosshair tooltips and a table view.
Deployment uses `firebase-tools` with gcloud application-default credentials (non-interactive).

## 2026-09-14 — Analytics: one BigQuery row per review; math shared across backends

**Decision:** every finished review — fresh, cached, failed, or benchmark — writes one row to
`plimsoll.reviews` (tokens per tier, `cost_usd`, `cost_usd_standard` re-priced at list price,
cache hit, escalation, score, finding counts). Findings keep going to `plimsoll.findings`.
`/v1/stats` aggregates per mode in SQL; the in-memory backend aggregates the same rows in Python;
both feed one pure `build_stats`. `/v1/insights` reads recurring dimensions/rules from `findings`
and weekly findings-per-review from `reviews`. Query results are cached in-process for 60 s
because dashboards poll and every BigQuery query bills a 10 MB minimum. History (`/v1/history`)
reads Firestore with a field mask (score, filename, cost flags) — never whole results.
**Why BigQuery for stats:** aggregation over all users is the warehouse's job; Firestore stays the
per-user hot path, as the locked decision intends.

## 2026-09-14 — Failure handling and cost guardrails

**Decision:**
- Unhandled errors return `{"error": "internal"}` with no internals; transient storage errors
  return 503 with `Retry-After`.
- `POST /v1/reviews` bodies larger than 6× the file limit (worst-case JSON escaping) are rejected
  with 413 before parsing.
- Each user may start `daily_review_limit` (default 100) **fresh** reviews per UTC day, counted in a
  Firestore transaction; cache hits are free and uncounted; admins are exempt.
- A review queued or running for more than `review_stall_minutes` (20) is reported as `stalled`.
  Failed or stalled reviews can be retried with `POST /v1/reviews/{id}:retry`; the Cloud Tasks
  task name carries a generation suffix because task names stay reserved after completion.
- Analytics writes never fail a review.

## 2026-09-14 — Demo account

**Decision:** a dedicated `demo@plimsoll.test` user with an `admin` custom claim was created in
the sandbox with the Firebase Admin SDK, its random password stored only in the gitignored `.env`.
`make seed` loads demo rules and reviews through the public URL; `make smoke` is the pre-demo check.
Delete the user from Firebase Authentication when no longer needed.

## 2026-09-14 — Session model: history grouped by filename

**Spec said:** "persistent per-user session history so development growth and optimization patterns
can be tracked over time". "Session" is not defined.
**Decision:** a session is the sequence of reviews of the **same filename** by the same user; the
flat reverse-chronological list of all reviews remains the default history view.
**Why:** it needs nothing extra from the user, and it shows growth on the same code directly
("app.py: 4.2 → 6.8 → 7.9"), which is what "development growth" means in practice. Explicit
user-named sessions can be layered on later without changing stored reviews.

## 2026-09-14 — Escalation and cost measured on real code

**Method:** 10 real source files (Python, TypeScript, JavaScript, TSX; 224–400 lines) from other
local projects, reviewed through the production orchestrator against Vertex AI with the 7-rule
corpus. Files were scanned for hard-coded secrets first; only metrics were kept.
**Result (9 of 10 completed; one hit a transient Gemini 429 after all in-process retries — a
deployed review would be redelivered by Cloud Tasks):**
- Escalation rate **3 / 9 (33%)**; all escalations were `high_severity`/`low_confidence` driven.
- Mean cost per review **$0.0288** = triage $0.0048 + Pro $0.0236 + query embeddings $0.0004.
- Pro spend on an escalated review averaged $0.071, so an all-Pro baseline is roughly
  **$0.07 per review — ~2.5× the tiered cost**. A formal paired baseline benchmark follows below.
- Thinking tokens were **75%** of all output tokens (13,298 of 17,708).
- Latency: triage ~5–24 s, Pro 32–56 s; mean end-to-end 46 s.
- The same file escalated in one run and not the next — model findings vary between runs even at
  temperature 0. Scores are deterministic *given findings*; identical-score reproducibility for an
  identical file comes from the content-hash cache.
**Not changed yet:** escalation thresholds. Options if Pro cost must fall further: escalate only
on `critical`, re-review only the lines around serious findings, or lower Pro's thinking level.

## 2026-09-14 — Bug: blank chunks broke retrieval on real files

**Found by:** the escalation measurement above — 6 of 10 real files failed with
`expected N embeddings, got M`.
**Cause:** a lone blank line between definitions becomes an empty chunk text. The Gemini embedding
API silently drops empty inputs ("Non-text part found"), returning fewer vectors than texts; the
backend's count check refused the misaligned response. Whitespace-only, punctuation-only and
duplicate texts are all returned (probed live).
**Fix:** the retriever embeds only non-blank chunks and gives blank chunks no rules; the gateway
rejects empty texts with a clear error; the fake embedder now drops empty strings like Vertex, so
tests reproduce the real behaviour.

## 2026-09-14 — Rule ingestion runs as a Cloud Run Job

**Supersedes:** items 6–8 of the embeddings decision below (API 900 s timeout and 1 GiB memory).
**What happened:** after the memory fixes, a 30k ingest inside the API succeeded but peaked at
878 MiB of 1 GiB, and the instance carried memory from one ingest into the next (a run started at
536 MiB because of the previous request). Cloud Run grew ~40 MiB per checkpoint versus ~4 MiB
locally, so local tuning could not guarantee headroom.
**Decision:** `POST /admin/rules:ingest` stores the CSV at `ingest/{job_id}.csv`, creates
`ingest_jobs/{job_id}` in Firestore, starts an execution of the `plimsoll-ingest` Cloud Run Job
(job ID passed as an env override) and returns **202**. `GET /admin/rules/ingest-jobs/{id}` returns
status, per-checkpoint progress and the final report. The job uses the worker identity, 2 CPU,
2 GiB, a 3600 s task timeout and `max_retries = 2`; a non-zero exit triggers a retry that resumes
from committed checkpoints. The API only needs `roles/run.jobsExecutorWithOverrides` on that one
job, and returns to 300 s / 512 MiB. `plimsoll rules ingest --job` uses the same path; plain
`plimsoll rules ingest` still runs in-process for local and CLI use.
**Measured (deployed):** 30,000 rules in 213 s, $0.098, first attempt, peak 727 MiB of 2 GiB
(36%), no errors. Every run starts in a fresh container, so memory cannot accumulate across ingests.

## 2026-09-13 — Embeddings: `gemini-embedding-001` on the global endpoint, checkpointed ingest

**Replaces:** `text-embedding-005` in `us-central1` (see "Model defaults" below).
**What happened:** a 30,000-row CSV failed with HTTP 500. Vertex AI returned `429
RESOURCE_EXHAUSTED`: the sandbox's regional `:predict` quota for `text-embedding-005` is
**10 requests/minute**, so at 250 rules per request the ceiling was 2,500 rules/minute (≥12 minutes
for 30k — beyond the API request timeout) and the gateway's 1 s / 2 s retries could not outlast a
per-minute window.
**Measured in the sandbox:**
- The global `embedContent` quota is 100,000 requests/min for `gemini-embedding-001`.
- `gemini-embedding-001` accepts 250 texts per request there (500 is rejected), returns per-text
  token counts, and supports `CODE_RETRIEVAL_QUERY`; code chunks ranked the correct rule first.
- `gemini-embedding-2` folds a multi-text request into a single vector, so it needs one request per
  text and plateaued around 2,400 texts/min — not viable for bulk ingest.
- Through the production gateway (8 parallel batches of 250): 5,000 rules in 26.6 s ≈ 11,300
  rules/min, one transient retry, $0.016. Projection for 30k rules: ~2.7 min, ~$0.10.
**Decision:**
1. `gemini-embedding-001`, location `global`, 768 dimensions, 8 concurrent batches of 250.
2. Priced per input token ($0.15 / 1M) using the API's reported token counts.
3. Rate-limited (429) retries back off 5 s → 10 s → 20 s → 40 s; transport errors 1 s → 2 s → 4 s.
   Default attempts raised from 3 to 5.
4. Ingest commits every 2,000 rules and publishes the corpus version after each checkpoint. A
   failure returns HTTP 503 with `complete: false`, `embedded` and `remaining`; re-running the same
   file resumes with only the rows not yet written.
5. A rule is re-embedded when its text **or its embedding model** changes — vectors from different
   models are not comparable, and the corpus version now includes each rule's model.
6. API request timeout raised from 300 s to 900 s for in-request ingestion.
7. **Memory (found on the first deployed 30k run):** the API instance was killed at 548 MiB of its
   512 MiB after embedding the first 5,000 rules. `load_table_from_json` held a second full copy of
   every 768-float vector as Python objects. Rows are now serialised line by line into an NDJSON
   buffer (`rules_ndjson`), checkpoints dropped from 5,000 to 2,000 rows, and the API memory limit
   raised to 1 GiB. Measured locally for 30k rules: peak 525 MiB (old) → 263 MiB (new).
8. **Memory growth across checkpoints (second deployed 30k run):** at 1 GiB the instance still died
   after 13 checkpoints (26,000 rules committed). Python object counts stayed flat and repeated
   BigQuery loads were flat (129 → 158 MiB), so the growth was native: each checkpoint created a
   fresh thread pool, and glibc assigns new threads their own malloc arenas, which are rarely
   returned. Fix: one long-lived embedding thread pool in the gateway plus `MALLOC_ARENA_MAX=2` in
   the image. Real-Vertex measurement over 6 checkpoints: 353 MiB → 222 MiB, creeping ~4 MiB per
   checkpoint afterwards. Each checkpoint now logs `rss_mib` so production memory is observable.
**Trade-off:** ~6× the per-token price of `text-embedding-005`, which is still ~$0.10 for 30k rules
and ~$0.0002 of query embeddings per review. Ingestion stays synchronous; if an evaluator-scale
file ever approaches 15 minutes it should move to a Cloud Tasks job like reviews.

## 2026-09-13 — Low confidence only escalates medium-or-worse findings

**Spec said:** escalate on "low classification confidence".
**Decision:** a finding below `escalation_confidence_threshold` (0.6) triggers escalation only when
its severity is medium, high or critical. High/critical findings and parse failures still always
escalate.
**Why:** an unsure formatting nit does not justify a Pro call. Escalating on it would inflate the
escalation rate — the main cost driver — without changing the score in any meaningful way.

## 2026-09-13 — Deductions apportioned with largest-remainder rounding

**Decision:** each finding's share of the deduction is computed in thousandths of a point and
rounded with the largest-remainder method.
**Why:** the property test found independently rounded shares could sum to 0.001 off the total.
The UI shows "you lost X because of these findings"; those numbers must add up exactly.

## 2026-09-13 — Score curve: `1 + 9·exp(−R/decay)`

**Spec said:** a deterministic weighted rubric, 1–10, floored at 1 and capped at 10.
**Decision:** `R = Σ severity_points × dimension_weight`; `overall = 1 + 9·e^(−R/8)`.
Per-dimension subscores use the same curve over unweighted severity points.
**Why:** a linear `10 − R` hits the floor quickly and then stops distinguishing bad from terrible;
the exponential stays within [1, 10] without clipping and strictly decreases as findings are
added, which makes the monotonicity property hold by construction. Calibration examples are in
`rubric/v1.yaml` and pinned by tests.

## 2026-09-13 — One model call per batch of chunks, not per function

**Decision:** tree-sitter chunks drive rule retrieval (one embedding per function), but chunks are
packed into batches of up to `batch_max_chars` (48k chars) for model calls. A typical file is one
triage call.
**Why:** the system prompt and retrieved rules are a fixed overhead per call. Paying it per
function multiplies input tokens for no quality gain on normal-sized files. Escalation is decided
and applied per batch, so a large file escalates only the batches that need it.

## 2026-09-13 — Escalation replaces the batch's triage findings

**Decision:** when a batch escalates, Pro reviews it from scratch and its findings replace Flash's.
If Pro's output is unparseable, Flash's findings are kept. If both fail, the review fails.
**Why:** merging two models' findings would double-count the same issue under different wording
and make scores depend on both models.

## 2026-09-13 — Model defaults

**Decision:** triage `gemini-3.8-flash` (GA), escalation `gemini-3.1-pro-preview`, embeddings
`text-embedding-005` (768-d) — *embeddings superseded above*. All overridable via `PLIMSOLL_*` env
and Terraform variables.
**Why:** Gemini 2.5 models retire 2026-10-20. 3.8 Flash is the current GA Flash tier. 3.1 Pro is
only available as preview. `text-embedding-005` accepts 250 texts per request (30k rules ≈ 120
calls) and supports `CODE_RETRIEVAL_QUERY`; `gemini-embedding-001` accepts one text per request.
3.8 Flash promo pricing ends 2026-12-31 — `config/pricing.yaml` carries both prices; projections
should quote standard pricing. **Verify IDs in the sandbox's Model Garden before deploying.**

## 2026-09-13 — Embeddings generated in Python through `llm/`, stored in BigQuery

**Spec said:** BigQuery holds the rules corpus and serves it by vector search.
**Decision:** embeddings are produced by the `llm/` gateway and loaded into BigQuery;
`VECTOR_SEARCH` serves retrieval. BigQuery ML remote models were not used.
**Why:** keeps the "every model call goes through `llm/`" rule and its cost logging intact, avoids
a BigQuery connection resource + extra IAM that a sandbox may not permit, and lets the whole
pipeline run offline with a fake embedder. All chunk queries for a review go into one
`VECTOR_SEARCH` job.

## 2026-09-13 — Ingest is an upsert keyed by content hash

**Decision:** only new or changed rules are embedded; rules missing from a later file are kept.
Duplicate IDs within a file: first occurrence wins, later ones are reported as rejected. Unknown
`type` values are accepted, stored verbatim and simply not mapped to a dimension.
**Why:** re-ingesting the same file costs nothing and leaves the corpus version (and every cache
entry) unchanged. Never deleting on a partial file avoids silently losing grounding data.

## 2026-09-13 — Rule IDs are strings

**Spec said:** `grounded_rule_ids: [3]`.
**Decision:** IDs are strings (`["3"]`).
**Why:** an unseen CSV may use IDs like `SEC-001`. Numeric IDs round-trip unchanged as strings.

## 2026-09-13 — Cache key uses the rules corpus version, not top-k rule IDs

**Spec said:** SHA-256 over content + rubric version + prompt version + model ID + top-k rule IDs.
**Decision:** SHA-256 over content + language + rubric version + prompt version + both model IDs +
**rules corpus version** + tenant (when `cache_scope=tenant`). The corpus version is a hash of all
rule IDs and content hashes plus the embedding model; it changes on any ingest that changes a rule.
**Why:** top-k IDs are only known after an embedding call and a BigQuery vector search, so every
cache *hit* would still pay for both. The corpus version is known before any billable call, so a
hit costs nothing, and it still invalidates whenever the retrievable rules could differ.
(Approved by owner.)

## 2026-09-13 — Cache is tenant-scoped by default

**Decision:** `PLIMSOLL_CACHE_SCOPE=tenant` includes the user ID in the cache key; `global` shares
entries across users.
**Why:** submitted source is tenant-scoped user data. A global cache would let one user's cache hit
reveal that someone else submitted identical code. `global` raises the hit rate if that trade-off
is acceptable.

## 2026-09-13 — Application owns BigQuery table DDL

**Decision:** Terraform creates the dataset; `plimsoll rules ingest` runs `CREATE TABLE IF NOT
EXISTS` and `CREATE VECTOR INDEX IF NOT EXISTS`.
**Why:** one command must take a raw CSV to queryable-and-grounding from a cold start, including a
fresh dataset. BigQuery only populates an IVF index on larger tables; below that, `VECTOR_SEARCH`
does exact brute-force search, which is correct for small corpora.

## 2026-09-13 — Health endpoint is `/health`

**Decision:** `/health`, not `/healthz`.
**Why:** Cloud Run reserves paths ending in `z` such as `/healthz`.

## 2026-09-13 — One image, two Cloud Run services

**Decision:** API and worker share one container image with different start commands. The worker
has no public invoker; only the Cloud Tasks service account holds `run.invoker`, so Cloud Run
verifies the OIDC token before the handler runs.
**Why:** one build, one version to reason about, identical code on both sides of the queue.

## 2026-09-13 — Offline dev wiring

**Decision:** `PLIMSOLL_*_BACKEND` switches between GCP adapters and in-memory/fake equivalents
(stores, inline queue, deterministic fake model and embedder, `dev:<uid>` auth). Dev auth refuses
to start when `K_SERVICE` is set (i.e. on Cloud Run).
**Why:** the sandbox is temporary and credits are limited; the pipeline, scoring and API contract
are fully testable without spending either.

## 2026-09-13 — README carries an event credit line

**Spec said:** keep the event out of the code, README and commit history.
**Decision:** the README includes the line "Created at Code Kitchen Season 01". Everything else
stays event-neutral.
**Why:** it is a mandatory condition of the organiser's IP terms, so the rule overrides the spec.
