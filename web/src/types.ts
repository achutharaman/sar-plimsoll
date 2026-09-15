export type Severity = "critical" | "high" | "medium" | "low";
export type Dimension =
  | "security" | "correctness" | "performance" | "maintainability" | "architecture" | "formatting";

export interface Finding {
  id: string;
  line_start: number;
  line_end: number;
  dimension: Dimension;
  severity: Severity;
  message: string;
  suggestion: string;
  grounded_rule_ids: string[];
  confidence: number;
  model_tier: "triage" | "escalation";
}

export interface Deduction { finding_id: string; dimension: Dimension; severity: Severity; points: number }
export interface DimensionScore { score: number; deducted: number; finding_ids: string[] }

export interface ReviewResult {
  score: {
    rubric_version: string;
    overall: number;
    deducted: number;
    dimensions: Record<Dimension, DimensionScore>;
    deductions: Deduction[];
  };
  findings: Finding[];
  rules_grounded: { id: string; type: string; description: string }[];
  escalations: { lines: [number, number]; reason: string }[];
  stats: Record<string, unknown>;
}

export interface ReviewCost {
  cache_hit: boolean;
  escalated: boolean;
  escalation_reasons: string[];
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  triage_calls: number;
  escalation_calls: number;
  wall_ms: number;
  source_review_id?: string | null;
}

export interface Review {
  id: string;
  status: "queued" | "running" | "done" | "failed";
  filename: string;
  language: string;
  line_count: number;
  size_bytes: number;
  created_at: string;
  completed_at: string | null;
  rubric_version: string;
  rules_corpus_version: string;
  models: { triage: string; escalation: string };
  score: number | null;
  result: ReviewResult | null;
  cost: ReviewCost | null;
  error: string | null;
  stalled: boolean;
}

export interface FileHistory {
  filename: string;
  language: string;
  reviews: number;
  first_score: number;
  latest_score: number;
  best_score: number;
  delta: number;
  last_reviewed_at: string;
  points: { review_id: string; created_at: string; score: number; cache_hit: boolean }[];
}

export interface History {
  files: FileHistory[];
  trend: { date: string; reviews: number; mean_score: number }[];
}

export interface Traffic {
  reviews: number;
  cache_hits: number;
  cache_hit_rate: number | null;
  fresh_reviews: number;
  escalation_rate: number | null;
  mean_cost_usd: number | null;
  mean_cost_usd_fresh: number | null;
  mean_cost_usd_standard: number | null;
  cost_per_1000_reviews_usd: number | null;
  cost_per_1000_reviews_usd_standard: number | null;
  cache_savings_usd: number | null;
  total_cost_usd: number | null;
  p50_wall_ms: number | null;
  mean_score: number | null;
}

export interface Benchmark {
  tiered_reviews: number;
  baseline_reviews: number;
  tiered_mean_cost_usd: number;
  baseline_mean_cost_usd: number;
  tiered_mean_cost_usd_standard: number;
  baseline_mean_cost_usd_standard: number;
  savings_pct: number | null;
  tiered_escalation_rate: number | null;
  tiered_p50_wall_ms: number | null;
  baseline_p50_wall_ms: number | null;
  tiered_mean_score: number | null;
  baseline_mean_score: number | null;
  naive_cost_per_1000_reviews_usd?: number | null;
  plimsoll_cost_per_1000_reviews_usd?: number | null;
}

export interface Stats { scope: string; window_days: number; traffic: Traffic; benchmark: Benchmark | null }

export interface Insights {
  window_days: number;
  dimensions: ({ dimension: Dimension; findings: number } & Record<Severity, number>)[];
  rules: { rule_id: string; hits: number; reviews: number; type: string | null; description: string | null }[];
  weekly: { week_start: string; reviews: number; findings: number; findings_per_review: number | null }[];
}

export interface IngestJob {
  id: string;
  status: "queued" | "running" | "retrying" | "done" | "failed";
  filename: string;
  size_bytes: number;
  attempts: number;
  progress: { committed_rows: number; remaining: number } | null;
  report: Record<string, unknown> | null;
  error: string | null;
}

export interface ClientConfig {
  firebase: { apiKey: string; authDomain: string; projectId: string };
  auth_mode: "dev" | "firebase";
  limits: { max_file_bytes: number; max_file_lines: number; daily_review_limit: number };
  languages: string[];
  rubric_version: string;
}
