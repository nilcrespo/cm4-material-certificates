import re
from dataclasses import dataclass, field
from typing import Optional

from .equivalence import EquivalenceTable, MatchKind, classify
from .models import CertificateData, OrphanCorrespondenceEntry

STRATEGY_CORRESPONDENCE = "correspondence"
STRATEGY_DIRECT_REFERENCE = "direct_reference"
STRATEGY_LOT_NUMBER = "lot_number"
STRATEGY_MATERIAL_HEURISTIC = "material_heuristic"

_TRAILING_SUFFIX_RE = re.compile(r"-\d+$")


@dataclass
class MatchOutcome:
    strategy: Optional[str]
    certificate: Optional[CertificateData] = None
    candidates: list[CertificateData] = field(default_factory=list)
    equivalence_kind: Optional[str] = None  # set only for material_heuristic matches
    confirmed_missing: bool = False  # correspondence document authoritatively has no certificate for this reference
    # True only when a correspondence document was supplied and this reference isn't in it at
    # all (not even under a reconciled suffix variant) - distinct from "in the document but no
    # strategy could place a certificate for it", which is a different failure to act on.
    not_in_correspondence: bool = False


def find_orphan_correspondence_entries(
    bom_references: set[str], correspondence_map: dict[str, Optional[str]]
) -> list[OrphanCorrespondenceEntry]:
    """Correspondence document rows whose ClientRef matches no BOM reference in scope at all
    (not even under the same suffix reconciliation `find_correspondence_key` applies) - a real
    case seen in `correspondencia_1170378.xlsx`: `M009-L114 -> ZA33404` has no corresponding
    row in the LASER-scoped BOM. This is a data mismatch worth surfacing on its own, distinct
    from any per-BOM-row verification outcome, since there is no BOM row to attach it to.
    Returns entries in the correspondence document's own order.
    """
    orphans = []
    for client_ref, lote in correspondence_map.items():
        if client_ref in bom_references:
            continue
        if _TRAILING_SUFFIX_RE.sub("", client_ref) in bom_references:
            continue
        orphans.append(OrphanCorrespondenceEntry(reference=client_ref, lote=lote))
    return orphans


def find_correspondence_key(reference: str, correspondence_map: dict[str, Optional[str]]) -> Optional[str]:
    """Find `reference`'s key in a correspondence document, tolerating a real quirk: EBRO's
    document sometimes appends a trailing "-<n>" position suffix to what is otherwise the
    exact BOM reference (confirmed real case: BOM "M009-L206" vs. the correspondence
    document's "M009-L206-1", both the same part - see README's former "known gaps" entry).
    An exact key match is tried first; a suffix-stripped match is the fallback, so this never
    papers over a genuinely different reference.
    """
    if reference in correspondence_map:
        return reference
    for key in correspondence_map:
        if _TRAILING_SUFFIX_RE.sub("", key) == reference:
            return key
    return None


def correspondence_match(
    reference: str, certificates: list[CertificateData], correspondence_map: dict[str, Optional[str]]
) -> MatchOutcome:
    """Match via a correspondence document (ClientRef -> Lote), the real EBRO
    reference-linking mechanism confirmed against CM4's own email data - see
    design.md's Context section.

    Three outcomes:
    - reference absent from the document -> strategy=None (fall through to other strategies)
    - reference present with a Lote, and a certificate's filename stem equals it -> a match
    - reference present with an empty Lote -> confirmed_missing (supplier says no cert exists yet)

    A Lote value present but with no matching certificate filename in this run is treated the
    same as "absent" (fall through) - the supplier says a certificate exists, we just don't have
    it in this batch, which is different from an authoritative "no certificate" confirmation.
    """
    key = find_correspondence_key(reference, correspondence_map)
    if key is None:
        return MatchOutcome(strategy=None)

    lote = correspondence_map[key]
    if lote is None:
        return MatchOutcome(strategy=STRATEGY_CORRESPONDENCE, confirmed_missing=True)

    for cert in certificates:
        stem = cert.path.rsplit("/", 1)[-1]
        if stem.lower().rsplit(".", 1)[0] == lote.strip().lower():
            return MatchOutcome(strategy=STRATEGY_CORRESPONDENCE, certificate=cert)

    return MatchOutcome(strategy=None)


