"""Ground-truth validation against real CM4 data (task group 4 of
certificate-correspondence-matching): the real M009 BOM, the real EBRO
certificates, and the correspondence table transcribed from the real
delivery email in CM4_spec_Certificats.pdf.

Two of these outcomes are independently confirmed by CM4 on the
source document, not just inferred by this tool.
"""

import glob
from pathlib import Path

from app.models import ReviewReason, Status
from app.pipeline import run_verification
from app.store import ConfirmedPairsStore


def _load_real_ebro_certs(examples_dir):
    certs = []
    for f in sorted(glob.glob(str(examples_dir / "EBRO" / "unzipped" / "*" / "*.pdf"))):
        p = Path(f)
        rel = f"EBRO/{p.parent.name}/{p.name}"
        certs.append((rel, p.read_bytes()))
    return certs


def test_ground_truth_ebro_correspondence(examples_dir, tmp_path):
    bom_bytes = (examples_dir / "EBRO" / "M009_BOM.xlsx").read_bytes()
    certificate_uploads = _load_real_ebro_certs(examples_dir)
    correspondence_bytes = (examples_dir / "EBRO" / "correspondencia_1170378.xlsx").read_bytes()

    confirmed_store = ConfirmedPairsStore(path=tmp_path / "confirmed_pairs.json")
    (tmp_path / "confirmed_pairs.json").write_text("[]")

    records, _, orphans = run_verification(
        bom_bytes,
        certificate_uploads,
        confirmed_store=confirmed_store,
        correspondence_bytes=correspondence_bytes,
        correspondence_filename="correspondencia_1170378.xlsx",
    )
    by_ref = {r.reference: r for r in records}

    # 4.1: real match, confirmed by inspecting the actual certificate image. The
    # correspondence link correctly and unambiguously identifies the right
    # certificate, and ZA34683's grade ("S235JR") was read at high, genuine OCR
    # confidence (~86.6, well above the trust threshold) with no confusable
    # alternate in the equivalence table - so per the OCR-confidence-trust
    # rule (ocr-confidence-trust change) this now resolves straight to `ok`,
    # not just "linked but still needs a look." Before correspondence matching
    # existed, this same reference resolved to ambiguous_match with no
    # certificate identified at all.
    ok_record = by_ref["I018-L211"]
    assert ok_record.certificate_filename.endswith("ZA34683.pdf")
    assert ok_record.extracted_material == "EN 10025-2 S235JR"
    assert ok_record.status == Status.OK
    assert ok_record.reason is None

    # 4.2: real, CM4-confirmed mismatch (BOM wants EN AW 6063, certificate reads
    # EN AW-5754). Correctly linked to the right certificate, and the extracted
    # material genuinely shows the contradiction - but ZA34620's grade was read
    # from a locally noisy region of a real scan (confidence ~15.6, confirmed by
    # inspecting the raw OCR output: the surrounding text is genuinely garbled,
    # not a parsing artifact), so it correctly stays needs_review/ocr_source
    # rather than an automatic mismatch. This is the trust check working
    # correctly on a per-reading basis: a real answer that happens to be right
    # is still held for confirmation because *this specific* OCR read of it
    # wasn't reliable, which is a more defensible reason than the old blanket
    # "all OCR needs review" rule even though the observable outcome here is
    # the same as before.
    mismatch_record = by_ref["M009-L111"]
    assert mismatch_record.certificate_filename.endswith("ZA34620.pdf")
    assert "5754" in mismatch_record.extracted_material
    assert mismatch_record.status == Status.NEEDS_REVIEW
    assert mismatch_record.reason == ReviewReason.OCR_SOURCE

    # 4.3: correspondence document confirms no certificate for these three.
    for ref in ("M009-L201", "M009-L202", "M009-L204"):
        record = by_ref[ref]
        assert record.status == Status.MISSING_CERTIFICATE, f"{ref} expected missing_certificate, got {record.status}"
        assert record.certificate_filename is None

    # 4.4: a reference not covered by this correspondence document at all - not even under a
    # suffix variant - is flagged distinctly from a generic "no match found": RCM4-L082 (LASER,
    # X5CrNi18-10) genuinely has no row in correspondencia_1170378.xlsx, so this is a document
    # gap to fix at the source, not a matching failure to debug.
    unrelated_record = by_ref["RCM4-L082"]
    assert unrelated_record.status == Status.NEEDS_REVIEW
    assert unrelated_record.reason == ReviewReason.NOT_IN_CORRESPONDENCE

    # 4.5: the BOM's "M009-L206" and the correspondence document's "M009-L206-1" are the same
    # part under a position-suffix quirk in EBRO's own document - reconciled via
    # matching.find_correspondence_key rather than left as an unhandled gap. The document
    # confirms no certificate for it (blank Lote), same as M009-L201/202/204 above.
    reconciled_record = by_ref["M009-L206"]
    assert reconciled_record.status == Status.MISSING_CERTIFICATE
    assert reconciled_record.certificate_filename is None

    # 4.6: the correspondence document also has a row for "M009-L114" (-> ZA33404), which is
    # not a LASER reference in this BOM at all (real data gap, not a matching bug) - flagged
    # as an orphan entry rather than silently ignored or attached to the wrong BOM row.
    orphan_refs = {o.reference: o.lote for o in orphans}
    assert orphan_refs.get("M009-L114") == "ZA33404"
