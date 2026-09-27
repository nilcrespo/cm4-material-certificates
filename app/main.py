import copy
import json
import logging
import subprocess
import tempfile
import uuid
import zipfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .config import DIRECT_LINK_SUPPLIERS
from .bom import bom_summary
from .correspondence import parse_correspondence
from .export import RunMetadata, build_export_workbook, export_filename, save_export
from .history import list_history
from .i18n import DEFAULT_LANG, normalize_lang, translate
from .locate import locate
from .models import CertificateData, OrphanCorrespondenceEntry, ReviewReason, Status, VerificationRecord
from .pipeline import run_verification_stream, summarize
from .preview import media_type_for, render_certificate_preview
from .store import ConfirmedPairsStore
from .verification import suggested_action_key

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="CM4 Material Certificate Verification")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def revalidate_app_files(request, call_next):
    """Make browsers re-check the page, scripts and styles on every load (a cheap 304 when
    unchanged) - otherwise an update can leave a cached old app.js talking to a new server,
    or the other way round."""
    response = await call_next(request)
    if request.url.path == "/" or (request.url.path.startswith("/static/") and not request.url.path.startswith("/static/fonts/")):
        response.headers["Cache-Control"] = "no-cache"
    return response

_confirmed_store = ConfirmedPairsStore()
_runs: dict[str, list[VerificationRecord]] = {}
_run_certificates: dict[str, dict[str, bytes]] = {}
_run_orphans: dict[str, list[OrphanCorrespondenceEntry]] = {}
_run_texts: dict[str, dict[str, str]] = {}
_run_metadata: dict[str, RunMetadata] = {}
_run_saved_as: dict[str, str] = {}  # run id -> filename of its auto-saved export in EXPORTS_DIR
_run_cert_data: dict[str, dict[str, CertificateData]] = {}
# Per run: stack of (reference, record before the decision, equivalence pair promoted by it or None)
_run_undo: dict[str, list[tuple[str, VerificationRecord, Optional[tuple[str, str, str]]]]] = {}
_page_cache: dict[tuple[str, str, int], bytes] = {}
PAGE_DPI = 110


def _autosave(run_id: str, lang: str) -> str | None:
    """Write (or rewrite) the run's export into the history folder. A failed save must not
    break verification or confirmation - the run is still on screen and downloadable - so it
    is logged and reported to the client as `saved_as: null` instead of raised."""
    metadata = _run_metadata[run_id]
    filename = _run_saved_as.get(run_id) or export_filename(metadata, lang)
    try:
        content = build_export_workbook(_runs[run_id], _run_orphans.get(run_id, []), lang=lang, metadata=metadata)
        save_export(content, config.EXPORTS_DIR, filename)
    except OSError:
        logger.exception("Could not save export for run %s to %s", run_id, config.EXPORTS_DIR)
        return None
    _run_saved_as[run_id] = filename
    return filename


@app.get("/api/config")
def get_config():
    return {"exports_dir": str(config.EXPORTS_DIR)}


def _preview_file(upload_name: str, content: bytes, default_suffix: str) -> Path:
    suffix = Path(upload_name or "").suffix.lower() or default_suffix
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    handle.write(content)
    handle.close()
    return Path(handle.name)


def _preview_error(exc: Exception, kind: str) -> HTTPException:
    """422 with the raw message plus a catalog key the UI can show in the user's language."""
    missing_columns = isinstance(exc, ValueError) and "missing expected columns" in str(exc)
    return HTTPException(
        status_code=422,
        detail={"message": str(exc), "key": f"{kind}.missing_columns" if missing_columns else "file.unreadable"},
    )


@app.post("/api/preview/bom")
async def preview_bom(bom: UploadFile = File(...)):
    """Validate a BOM the moment it's picked - before any (slow) certificate OCR runs."""
    path = _preview_file(bom.filename, await bom.read(), ".xlsx")
    try:
        return bom_summary(path)
    except (ValueError, KeyError, OSError, zipfile.BadZipFile) as exc:
        raise _preview_error(exc, "bom")
    finally:
        path.unlink(missing_ok=True)


@app.post("/api/preview/correspondence")
async def preview_correspondence(correspondence: UploadFile = File(...)):
    path = _preview_file(correspondence.filename, await correspondence.read(), ".xlsx")
    try:
        mapping = parse_correspondence(path)
    except (ValueError, KeyError, OSError, zipfile.BadZipFile) as exc:
        raise _preview_error(exc, "corr")
    finally:
        path.unlink(missing_ok=True)
    return {"rows": len(mapping), "without_lote": sum(1 for lote in mapping.values() if lote is None)}


