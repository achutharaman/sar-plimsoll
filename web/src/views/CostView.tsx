import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { useAuth } from "../auth";
import { Card, Empty, Notice, Segmented, Spinner, StatTile } from "../components/ui";
import { pct, seconds, usd } from "../format";
import type { Stats } from "../types";

export function CostView() {
  const { session } = useAuth();
  const [days, setDays] = useState(30);
  const [scope, setScope] = useState<"me" | "system">(session?.admin ? "system" : "me");
  const [data, setData] = useState<{ me: Stats; system?: Stats } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    api.stats(days).then(setData).catch((e: ApiError) => setError(e.message));
  }, [days]);

  const stats = data ? (scope === "system" && data.system ? data.system : data.me) : null;
  const t = stats?.traffic;
  const b = stats?.benchmark;

  return (
    <div className={`space-y-5 ${data ? "" : ""}`}>
      <div className="flex flex-wrap gap-3">
        <Segmented label="Time range" value={days} onChange={setDays} options={[{ value: 7, label: "7 days" }, { value: 30, label: "30 days" }, { value: 90, label: "90 days" }]} />
        {data?.system && (
          <Segmented label="Scope" value={scope} onChange={setScope} options={[{ value: "system", label: "All users" }, { value: "me", label: "Me" }]} />
        )}
      </div>
      {error && <Notice>{error}</Notice>}
      {!data && !error && <Spinner label="Loading cost data…" />}

      {b && b.savings_pct !== null && (
        <Card title="Measured against an all-Pro baseline" subtitle={`Same ${b.tiered_reviews} files reviewed both ways, no cache`}>
          <div className="flex flex-wrap items-end gap-8">
            <div>
              <div className="text-[56px] font-semibold leading-none text-ink">{pct(b.savings_pct)}</div>
              <div className="mt-1 text-sm text-ink-2">cheaper per review than sending everything to the expensive model</div>
            </div>
            <div className="min-w-64 flex-1 space-y-2.5">
              <CompareBar label="Two-tier (Flash → Pro on escalation)" value={b.tiered_mean_cost_usd} max={b.baseline_mean_cost_usd} />
              <CompareBar label="Baseline (Pro for everything)" value={b.baseline_mean_cost_usd} max={b.baseline_mean_cost_usd} muted />
              <p className="text-xs text-muted">
                Mean cost per review · escalation rate {pct(b.tiered_escalation_rate)} · at list price {usd(b.tiered_mean_cost_usd_standard)} vs {usd(b.baseline_mean_cost_usd_standard)}
              </p>
            </div>
          </div>
        </Card>
      )}

      {t && t.reviews === 0 && <Empty title="No reviews in this period" />}
      {t && t.reviews > 0 && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile label="Mean cost per review" value={usd(t.mean_cost_usd)} note={`fresh reviews ${usd(t.mean_cost_usd_fresh)}`} />
            <StatTile label="Cost per 1,000 reviews" value={usd(t.cost_per_1000_reviews_usd)} note={`at list price ${usd(t.cost_per_1000_reviews_usd_standard)}`} />
            <StatTile label="Cache hit rate" value={pct(t.cache_hit_rate)} note={`${t.cache_hits} of ${t.reviews} · saved ${usd(t.cache_savings_usd)}`} />
            <StatTile label="Escalation rate" value={pct(t.escalation_rate)} note={`of ${t.fresh_reviews} fresh reviews`} />
          </div>
          <Card title="Details">
            <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
              <Row k="Reviews" v={t.reviews.toLocaleString()} />
              <Row k="Total spend" v={usd(t.total_cost_usd)} />
              <Row k="Median review time" v={seconds(t.p50_wall_ms)} />
              <Row k="Mean score" v={t.mean_score?.toFixed(2) ?? "—"} />
              {b?.naive_cost_per_1000_reviews_usd != null && <Row k="Naive cost / 1,000" v={usd(b.naive_cost_per_1000_reviews_usd)} />}
              {b?.plimsoll_cost_per_1000_reviews_usd != null && <Row k="With tiers + cache / 1,000" v={usd(b.plimsoll_cost_per_1000_reviews_usd)} />}
            </dl>
            <p className="mt-3 text-xs text-muted">
              Costs are computed from token counts at current Vertex AI prices; “list price” re-prices the same usage after promotional pricing ends.
            </p>
          </Card>
        </>
      )}
    </div>
  );
}

function CompareBar({ label, value, max, muted }: { label: string; value: number; max: number; muted?: boolean }) {
  return (
    <div>
      <div className="flex justify-between text-[13px]">
        <span className="text-ink-2">{label}</span>
        <span className="font-medium text-ink tabular">{usd(value)}</span>
      </div>
      <div className="mt-1 h-2.5">
        <div className="h-full rounded-r-[4px]" style={{ width: `${Math.max(1, (value / (max || 1)) * 100)}%`, background: muted ? "var(--low)" : "var(--accent)" }} />
      </div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div>
      <dt className="text-xs text-muted">{k}</dt>
      <dd className="m-0 text-ink tabular">{v}</dd>
    </div>
  );
}
