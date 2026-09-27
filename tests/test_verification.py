from app.equivalence import EquivalenceTable
from app.models import BomRow, CertificateData, ExtractionSource, ReviewReason, Status
from app.store import ConfirmedPairsStore
from app.verification import build_verification_record

TABLE = EquivalenceTable.load()


def _cert(path, supplier, grade=None, standard=None, lot_number=None, source=ExtractionSource.TEXT_LAYER, grade_confidence=None, certificate_type=None):
    return CertificateData(
        path=path, supplier=supplier, text="", source=source, grade=grade, standard=standard,
        lot_number=lot_number, grade_confidence=grade_confidence, certificate_type=certificate_type,
    )


def _verify(reference, material, certs, lot_map=None, direct_suppliers=None, confirmed_store=None, correspondence_map=None):
    return build_verification_record(
        BomRow(reference=reference, material=material),
        certs,
        direct_link_suppliers=direct_suppliers or {"EBRO"},
        lot_map=lot_map or {},
        table=TABLE,
        confirmed_store=confirmed_store or ConfirmedPairsStore(path=_tmp_store()),
        correspondence_map=correspondence_map,
    )


def _tmp_store(tmp_path=None):
    import tempfile
    from pathlib import Path

    d = Path(tempfile.mkdtemp())
    p = d / "confirmed_pairs.json"
    p.write_text("[]")
    return p


def test_text_layer_direct_match_equal_material_is_ok():
    cert = _cert("EBRO/REF1/ZA1.pdf", "EBRO", grade="S355MC", standard="EN 10149")
    record = _verify("REF1", "EN 10149 S355MC", [cert])
    assert record.status == Status.OK
    assert record.suggested_action.startswith("Guardar com")


def test_text_layer_mismatch():
    cert = _cert("EBRO/REF1/ZA1.pdf", "EBRO", grade="EN AW-5052")
    record = _verify("REF1", "EN AW-6082", [cert])
    assert record.status == Status.MISMATCH


def test_ocr_sourced_result_forced_to_needs_review():
    cert = _cert("EBRO/REF1/ZA1.pdf", "EBRO", grade="S355MC", standard="EN 10149", source=ExtractionSource.OCR)
    record = _verify("REF1", "EN 10149 S355MC", [cert])
    assert record.status == Status.NEEDS_REVIEW
    assert record.reason == ReviewReason.OCR_SOURCE


def test_no_candidate_found_with_certificates_present():
    cert = _cert("RIERA/other.pdf", "RIERA", grade="EN AW-5052")
    record = _verify("REF9", "EN AW-6082", [cert], direct_suppliers=set())
    assert record.status == Status.NEEDS_REVIEW
    assert record.reason == ReviewReason.NO_MATCH_FOUND


def test_missing_certificate_when_batch_is_empty():
    record = _verify("REF1", "EN 10149 S355MC", [])
    assert record.status == Status.MISSING_CERTIFICATE


def test_certificate_type_2_2_forces_review_even_with_clean_text_layer_reading():
    # A clean, high-confidence text-layer match would normally resolve to ok - but a non-3.1
    # certificate always needs a human look regardless of read confidence, and the reason
    # shown should say so, not generically blame OCR (real case: EBRO's own EN 10204 2.2
    # certificates, e.g. examples/certificats/EBRO/unzipped/11703780001/ZA34852.pdf).
    cert = _cert("EBRO/x/ZA1.pdf", "EBRO", grade="S235JR", standard="EN 10025-2", certificate_type="2.2")
    record = _verify("REF1", "EN 10025-2:2004 S235JR", [cert])
    assert record.status == Status.NEEDS_REVIEW
    assert record.reason == ReviewReason.CERTIFICATE_TYPE
    assert record.extracted_material == "EN 10025-2 S235JR"  # the reading itself is still shown
    assert "2.2" in record.suggested_action


def test_certificate_type_3_1_does_not_force_review():
    cert = _cert("EBRO/x/ZA1.pdf", "EBRO", grade="S235JR", standard="EN 10025-2", certificate_type="3.1")
    record = _verify("REF1", "EN 10025-2:2004 S235JR", [cert])
    assert record.status == Status.OK
    assert record.reason is None


def test_certificate_type_not_found_is_not_critical():
    cert = _cert("EBRO/x/ZA1.pdf", "EBRO", grade="S235JR", standard="EN 10025-2", certificate_type=None)
    record = _verify("REF1", "EN 10025-2:2004 S235JR", [cert])
    assert record.status == Status.OK
    assert record.reason is None


def test_reference_absent_from_correspondence_flagged_distinctly():
    # A correspondence document is present, has certificates in the batch, but this
    # reference isn't in it at all - a document gap, not a matching failure - so it
    # must not be lumped in with the generic "cap coincidència" (no_match_found) message.
    cert = _cert("EBRO/x/ZA1.pdf", "EBRO", grade="S235JR", standard="EN 10025-2")
    record = _verify(
        "RCM4-L082",
        "EN 10088-1:1995 X5CrNi18-10",
        [cert],
        correspondence_map={"M009-L201": None},
    )
    assert record.status == Status.NEEDS_REVIEW
    assert record.reason == ReviewReason.NOT_IN_CORRESPONDENCE
    assert "correspondènci" in record.suggested_action.lower()