def _record_to_dict(record: VerificationRecord) -> dict:
    d = asdict(record)
    d["status"] = record.status.value
    d["reason"] = record.reason.value if record.reason else None
    d["source"] = record.source.value if record.source else None
    return d


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/verify")
async def verify(
    bom: UploadFile = File(...),
    certificates: list[UploadFile] = File(...),
    correspondence: Optional[UploadFile] = File(None),
    lang: str = Form("ca"),
):
    """Streams newline-delimited JSON: one progress event per real pipeline phase as it
    actually happens (see pipeline.run_verification_stream), ending with a "done" event
    carrying the same run_id/summary/records/config shape this endpoint used to return
    directly - so the client's progress UI reflects real work instead of a fixed animation.
    """
    bom_bytes = await bom.read()
    certificate_uploads = [(cert.filename, await cert.read()) for cert in certificates]

    correspondence_bytes = None
    correspondence_filename = None
    if correspondence is not None and correspondence.filename:
        correspondence_bytes = await correspondence.read()
        correspondence_filename = correspondence.filename

    lang = normalize_lang(lang)

    def generate():
        run_id = str(uuid.uuid4())
        created_at = datetime.now()
        try:
            for event in run_verification_stream(
                bom_bytes,
                certificate_uploads,
                confirmed_store=_confirmed_store,
                correspondence_bytes=correspondence_bytes,
                correspondence_filename=correspondence_filename,
                bom_filename=bom.filename,
            ):
                if event["stage"] == "done":
                    records = event["records"]
                    orphans = event["orphan_correspondence_entries"]
                    raw = event["raw_bytes_by_path"]
                    _runs[run_id] = records
                    _run_certificates[run_id] = raw
                    _run_orphans[run_id] = orphans
                    _run_texts[run_id] = event.get("certificate_texts", {})
                    _run_cert_data[run_id] = {c.path: c for c in event.get("certificates", [])}
                    _run_metadata[run_id] = RunMetadata(
                        run_id=run_id,
                        created_at=created_at,
                        bom_filename=bom.filename,
                        certificate_count=len(raw),
                        suppliers=sorted({path.split("/")[0] for path in raw if "/" in path}),
                        correspondence_filename=correspondence_filename,
                    )
                    payload = {
                        "stage": "done",
                        "run_id": run_id,
                        "summary": summarize(records),
                        "records": [_record_to_dict(r) for r in records],
                        "orphan_correspondence_entries": [asdict(o) for o in orphans],
                        "config": {"direct_link_suppliers": sorted(DIRECT_LINK_SUPPLIERS)},
                        "saved_as": _autosave(run_id, lang),
                        "exports_dir": str(config.EXPORTS_DIR),
                    }
                else:
                    payload = event
                yield json.dumps(payload, ensure_ascii=False) + "\n"
        except ValueError as exc:
            # Bad input (e.g. a BOM without the expected columns): report it on the stream -
            # the 200 status line has already been sent, so an HTTP error is no longer possible.
            yield json.dumps({"stage": "error", "detail": str(exc)}, ensure_ascii=False) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    records = _runs.get(run_id)
    if records is None:
        raise HTTPException(status_code=404, detail="Run not found")
    orphans = _run_orphans.get(run_id, [])
    return {
        "summary": summarize(records),
        "records": [_record_to_dict(r) for r in records],
        "orphan_correspondence_entries": [asdict(o) for o in orphans],
    }


@app.get("/api/runs/{run_id}/export")
def export_run(run_id: str, lang: str = "ca"):
    records = _runs.get(run_id)
    if records is None:
        raise HTTPException(status_code=404, detail="Run not found")
    lang = normalize_lang(lang)
    metadata = _run_metadata.get(run_id) or RunMetadata(run_id=run_id, created_at=datetime.now())
    workbook_bytes = build_export_workbook(records, _run_orphans.get(run_id, []), lang=lang, metadata=metadata)
    filename = export_filename(metadata, lang)
    return Response(
        content=workbook_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@app.get("/api/runs/{run_id}/certificate")
def get_certificate(run_id: str, path: str):
    certs = _run_certificates.get(run_id)
    if certs is None:
        raise HTTPException(status_code=404, detail="Run not found")
    content = certs.get(path)
    if content is None:
        raise HTTPException(status_code=404, detail="Certificate not found in this run")
    headers = {}
    if not path.lower().endswith(".pdf"):
        # Word files can't be shown inline - make the browser download them with their own name.
        headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(Path(path).name)}"
    return Response(content=content, media_type=media_type_for(path), headers=headers)


@app.get("/api/runs/{run_id}/certificate/preview")
def preview_certificate(run_id: str, path: str):
    certs = _run_certificates.get(run_id)
    if certs is None:
        raise HTTPException(status_code=404, detail="Run not found")
    content = certs.get(path)
    if content is None:
        raise HTTPException(status_code=404, detail="Certificate not found in this run")
    cert = _run_cert_data.get(run_id, {}).get(path)
    terms = [t for t in (cert.grade, cert.standard) if t] if cert else []
    page = render_certificate_preview(path, content, _run_texts.get(run_id, {}).get(path, ""), terms)
    # The preview is shown in a sandboxed iframe; the CSP also forbids scripts outright.
    return HTMLResponse(page, headers={"Content-Security-Policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'"})


