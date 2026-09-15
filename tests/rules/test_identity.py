from sar_plimsoll.rules.identity import rules_label, short_version


def test_label_is_deterministic_and_readable():
    assert rules_label("rc_4905f02b8dfdd414") == rules_label("rc_4905f02b8dfdd414")
    adjective, noun = rules_label("rc_4905f02b8dfdd414").split("-")
    assert adjective.isalpha() and noun.isalpha()


def test_different_versions_usually_get_different_names():
    labels = {rules_label(f"rc_{i:016x}") for i in range(200)}
    assert len(labels) > 190  # 4,096 combinations; the short hash disambiguates the rare collision


def test_empty_corpus():
    assert rules_label("empty") == "no rules" and rules_label(None) == "no rules"
    assert short_version("empty") is None
    assert short_version("rc_4905f02b8dfdd414") == "rc_4905f0"
