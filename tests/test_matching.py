from app.equivalence import EquivalenceTable
from app.matching import (
    STRATEGY_CORRESPONDENCE,
    STRATEGY_DIRECT_REFERENCE,
    STRATEGY_LOT_NUMBER,
    STRATEGY_MATERIAL_HEURISTIC,
    correspondence_match,
    direct_reference_match,
    find_correspondence_key,
    find_orphan_correspondence_entries,
    lot_number_match,
    match_reference,
    material_heuristic_match,
)
from app.models import CertificateData, ExtractionSource


def _cert(path, supplier, grade=None, standard=None, lot_number=None):
    return CertificateData(
        path=path,
        supplier=supplier,
        text="",
        source=ExtractionSource.TEXT_LAYER,
        grade=grade,
        standard=standard,
        lot_number=lot_number,
    )


def test_direct_reference_match_links_ebro_folder_structure(examples_dir):
    # Mirrors the real EBRO example: refs 11703780002/003/011 all map to ZA34683.pdf
    certs = [
        _cert("EBRO/11703780002/ZA34683.pdf", "EBRO"),
        _cert("EBRO/11703780003/ZA34683.pdf", "EBRO"),
        _cert("EBRO/11703780011/ZA34683.pdf", "EBRO"),
        _cert("EBRO/11703780001/ZA34852.pdf", "EBRO"),
    ]
    for ref in ("11703780002", "11703780003", "11703780011"):
        match = direct_reference_match(ref, certs, direct_link_suppliers={"EBRO"})
        assert match is not None
        assert match.path == f"EBRO/{ref}/ZA34683.pdf"


def test_lot_number_match_uses_shared_coil_number():
    cert = _cert("RIERA/S355-DEC.pdf", "RIERA", grade="S355MC", lot_number="AX5713")
    lot_map = {"M007-0099": "AX5713"}
    match = lot_number_match("M007-0099", [cert], lot_map)
    assert match is cert


def test_material_heuristic_finds_equivalent_grade_as_single_candidate():
    table = EquivalenceTable.load()
    cert = _cert("RIERA/S355-DEC.pdf", "RIERA", grade="S355MC", standard="EN 10149")
    candidates, kind = material_heuristic_match("EN 10149 S355MC", [cert], table)
    assert candidates == [cert]


def test_no_candidate_found():
    table = EquivalenceTable.load()
    candidates, _ = material_heuristic_match("EN AW-6082", [], table)
    assert candidates == []


def test_ambiguous_match_returns_multiple_candidates():
    table = EquivalenceTable.load()
    cert_a = _cert("RIERA/a.pdf", "RIERA", grade="S355MC", standard="EN 10149")
    cert_b = _cert("RIERA/b.pdf", "RIERA", grade="S355MC", standard="EN 10149")
    candidates, _ = material_heuristic_match("EN 10149 S355MC", [cert_a, cert_b], table)
    assert len(candidates) == 2


def test_match_reference_prefers_direct_link_over_heuristic():
    table = EquivalenceTable.load()
    direct_cert = _cert("EBRO/REF1/ZA1.pdf", "EBRO", grade="S355MC")
    other_cert = _cert("EBRO/REF1/ZA1.pdf", "EBRO", grade="S355MC")
    outcome = match_reference(
        "REF1",
        "EN 10149 S355MC",
        [direct_cert, other_cert],
        direct_link_suppliers={"EBRO"},
        lot_map={},
        table=table,
    )
    assert outcome.strategy == STRATEGY_DIRECT_REFERENCE


def test_match_reference_falls_back_to_lot_then_heuristic():
    table = EquivalenceTable.load()
    cert = _cert("RIERA/S355-DEC.pdf", "RIERA", grade="S355MC", standard="EN 10149", lot_number="AX5713")
    outcome = match_reference(
        "REF2",
        "EN 10149 S355MC",
        [cert],
        direct_link_suppliers={"EBRO"},
        lot_map={"REF2": "AX5713"},
        table=table,
    )
    assert outcome.strategy == STRATEGY_LOT_NUMBER

    outcome_heuristic = match_reference(
        "REF3",
        "EN 10149 S355MC",
        [cert],
        direct_link_suppliers={"EBRO"},
        lot_map={},
        table=table,
    )
    assert outcome_heuristic.strategy == STRATEGY_MATERIAL_HEURISTIC


def test_match_reference_no_strategy_finds_anything():
    table = EquivalenceTable.load()
    outcome = match_reference(
        "REF4", "EN AW-6082", [], direct_link_suppliers={"EBRO"}, lot_map={}, table=table
    )
    assert outcome.strategy is None
    assert outcome.certificate is None
    assert outcome.candidates == []


