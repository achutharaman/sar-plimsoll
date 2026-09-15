import pytest

from sar_plimsoll.review.cache_key import compute_cache_key

BASE = dict(
    content_sha256="abc",
    language="python",
    rubric_version="v1",
    prompt_version="p1",
    triage_model="flash",
    escalation_model="pro",
    rules_corpus_version="rc_1",
    tenant="u1",
)


def test_key_is_stable():
    assert compute_cache_key(**BASE) == compute_cache_key(**dict(BASE))


@pytest.mark.parametrize("field", sorted(BASE))
def test_every_input_invalidates(field):
    changed = BASE | {field: BASE[field] + "-changed"}
    assert compute_cache_key(**changed) != compute_cache_key(**BASE)


def test_global_scope_shares_across_tenants():
    a = compute_cache_key(**BASE | {"tenant": None})
    b = compute_cache_key(**BASE | {"tenant": None})
    assert a == b
