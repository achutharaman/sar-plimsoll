"""Property tests for the rubric. These are the project's core guarantees."""

import random

from hypothesis import given, settings
from hypothesis import strategies as st

from sar_plimsoll.paths import project_root
from sar_plimsoll.review.schema import DIMENSIONS, SEVERITIES, Finding
from sar_plimsoll.scoring.engine import score
from sar_plimsoll.scoring.rubric import load_rubric

RUBRIC = load_rubric(project_root() / "rubric" / "v1.yaml")


@st.composite
def findings(draw) -> Finding:
    line = draw(st.integers(min_value=1, max_value=5000))
    return Finding(
        id="f_" + draw(st.text(alphabet="0123456789abcdef", min_size=12, max_size=12)),
        line_start=line,
        line_end=line + draw(st.integers(min_value=0, max_value=50)),
        dimension=draw(st.sampled_from(DIMENSIONS)),
        severity=draw(st.sampled_from(SEVERITIES)),
        message="m",
        confidence=draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False)),
        model_tier="triage",
    )


finding_lists = st.lists(findings(), max_size=200)


@given(finding_lists)
def test_identical_findings_give_identical_scores(fs):
    assert score(fs, RUBRIC) == score(list(fs), RUBRIC)


@given(finding_lists, st.randoms())
def test_order_does_not_matter(fs, rnd: random.Random):
    shuffled = list(fs)
    rnd.shuffle(shuffled)
    assert score(fs, RUBRIC) == score(shuffled, RUBRIC)


@given(finding_lists, findings())
def test_adding_a_finding_never_increases_the_score(fs, extra):
    before = score(fs, RUBRIC)
    after = score([*fs, extra], RUBRIC)
    assert after.overall <= before.overall
    for dim in DIMENSIONS:
        assert after.dimensions[dim].score <= before.dimensions[dim].score


@settings(max_examples=300)
@given(finding_lists)
def test_scores_always_within_bounds(fs):
    result = score(fs, RUBRIC)
    assert 1.0 <= result.overall <= 10.0
    assert all(1.0 <= d.score <= 10.0 for d in result.dimensions.values())


@given(finding_lists)
def test_every_deducted_point_is_attributed_to_a_finding(fs):
    result = score(fs, RUBRIC)
    attributed_milli = sum(round(d.points * 1000) for d in result.deductions)
    assert attributed_milli == round(result.deducted * 1000)
    assert abs((10 - result.deducted) - result.overall) <= 0.05 + 1e-9
    assert {d.finding_id for d in result.deductions} == {f.id for f in fs}
