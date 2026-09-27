from app.locate import locate, mark_terms_in_html


def _w(text, page, x):
    return (text, page, x, 0.1, x + 0.05, 0.12)


def test_finds_grade_split_across_words_and_ignores_punctuation():
    words = [_w("Calidad:", 0, 0.1), _w("EN", 0, 0.2), _w("AW-", 0, 0.3), _w("5754", 0, 0.4), _w("H111", 0, 0.5)]
    [box] = locate(words, grade="EN AW-5754")
    assert box["kind"] == "grade" and box["page"] == 0
    assert (box["x0"], box["x1"]) == (0.2, 0.45)


def test_certificate_type_after_10204():
    words = [_w("EN", 0, 0.1), _w("10204", 0, 0.2), _w("TIPO", 0, 0.3), _w("2.2", 0, 0.4), _w("S235JR", 0, 0.6)]
    boxes = locate(words, grade="S235JR", certificate_type="2.2")
    assert {b["kind"] for b in boxes} == {"grade", "type"}
    type_box = next(b for b in boxes if b["kind"] == "type")
    assert (type_box["x0"], type_box["x1"]) == (0.2, 0.45)


def test_never_matches_across_pages_and_missing_values_give_no_box():
    words = [_w("S235", 0, 0.9), _w("JR", 1, 0.1)]
    assert locate(words, grade="S235JR", standard=None) == []
    assert locate([], grade="S235JR") == []


def test_mark_terms_only_in_text_not_in_tags():
    html = '<p class="S235JR">Material EN 10025-2 S-235JR</p>'
    marked = mark_terms_in_html(html, ["S235JR", "EN 10025-2"])
    assert '<p class="S235JR">' in marked
    assert "<mark>S-235JR</mark>" in marked and "<mark>EN 10025-2</mark>" in marked
