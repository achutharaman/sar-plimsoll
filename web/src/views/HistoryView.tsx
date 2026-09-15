import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { LineChart, Sparkline } from "../components/charts";
import { Card, Empty, Notice, Spinner } from "../components/ui";
import { day, when } from "../format";
import type { History } from "../types";

export function HistoryView({ onOpen }: { onOpen: (reviewId: string) => void }) {
  const [history, setHistory] = useState<History | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.history().then(setHistory).catch((e: ApiError) => setError(e.message));
  }, []);

  if (error) return <Notice>{error}</Notice>;
  if (!history) return <Spinner label="Loading history…" />;
  if (history.files.length === 0) return <Empty title="No reviews yet">Submit code to start tracking scores over time.</Empty>;

  return (
    <div className="space-y-5">
      <Card title="Average score over time" subtitle="Mean of completed reviews per day">
        <LineChart
          title="Mean score per day"
          points={history.trend.map((t) => ({ label: day(t.date), value: t.mean_score, detail: `${t.reviews} review${t.reviews === 1 ? "" : "s"}` }))}
          yMin={0}
          yMax={10}
          format={(v) => v.toFixed(1)}
        />
      </Card>

      <Card title="Files" subtitle="Resubmissions of the same filename form one history, so growth on the same code is visible">
        <ul className="m-0 list-none divide-y divide-[var(--border)] p-0">
          {history.files.map((f) => (
            <li key={f.filename} className="py-3">
              <div className="flex flex-wrap items-center gap-4">
                <div className="min-w-40 flex-1">
                  <div className="font-mono text-sm text-ink">{f.filename}</div>
                  <div className="text-xs text-ink-2">{f.language} · {f.reviews} review{f.reviews === 1 ? "" : "s"} · last {when(f.last_reviewed_at)}</div>
                </div>
                <Sparkline values={f.points.map((p) => p.score)} label={`${f.filename} scores: ${f.points.map((p) => p.score).join(", ")}`} />
                <div className="w-28 text-right">
                  <div className="text-lg font-semibold text-ink">{f.latest_score.toFixed(1)}</div>
                  <div className={`text-xs tabular ${f.delta > 0 ? "text-good" : f.delta < 0 ? "text-critical" : "text-muted"}`}>
                    {f.reviews > 1 ? `${f.delta > 0 ? "▲ +" : f.delta < 0 ? "▼ " : ""}${f.delta.toFixed(1)} since first` : "first review"}
                  </div>
                </div>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {f.points.map((p) => (
                  <button
                    key={p.review_id}
                    onClick={() => onOpen(p.review_id)}
                    className="rounded-md border border-line px-2 py-0.5 text-xs text-ink-2 hover:bg-surface-2 hover:text-ink"
                  >
                    {p.score.toFixed(1)} · {when(p.created_at)}{p.cache_hit ? " · cached" : ""}
                  </button>
                ))}
              </div>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
