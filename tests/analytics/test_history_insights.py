from datetime import UTC, datetime, timedelta

from sar_plimsoll.analytics.history import daily_trend, file_histories
from sar_plimsoll.analytics.insights import build_recurring

T0 = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)


def summary(i, filename, score, hours, status="done", cache_hit=False):
    return {
        "id": f"r{i}",
        "filename": filename,
        "language": "python",
        "status": status,
        "created_at": T0 + timedelta(hours=hours),
        "score": score,
        "cost": {"cache_hit": cache_hit, "cost_usd": 0.01},
    }


def test_file_histories_track_growth_per_filename():
    summaries = [
        summary(1, "app.py", 4.2, 0),
        summary(2, "app.py", 6.8, 24),
        summary(3, "app.py", 7.9, 48),
        summary(4, "util.py", 9.0, 30),
        summary(5, "app.py", None, 50, status="queued"),
    ]
    files = file_histories(list(reversed(summaries)))  # store returns newest first
    assert [f["filename"] for f in files] == ["app.py", "util.py"]
    app = files[0]
    assert (app["reviews"], app["first_score"], app["latest_score"], app["delta"]) == (
        3,
        4.2,
        7.9,
        3.7,
    )
    assert [p["score"] for p in app["points"]] == [4.2, 6.8, 7.9]


def test_daily_trend_means_by_day():
    trend = daily_trend(
        [summary(1, "a.py", 4.0, 0), summary(2, "b.py", 6.0, 2), summary(3, "a.py", 8.0, 24)]
    )
    assert trend == [
        {"date": "2026-09-10", "reviews": 2, "mean_score": 5.0},
        {"date": "2026-09-11", "reviews": 1, "mean_score": 8.0},
    ]


def test_recurring_ranks_dimensions_and_rules():
    findings = [
        {
            "review_id": "r1",
            "dimension": "security",
            "severity": "critical",
            "grounded_rule_ids": ["3"],
        },
        {
            "review_id": "r2",
            "dimension": "security",
            "severity": "high",
            "grounded_rule_ids": ["3"],
        },
        {
            "review_id": "r2",
            "dimension": "performance",
            "severity": "medium",
            "grounded_rule_ids": ["2"],
        },
        {"review_id": "r2", "dimension": "formatting", "severity": "low", "grounded_rule_ids": []},
    ]
    reviews = [
        {"created_at": "2026-09-08T10:00:00+00:00", "findings": 1},
        {"created_at": "2026-09-09T10:00:00+00:00", "findings": 3},
        {"created_at": "2026-09-15T10:00:00+00:00", "findings": 0},
    ]
    rules = {"3": {"type": "security", "description": "No SQL interpolation"}}
    out = build_recurring(findings, reviews, rules.get)
    assert out["dimensions"][0] == {
        "dimension": "security",
        "findings": 2,
        "low": 0,
        "medium": 0,
        "high": 1,
        "critical": 1,
    }
    assert out["rules"][0] == {
        "rule_id": "3",
        "hits": 2,
        "reviews": 2,
        "type": "security",
        "description": "No SQL interpolation",
    }
    assert out["rules"][1]["description"] is None
    assert out["weekly"] == [
        {"week_start": "2026-09-07", "reviews": 2, "findings": 4, "findings_per_review": 2.0},
        {"week_start": "2026-09-14", "reviews": 1, "findings": 0, "findings_per_review": 0.0},
    ]