def test_ambiguous_match():
    cert_a = _cert("RIERA/a.pdf", "RIERA", grade="S355MC", standard="EN 10149")
    cert_b = _cert("RIERA/b.pdf", "RIERA", grade="S355MC", standard="EN 10149")
    record = _verify("REF1", "EN 10149 S355MC", [cert_a, cert_b], direct_suppliers=set())
    assert record.status == Status.NEEDS_REVIEW
    assert record.reason == ReviewReason.AMBIGUOUS_MATCH
    assert set(record.candidates) == {"RIERA/a.pdf", "RIERA/b.pdf"}


def test_unconfirmed_equivalence_needs_review():
    cert = _cert("RIERA/a.pdf", "RIERA", grade="AISI 304")
    record = _verify("REF1", "EN 10088-1:1995 X5CrNi18-10", [cert], direct_suppliers=set())
    assert record.status == Status.NEEDS_REVIEW
    assert record.reason == ReviewReason.EQUIVALENCE


def test_confirmed_equivalence_resolves_to_ok():
    store = ConfirmedPairsStore(path=_tmp_store())
    store.confirm("X5CrNi18-10", "X5CrNi18-10", "AISI 304")
    cert = _cert("RIERA/a.pdf", "RIERA", grade="AISI 304")
    record = _verify(
        "REF1", "EN 10088-1:1995 X5CrNi18-10", [cert], direct_suppliers=set(), confirmed_store=store
    )
    assert record.status == Status.OK


def test_correspondence_match_resolves_ok():
    cert = _cert("EBRO/x/ZA34683.pdf", "EBRO", grade="S235JR", standard="EN 10025-2")
    record = _verify(
        "I018-L211",
        "EN 10025-2:2004 S235JR",
        [cert],
        correspondence_map={"I018-L211": "ZA34683"},
    )
    assert record.status == Status.OK
    assert record.certificate_filename == "EBRO/x/ZA34683.pdf"


def test_correspondence_confirmed_missing_short_circuits_before_heuristic():
    # This certificate WOULD satisfy the material heuristically - proves the
    # confirmed_missing short-circuit skips heuristic matching entirely rather
    # than merely happening to reach the same answer.
    cert = _cert("EBRO/x/ZA_unrelated.pdf", "EBRO", grade="S235JR", standard="EN 10025-2")
    record = _verify(
        "M009-L201",
        "EN 10025-2:2004 S235JR",
        [cert],
        correspondence_map={"M009-L201": None},
    )
    assert record.status == Status.MISSING_CERTIFICATE
    assert record.certificate_filename is None
    assert "correspondènci" in record.suggested_action.lower()


def test_trustworthy_ocr_reading_resolves_to_ok():
    cert = _cert(
        "EBRO/x/ZA1.pdf", "EBRO", grade="S235JR", standard="EN 10025-2",
        source=ExtractionSource.OCR, grade_confidence=95.0,
    )
    record = _verify("REF1", "EN 10025-2:2004 S235JR", [cert])
    assert record.status == Status.OK
    assert record.reason is None


def test_trustworthy_ocr_reading_resolves_to_mismatch():
    # Heuristic search only ever surfaces materially-plausible candidates, so a
    # genuine mismatch can only be observed once a certificate is *definitely*
    # linked to a reference (correspondence/direct/lot) independent of whether
    # its material actually fits - exactly how the real M009-L111 case works.
    cert = _cert(
        "EBRO/x/ZA1.pdf", "EBRO", grade="EN AW-5754",
        source=ExtractionSource.OCR, grade_confidence=95.0,
    )
    record = _verify("REF1", "EN AW-6063", [cert], correspondence_map={"REF1": "ZA1"})
    assert record.status == Status.MISMATCH
    assert record.reason is None


def test_low_confidence_ocr_reading_still_forced_to_review():
    cert = _cert(
        "EBRO/x/ZA1.pdf", "EBRO", grade="S235JR", standard="EN 10025-2",
        source=ExtractionSource.OCR, grade_confidence=15.6,
    )
    record = _verify("REF1", "EN 10025-2:2004 S235JR", [cert])
    assert record.status == Status.NEEDS_REVIEW
    assert record.reason == ReviewReason.OCR_SOURCE


def test_confusable_ocr_reading_still_forced_to_review_despite_high_confidence():
    # "6063" is one confusable digit (6/8) from "6083" - not itself in the real
    # table, so use a synthetic table to make the point without depending on
    # exactly which grades happen to be seeded today.
    from app.equivalence import EquivalenceTable

    synthetic_table = EquivalenceTable(
        [
            {"canonical": "A", "members": [{"value": "6063", "family": "en_name"}]},
            {"canonical": "B", "members": [{"value": "6083", "family": "en_name"}]},
        ]
    )
    cert = _cert("EBRO/x/ZA1.pdf", "EBRO", grade="6063", source=ExtractionSource.OCR, grade_confidence=99.0)
    record = build_verification_record(
        BomRow(reference="REF1", material="6063"),
        [cert],
        direct_link_suppliers={"EBRO"},
        lot_map={},
        table=synthetic_table,
        confirmed_store=ConfirmedPairsStore(path=_tmp_store()),
    )
    assert record.status == Status.NEEDS_REVIEW
    assert record.reason == ReviewReason.OCR_SOURCE