def direct_reference_match(
    reference: str, certificates: list[CertificateData], direct_link_suppliers: set[str]
) -> Optional[CertificateData]:
    """Match via filename/folder structure for suppliers known to tie each CM4
    reference to its own certificate (e.g. EBRO: `EBRO/<reference>/<cert>.pdf`).
    """
    for cert in certificates:
        if cert.supplier not in direct_link_suppliers:
            continue
        parts = cert.path.split("/")
        # Expect SUPPLIER/<reference>/<file>.pdf (already-unzipped structure).
        if len(parts) >= 3 and parts[1] == reference:
            return cert
    return None


def lot_number_match(
    reference: str, certificates: list[CertificateData], lot_map: dict[str, str]
) -> Optional[CertificateData]:
    """Match via a lot/heat number shared between a certificate and a reference,
    as recorded in a supplier's albarà or purchase order (`lot_map`).
    """
    lot = lot_map.get(reference)
    if not lot:
        return None
    for cert in certificates:
        if cert.lot_number and cert.lot_number.strip().upper() == lot.strip().upper():
            return cert
    return None


def material_heuristic_match(
    specified_material: str, certificates: list[CertificateData], table: EquivalenceTable
) -> tuple[list[CertificateData], Optional[str]]:
    """Last-resort matching: compare the BOM's specified material against every
    remaining certificate's extracted material via the equivalence table.

    Returns (candidates, equivalence_kind). `equivalence_kind` is only meaningful
    when exactly one candidate was found - callers must not rely on it for zero
    or many candidates.
    """
    candidates = []
    kind_for_candidate = None
    for cert in certificates:
        if not cert.grade and not cert.standard:
            continue
        extracted = " ".join(filter(None, [cert.standard, cert.grade]))
        result = classify(specified_material, extracted, table)
        if result.kind in (MatchKind.EXACT, MatchKind.SAME_STANDARD, MatchKind.CROSS_STANDARD):
            candidates.append(cert)
            kind_for_candidate = result.kind
    if len(candidates) != 1:
        kind_for_candidate = None
    return candidates, kind_for_candidate


def match_reference(
    reference: str,
    specified_material: str,
    certificates: list[CertificateData],
    *,
    direct_link_suppliers: set[str],
    lot_map: dict[str, str],
    table: EquivalenceTable,
    correspondence_map: Optional[dict[str, Optional[str]]] = None,
) -> MatchOutcome:
    """Try the matching strategies in order of reliability: correspondence document,
    direct reference, lot number, material heuristic.
    """
    not_in_correspondence = False
    if correspondence_map:
        by_correspondence = correspondence_match(reference, certificates, correspondence_map)
        if by_correspondence.confirmed_missing or by_correspondence.certificate:
            return by_correspondence
        not_in_correspondence = find_correspondence_key(reference, correspondence_map) is None

    direct = direct_reference_match(reference, certificates, direct_link_suppliers)
    if direct:
        return MatchOutcome(strategy=STRATEGY_DIRECT_REFERENCE, certificate=direct)

    by_lot = lot_number_match(reference, certificates, lot_map)
    if by_lot:
        return MatchOutcome(strategy=STRATEGY_LOT_NUMBER, certificate=by_lot)

    candidates, kind = material_heuristic_match(specified_material, certificates, table)
    if len(candidates) == 1:
        return MatchOutcome(strategy=STRATEGY_MATERIAL_HEURISTIC, certificate=candidates[0], equivalence_kind=kind)
    if len(candidates) > 1:
        return MatchOutcome(strategy=STRATEGY_MATERIAL_HEURISTIC, candidates=candidates)

    return MatchOutcome(strategy=None, not_in_correspondence=not_in_correspondence)
