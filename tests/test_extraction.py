from app.extraction import (
    confidence_for_span,
    extract_certificate,
    extract_text_layer,
    extract_via_ocr,
    extract_via_ocr_with_confidence,
    has_text_layer,
    parse_certificate_type,
    parse_grade,
    parse_lot_number,
    parse_standard,
    split_norma_and_grade,
)
from app.models import ExtractionSource


def test_text_layer_extraction_pulls_grade_and_standard_from_riera(examples_dir):
    path = examples_dir / "RIERA" / "S355-DEC 3X15X4 13-04-2023 GUTSER.pdf"
    text = extract_text_layer(path)
    assert "437553" in text  # certificate number
    assert "202300763" in text  # order number
    assert parse_grade(text) == "S355MC"
    assert parse_standard(text) == "EN 10149"


def test_has_text_layer_distinguishes_digital_from_scanned(examples_dir):
    riera_text = extract_text_layer(examples_dir / "RIERA" / "S355-DEC 3X15X4 13-04-2023 GUTSER.pdf")
    iamcut_text = extract_text_layer(examples_dir / "IAMCUT" / "CH51704.pdf")
    ebro_text = extract_text_layer(examples_dir / "EBRO" / "unzipped" / "11703780001" / "ZA34852.pdf")

    assert has_text_layer(riera_text) is True
    assert has_text_layer(iamcut_text) is False
    assert has_text_layer(ebro_text) is False


def test_ocr_fallback_produces_nonempty_text(examples_dir):
    iamcut_text = extract_via_ocr(examples_dir / "IAMCUT" / "CH51704.pdf")
    ebro_text = extract_via_ocr(examples_dir / "EBRO" / "unzipped" / "11703780001" / "ZA34852.pdf")

    assert len(iamcut_text.strip()) > 0
    assert len(ebro_text.strip()) > 0


def test_lot_number_extraction_from_riera_coil_number(examples_dir):
    text = extract_text_layer(examples_dir / "RIERA" / "S355-DEC 3X15X4 13-04-2023 GUTSER.pdf")
    assert parse_lot_number(text) == "AX5713"


def test_extract_certificate_tags_source_text_layer(examples_dir):
    cert = extract_certificate(
        examples_dir / "RIERA" / "S355-DEC 3X15X4 13-04-2023 GUTSER.pdf",
        supplier="RIERA",
        relative_path="RIERA/S355-DEC.pdf",
    )
    assert cert.source == ExtractionSource.TEXT_LAYER
    assert cert.grade == "S355MC"


def test_extract_certificate_tags_source_ocr(examples_dir):
    cert = extract_certificate(
        examples_dir / "IAMCUT" / "CH51704.pdf",
        supplier="IAMCUT",
        relative_path="IAMCUT/CH51704.pdf",
    )
    assert cert.source == ExtractionSource.OCR


def test_ocr_with_confidence_produces_valid_confidence_scores(examples_dir):
    text, spans = extract_via_ocr_with_confidence(
        examples_dir / "EBRO" / "unzipped" / "11703780002" / "ZA34683.pdf"
    )
    assert len(text.strip()) > 0
    assert len(spans) > 0
    for start, end, conf in spans:
        assert 0 <= start < end <= len(text)
        assert 0 <= conf <= 100


def test_confidence_for_span_finds_overlapping_word_confidence():
    text = "hello S235JR world"
    spans = [(0, 5, 95.0), (6, 12, 88.5), (13, 18, 91.0)]
    assert confidence_for_span(text, "S235JR", spans) == 88.5


def test_confidence_for_span_returns_none_when_target_not_found():
    assert confidence_for_span("hello world", "MISSING", [(0, 5, 90.0)]) is None


def test_confidence_for_span_returns_none_without_ocr_word_data():
    # Text-layer extraction has no word spans at all.
    assert confidence_for_span("hello S235JR world", "S235JR", []) is None


def test_ground_truth_certificates_have_grade_confidence(examples_dir):
    # Grounding the confidence threshold picked in equivalence.py against the
    # two real, confirmed ground-truth certificates - see design.md.
    ok_cert = extract_certificate(
        examples_dir / "EBRO" / "unzipped" / "11703780002" / "ZA34683.pdf",
        supplier="EBRO",
        relative_path="EBRO/11703780002/ZA34683.pdf",
    )
    mismatch_cert = extract_certificate(
        examples_dir / "EBRO" / "unzipped" / "11703780006" / "ZA34620.pdf",
        supplier="EBRO",
        relative_path="EBRO/11703780006/ZA34620.pdf",
    )
    assert ok_cert.grade == "S235JR"
    assert ok_cert.grade_confidence is not None
    assert mismatch_cert.grade == "EN AW-5754"
    assert mismatch_cert.grade_confidence is not None
    print(f"\nZA34683 grade confidence: {ok_cert.grade_confidence}")
    print(f"ZA34620 grade confidence: {mismatch_cert.grade_confidence}")


def test_parse_certificate_type_from_real_ocr_scan(examples_dir):
    # Real EBRO certificate, OCR'd: reads literally "EN 10204 TIPO 2.2".
    text = extract_via_ocr(examples_dir / "EBRO" / "unzipped" / "11703780001" / "ZA34852.pdf")
    assert parse_certificate_type(text) == "2.2"


def test_parse_certificate_type_from_clean_text_layer():
    assert parse_certificate_type("Mill Test Certificate per EN 10204 3.1") == "3.1"


def test_parse_certificate_type_returns_none_when_absent():
    assert parse_certificate_type("no inspection document type mentioned here") is None


def test_extract_certificate_tags_certificate_type(examples_dir):
    cert = extract_certificate(
        examples_dir / "EBRO" / "unzipped" / "11703780001" / "ZA34852.pdf",
        supplier="EBRO",
        relative_path="EBRO/11703780001/ZA34852.pdf",
    )
    assert cert.certificate_type == "2.2"


def test_split_norma_and_grade_separates_standard_from_grade():
    assert split_norma_and_grade("EN 10025-2:2004 S235JR") == ("EN 10025-2:2004", "S235JR")


def test_split_norma_and_grade_returns_none_when_no_grade_recognized():
    assert split_norma_and_grade("some unrecognized material text") == (None, None)
