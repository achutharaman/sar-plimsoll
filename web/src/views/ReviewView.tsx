import { useEffect, useMemo, useState, type FormEvent } from "react";
import { ApiError, api, poll } from "../api";
import { useAuth } from "../auth";
import { BarList } from "../components/charts";
import { Button, Card, Notice, SeverityBadge, Spinner } from "../components/ui";
import { scoreBand, seconds, usd, when } from "../format";
import type { Dimension, Review, Severity } from "../types";

const DIMENSIONS: Dimension[] = ["security", "correctness", "performance", "architecture", "maintainability", "formatting"];
const TONE = { good: "var(--good)", warning: "var(--warning)", serious: "var(--serious)", critical: "var(--critical)" };

export function ReviewView({ openReviewId, onOpened }: { openReviewId: string | null; onOpened: () => void }) {
  const { config } = useAuth();
  const [filename, setFilename] = useState("");
  const [content, setContent] = useState("");
  const [review, setReview] = useState<Review | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!openReviewId) return;
    onOpened();
    setError(null);
    api.review(openReviewId).then(setReview).catch((e: ApiError) => setError(e.message));
  }, [openReviewId, onOpened]);

  async function track(start: Review) {
    setReview(start);
    if (start.status === "done" || start.status === "failed") return;
    const final = await poll(() => api.review(start.id), (r) => r.status === "done" || r.status === "failed" || r.stalled, setReview);
    setReview(final);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { body } = await api.submit(filename.trim(), content);
      await track(body);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  async function retry() {
    if (!review) return;
    setBusy(true);
    setError(null);
    try {
      await track(await api.retry(review.id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Retry failed.");
    } finally {
      setBusy(false);
    }
  }

  async function loadFile(file: File) {
    if (config && file.size > config.limits.max_file_bytes) {
      setError(`File is ${file.size.toLocaleString()} bytes; the limit is ${config.limits.max_file_bytes.toLocaleString()}.`);
      return;
    }
    setFilename(file.name);
    setContent(await file.text());
  }

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      <Card title="Submit code" subtitle={config ? `${config.languages.length} languages · rubric ${config.rubric_version}` : undefined}>
        <form onSubmit={submit} className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <input
              aria-label="Filename"
              placeholder="filename, e.g. orders.py"
              required
              value={filename}
              onChange={(e) => setFilename(e.target.value)}
              className="h-9 min-w-0 flex-1 rounded-lg border border-line bg-page px-3 text-sm text-ink"
            />
            <label className="inline-flex h-9 cursor-pointer items-center rounded-lg border border-line px-3 text-sm text-ink-2 hover:bg-surface-2">
              Upload
              <input type="file" className="sr-only" onChange={(e) => e.target.files?.[0] && loadFile(e.target.files[0])} />
            </label>
          </div>
          <textarea
            aria-label="Source code"
            required
            spellCheck={false}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder="Paste source code…"
            className="h-80 w-full resize-y rounded-lg border border-line bg-page p-3 font-mono text-[12.5px] leading-5 text-ink"
          />
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs text-muted">Your code is reviewed privately and never used for training.</span>
            <Button type="submit" disabled={busy || !content.trim()}>{busy ? "Reviewing…" : "Review"}</Button>
          </div>
          {error && <Notice>{error}</Notice>}
        </form>
      </Card>

      <div className="min-w-0">
        {review ? <ReviewResultPanel review={review} onRetry={retry} busy={busy} /> : (
          <Card title="How the score works">
            <p className="text-sm leading-6 text-ink-2">
              The model only classifies findings. A versioned rubric turns their severity and dimension into a 1–10 score, so the same
              findings always give the same number, and you can see exactly which findings cost points. Submitting unchanged code returns the
              identical result from cache at no cost.
            </p>
          </Card>
        )}
      </div>
    </div>
  );
}

function ReviewResultPanel({ review, onRetry, busy }: { review: Review; onRetry: () => void; busy: boolean }) {
  const result = review.result;
  const findings = useMemo(() => new Map((result?.findings ?? []).map((f) => [f.id, f])), [result]);
  const rules = useMemo(() => new Map((result?.rules_grounded ?? []).map((r) => [r.id, r])), [result]);

  if (review.status === "failed" || review.stalled) {
    return (
      <Card title={review.filename} subtitle={review.stalled ? "This review has not finished in time." : "This review failed."}>
        {review.error && <p className="mb-3 text-sm text-ink-2">{review.error}</p>}
        <Button onClick={onRetry} disabled={busy}>Retry review</Button>
      </Card>
    );
  }
  if (!result) {
    return (
      <Card title={review.filename} subtitle={`${review.language} · ${review.line_count} lines`}>
        <Spinner label={review.status === "running" ? "Reviewing — the fast model checks everything, a stronger model double-checks serious issues…" : "Queued…"} />
      </Card>
    );
  }

  const score = result.score;
  const band = scoreBand(score.overall);
  const deductions = [...score.deductions].filter((d) => d.points > 0).sort((a, b) => b.points - a.points);
  const shown = result.findings.filter((f) => !deductions.some((d) => d.finding_id === f.id));

  return (
    <div className="space-y-5">
      <Card
        title={review.filename}
        subtitle={`${review.language} · ${review.line_count} lines · ${when(review.created_at)}`}
        action={review.cost?.cache_hit ? <span className="rounded-md bg-accent-wash px-2 py-1 text-xs font-medium text-ink">From cache · $0</span> : undefined}
      >
        <div className="flex flex-wrap items-end gap-6">
          <div>
            <div className="text-[56px] font-semibold leading-none text-ink">{score.overall.toFixed(1)}</div>
            <div className="mt-1 text-xs text-muted">out of 10 · rubric {score.rubric_version}</div>
          </div>
          <div className="min-w-48 flex-1">
            <div className="flex items-center gap-2 text-sm font-medium text-ink">
              <span aria-hidden className="h-2.5 w-2.5 rounded-full" style={{ background: TONE[band.tone] }} />
              {band.label}
            </div>
            <PlimsollMeter score={score.overall} color={TONE[band.tone]} />
            <div className="mt-1 text-xs text-ink-2">
              {score.deducted.toFixed(3)} points deducted across {deductions.length} finding{deductions.length === 1 ? "" : "s"}
            </div>
          </div>
        </div>
        {review.cost && (
          <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-1 border-t border-line pt-3 text-xs sm:grid-cols-4">
            <Meta k="Cost" v={usd(review.cost.cost_usd)} />
            <Meta k="Time" v={seconds(review.cost.wall_ms)} />
            <Meta k="Tokens" v={`${review.cost.input_tokens.toLocaleString()} in · ${review.cost.output_tokens.toLocaleString()} out`} />
            <Meta k="Model" v={review.cost.cache_hit ? "cached result" : review.cost.escalated ? "escalated to Pro" : "Flash only"} />
          </dl>
        )}
      </Card>

      <Card title="Score by dimension" subtitle="Each dimension scored on the same 1–10 curve">
        <BarList
          ariaLabel="Score by dimension"
          max={10}
          format={(v) => v.toFixed(1)}
          rows={DIMENSIONS.map((d) => ({ key: d, label: d, value: score.dimensions[d].score }))}
        />
      </Card>

      <Card title="Where the points went" subtitle="Deductions add up exactly to the total">
        {deductions.length === 0 ? (
          <p className="text-sm text-ink-2">No deductions — nothing cost points.</p>
        ) : (
          <ol className="m-0 list-none space-y-3 p-0">
            {deductions.map((d) => {
              const f = findings.get(d.finding_id);
              if (!f) return null;
              return (
                <li key={d.finding_id} className="rounded-lg border border-line p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <SeverityBadge severity={f.severity as Severity} />
                      <span className="text-xs text-ink-2">{f.dimension} · {f.line_end !== f.line_start ? `lines ${f.line_start}–${f.line_end}` : `line ${f.line_start}`}</span>
                    </div>
                    <span className="text-sm font-semibold text-ink tabular">−{d.points.toFixed(3)}</span>
                  </div>
                  <p className="mt-1.5 text-sm text-ink">{f.message}</p>
                  {f.suggestion && <p className="mt-1 text-[13px] text-ink-2">Fix: {f.suggestion}</p>}
                  {f.grounded_rule_ids.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {f.grounded_rule_ids.map((id) => (
                        <span key={id} title={rules.get(id)?.description} className="rounded-md bg-accent-wash px-1.5 py-0.5 text-xs text-ink">
                          rule {id}{rules.get(id) ? ` · ${truncate(rules.get(id)!.description, 60)}` : ""}
                        </span>
                      ))}
                    </div>
                  )}
                </li>
              );
            })}
          </ol>
        )}
        {shown.length > 0 && (
          <p className="mt-3 text-xs text-muted">{shown.length} low-confidence finding{shown.length === 1 ? "" : "s"} shown in data but not scored.</p>
        )}
      </Card>
    </div>
  );
}

/** The namesake: a 1–10 track with the fill at the score and a hairline "load line" at 8. */
function PlimsollMeter({ score, color }: { score: number; color: string }) {
  const at = (v: number) => `${((v - 1) / 9) * 100}%`;
  return (
    <div className="relative mt-2 h-2.5 rounded-full" style={{ background: "var(--surface-2)" }} aria-hidden>
      <div className="absolute inset-y-0 left-0 rounded-full" style={{ width: at(score), background: color }} />
      <div className="absolute -top-1 -bottom-1 w-px" style={{ left: at(8), background: "var(--ink-2)" }} />
    </div>
  );
}

function Meta({ k, v }: { k: string; v: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-muted">{k}</dt>
      <dd className="m-0 truncate text-ink">{v}</dd>
    </div>
  );
}

const truncate = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);
