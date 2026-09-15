"""Deterministic scoring. Pure: no I/O, no model calls, no clocks, no randomness.

    points(f) = severity_points[f.severity] * dimension_weights[f.dimension]
                (0 when f.confidence < min_confidence)
    R         = sum of points
    overall   = 1 + 9 * exp(-R / decay)

The exponential keeps the result inside [1, 10] without clipping, and it strictly decreases as
points are added, so adding a finding can never raise the score. The deduction (10 - overall) is
split across findings in proportion to their points (largest-remainder rounding to 0.001), so
every lost point is attributable and the displayed parts add up exactly to the total.
"""

import math
from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel

from sar_plimsoll.review.schema import DIMENSIONS
from sar_plimsoll.scoring.rubric import Rubric

MAX_SCORE = 10.0
MIN_SCORE = 1.0
_SPAN = MAX_SCORE - MIN_SCORE


class Scorable(Protocol):
    id: str
    dimension: str
    severity: str
    confidence: float


class Deduction(BaseModel):
    finding_id: str
    dimension: str
    severity: str
    points: float


class DimensionScore(BaseModel):
    score: float
    deducted: float
    finding_ids: list[str]


class ScoreResult(BaseModel):
    rubric_version: str
    overall: float
    deducted: float
    dimensions: dict[str, DimensionScore]
    deductions: list[Deduction]


def _curve(raw_points: float, decay: float) -> float:
    return MIN_SCORE + _SPAN * math.exp(-raw_points / decay)


def _points(f: Scorable, rubric: Rubric) -> float:
    if f.confidence < rubric.min_confidence:
        return 0.0
    return rubric.severity_points[f.severity] * rubric.dimension_weights[f.dimension]


def _apportion(total_milli: int, points: list[float], total_points: float) -> list[int]:
    """Largest-remainder split of the deduction in thousandths, so displayed parts sum exactly."""
    if total_points <= 0 or total_milli <= 0:
        return [0] * len(points)
    exact = [total_milli * p / total_points for p in points]
    shares = [math.floor(x) for x in exact]
    remainder = total_milli - sum(shares)
    # Ties resolve by canonical finding order, so the split is deterministic.
    by_fraction = sorted(range(len(points)), key=lambda i: (-(exact[i] - shares[i]), i))
    for i in by_fraction[:remainder]:
        shares[i] += 1
    return shares


def score(findings: Sequence[Scorable], rubric: Rubric) -> ScoreResult:
    # Canonical order, and fsum (exactly rounded) so input order can never change the result.
    ordered = sorted(findings, key=lambda f: (f.id, f.dimension, f.severity, f.confidence))
    points = [_points(f, rubric) for f in ordered]
    total = math.fsum(points)

    overall_exact = _curve(total, rubric.decay)
    deducted_milli = round((MAX_SCORE - overall_exact) * 1000)
    shares = _apportion(deducted_milli, points, total)

    deductions = [
        Deduction(finding_id=f.id, dimension=f.dimension, severity=f.severity, points=m / 1000)
        for f, m in zip(ordered, shares, strict=True)
    ]

    dimensions: dict[str, DimensionScore] = {}
    for dim in DIMENSIONS:
        members = [(f, p) for f, p in zip(ordered, points, strict=True) if f.dimension == dim]
        # Subscores use severity points only; dimension weights express importance to the overall.
        dim_raw = math.fsum(
            rubric.severity_points[f.severity]
            for f, _ in members
            if f.confidence >= rubric.min_confidence
        )
        dim_score = _curve(dim_raw, rubric.decay)
        dimensions[dim] = DimensionScore(
            score=round(dim_score, 1),
            deducted=sum(m for f, m in zip(ordered, shares, strict=True) if f.dimension == dim)
            / 1000,
            finding_ids=[f.id for f, _ in members],
        )

    return ScoreResult(
        rubric_version=rubric.version,
        overall=min(MAX_SCORE, max(MIN_SCORE, round(overall_exact, 1))),
        deducted=deducted_milli / 1000,
        dimensions=dimensions,
        deductions=deductions,
    )
