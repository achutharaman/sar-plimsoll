import type { ReactNode } from "react";
import type { Severity } from "../types";

export function Card({ title, subtitle, action, children, className = "" }: {
  title?: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-xl border border-line bg-surface p-5 ${className}`}>
      {(title || action) && (
        <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            {title && <h2 className="text-[15px] font-semibold text-ink">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-[13px] text-ink-2">{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

export function Button({ children, variant = "primary", className = "", ...props }: {
  variant?: "primary" | "secondary" | "ghost";
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const styles = {
    primary: "bg-accent text-white hover:brightness-110 disabled:opacity-50",
    secondary: "border border-line bg-surface text-ink hover:bg-surface-2 disabled:opacity-50",
    ghost: "text-ink-2 hover:bg-surface-2 hover:text-ink",
  }[variant];
  return (
    <button
      {...props}
      className={`inline-flex h-9 items-center justify-center gap-2 rounded-lg px-3.5 text-sm font-medium transition disabled:cursor-not-allowed ${styles} ${className}`}
    >
      {children}
    </button>
  );
}

/** Stat tile: label · value · optional note (figure contract). */
export function StatTile({ label, value, note }: { label: string; value: ReactNode; note?: ReactNode }) {
  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <div className="text-[13px] text-ink-2">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-ink">{value}</div>
      {note && <div className="mt-1 text-xs text-muted">{note}</div>}
    </div>
  );
}

const SEVERITY: Record<Severity, { color: string; icon: string }> = {
  critical: { color: "var(--critical)", icon: "▲" },
  high: { color: "var(--serious)", icon: "◆" },
  medium: { color: "var(--warning)", icon: "●" },
  low: { color: "var(--low)", icon: "○" },
};

export const severityColor = (s: Severity) => SEVERITY[s].color;

/** Status color never alone: icon + label ride with it. */
export function SeverityBadge({ severity }: { severity: Severity }) {
  const { color, icon } = SEVERITY[severity];
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md border border-line px-1.5 py-0.5 text-xs font-medium text-ink">
      <span aria-hidden style={{ color }}>{icon}</span>
      {severity}
    </span>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-ink-2" role="status">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-grid border-t-accent" />
      {label}
    </span>
  );
}

export function Notice({ tone = "error", children }: { tone?: "error" | "info"; children: ReactNode }) {
  const border = tone === "error" ? "border-l-[var(--critical)]" : "border-l-[var(--accent)]";
  return (
    <div role={tone === "error" ? "alert" : "status"} className={`rounded-lg border border-line border-l-4 ${border} bg-surface px-4 py-3 text-sm text-ink`}>
      {children}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-line px-6 py-10 text-center">
      <div className="text-sm font-medium text-ink">{title}</div>
      {children && <div className="mt-1 text-sm text-ink-2">{children}</div>}
    </div>
  );
}

export function Segmented<T extends string | number>({ value, options, onChange, label }: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-lg border border-line bg-surface p-0.5">
      {options.map((o) => (
        <button
          key={String(o.value)}
          role="radio"
          aria-checked={o.value === value}
          onClick={() => onChange(o.value)}
          className={`h-8 rounded-md px-3 text-sm ${o.value === value ? "bg-surface-2 font-semibold text-ink" : "text-ink-2 hover:text-ink"}`}
        >
          {o.value === value && <span aria-hidden className="mr-1">✓</span>}
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** The Plimsoll mark at text size: identifies a rule set wherever it appears. */
export function RulesMark({ size = 12 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden className="shrink-0">
      <circle cx="16" cy="16" r="10" fill="none" stroke="var(--accent)" strokeWidth="4" />
      <path d="M2 16h28" stroke="var(--accent)" strokeWidth="4" />
    </svg>
  );
}

/** Readable identity of the rules corpus a review was scored with: name, size, short hash. */
export function RulesBadge({ label, short, size, version }: {
  label: string;
  short: string | null;
  size?: number | null;
  version?: string | null;
}) {
  const detail = version ? `Rules version ${version}${size != null ? ` · ${size} rules` : ""}` : "Reviewed before any rules were ingested";
  return (
    <span
      title={detail}
      className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-line bg-surface px-2 py-1 text-xs text-ink"
    >
      <RulesMark />
      <span className="font-semibold">{label}</span>
      {size != null && size > 0 && <span className="text-ink-2">· {size.toLocaleString()} rules</span>}
      {short && <span className="font-mono text-[11px] text-muted">{short}</span>}
    </span>
  );
}
