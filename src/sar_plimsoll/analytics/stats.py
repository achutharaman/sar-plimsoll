"""Cost and quality statistics. Warehouse queries return per-mode aggregates; this module turns them
into the numbers the cost panel shows, identically for every backend.

Modes:
  tiered              production traffic (includes cache hits)
  benchmark_tiered    controlled benchmark: two-tier pipeline, no cache
  benchmark_baseline  the same files, every call sent straight to the expensive model
"""

import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModeAggregate:
    reviews: int = 0
    cache_hits: int = 0
    escalated: int = 0
    cost_usd: float = 0.0
    cost_usd_standard: float = 0.0
    p50_wall_ms: int | None = None
    score_sum: float = 0.0
    scored: int = 0
    wall_samples: list[int] = field(default_factory=list, repr=False)


def aggregate_rows(rows: Iterable[dict[str, Any]]) -> dict[str, ModeAggregate]:
    """Pure-Python equivalent of the warehouse aggregation query (used by the in-memory backend)."""
    out: dict[str, ModeAggregate] = {}
    for row in rows:
        if row["status"] != "done":
            continue
        agg = out.setdefault(row["mode"], ModeAggregate())
        agg.reviews += 1
        agg.cache_hits += int(row["cache_hit"])
        agg.escalated += int(row["escalated"] and not row["cache_hit"])
        agg.cost_usd += row["cost_usd"]
        agg.cost_usd_standard += row["cost_usd_standard"]
        if row["score"] is not None:
            agg.score_sum += row["score"]
            agg.scored += 1
        if not row["cache_hit"]:
            agg.wall_samples.append(row["wall_ms"])
    for agg in out.values():
        agg.p50_wall_ms = int(statistics.median(agg.wall_samples)) if agg.wall_samples else None
    return out


def _ratio(num: float, den: float) -> float | None:
    return round(num / den, 4) if den else None


def _money(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def _traffic(agg: ModeAggregate) -> dict[str, Any]:
    fresh = agg.reviews - agg.cache_hits
    mean_fresh = agg.cost_usd / fresh if fresh else None
    mean_all = agg.cost_usd / agg.reviews if agg.reviews else None
    mean_all_std = agg.cost_usd_standard / agg.reviews if agg.reviews else None
    return {
        "reviews": agg.reviews,
        "cache_hits": agg.cache_hits,
        "cache_hit_rate": _ratio(agg.cache_hits, agg.reviews),
        "fresh_reviews": fresh,
        "escalation_rate": _ratio(agg.escalated, fresh),
        "mean_cost_usd": _money(mean_all),
        "mean_cost_usd_fresh": _money(mean_fresh),
        "mean_cost_usd_standard": _money(mean_all_std),
        "cost_per_1000_reviews_usd": _money(mean_all * 1000 if mean_all is not None else None),
        "cost_per_1000_reviews_usd_standard": _money(
            mean_all_std * 1000 if mean_all_std is not None else None
        ),
        "cache_savings_usd": _money(agg.cache_hits * mean_fresh if mean_fresh else 0.0),
        "total_cost_usd": _money(agg.cost_usd),
        "p50_wall_ms": agg.p50_wall_ms,
        "mean_score": round(agg.score_sum / agg.scored, 2) if agg.scored else None,
    }


def build_stats(aggs: dict[str, ModeAggregate], *, scope: str, days: int) -> dict[str, Any]:
    traffic = _traffic(aggs.get("tiered", ModeAggregate()))
    tiered, baseline = aggs.get("benchmark_tiered"), aggs.get("benchmark_baseline")
    benchmark = None
    if tiered and baseline and tiered.reviews and baseline.reviews:
        t, b = _traffic(tiered), _traffic(baseline)
        benchmark = {
            "tiered_reviews": tiered.reviews,
            "baseline_reviews": baseline.reviews,
            "tiered_mean_cost_usd": t["mean_cost_usd"],
            "baseline_mean_cost_usd": b["mean_cost_usd"],
            "tiered_mean_cost_usd_standard": t["mean_cost_usd_standard"],
            "baseline_mean_cost_usd_standard": b["mean_cost_usd_standard"],
            "savings_pct": round(1 - t["mean_cost_usd"] / b["mean_cost_usd"], 4)
            if b["mean_cost_usd"]
            else None,
            "tiered_escalation_rate": t["escalation_rate"],
            "tiered_p50_wall_ms": t["p50_wall_ms"],
            "baseline_p50_wall_ms": b["p50_wall_ms"],
            "tiered_mean_score": t["mean_score"],
            "baseline_mean_score": b["mean_score"],
        }
        # Projection that combines both levers: the benchmark's tier savings at the observed cache rate.
        if traffic["cache_hit_rate"] is not None and b["mean_cost_usd"]:
            miss = 1 - traffic["cache_hit_rate"]
            benchmark["naive_cost_per_1000_reviews_usd"] = _money(b["mean_cost_usd"] * 1000)
            benchmark["plimsoll_cost_per_1000_reviews_usd"] = _money(
                t["mean_cost_usd"] * miss * 1000
            )
    return {"scope": scope, "window_days": days, "traffic": traffic, "benchmark": benchmark}
