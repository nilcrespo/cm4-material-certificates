import io
import json
import zipfile

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _zip_bytes(*file_paths):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path in file_paths:
            zf.write(path, arcname=path.name)
    return buf.getvalue()


def test_upload_ebro_bom_and_certificates_returns_verification_table(examples_dir, verify_result):
    bom_path = examples_dir / "EBRO" / "M009_BOM.xlsx"

    # Reproduce the real EBRO delivery shape: EBRO/<reference>.zip containing one cert PDF.
    cert_pdf = examples_dir / "EBRO" / "unzipped" / "11703780004" / "ZA34771.pdf"
    cert_zip_bytes = _zip_bytes(cert_pdf)

    response = client.post(
        "/api/verify",
        files={
            "bom": ("M009_BOM.xlsx", bom_path.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "certificates": ("EBRO/11703780004.zip", cert_zip_bytes, "application/zip"),
        },
    )

    assert response.status_code == 200
    body = verify_result(response)
    assert "run_id" in body
    assert "summary" in body
    assert isinstance(body["records"], list)
    assert len(body["records"]) > 0
    assert set(body["summary"].keys()) == {"ok", "needs_review", "mismatch", "missing_certificate"}


def test_verify_streams_read_progress_events_before_the_final_result(examples_dir):
    bom_path = examples_dir / "EBRO" / "M009_BOM.xlsx"
    cert_pdf = examples_dir / "EBRO" / "unzipped" / "11703780004" / "ZA34771.pdf"

    response = client.post(
        "/api/verify",
        files={
            "bom": ("M009_BOM.xlsx", bom_path.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "certificates": ("EBRO/11703780004.zip", _zip_bytes(cert_pdf), "application/zip"),
        },
    )

    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    stages = [e["stage"] for e in events]
    assert stages[0] == "read"
    assert "verify" in stages
    assert "prepare" in stages
    assert stages[-1] == "done"
    # Real per-file progress, not a fixed-count animation: exactly one certificate uploaded.
    assert any(e.get("detail") == "1/1 certificats" for e in events)


def test_get_run_returns_stored_results(examples_dir, verify_result):
    bom_path = examples_dir / "EBRO" / "M009_BOM.xlsx"
    cert_pdf = examples_dir / "EBRO" / "unzipped" / "11703780004" / "ZA34771.pdf"

    response = client.post(
        "/api/verify",
        files={
            "bom": ("M009_BOM.xlsx", bom_path.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "certificates": ("EBRO/11703780004.zip", _zip_bytes(cert_pdf), "application/zip"),
        },
    )
    body = verify_result(response)
    run_id = body["run_id"]

    run_response = client.get(f"/api/runs/{run_id}")
    assert run_response.status_code == 200
    assert run_response.json()["records"] == body["records"]


def test_unknown_run_returns_404():
    response = client.get("/api/runs/does-not-exist")
    assert response.status_code == 404
