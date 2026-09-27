from pathlib import Path
from typing import Optional

from .equivalence import EquivalenceTable, MatchKind, classify, is_reading_trustworthy
from .extraction import split_norma_and_grade
from .i18n import DEFAULT_LANG, translate
from .matching import STRATEGY_MATERIAL_HEURISTIC, match_reference
from .models import BomRow, CertificateData, ExtractionSource, ReviewReason, Status, VerificationRecord
from .store import ConfirmedPairsStore


def _extracted_material_text(cert: CertificateData) -> str:
    return " ".join(filter(None, [cert.standard, cert.grade]))


def _classify_equivalence_kind(
    specified_material: str, cert: CertificateData, table: EquivalenceTable
) -> "tuple[str, Optional[str], Optional[str], Optional[str]]":
    """Returns (kind, canonical, specified_value, extracted_value)."""
    extracted = _extracted_material_text(cert)
    result = classify(specified_material, extracted, table)
    return result.kind, result.canonical, result.specified_value, result.extracted_value


def build_verification_record(
    bom_row: BomRow,
    certificates: list[CertificateData],
    **kwargs,
) -> VerificationRecord:
    """Build one verification record (see `_build_record`) and make sure it carries a
    suggested action - both as a catalog key/params pair and as Catalan text."""
    record = _build_record(bom_row, certificates, **kwargs)
    if record.action_key is None:
        record.action_key, record.action_params = suggested_action_key(record)
    record.suggested_action = translate(DEFAULT_LANG, record.action_key, **record.action_params)
    return record


def _build_record(
    bom_row: BomRow,
    certificates: list[CertificateData],
    *,
    direct_link_suppliers: set[str],
    lot_map: dict[str, str],
    table: EquivalenceTable,
    confirmed_store: ConfirmedPairsStore,
    correspondence_map: Optional[dict[str, Optional[str]]] = None,
) -> VerificationRecord:
    """Combine matching and extraction confidence into one verification outcome.

    Note on `missing_certificate` vs `needs_review`/`no_match_found`: if the whole
    batch has zero certificates at all, there is nothing to look at - that's an
    unambiguous "go get the certificate" case (`missing_certificate`). If the batch
    has certificates but none correspond to this part's material, that's treated
    more conservatively as `needs_review`/`no_match_found` - it's plausible the
    right certificate is present but our extraction/matching simply failed to
    place it, which deserves a human look rather than an "urgent, go chase the
    supplier" action. A correspondence document can also authoritatively confirm
    a reference has no certificate (`confirmed_missing`), which short-circuits
    straight to `missing_certificate` without ever attempting heuristic matching -
    see design.md's "correspondence-document matching" decision.
    """
    specified_norma, specified_grade = split_norma_and_grade(bom_row.material)

    outcome = match_reference(
        bom_row.reference,
        bom_row.material,
        certificates,
        direct_link_suppliers=direct_link_suppliers,
        lot_map=lot_map,
        table=table,
        correspondence_map=correspondence_map,
    )

    if outcome.confirmed_missing:
        return VerificationRecord(
            reference=bom_row.reference,
            specified_material=bom_row.material,
            extracted_material=None,
            certificate_filename=None,
            status=Status.MISSING_CERTIFICATE,
            action_key="action.chase_confirmed_missing",
            action_params={"reference": bom_row.reference},
            specified_norma=specified_norma,
            specified_grade=specified_grade,
        )

    if outcome.strategy is None:
        if not certificates:
            return VerificationRecord(
                reference=bom_row.reference,
                specified_material=bom_row.material,
                extracted_material=None,
                certificate_filename=None,
                status=Status.MISSING_CERTIFICATE,
                specified_norma=specified_norma,
                specified_grade=specified_grade,
            )
        if outcome.not_in_correspondence:
            return VerificationRecord(
                reference=bom_row.reference,
                specified_material=bom_row.material,
                extracted_material=None,
                certificate_filename=None,
                status=Status.NEEDS_REVIEW,
                reason=ReviewReason.NOT_IN_CORRESPONDENCE,
                action_key="action.add_to_correspondence",
                action_params={"reference": bom_row.reference},
                specified_norma=specified_norma,
                specified_grade=specified_grade,
            )
        return VerificationRecord(
            reference=bom_row.reference,
            specified_material=bom_row.material,
            extracted_material=None,
            certificate_filename=None,
            status=Status.NEEDS_REVIEW,
            reason=ReviewReason.NO_MATCH_FOUND,
            specified_norma=specified_norma,
            specified_grade=specified_grade,
        )

    if outcome.candidates:
        return VerificationRecord(
            reference=bom_row.reference,
            specified_material=bom_row.material,
            extracted_material=None,
            certificate_filename=None,
            status=Status.NEEDS_REVIEW,
            reason=ReviewReason.AMBIGUOUS_MATCH,
            candidates=[c.path for c in outcome.candidates],
            match_strategy=outcome.strategy,
            specified_norma=specified_norma,
            specified_grade=specified_grade,
        )

    cert = outcome.certificate
    assert cert is not None  # every remaining outcome branch resolves a single certificate

    if outcome.strategy == STRATEGY_MATERIAL_HEURISTIC:
        kind = outcome.equivalence_kind
        canonical = specified_value = extracted_value = None
        if kind == MatchKind.CROSS_STANDARD:
            _, canonical, specified_value, extracted_value = _classify_equivalence_kind(
                bom_row.material, cert, table
            )
    else:
        kind, canonical, specified_value, extracted_value = _classify_equivalence_kind(
            bom_row.material, cert, table
        )

    if kind in (MatchKind.EXACT, MatchKind.SAME_STANDARD):
        status, reason = Status.OK, None
    elif kind == MatchKind.CROSS_STANDARD:
        if confirmed_store.is_confirmed(canonical, specified_value, extracted_value):
            status, reason = Status.OK, None
        else:
            status, reason = Status.NEEDS_REVIEW, ReviewReason.EQUIVALENCE
    elif kind == MatchKind.MISMATCH:
        status, reason = Status.MISMATCH, None
    else:  # UNKNOWN - a certificate was linked but its material couldn't be placed at all
        status, reason = Status.NEEDS_REVIEW, ReviewReason.NO_MATCH_FOUND

    certificate_type_warning = None
    warning_key = None
    warning_params: dict = {}
    if cert.certificate_type and cert.certificate_type != "3.1":
        warning_key = "warning.cert_type"
        warning_params = {"cert_type": cert.certificate_type}
        certificate_type_warning = translate(DEFAULT_LANG, warning_key, **warning_params)

    record = VerificationRecord(
        reference=bom_row.reference,
        specified_material=bom_row.material,
        extracted_material=_extracted_material_text(cert) or None,
        certificate_filename=cert.path,
        status=status,
        reason=reason,
        source=cert.source,
        equivalence_canonical=canonical if reason == ReviewReason.EQUIVALENCE else None,
        equivalence_specified_value=specified_value if reason == ReviewReason.EQUIVALENCE else None,
        equivalence_extracted_value=extracted_value if reason == ReviewReason.EQUIVALENCE else None,
        specified_norma=specified_norma,
        specified_grade=specified_grade,
        certificate_type=cert.certificate_type,
        certificate_type_warning=certificate_type_warning,
        warning_key=warning_key,
        warning_params=warning_params,
        match_strategy=outcome.strategy,
        grade_confidence=cert.grade_confidence,
    )

    # A non-3.1 certificate takes priority over the generic OCR-trust check when the material
    # itself matched cleanly: a supplier's own 2.2 declaration always needs a human look
    # regardless of how confidently it was read, so the displayed reason should say that,
    # not generically blame OCR (real case: examples/certificats/EBRO/unzipped/11703780001/
    # ZA34852.pdf reads "S235JR" correctly, matching the BOM, but is a 2.2, not 3.1).
    if status == Status.OK and certificate_type_warning:
        record.status = Status.NEEDS_REVIEW
        record.reason = ReviewReason.CERTIFICATE_TYPE
    elif cert.source == ExtractionSource.OCR and not is_reading_trustworthy(cert.grade, cert.grade_confidence, table):
        record.status = Status.NEEDS_REVIEW
        record.reason = ReviewReason.OCR_SOURCE

    return record


