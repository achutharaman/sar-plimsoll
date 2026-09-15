import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { BarList, Legend, LineChart, StackedBar } from "../components/charts";
import { Card, Empty, Notice, Segmented, Spinner, severityColor } from "../components/ui";
import { day } from "../format";
import type { Insights, Severity } from "../types";

const SEVERITIES: Severity[] = ["critical", "high", "medium", "low"];

export function InsightsView() {
  const [days, setDays] = useState(90);
  const [data, setData] = useState<Insights | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    api.insights(days).then(setData).catch((e: ApiError) => setError(e.message));
  }, [days]);

  return (
    <div className="space-y-5">
      <Segmented label="Time range" value={days} onChange={setDays} options={[{ value: 7, label: "7 days" }, { value: 30, label: "30 days" }, { value: 90, label: "90 days" }]} />
      {error && <Notice>{error}</Notice>}
      {!data && !error && <Spinner label="Loading insights…" />}
      {data && data.dimensions.length === 0 && <Empty title="No findings in this period">Recurring issues appear once reviews have findings.</Empty>}
      {data && data.dimensions.length > 0 && (
        <div className={`grid gap-5 lg:grid-cols-2 ${data ? "" : "opacity-60"}`}>
          <Card title="Recurring issues by dimension" subtitle="Findings in the period, split by severity">
            <Legend items={SEVERITIES.map((s) => ({ name: s, color: severityColor(s) }))} />
            <ul className="m-0 mt-4 list-none space-y-3 p-0">
              {data.dimensions.map((d) => (
                <li key={d.dimension} className="grid grid-cols-[7rem_1fr_2.5rem] items-center gap-3">
                  <span className="text-[13px] text-ink-2">{d.dimension}</span>
                  <StackedBar
                    label={d.dimension}
                    total={Math.max(...data.dimensions.map((x) => x.findings))}
                    segments={[
                      ...SEVERITIES.map((s) => ({ key: s, name: s, value: d[s], color: severityColor(s) })),
                      { key: "rest", name: "", value: Math.max(...data.dimensions.map((x) => x.findings)) - d.findings, color: "transparent" },
                    ]}
                  />
                  <span className="text-right text-[13px] font-medium text-ink tabular">{d.findings}</span>
                </li>
              ))}
            </ul>
          </Card>

          <Card title="Most-triggered historical rules" subtitle="Ingested rules most often cited as grounding">
            {data.rules.length === 0 ? (
              <p className="text-sm text-ink-2">No findings were grounded in ingested rules yet.</p>
            ) : (
              <BarList
                stacked
                ariaLabel="Rule hits"
                max={Math.max(...data.rules.map((r) => r.hits))}
                format={(v) => String(v)}
                rows={data.rules.map((r) => ({
                  key: r.rule_id,
                  label: <span title={r.description ?? undefined}><span className="font-mono">{r.rule_id}</span> {r.description ? `· ${r.description}` : ""}</span>,
                  value: r.hits,
                  hint: `${r.hits} findings in ${r.reviews} reviews`,
                }))}
              />
            )}
          </Card>

          {data.weekly.length > 0 && (
            <Card title="Findings per review" subtitle="Weekly — lower is better" className="lg:col-span-2">
              <LineChart
                title="Findings per review by week"
                points={data.weekly.map((w) => ({ label: day(w.week_start), value: w.findings_per_review ?? 0, detail: `${w.reviews} reviews · ${w.findings} findings` }))}
                yMin={0}
                yMax={Math.max(1, ...data.weekly.map((w) => w.findings_per_review ?? 0)) * 1.2}
                format={(v) => v.toFixed(1)}
              />
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
