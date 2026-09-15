import pytest

from sar_plimsoll.review.schema import Finding
from sar_plimsoll.scoring.engine import score
from sar_plimsoll.scoring.rubric import Rubric


def make(i: int, dimension: str, severity: str, confidence: float = 0.9) -> Finding:
    return Finding(
        id=f"f_{i:012d}",
        line_start=1,
        line_end=1,
        dimension=dimension,
        severity=severity,
        message="m",
        confidence=confidence,
        model_tier="triage",
    )


def test_clean_code_scores_ten(rubric):
    result = score([], rubric)
    assert result.overall == 10.0
    assert result.deducted == 0.0
    assert all(d.score == 10.0 for d in result.dimensions.values())


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ([("formatting", "low")] * 10, 8.9),
        ([("correctness", "high")], 8.1),
        ([("security", "critical")], 6.1),
        ([("security", "high")] * 3, 4.9),
        ([("security", "critical")] * 5, 1.5),
    ],
)
def test_calibration_matches_rubric_comments(rubric, spec, expected):
    fs = [make(i, d, s) for i, (d, s) in enumerate(spec)]
    assert score(fs, rubric).overall == expected


def test_low_confidence_findings_are_shown_but_do_not_deduct(rubric):
    result = score([make(1, "security", "critical", confidence=0.1)], rubric)
    assert result.overall == 10.0
    assert result.deductions[0].points == 0.0
    assert result.dimensions["security"].finding_ids == ["f_000000000001"]


def test_deduction_is_split_by_points(rubric):
    fs = [make(1, "security", "critical"), make(2, "formatting", "low")]
    result = score(fs, rubric)
    by_id = {d.finding_id: d.points for d in result.deductions}
    # critical security = 4.5 points, low formatting = 0.1 points → 45:1 split.
    assert by_id["f_000000000001"] == pytest.approx(45 * by_id["f_000000000002"], rel=1e-2)


def test_rubric_rejects_missing_dimension(rubric):
    data = rubric.model_dump()
    del data["dimension_weights"]["security"]
    with pytest.raises(ValueError, match="dimension_weights"):
        Rubric.model_validate(data)
