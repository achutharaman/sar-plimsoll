import pytest

from sar_plimsoll.analytics.stats import ModeAggregate, aggregate_rows, build_stats


def row(
    mode="tiered",
    cache_hit=False,
    escalated=False,
    cost=0.01,
    std=0.02,
    score=7.0,
    wall=1000,
    status="done",
):
    return {
        "mode": mode,
        "cache_hit": cache_hit,
        "escalated": escalated,
        "cost_usd": cost,
        "cost_usd_standard": std,
        "score": score,
        "wall_ms": wall,
        "status": status,
    }


def test_traffic_metrics_count_cache_hits_as_free_reviews():
    rows = [
        row(cost=0.02, std=0.04, escalated=True, wall=3000),
        row(cost=0.01, std=0.02, wall=1000),
        row(cache_hit=True, cost=0.0, std=0.0, escalated=False, wall=5),
        row(cache_hit=True, cost=0.0, std=0.0, wall=7),
        row(status="failed", cost=0.5),  # failed reviews are excluded from traffic metrics
    ]
    t = build_stats(aggregate_rows(rows), scope="me", days=30)["traffic"]
    assert t["reviews"] == 4
    assert t["cache_hit_rate"] == 0.5
    assert t["escalation_rate"] == 0.5  # 1 of 2 fresh reviews
    assert t["mean_cost_usd"] == pytest.approx(0.0075)
    assert t["mean_cost_usd_fresh"] == pytest.approx(0.015)
    assert t["cost_per_1000_reviews_usd"] == pytest.approx(7.5)
    assert t["cost_per_1000_reviews_usd_standard"] == pytest.approx(15.0)
    assert t["cache_savings_usd"] == pytest.approx(0.03)
    assert t["p50_wall_ms"] == 2000  # cache hits are not in the latency sample


def test_benchmark_savings_and_projection():
    rows = [row(mode="benchmark_tiered", cost=0.03), row(mode="benchmark_baseline", cost=0.10)]
    rows += [row(), row(cache_hit=True, cost=0.0)]
    b = build_stats(aggregate_rows(rows), scope="system", days=30)["benchmark"]
    assert b["savings_pct"] == pytest.approx(0.7)
    assert b["naive_cost_per_1000_reviews_usd"] == pytest.approx(100.0)
    # tiered benchmark cost at a 50% cache miss rate
    assert b["plimsoll_cost_per_1000_reviews_usd"] == pytest.approx(15.0)


def test_empty_stats_are_well_formed():
    stats = build_stats({}, scope="me", days=7)
    assert stats["traffic"]["reviews"] == 0
    assert stats["traffic"]["mean_cost_usd"] is None
    assert stats["benchmark"] is None


def test_warehouse_aggregate_shape_is_accepted():
    aggs = {
        "tiered": ModeAggregate(
            reviews=10,
            cache_hits=4,
            escalated=2,
            cost_usd=0.12,
            cost_usd_standard=0.2,
            p50_wall_ms=900,
            score_sum=70,
            scored=10,
        )
    }
    t = build_stats(aggs, scope="system", days=30)["traffic"]
    assert (t["escalation_rate"], t["mean_score"], t["p50_wall_ms"]) == (
        pytest.approx(0.3333),
        7.0,
        900,
    )
