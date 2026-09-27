"""Full-pipeline validation against the real example certificates (task 7.1).

Runs the actual extraction (real pdftotext/OCR subprocess calls, no mocking)
against real files from all three example suppliers, combined with a small
synthetic BOM built to exercise each supplier's realistic matching path:
EBRO via its real direct-reference folder structure, RIERA via a clean
text-layer exact match, IAMCUT via material-heuristic matching (which this
equivalence table's current coverage doesn't resolve, so it lands on
needs_review - itself a correct, safe outcome, not a failure).
"""

import openpyxl

from app.pipeline import run_verification, run_verification_stream
from app.models import Status, ExtractionSource
from app.store import ConfirmedPairsStore


def _make_bom_bytes(tmp_path, rows):
    path = tmp_path / "bom.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "BOM"
    ws.append(["Elemento", "Nº de pieza", "Descripción", "CTDAD", "Categoría", "Empresa", "Referencia de almacén", "Material"])
    for ref, material in rows:
        ws.append(["1", ref, "desc", 1, "LASER", "xx", "", material])
    wb.save(path)
    return path.read_bytes()


def test_full_pipeline_against_all_three_example_suppliers(examples_dir, tmp_path):
    bom_bytes = _make_bom_bytes(
        tmp_path,
        [
            # EBRO: matches the real direct-reference folder structure - see
            # examples/certificats/EBRO/unzipped/11703780004/ZA34771.pdf
            ("11703780004", "SOME MATERIAL NOT IN THE TABLE"),
            # RIERA: exact grade match against the real certificate's text layer.
            ("RIERA-PART", "S355MC"),
            # IAMCUT: real OCR runs, but this table doesn't cover its aluminum grade yet.
            ("IAMCUT-PART", "AL 5052-T51"),
        ],
    )

    certificate_uploads = [
        ("EBRO/11703780004/ZA34771.pdf", (examples_dir / "EBRO" / "unzipped" / "11703780004" / "ZA34771.pdf").read_bytes()),
        ("RIERA/S355.pdf", (examples_dir / "RIERA" / "S355-DEC 3X15X4 13-04-2023 GUTSER.pdf").read_bytes()),
        ("IAMCUT/CH51704.pdf", (examples_dir / "IAMCUT" / "CH51704.pdf").read_bytes()),
    ]

    confirmed_store = ConfirmedPairsStore(path=tmp_path / "confirmed_pairs.json")
    (tmp_path / "confirmed_pairs.json").write_text("[]")

    records, _, _ = run_verification(bom_bytes, certificate_uploads, confirmed_store=confirmed_store)
    by_ref = {r.reference: r for r in records}

    assert len(records) == 3
    assert all(r.status in Status for r in records)  # every part produced one of the four valid statuses

    # EBRO: real certificate is scanned -> OCR path -> forced needs_review/ocr_source,
    # regardless of the (irrelevant, deliberately mismatched) BOM material.
    ebro_record = by_ref["11703780004"]
    assert ebro_record.certificate_filename == "EBRO/11703780004/ZA34771.pdf"
    assert ebro_record.source == ExtractionSource.OCR
    assert ebro_record.status == Status.NEEDS_REVIEW
    assert ebro_record.reason.value == "ocr_source"

    # RIERA: clean text-layer certificate, exact grade match -> ok.
    riera_record = by_ref["RIERA-PART"]
    assert riera_record.status == Status.OK
    assert riera_record.certificate_filename == "RIERA/S355.pdf"

    # IAMCUT: real OCR runs against a real scanned certificate; not silently
    # marked ok even though it could plausibly be the right material - it's
    # correctly held for review rather than guessed.
    iamcut_record = by_ref["IAMCUT-PART"]
    assert iamcut_record.status == Status.NEEDS_REVIEW


def test_streaming_pipeline_reports_real_progress_and_matches_non_streaming_result(examples_dir, tmp_path):
    bom_bytes = _make_bom_bytes(tmp_path, [("11703780004", "SOME MATERIAL NOT IN THE TABLE")])
    certificate_uploads = [
        ("EBRO/11703780004/ZA34771.pdf", (examples_dir / "EBRO" / "unzipped" / "11703780004" / "ZA34771.pdf").read_bytes()),
    ]
    confirmed_store = ConfirmedPairsStore(path=tmp_path / "confirmed_pairs.json")
    (tmp_path / "confirmed_pairs.json").write_text("[]")

    events = list(
        run_verification_stream(bom_bytes, certificate_uploads, confirmed_store=confirmed_store)
    )

    stages = [e["stage"] for e in events]
    # Real phases in order, each reached at least once - not a fixed-count animation.
    assert stages[0] == "read"
    assert "verify" in stages
    assert "prepare" in stages
    assert stages[-1] == "done"

    read_details = [e["detail"] for e in events if e["stage"] == "read" and e["detail"]]
    assert read_details == ["1/1 certificats"]  # one real certificate, one real progress tick

    verify_details = [e["detail"] for e in events if e["stage"] == "verify" and e["detail"]]
    assert verify_details == ["1/1 referències"]  # one BOM row, one real progress tick

    done_event = events[-1]
    non_streaming_records, _, _ = run_verification(bom_bytes, certificate_uploads, confirmed_store=confirmed_store)
    assert [r.status for r in done_event["records"]] == [r.status for r in non_streaming_records]
    assert done_event["orphan_correspondence_entries"] == []
