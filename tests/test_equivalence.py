from app.equivalence import (
    EquivalenceTable,
    MatchKind,
    classify,
    has_confusable_alternate,
    is_reading_trustworthy,
    is_single_confusable_substitution,
)


def test_table_loads_and_is_queryable():
    table = EquivalenceTable.load()
    members = table.find_members("EN 10088-1:1995 X5CrNi18-10")
    assert any(m.canonical == "X5CrNi18-10" for m in members)


def test_exact_string_match():
    table = EquivalenceTable.load()
    result = classify("S235JR", "S235JR", table)
    assert result.kind == MatchKind.EXACT


def test_same_standard_alias_is_not_a_judgment_call():
    table = EquivalenceTable.load()
    # EN name vs EN Werkstoff number - same standards body, always equivalent.
    result = classify("EN 10088-1:1995 X5CrNi18-10", "1.4301", table)
    assert result.kind == MatchKind.SAME_STANDARD
    assert result.canonical == "X5CrNi18-10"


def test_cross_standard_equivalence_flagged_not_auto_ok():
    table = EquivalenceTable.load()
    # X5CrNi18-10 (EN) vs AISI 304 (a different standards body) - real judgment call.
    result = classify("EN 10088-1:1995 X5CrNi18-10", "AISI 304", table)
    assert result.kind == MatchKind.CROSS_STANDARD
    assert result.canonical == "X5CrNi18-10"
    assert result.specified_value == "X5CrNi18-10"
    assert result.extracted_value == "AISI 304"


def test_mismatch_between_different_known_materials():
    table = EquivalenceTable.load()
    result = classify("EN AW-6082", "EN AW-5052", table)
    assert result.kind == MatchKind.MISMATCH


def test_unknown_when_neither_side_recognized():
    table = EquivalenceTable.load()
    result = classify("Plastic LCP", "Some unrelated text", table)
    assert result.kind == MatchKind.UNKNOWN


def _synthetic_table():
    return EquivalenceTable(
        [
            {"canonical": "A", "members": [{"value": "6063", "family": "en_name"}]},
            {"canonical": "B", "members": [{"value": "6083", "family": "en_name"}]},  # one confusable digit (6/8) from 6063
            {"canonical": "C", "members": [{"value": "S235JR", "family": "en_name"}]},
        ]
    )


def test_is_single_confusable_substitution_true_for_known_pair():
    assert is_single_confusable_substitution("6063", "6083") is True  # 6 vs 8 at index 2


def test_is_single_confusable_substitution_false_for_non_confusable_difference():
    assert is_single_confusable_substitution("6063", "6064") is False  # 3 vs 4 - not a confusable pair


def test_is_single_confusable_substitution_false_for_different_lengths():
    assert is_single_confusable_substitution("6063", "60633") is False


def test_has_confusable_alternate_true_when_another_canonical_is_one_swap_away():
    table = _synthetic_table()
    assert has_confusable_alternate("6063", table) is True  # confusable with canonical B's "6083"


def test_has_confusable_alternate_false_when_unique():
    table = _synthetic_table()
    assert has_confusable_alternate("S235JR", table) is False


def test_is_reading_trustworthy_requires_both_confidence_and_no_confusable_alternate():
    table = _synthetic_table()
    assert is_reading_trustworthy("S235JR", 95.0, table) is True  # high confidence, unambiguous
    assert is_reading_trustworthy("S235JR", 50.0, table) is False  # low confidence
    assert is_reading_trustworthy("6063", 95.0, table) is False  # high confidence but confusable
    assert is_reading_trustworthy(None, 95.0, table) is False  # nothing extracted
    assert is_reading_trustworthy("S235JR", None, table) is False  # no confidence data at all