def suggested_action_key(record: VerificationRecord) -> tuple[str, dict]:
    """The next step for a record, as a catalog key plus placeholder values (see
    static/i18n.json) - rendered to text by whoever displays it, in the user's language."""
    if record.status == Status.OK:
        cert_path = Path(record.certificate_filename) if record.certificate_filename else Path("CERT.pdf")
        material_slug = ((record.extracted_material or record.specified_material or "").split() or [""])[-1]
        filename = f"{record.reference}_{cert_path.stem}_{material_slug}{cert_path.suffix or '.pdf'}"
        return "action.save_as", {"filename": filename}

    if record.status == Status.MISMATCH:
        return "action.block_mismatch", {}

    if record.status == Status.MISSING_CERTIFICATE:
        return "action.chase_supplier", {"reference": record.reference}

    if record.status == Status.NEEDS_REVIEW:
        if record.reason == ReviewReason.CERTIFICATE_TYPE:
            return "action.cert_type", {"material": record.extracted_material, "cert_type": record.certificate_type}
        if record.reason == ReviewReason.OCR_SOURCE:
            return "action.ocr", {}
        if record.reason == ReviewReason.EQUIVALENCE:
            return "action.equivalence", {
                "specified": record.equivalence_specified_value,
                "extracted": record.equivalence_extracted_value,
            }
        if record.reason == ReviewReason.AMBIGUOUS_MATCH:
            return "action.ambiguous", {"candidates": ", ".join(record.candidates)}
        if record.reason == ReviewReason.NO_MATCH_FOUND:
            return "action.no_match", {}

    return "action.generic", {}


def suggested_action(record: VerificationRecord, lang: str = DEFAULT_LANG) -> str:
    key, params = suggested_action_key(record)
    return translate(lang, key, **params)
