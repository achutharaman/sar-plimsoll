import { useEffect, useState, type FormEvent } from "react";
import { ApiError, api, poll } from "../api";
import { Button, Card, Notice, RulesBadge } from "../components/ui";
import type { IngestJob } from "../types";

export function RulesView() {
  const [corpus, setCorpus] = useState<{ version: string; rule_count: number; label: string; short: string | null } | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [job, setJob] = useState<IngestJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = () => api.corpus().then(setCorpus).catch(() => undefined);
  useEffect(() => {
    refresh();
  }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const started = await api.ingest(file);
      const final = await poll(() => api.ingestJob(started.id), (j) => j.status === "done" || j.status === "failed", setJob);
      setJob(final);
      refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Ingest failed.");
    } finally {
      setBusy(false);
    }
  }

  const progress = job?.progress;
  const total = progress ? progress.committed_rows + progress.remaining : 0;
  const report = job?.report as Record<string, number | string | boolean | unknown[]> | null;

  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <Card title="Historical rules" subtitle="CSV with columns id, type, description. Re-ingesting is safe and resumes after failures.">
        <form onSubmit={submit} className="space-y-3">
          <input type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="block w-full text-sm text-ink-2 file:mr-3 file:rounded-lg file:border file:border-line file:bg-surface file:px-3 file:py-1.5 file:text-ink" />
          <Button type="submit" disabled={!file || busy}>{busy ? "Ingesting…" : "Ingest rules"}</Button>
          {error && <Notice>{error}</Notice>}
        </form>
        {corpus && (
          <div className="mt-4 flex flex-wrap items-center gap-2 text-sm text-ink-2">
            Current rule set
            <RulesBadge label={corpus.label} short={corpus.short} size={corpus.rule_count} version={corpus.version} />
          </div>
        )}
      </Card>

      {job && (
        <Card title={`Ingest job · ${job.filename}`} subtitle={`${job.status} · attempt ${job.attempts || 1}`}>
          {progress && total > 0 && (
            <div className="mb-4">
              <div className="flex justify-between text-[13px]">
                <span className="text-ink-2">Committed</span>
                <span className="font-medium text-ink tabular">{progress.committed_rows.toLocaleString()} / {total.toLocaleString()}</span>
              </div>
              <div className="mt-1 h-2.5 rounded-full" style={{ background: "var(--accent-track)" }}>
                <div className="h-full rounded-full" style={{ width: `${(progress.committed_rows / total) * 100}%`, background: "var(--accent)" }} />
              </div>
            </div>
          )}
          {report && (
            <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
              <div className="col-span-2">
                <dt className="text-xs text-muted">rule set after ingest</dt>
                <dd className="m-0 text-ink">{String(report.corpus_label ?? "—")} <span className="font-mono text-xs text-muted">{String(report.corpus_version ?? "").slice(0, 9)}</span></dd>
              </div>
              {(["accepted", "inserted", "updated", "unchanged", "rejected_count", "embedded", "corpus_size"] as const).map((k) => (
                <div key={k}>
                  <dt className="text-xs text-muted">{k.replace("_", " ")}</dt>
                  <dd className="m-0 text-ink tabular">{String(report[k] ?? "—")}</dd>
                </div>
              ))}
              <div>
                <dt className="text-xs text-muted">cost</dt>
                <dd className="m-0 text-ink tabular">${Number(report.cost_usd ?? 0).toFixed(4)}</dd>
              </div>
            </dl>
          )}
          {job.error && <div className="mt-3"><Notice>{job.error}</Notice></div>}
        </Card>
      )}
    </div>
  );
}
