from app.correspondence import parse_correspondence_xlsx


def test_parses_client_ref_to_lote(examples_dir):
    mapping = parse_correspondence_xlsx(examples_dir / "EBRO" / "correspondencia_1170378.xlsx")
    assert mapping["I018-L211"] == "ZA34683"
    assert mapping["M009-L111"] == "ZA34620"
    assert mapping["I018-L209"] == "ZA34683"


def test_blank_lote_kept_as_none_not_dropped(examples_dir):
    mapping = parse_correspondence_xlsx(examples_dir / "EBRO" / "correspondencia_1170378.xlsx")
    assert "M009-L201" in mapping
    assert mapping["M009-L201"] is None
    assert "M009-L202" in mapping
    assert mapping["M009-L202"] is None
    assert "M009-L204" in mapping
    assert mapping["M009-L204"] is None


def test_suffixed_reference_kept_as_its_own_literal_key(examples_dir):
    mapping = parse_correspondence_xlsx(examples_dir / "EBRO" / "correspondencia_1170378.xlsx")
    assert "M009-L206-1" in mapping
    assert mapping["M009-L206-1"] is None
    # Deliberately not reconciled with the BOM's plain "M009-L206" - see design.md non-goals.
    assert "M009-L206" not in mapping


def test_reference_not_in_document_is_simply_absent(examples_dir):
    mapping = parse_correspondence_xlsx(examples_dir / "EBRO" / "correspondencia_1170378.xlsx")
    assert "I018-M108" not in mapping


def test_semicolon_csv_correspondence():
    from app.correspondence import parse_correspondence_csv

    content = "Albarán;Pos;ClientRef;Lote\n1170378;1;I018-L211;ZA34683\n1170378;2;M009-L201;\n".encode("cp1252")
    assert parse_correspondence_csv(content) == {"I018-L211": "ZA34683", "M009-L201": None}
