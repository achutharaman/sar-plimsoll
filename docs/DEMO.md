# Demo script

A 6–8 minute walkthrough of the deployed system. Every step uses the live dashboard at
`https://<PROJECT_ID>.web.app`, signed in as the demo admin (credentials in `.env`).

## Before you start (5 minutes before)

```bash
make smoke          # health, sign-in, a real review, identical cached resubmission, analytics
```

- All checks must pass. If the review step is slow (>60 s), the models are cold — run it once more.
- Optional: set `api_min_instances = 1` in `infra/terraform.tfvars` and `make tf-apply` to remove API
  cold starts during the session (billed while idle; set back to 0 afterwards).
- Have these files ready to drag in: `demo/files/orders_v1.py`, `orders_v3.py`, `checkout.ts`,
  and an unseen rules CSV (e.g. `tests/fixtures/rules_messy.csv`).

## 1. The problem with LLM scores (30 s)

> "Ask a model to rate code 1–10 twice and you get two numbers. You can't defend that number to the
> developer who receives it. Plimsoll never lets the model produce the score."

## 2. Review a file (90 s) — **Review** tab

1. Upload `demo/files/orders_v1.py`, filename `orders.py`, press **Review**.
2. While it runs: "The cheap model reviews everything. Only serious or uncertain findings go to
   the expensive model."
3. Point at:
   - the **hero score** and the load-line meter,
   - **Where the points went** — each deduction maps to one finding, and they add up exactly to
     the total,
   - the **rule chips** on findings: "this finding is grounded in rule SEC-001 from the historical
     CSV — that's the ingested data shaping the review",
   - the **cost line**: tokens, dollars, which model tier ran.

## 3. Same file twice → identical score (45 s) — the key moment

1. Press **Review** again without changing anything.
2. It returns instantly with **From cache · $0** and the **identical** score and findings.

> "Same input, same number, every time — and it cost nothing. The cache key covers the content,
> the rubric version, the prompt version, both model IDs and the rules corpus version, so it can
> never serve a stale answer."

## 4. Growth over time (60 s) — **History** tab

1. Show `orders.py`: seeded reviews climb from about **2 → 7 → 10** across versions v1 → v3.
2. Click a point to reopen that exact review.

> "A session is every review of the same file, so improvement on the same code is visible."

## 5. Learning from historical data (90 s) — **Rules** tab

1. Upload an unseen CSV. It returns immediately and runs as a **Cloud Run Job**; the progress bar
   shows committed checkpoints.
2. Point at the report: accepted / rejected rows with reasons (quoted commas, duplicates, unknown
   types all handled), rows embedded, new corpus version.
3. "Re-uploading the same file embeds nothing. A 30,000-row file ingests in about 3½ minutes for
   about ten cents, and resumes if it fails half-way."

## 6. Recurring issues (45 s) — **Insights** tab

- Findings by dimension split by severity, and the historical rules triggered most often.

## 7. Cost (90 s) — **Cost** tab, scope **All users**

- Lead with the **benchmark hero number**: measured savings versus sending everything to the
  expensive model, on the same files.
- Then the tiles: mean cost per review, cost per 1,000 reviews (also at list price, after
  promotional pricing ends), cache hit rate, escalation rate.

> "These are measured, not estimated: every model call records its tokens and cost, and the baseline
> was actually run."

## Likely questions

| Question | Short answer |
|---|---|
| Is the score really deterministic? | Given findings, yes — a pure function with property tests (same input → same output; adding a finding never raises the score; always 1–10). The model's findings can vary between runs; identical submissions are served from cache, so users see the same result. |
| What does the cheap tier miss? | Sometimes real issues — the benchmark shows Flash-only reviews scoring higher than Pro on some files. It's a cost/quality trade-off we measure and report (DECISIONS.md). |
| What happens with a 30k-row CSV? | Cloud Run Job, 2 GiB, checkpoints every 2,000 rules, parallel embedding on the global endpoint; measured 213 s and $0.098. |
| What if the model provider is overloaded? | Retries with backoff (long on 429), Cloud Tasks redelivery, reviews marked stalled/failed can be retried from the UI. |
| Where is the code stored, is it used for training? | Tenant-scoped in Cloud Storage (deleted after 30 days), never logged, never used for training. |
