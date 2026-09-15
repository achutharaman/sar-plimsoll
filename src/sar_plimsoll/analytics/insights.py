"""Recurring-issue analytics: which dimensions and historical rules keep showing up for a user."""

from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from sar_plimsoll.review.schema import DIMENSIONS, SEVERITIES

TOP_RULES = 10


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def build_recurring(
    findings: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    rule_lookup: Callable[[str], dict[str, Any] | None],
) -> dict[str, Any]:
    by_dim: dict[str, Counter] = {d: Counter() for d in DIMENSIONS}
    rule_hits: Counter = Counter()
    rule_reviews: dict[str, set[str]] = defaultdict(set)
    for f in findings:
        by_dim[f["dimension"]][f["severity"]] += 1
        for rule_id in f.get("grounded_rule_ids", []):
            rule_hits[rule_id] += 1
            rule_reviews[rule_id].add(f["review_id"])

    dimensions = [
        {"dimension": d, "findings": sum(c.values()), **{s: c[s] for s in SEVERITIES}}
        for d, c in by_dim.items()
        if sum(c.values())
    ]
    dimensions.sort(key=lambda d: (-d["findings"], d["dimension"]))

    rules = []
    for rule_id, hits in sorted(rule_hits.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_RULES]:
        rule = rule_lookup(rule_id) or {}
        rules.append(
            {
                "rule_id": rule_id,
                "hits": hits,
                "reviews": len(rule_reviews[rule_id]),
                "type": rule.get("type"),
                "description": rule.get("description"),
            }
        )

    weeks: dict[date, dict[str, int]] = defaultdict(lambda: {"reviews": 0, "findings": 0})
    for r in reviews:
        wk = week_start(datetime.fromisoformat(r["created_at"]).date())
        weeks[wk]["reviews"] += 1
        weeks[wk]["findings"] += r["findings"]
    weekly = [
        {
            "week_start": wk.isoformat(),
            "reviews": v["reviews"],
            "findings": v["findings"],
            "findings_per_review": round(v["findings"] / v["reviews"], 2) if v["reviews"] else None,
        }
        for wk, v in sorted(weeks.items())
    ]
    return {"dimensions": dimensions, "rules": rules, "weekly": weekly}
