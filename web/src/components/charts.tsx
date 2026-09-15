import { useEffect, useId, useRef, useState, type ReactNode } from "react";

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    if (!ref.current) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

export interface LinePoint { label: string; value: number; detail?: string }

/**
 * Single-series line chart: 2px line, 10% area wash, end marker with value label,
 * crosshair + tooltip that snaps to the nearest point, keyboard stepping, and a table view.
 */
export function LineChart({ title, points, yMin, yMax, format, height = 200 }: {
  title: string;
  points: LinePoint[];
  yMin: number;
  yMax: number;
  format: (v: number) => string;
  height?: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [active, setActive] = useState<number | null>(null);
  const [table, setTable] = useState(false);
  const id = useId();
  const pad = { top: 16, right: 44, bottom: 26, left: 34 };
  const w = Math.max(width, 240);
  const plotW = w - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const x = (i: number) => pad.left + (points.length <= 1 ? plotW / 2 : (i / (points.length - 1)) * plotW);
  const y = (v: number) => pad.top + plotH - ((v - yMin) / (yMax - yMin || 1)) * plotH;
  const ticks = niceTicks(yMin, yMax, 4);
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.value)}`).join("");
  const area = points.length > 1 ? `${path}L${x(points.length - 1)},${y(yMin)}L${x(0)},${y(yMin)}Z` : "";
  const last = points.length - 1;
  const labelEvery = Math.ceil(points.length / Math.max(2, Math.floor(plotW / 70)));

  const pick = (clientX: number, rect: DOMRect) => {
    const rel = clientX - rect.left - pad.left;
    const i = points.length <= 1 ? 0 : Math.round((rel / plotW) * (points.length - 1));
    setActive(Math.max(0, Math.min(last, i)));
  };

  return (
    <figure className="m-0">
      <div className="mb-2 flex items-center justify-between">
        <figcaption className="text-[13px] text-ink-2">{title}</figcaption>
        <button className="text-xs text-ink-2 underline-offset-2 hover:underline" onClick={() => setTable((t) => !t)}>
          {table ? "Show chart" : "Show table"}
        </button>
      </div>
      {table ? (
        <DataTable head={["Date", "Value"]} rows={points.map((p) => [p.label, format(p.value)])} />
      ) : (
        <div ref={ref} className="relative" style={{ height }}>
          {width > 0 && (
            <svg
              width={w}
              height={height}
              role="img"
              aria-labelledby={`${id}-desc`}
              tabIndex={0}
              onPointerMove={(e) => pick(e.clientX, e.currentTarget.getBoundingClientRect())}
              onPointerLeave={() => setActive(null)}
              onBlur={() => setActive(null)}
              onKeyDown={(e) => {
                if (e.key === "ArrowRight") setActive((a) => Math.min(last, (a ?? -1) + 1));
                if (e.key === "ArrowLeft") setActive((a) => Math.max(0, (a ?? last + 1) - 1));
              }}
              className="block touch-none"
            >
              <desc id={`${id}-desc`}>{`${title}: ${points.map((p) => `${p.label} ${format(p.value)}`).join(", ")}`}</desc>
              {ticks.map((t) => (
                <g key={t}>
                  <line x1={pad.left} x2={w - pad.right} y1={y(t)} y2={y(t)} stroke="var(--grid)" strokeWidth={1} />
                  <text x={pad.left - 8} y={y(t)} dy="0.32em" textAnchor="end" fontSize={11} fill="var(--muted)" className="tabular">
                    {format(t)}
                  </text>
                </g>
              ))}
              {points.map((p, i) =>
                i % labelEvery === 0 || i === last ? (
                  <text key={p.label + i} x={x(i)} y={height - 6} textAnchor="middle" fontSize={11} fill="var(--muted)">
                    {p.label}
                  </text>
                ) : null,
              )}
              {area && <path d={area} fill="var(--accent)" opacity={0.1} />}
              <path d={path} fill="none" stroke="var(--accent)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
              {last >= 0 && (
                <>
                  <circle cx={x(last)} cy={y(points[last].value)} r={4} fill="var(--accent)" stroke="var(--surface)" strokeWidth={2} />
                  <text x={x(last) + 8} y={y(points[last].value)} dy="0.32em" fontSize={12} fontWeight={600} fill="var(--ink)">
                    {format(points[last].value)}
                  </text>
                </>
              )}
              {active !== null && (
                <>
                  <line x1={x(active)} x2={x(active)} y1={pad.top} y2={pad.top + plotH} stroke="var(--axis)" strokeWidth={1} />
                  <circle cx={x(active)} cy={y(points[active].value)} r={5} fill="var(--accent)" stroke="var(--surface)" strokeWidth={2} />
                </>
              )}
            </svg>
          )}
          {active !== null && (
            <Tooltip x={Math.min(x(active) + 12, w - 150)} y={Math.max(0, y(points[active].value) - 52)}>
              <div className="text-sm font-semibold text-ink">{format(points[active].value)}</div>
              <div className="text-xs text-ink-2">{points[active].label}</div>
              {points[active].detail && <div className="text-xs text-muted">{points[active].detail}</div>}
            </Tooltip>
          )}
        </div>
      )}
    </figure>
  );
}

/** Tiny trend line for a tile or list row. The latest value is printed beside it, so nothing is gated. */
export function Sparkline({ values, min = 0, max = 10, label }: { values: number[]; min?: number; max?: number; label: string }) {
  const w = 96;
  const h = 28;
  const x = (i: number) => (values.length <= 1 ? w / 2 : 3 + (i / (values.length - 1)) * (w - 6));
  const y = (v: number) => 3 + (h - 6) - ((v - min) / (max - min || 1)) * (h - 6);
  const d = values.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join("");
  return (
    <svg width={w} height={h} role="img" aria-label={label} className="shrink-0">
      <path d={d} fill="none" stroke="var(--accent)" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
      {values.length > 0 && (
        <circle cx={x(values.length - 1)} cy={y(values[values.length - 1])} r={3.5} fill="var(--accent)" stroke="var(--surface)" strokeWidth={2} />
      )}
    </svg>
  );
}

/** Horizontal magnitude bars in one hue; value printed at each tip. */
export function BarList({ rows, max, format, ariaLabel, stacked = false }: {
  rows: { key: string; label: ReactNode; value: number; hint?: string }[];
  max: number;
  format: (v: number) => string;
  ariaLabel: string;
  /** Long labels (e.g. rule descriptions) sit on their own line instead of being truncated. */
  stacked?: boolean;
}) {
  return (
    <ul aria-label={ariaLabel} className="m-0 list-none space-y-2.5 p-0">
      {rows.map((r) => {
        const share = max > 0 ? Math.max(0, Math.min(1, r.value / max)) : 0;
        return (
          <li
            key={r.key}
            className={stacked ? "grid grid-cols-1 gap-1" : "grid grid-cols-[minmax(7rem,9rem)_1fr] items-center gap-3"}
            title={r.hint}
          >
            <span className={`text-[13px] text-ink-2 ${stacked ? "" : "truncate"}`}>{r.label}</span>
            <span className="flex items-center gap-2">
              <span className="relative h-2.5 flex-1">
                <span
                  className="absolute inset-y-0 left-0 rounded-r-[4px]"
                  style={{ width: `${share * 100}%`, background: "var(--accent)", minWidth: r.value > 0 ? 3 : 0 }}
                />
              </span>
              <span className="w-12 text-right text-[13px] font-medium text-ink tabular">{format(r.value)}</span>
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/** Part-to-whole bar with 2px surface gaps between segments; legend + per-segment tooltip. */
export function StackedBar({ segments, total, label }: {
  segments: { key: string; name: string; value: number; color: string }[];
  total: number;
  label: string;
}) {
  const [hover, setHover] = useState<string | null>(null);
  const visible = segments.filter((s) => s.value > 0);
  return (
    <div className="relative">
      <div className="flex h-2.5 w-full gap-[2px]" role="img" aria-label={`${label}: ${visible.map((s) => `${s.value} ${s.name}`).join(", ")}`}>
        {visible.map((s, i) => (
          <span
            key={s.key}
            tabIndex={0}
            onPointerEnter={() => setHover(s.key)}
            onPointerLeave={() => setHover(null)}
            onFocus={() => setHover(s.key)}
            onBlur={() => setHover(null)}
            style={{ flexGrow: s.value / (total || 1), background: s.color, opacity: hover && hover !== s.key ? 0.55 : 1 }}
            className={`h-full ${i === visible.length - 1 ? "rounded-r-[4px]" : ""}`}
          />
        ))}
      </div>
      {hover && (
        <Tooltip x={0} y={14}>
          <span className="text-sm font-semibold text-ink">{visible.find((s) => s.key === hover)?.value}</span>{" "}
          <span className="text-xs text-ink-2">{visible.find((s) => s.key === hover)?.name}</span>
        </Tooltip>
      )}
    </div>
  );
}

export function Legend({ items }: { items: { name: string; color: string }[] }) {
  return (
    <ul className="m-0 flex list-none flex-wrap gap-x-4 gap-y-1 p-0">
      {items.map((i) => (
        <li key={i.name} className="flex items-center gap-1.5 text-xs text-ink-2">
          <span aria-hidden className="h-2.5 w-2.5 rounded-[3px]" style={{ background: i.color }} />
          {i.name}
        </li>
      ))}
    </ul>
  );
}

function Tooltip({ x, y, children }: { x: number; y: number; children: ReactNode }) {
  return (
    <div
      className="pointer-events-none absolute z-10 min-w-24 rounded-lg border border-line bg-surface px-2.5 py-1.5 shadow-lg"
      style={{ left: x, top: y }}
    >
      {children}
    </div>
  );
}

export function DataTable({ head, rows }: { head: string[]; rows: ReactNode[][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr>
            {head.map((h) => (
              <th key={h} className="border-b border-line px-2 py-1.5 text-left text-xs font-medium text-ink-2">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {r.map((c, j) => (
                <td key={j} className="border-b border-line px-2 py-1.5 text-ink tabular">{c}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function niceTicks(min: number, max: number, count: number): number[] {
  const span = max - min || 1;
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const ticks: number[] = [];
  for (let t = Math.ceil(min / step) * step; t <= max + 1e-9; t += step) ticks.push(Number(t.toFixed(6)));
  return ticks;
}