def test_correspondence_match_links_by_filename_stem():
    cert = _cert("EBRO/11703780003/ZA34683.pdf", "EBRO", grade="S235JR")
    outcome = correspondence_match("I018-L211", [cert], {"I018-L211": "ZA34683"})
    assert outcome.strategy == STRATEGY_CORRESPONDENCE
    assert outcome.certificate is cert
    assert outcome.confirmed_missing is False


def test_correspondence_match_confirmed_missing_on_blank_lote():
    outcome = correspondence_match("M009-L201", [], {"M009-L201": None})
    assert outcome.confirmed_missing is True
    assert outcome.certificate is None


def test_correspondence_match_falls_through_when_reference_absent():
    outcome = correspondence_match("NOT-IN-DOC", [], {"M009-L201": None})
    assert outcome.strategy is None
    assert outcome.confirmed_missing is False


def test_correspondence_match_falls_through_when_lote_has_no_matching_file():
    outcome = correspondence_match("I018-L211", [], {"I018-L211": "ZA34683"})
    assert outcome.strategy is None
    assert outcome.confirmed_missing is False


def test_match_reference_prefers_correspondence_over_direct_reference():
    table = EquivalenceTable.load()
    # Same cert is reachable via EBRO's folder-based direct link AND via correspondence;
    # correspondence must win per design.md's priority order.
    cert = _cert("EBRO/99999999999/ZA34683.pdf", "EBRO", grade="S235JR")
    outcome = match_reference(
        "I018-L211",
        "EN 10025-2:2004 S235JR",
        [cert],
        direct_link_suppliers={"EBRO"},
        lot_map={},
        table=table,
        correspondence_map={"I018-L211": "ZA34683"},
    )
    assert outcome.strategy == STRATEGY_CORRESPONDENCE


def test_find_correspondence_key_exact_match():
    assert find_correspondence_key("I018-L211", {"I018-L211": "ZA34683"}) == "I018-L211"


def test_find_correspondence_key_reconciles_trailing_suffix():
    # Real case: BOM's "M009-L206" vs. the correspondence document's "M009-L206-1".
    assert find_correspondence_key("M009-L206", {"M009-L206-1": None}) == "M009-L206-1"


def test_find_correspondence_key_returns_none_for_genuinely_absent_reference():
    assert find_correspondence_key("RCM4-L082", {"M009-L206-1": None}) is None


def test_correspondence_match_resolves_via_suffix_reconciliation():
    outcome = correspondence_match("M009-L206", [], {"M009-L206-1": None})
    assert outcome.confirmed_missing is True


def test_match_reference_flags_not_in_correspondence_when_reference_genuinely_absent():
    table = EquivalenceTable.load()
    outcome = match_reference(
        "RCM4-L082",
        "EN 10088-1:1995 X5CrNi18-10",
        [],
        direct_link_suppliers={"EBRO"},
        lot_map={},
        table=table,
        correspondence_map={"M009-L201": None},
    )
    assert outcome.strategy is None
    assert outcome.not_in_correspondence is True


def test_match_reference_not_in_correspondence_is_false_when_another_strategy_resolves():
    table = EquivalenceTable.load()
    cert = _cert("RIERA/S355-DEC.pdf", "RIERA", grade="S355MC", standard="EN 10149", lot_number="AX5713")
    outcome = match_reference(
        "REF2",
        "EN 10149 S355MC",
        [cert],
        direct_link_suppliers={"EBRO"},
        lot_map={"REF2": "AX5713"},
        table=table,
        correspondence_map={"M009-L201": None},
    )
    assert outcome.strategy == STRATEGY_LOT_NUMBER
    assert outcome.not_in_correspondence is False


def test_find_orphan_correspondence_entries_flags_reference_absent_from_bom():
    orphans = find_orphan_correspondence_entries(
        {"I018-L106", "M009-L206"}, {"M009-L114": "ZA33404", "I018-L106": "ZA34852"}
    )
    assert [(o.reference, o.lote) for o in orphans] == [("M009-L114", "ZA33404")]


def test_find_orphan_correspondence_entries_excludes_suffix_reconciled_references():
    # M009-L206-1 reconciles to the BOM's M009-L206 - not an orphan.
    orphans = find_orphan_correspondence_entries({"M009-L206"}, {"M009-L206-1": None})
    assert orphans == []


def test_match_reference_correspondence_confirmed_missing_short_circuits():
    table = EquivalenceTable.load()
    # A certificate that WOULD match materially is present, but correspondence says
    # this reference has no certificate - confirmed_missing must win regardless.
    cert = _cert("EBRO/x/ZA_other.pdf", "EBRO", grade="S235JR", standard="EN 10025-2")
    outcome = match_reference(
        "M009-L201",
        "EN 10025-2:2004 S235JR",
        [cert],
        direct_link_suppliers={"EBRO"},
        lot_map={},
        table=table,
        correspondence_map={"M009-L201": None},
    )
    assert outcome.confirmed_missing is True
    assert outcome.certificate is None
