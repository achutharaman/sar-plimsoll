"""Per-user history views built from review summaries (no findings, no source).

A "session" is the sequence of reviews of one filename (see DECISIONS.md), so growth on the same
code is visible directly: app.py 4.2 → 6.8 → 7.9.
"""

from collections import defaultdict
from datetime import datetime
from typing import Any


def _done(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [s for s in summaries if s.get("status") == "done" and s.get("score") is not None]


def file_histories(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in _done(summaries):
        by_file[s["filename"]].append(s)

    files = []
    for filename, items in by_file.items():
        items.sort(key=lambda s: s["created_at"])
        scores = [s["score"] for s in items]
        files.append(
            {
                "filename": filename,
                "language": items[-1].get("language"),
                "reviews": len(items),
                "first_score": scores[0],
                "latest_score": scores[-1],
                "best_score": max(scores),
                "delta": round(scores[-1] - scores[0], 1),
                "last_reviewed_at": items[-1]["created_at"],
                "points": [
                    {
                        "review_id": s["id"],
                        "created_at": s["created_at"],
                        "score": s["score"],
                        "cache_hit": bool((s.get("cost") or {}).get("cache_hit")),
                    }
                    for s in items
                ],
            }
        )
    return sorted(files, key=lambda f: f["last_reviewed_at"], reverse=True)


def daily_trend(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_day: dict[str, list[float]] = defaultdict(list)
    for s in _done(summaries):
        created: datetime = s["created_at"]
        by_day[created.date().isoformat()].append(s["score"])
    return [
        {"date": day, "reviews": len(scores), "mean_score": round(sum(scores) / len(scores), 2)}
        for day, scores in sorted(by_day.items())
    ]