def _pdf_page_count(content: bytes) -> int:
    with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
        handle.write(content)
        handle.flush()
        result = subprocess.run(["pdfinfo", handle.name], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split()[1])
    return 1


@app.get("/api/runs/{run_id}/certificate/layout")
def certificate_layout(run_id: str, path: str):
    """Page count plus boxes around the extracted grade/standard/type, for the review viewer."""
    content = _run_certificates.get(run_id, {}).get(path)
    if content is None:
        raise HTTPException(status_code=404, detail="Certificate not found in this run")
    if not path.lower().endswith(".pdf"):
        return {"pages": 0, "highlights": []}
    cert = _run_cert_data.get(run_id, {}).get(path)
    highlights = (
        locate(cert.words, grade=cert.grade, standard=cert.standard, certificate_type=cert.certificate_type)
        if cert
        else []
    )
    return {"pages": _pdf_page_count(content), "highlights": highlights}


@app.get("/api/runs/{run_id}/certificate/page")
def certificate_page(run_id: str, path: str, n: int = 1):
    """One PDF page as PNG. The review viewer shows these (with highlight overlays) instead of
    the browser's PDF plugin, whose rendering and coordinates we can't draw on."""
    content = _run_certificates.get(run_id, {}).get(path)
    if content is None or not path.lower().endswith(".pdf"):
        raise HTTPException(status_code=404, detail="Certificate not found in this run")
    key = (run_id, path, n)
    if key not in _page_cache:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "cert.pdf"
            src.write_bytes(content)
            subprocess.run(
                ["pdftoppm", "-png", "-r", str(PAGE_DPI), "-f", str(n), "-l", str(n), "-singlefile", str(src), str(Path(tmp) / "page")],
                capture_output=True,
            )
            out = Path(tmp) / "page.png"
            if not out.exists():
                raise HTTPException(status_code=404, detail="Page not found")
            _page_cache[key] = out.read_bytes()
    return Response(content=_page_cache[key], media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/history")
def history():
    return list_history(config.EXPORTS_DIR)


class ConfirmRequest(BaseModel):
    reference: str
    resolution: str  # "ok" or "mismatch"
    lang: str = "ca"


@app.post("/api/runs/{run_id}/confirm")
def confirm(run_id: str, body: ConfirmRequest):
    records = _runs.get(run_id)
    if records is None:
        raise HTTPException(status_code=404, detail="Run not found")

    record = next((r for r in records if r.reference == body.reference), None)
    if record is None:
        raise HTTPException(status_code=404, detail="Reference not found in this run")
    if record.certificate_filename is None:
        raise HTTPException(status_code=400, detail="No certificate linked to this reference to confirm against")
    if body.resolution not in ("ok", "mismatch"):
        raise HTTPException(status_code=400, detail="resolution must be 'ok' or 'mismatch'")

    before = copy.deepcopy(record)
    promoted = None
    if (
        body.resolution == "ok"
        and record.reason == ReviewReason.EQUIVALENCE
        and record.equivalence_canonical
    ):
        pair = (record.equivalence_canonical, record.equivalence_specified_value, record.equivalence_extracted_value)
        if not _confirmed_store.is_confirmed(*pair):
            promoted = pair
        _confirmed_store.confirm(*pair)
    _run_undo.setdefault(run_id, []).append((record.reference, before, promoted))

    record.status = Status.OK if body.resolution == "ok" else Status.MISMATCH
    record.reason = None
    record.human_confirmed = True

    record.action_key, record.action_params = suggested_action_key(record)
    record.suggested_action = translate(DEFAULT_LANG, record.action_key, **record.action_params)
    saved_as = _autosave(run_id, normalize_lang(body.lang)) if run_id in _run_metadata else None

    return {"summary": summarize(records), "record": _record_to_dict(record), "saved_as": saved_as}


class UndoRequest(BaseModel):
    reference: str
    lang: str = "ca"


@app.post("/api/runs/{run_id}/undo")
def undo(run_id: str, body: UndoRequest):
    """Revert the most recent decision on `reference` in this run (the review UI's undo toast),
    including an equivalence pair that decision promoted, and re-save the export."""
    records = _runs.get(run_id)
    if records is None:
        raise HTTPException(status_code=404, detail="Run not found")
    stack = _run_undo.get(run_id, [])
    index = next((i for i in range(len(stack) - 1, -1, -1) if stack[i][0] == body.reference), None)
    if index is None:
        raise HTTPException(status_code=400, detail="Nothing to undo for this reference")
    _, before, promoted = stack.pop(index)
    position = next(i for i, r in enumerate(records) if r.reference == body.reference)
    records[position] = before
    if promoted:
        _confirmed_store.unconfirm(*promoted)
    saved_as = _autosave(run_id, normalize_lang(body.lang)) if run_id in _run_metadata else None
    return {"summary": summarize(records), "record": _record_to_dict(before), "saved_as": saved_as}
