import io
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator

from .bom import parse_bom
from .config import DIRECT_LINK_SUPPLIERS
from .correspondence import parse_correspondence
from .equivalence import EquivalenceTable
from .extraction import CERTIFICATE_EXTENSIONS, extract_certificate
from .matching import find_orphan_correspondence_entries
from .models import BomRow, CertificateData, OrphanCorrespondenceEntry, VerificationRecord
from .store import ConfirmedPairsStore
from .verification import build_verification_record

_TABLE = EquivalenceTable.load()


def _supplier_from_path(relative_path: str) -> str:
    return relative_path.split("/")[0]


def is_certificate_path(relative_path: str) -> bool:
    name = Path(relative_path).name
    # "~$cert.docx" is Word's lock file for an open document, not a certificate.
    return relative_path.lower().endswith(CERTIFICATE_EXTENSIONS) and not name.startswith("~$")


def _bom_temp_name(bom_filename: str | None) -> str:
    return "bom.csv" if (bom_filename or "").lower().endswith(".csv") else "bom.xlsx"


def _expand_certificate_uploads(uploads: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    """Expand any uploaded .zip into its member files, prefixed by the zip's own
    upload path (minus the .zip extension) so the resulting paths look exactly
    like an already-unzipped delivery, e.g. "EBRO/11703780001.zip" containing
    "ZA34852.pdf" becomes "EBRO/11703780001/ZA34852.pdf" - matching how CM4's
    real EBRO deliveries are structured.
    """
    expanded: list[tuple[str, bytes]] = []
    for relative_path, content in uploads:
        if relative_path.lower().endswith(".zip"):
            prefix = relative_path[: -len(".zip")]
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    inner_bytes = zf.read(info.filename)
                    expanded.append((f"{prefix}/{info.filename}", inner_bytes))
        else:
            expanded.append((relative_path, content))
    return expanded


def extract_all_certificates(
    uploads: list[tuple[str, bytes]], tmpdir: Path
) -> tuple[list[CertificateData], dict[str, bytes]]:
    certificates = []
    raw_bytes_by_path: dict[str, bytes] = {}
    for index, (relative_path, content) in enumerate(_expand_certificate_uploads(uploads)):
        if not is_certificate_path(relative_path):
            continue
        local_path = tmpdir / f"{index}_{Path(relative_path).name}"
        local_path.write_bytes(content)
        supplier = _supplier_from_path(relative_path)
        certificates.append(extract_certificate(local_path, supplier=supplier, relative_path=relative_path))
        raw_bytes_by_path[relative_path] = content
    return certificates, raw_bytes_by_path


def _extract_all_certificates_stream(
    uploads: list[tuple[str, bytes]], tmpdir: Path
) -> Iterator[dict]:
    """Same extraction as `extract_all_certificates`, but yields a progress event after each
    certificate instead of returning silently - the slow, OCR-bound step, so this is where
    real per-file progress actually matters. The final yielded event carries the same
    (certificates, raw_bytes_by_path) result under "result", since a generator can't also
    `return` a value to a plain `for` loop.
    """
    expanded = _expand_certificate_uploads(uploads)
    total = sum(1 for relative_path, _ in expanded if is_certificate_path(relative_path))

    certificates = []
    raw_bytes_by_path: dict[str, bytes] = {}
    done = 0
    for index, (relative_path, content) in enumerate(expanded):
        if not is_certificate_path(relative_path):
            continue
        local_path = tmpdir / f"{index}_{Path(relative_path).name}"
        local_path.write_bytes(content)
        supplier = _supplier_from_path(relative_path)
        certificates.append(extract_certificate(local_path, supplier=supplier, relative_path=relative_path))
        raw_bytes_by_path[relative_path] = content
        done += 1
        yield {"stage": "read", "detail": f"{done}/{total} certificats", "done": done, "total": total}

    yield {"stage": "read", "detail": None, "result": (certificates, raw_bytes_by_path)}


def run_verification(
    bom_bytes: bytes,
    certificate_uploads: list[tuple[str, bytes]],
    *,
    confirmed_store: ConfirmedPairsStore | None = None,
    lot_map: dict[str, str] | None = None,
    correspondence_bytes: bytes | None = None,
    correspondence_filename: str | None = None,
    bom_filename: str | None = None,
) -> tuple[list[VerificationRecord], dict[str, bytes], list[OrphanCorrespondenceEntry]]:
    """Run the full pipeline: parse the BOM, extract every certificate, and
    produce one verification record per BOM row. Also returns the raw bytes of
    every certificate (by relative path) so the caller can serve them back for
    the review UI after the extraction working directory is cleaned up, and any
    correspondence document rows that match no BOM reference in scope at all
    (see matching.find_orphan_correspondence_entries) - a data mismatch worth
    surfacing on its own, since there is no BOM row to attach it to.

    `correspondence_bytes`/`correspondence_filename` are optional - a run without
    a correspondence document behaves exactly as before (direct/lot/heuristic
    matching only).
    """
    confirmed_store = confirmed_store or ConfirmedPairsStore()
    lot_map = lot_map or {}

    with TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        bom_path = tmpdir / _bom_temp_name(bom_filename)
        bom_path.write_bytes(bom_bytes)
        bom_rows: list[BomRow] = parse_bom(bom_path)

        certificates, raw_bytes_by_path = extract_all_certificates(certificate_uploads, tmpdir)

        correspondence_map = None
        if correspondence_bytes is not None:
            suffix = Path(correspondence_filename or "correspondence.xlsx").suffix or ".xlsx"
            correspondence_path = tmpdir / f"correspondence{suffix}"
            correspondence_path.write_bytes(correspondence_bytes)
            correspondence_map = parse_correspondence(correspondence_path)

    records = [
        build_verification_record(
            row,
            certificates,
            direct_link_suppliers=DIRECT_LINK_SUPPLIERS,
            lot_map=lot_map,
            table=_TABLE,
            confirmed_store=confirmed_store,
            correspondence_map=correspondence_map,
        )
        for row in bom_rows
    ]
    orphans = (
        find_orphan_correspondence_entries({row.reference for row in bom_rows}, correspondence_map)
        if correspondence_map
        else []
    )
    return records, raw_bytes_by_path, orphans


def run_verification_stream(
    bom_bytes: bytes,
    certificate_uploads: list[tuple[str, bytes]],
    *,
    confirmed_store: ConfirmedPairsStore | None = None,
    lot_map: dict[str, str] | None = None,
    correspondence_bytes: bytes | None = None,
    correspondence_filename: str | None = None,
    bom_filename: str | None = None,
) -> Iterator[dict]:
    """Like `run_verification`, but yields a progress event as each phase of real work
    actually happens, instead of the caller only getting a result once everything is done.
    Kept as a separate generator (rather than adding a callback param to `run_verification`)
    so the plain call/return pipeline used by tests and scripts is untouched.

    Stages, in order: "read" (parsing the BOM/correspondence and extracting every
    certificate - the slow, OCR-bound step, with a "done/total certificates" detail),
    "verify" (matching + classifying each BOM row, with a "done/total referències" detail),
    "prepare" (assembling the final response), then a final "done" event carrying the
    records and raw certificate bytes, matching `run_verification`'s return shape.
    """
    confirmed_store = confirmed_store or ConfirmedPairsStore()
    lot_map = lot_map or {}

    yield {"stage": "read", "detail": None}

    with TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        bom_path = tmpdir / _bom_temp_name(bom_filename)
        bom_path.write_bytes(bom_bytes)
        bom_rows: list[BomRow] = parse_bom(bom_path)

        certificates: list[CertificateData] = []
        raw_bytes_by_path: dict[str, bytes] = {}
        for event in _extract_all_certificates_stream(certificate_uploads, tmpdir):
            if "result" in event:
                certificates, raw_bytes_by_path = event["result"]
            else:
                yield event

        correspondence_map = None
        if correspondence_bytes is not None:
            suffix = Path(correspondence_filename or "correspondence.xlsx").suffix or ".xlsx"
            correspondence_path = tmpdir / f"correspondence{suffix}"
            correspondence_path.write_bytes(correspondence_bytes)
            correspondence_map = parse_correspondence(correspondence_path)

    yield {"stage": "verify", "detail": None}
    records: list[VerificationRecord] = []
    for index, row in enumerate(bom_rows):
        records.append(
            build_verification_record(
                row,
                certificates,
                direct_link_suppliers=DIRECT_LINK_SUPPLIERS,
                lot_map=lot_map,
                table=_TABLE,
                confirmed_store=confirmed_store,
                correspondence_map=correspondence_map,
            )
        )
        yield {"stage": "verify", "detail": f"{index + 1}/{len(bom_rows)} referències", "done": index + 1, "total": len(bom_rows)}

    yield {"stage": "prepare", "detail": None}
    orphans = (
        find_orphan_correspondence_entries({row.reference for row in bom_rows}, correspondence_map)
        if correspondence_map
        else []
    )
    yield {
        "stage": "done",
        "records": records,
        "raw_bytes_by_path": raw_bytes_by_path,
        "certificate_texts": {c.path: c.text for c in certificates},
        "orphan_correspondence_entries": orphans,
    }


def summarize(records: list[VerificationRecord]) -> dict[str, int]:
    counts = {"ok": 0, "needs_review": 0, "mismatch": 0, "missing_certificate": 0}
    for record in records:
        counts[record.status.value] += 1
    return counts
