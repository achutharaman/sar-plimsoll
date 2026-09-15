export function usd(value: number | null | undefined, digits?: number): string {
  if (value === null || value === undefined) return "—";
  const d = digits ?? (value !== 0 && Math.abs(value) < 0.01 ? 4 : 2);
  return `$${value.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`;
}

export function pct(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function compact(value: number): string {
  return Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

export function seconds(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function day(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export const scoreBand = (score: number) =>
  score >= 8 ? { label: "Above the line", tone: "good" as const }
  : score >= 6 ? { label: "Close to the line", tone: "warning" as const }
  : score >= 4 ? { label: "Below the line", tone: "serious" as const }
  : { label: "Not seaworthy", tone: "critical" as const };
