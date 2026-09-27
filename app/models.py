from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ExtractionSource(str, Enum):
    TEXT_LAYER = "text-layer"
    OCR = "ocr"


class Status(str, Enum):
    OK = "ok"
    NEEDS_REVIEW = "needs_review"
    MISMATCH = "mismatch"
    MISSING_CERTIFICATE = "missing_certificate"


class ReviewReason(str, Enum):
    OCR_SOURCE = "ocr_source"
    EQUIVALENCE = "equivalence"
    AMBIGUOUS_MATCH = "ambiguous_match"
    NO_MATCH_FOUND = "no_match_found"
    NOT_IN_CORRESPONDENCE = "not_in_correspondence"
    CERTIFICATE_TYPE = "certificate_type"


@dataclass
class BomRow:
    reference: str
    material: str


@dataclass
class OrphanCorrespondenceEntry:
    """A correspondence document row whose ClientRef matches no BOM reference in scope -
    see matching.find_orphan_correspondence_entries."""

    reference: str
    lote: Optional[str]


@dataclass
class CertificateData:
    """Extracted content of one certificate file."""

    path: str  # relative path as uploaded, e.g. "EBRO/11703780002/ZA34683.pdf"
    supplier: str
    text: str
    source: ExtractionSource
    standard: Optional[str] = None
    grade: Optional[str] = None
    lot_number: Optional[str] = None
    grade_confidence: Optional[float] = None  # 0-100, OCR-sourced only; see equivalence.is_reading_trustworthy
    certificate_type: Optional[str] = None  # EN 10204 inspection-document type, e.g. "3.1", "2.2"
    # Every word with its position, for highlighting extracted values on the page in the review
    # UI: (text, page index from 0, x0, y0, x1, y1) with coordinates as 0-1 fractions of the page.
    # Empty for Word documents (their preview marks terms in HTML instead).
    words: list = field(default_factory=list)


@dataclass
class MatchResult:
    """Result of trying to link a BOM row to a certificate."""

    certificate: Optional[CertificateData]
    candidates: list = field(default_factory=list)  # other plausible CertificateData, if ambiguous
    strategy: Optional[str] = None  # "direct_reference" | "lot_number" | "material_heuristic" | None


@dataclass
class VerificationRecord:
    reference: str
    specified_material: str
    extracted_material: Optional[str]
    certificate_filename: Optional[str]
    status: Status
    reason: Optional[ReviewReason] = None
    candidates: list = field(default_factory=list)
    suggested_action: str = ""
    source: Optional[ExtractionSource] = None
    human_confirmed: bool = False
    # Populated only when `reason == EQUIVALENCE`, so a later confirmation can
    # promote exactly this pair via the ConfirmedPairsStore.
    equivalence_canonical: Optional[str] = None
    equivalence_specified_value: Optional[str] = None
    equivalence_extracted_value: Optional[str] = None
    # Display-only split of `specified_material` into norma + grade (e.g.
    # "EN 10025-2:2004" / "S235JR") - see extraction.split_norma_and_grade. Comparison
    # logic never uses these; it goes through equivalence.classify() instead.
    specified_norma: Optional[str] = None
    specified_grade: Optional[str] = None
    # EN 10204 inspection-document type of the linked certificate, and a non-blocking
    # warning when it isn't "3.1" (e.g. "2.2"). A missing/undetected type is not a
    # warning condition on its own.
    certificate_type: Optional[str] = None
    certificate_type_warning: Optional[str] = None
    # Material the supplier committed to on the albarán (delivery note), when one states
    # it. No example document seen so far states this, so there is no producer for this
    # field yet - see README's "Known gaps". Wired through the model/export so it can be
    # populated the moment a real source is added, without another schema change.
    committed_material: Optional[str] = None
    # Display-only context for the review UI: how the certificate was linked (see
    # matching.STRATEGY_*) and, for OCR readings, the grade's OCR confidence (0-100).
    match_strategy: Optional[str] = None
    grade_confidence: Optional[float] = None
    # Language-independent form of `suggested_action` / `certificate_type_warning`: a key into
    # the shared catalog (static/i18n.json) plus its placeholder values, so the UI and export
    # can render either in Catalan or Spanish. The plain-text fields above stay filled (in
    # Catalan) for any consumer that doesn't know about the catalog.
    action_key: Optional[str] = None
    action_params: dict = field(default_factory=dict)
    warning_key: Optional[str] = None
    warning_params: dict = field(default_factory=dict)
