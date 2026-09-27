import json

import openpyxl
import pytest
from fastapi.testclient import TestClient

import app.pipeline as pipeline
from app.main import app, _confirmed_store
from app.models import CertificateData, ExtractionSource

client = TestClient(app)


def _make_bom(path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "BOM"
    ws.append(["Elemento", "Nº de pieza", "Descripción", "CTDAD", "Categoría", "Empresa", "Referencia de almacén", "Material"])
    for ref, material in rows:
        ws.append(["1", ref, "desc", 1, "LASER", "xx", "", material])
    wb.save(path)


@pytest.fixture
def isolated_confirmed_store(tmp_path):
    """Prevent this test from mutating the app's real confirmed_pairs.json."""
    original_path = _confirmed_store.path
    test_path = tmp_path / "confirmed_pairs.json"
    test_path.write_text("[]")
    _confirmed_store.path = test_path
    _confirmed_store._load()
    yield
    _confirmed_store.path = original_path
    _confirmed_store._load()


def test_confirming_equivalence_row_promotes_pair_for_future_runs(tmp_path, monkeypatch, isolated_confirmed_store, verify_result):
    bom_path = tmp_path / "bom.xlsx"
    _make_bom(bom_path, [("REF-EQUIV", "EN 10088-1:1995 X5CrNi18-10")])

    fake_cert = CertificateData(
        path="SUPPLIER/cert.pdf",
        supplier="SUPPLIER",
        text="",
        source=ExtractionSource.TEXT_LAYER,
        grade="AISI 304",
    )
    monkeypatch.setattr(pipeline, "extract_certificate", lambda *a, **k: fake_cert)

    response = client.post(
        "/api/verify",
        files={
            "bom": ("bom.xlsx", bom_path.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "certificates": ("SUPPLIER/cert.pdf", b"%PDF-1.4 fake", "application/pdf"),
        },
    )
    body = verify_result(response)
    record = body["records"][0]
    assert record["status"] == "needs_review"
    assert record["reason"] == "equivalence"

    confirm_response = client.post(
        f"/api/runs/{body['run_id']}/confirm",
        json={"reference": "REF-EQUIV", "resolution": "ok"},
    )
    assert confirm_response.status_code == 200
    assert confirm_response.json()["record"]["status"] == "ok"

    # A brand-new run hitting the same equivalence pair should now auto-resolve to ok.
    response2 = client.post(
        "/api/verify",
        files={
            "bom": ("bom.xlsx", bom_path.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "certificates": ("SUPPLIER/cert.pdf", b"%PDF-1.4 fake", "application/pdf"),
        },
    )
    record2 = verify_result(response2)["records"][0]
    assert record2["status"] == "ok"
    assert record2["reason"] is None


def test_confirming_an_ok_row_can_still_flip_its_decision(tmp_path, monkeypatch, isolated_confirmed_store, verify_result):
    # A user who visually reviewed the certificate can override an already-resolved row too -
    # not just needs_review ones - as long as a certificate is actually linked to look at.
    bom_path = tmp_path / "bom.xlsx"
    _make_bom(bom_path, [("REF-OK", "S355MC")])

    fake_cert = CertificateData(
        path="SUPPLIER/cert.pdf", supplier="SUPPLIER", text="", source=ExtractionSource.TEXT_LAYER, grade="S355MC"
    )
    monkeypatch.setattr(pipeline, "extract_certificate", lambda *a, **k: fake_cert)

    response = client.post(
        "/api/verify",
        files={
            "bom": ("bom.xlsx", bom_path.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "certificates": ("SUPPLIER/cert.pdf", b"%PDF-1.4 fake", "application/pdf"),
        },
    )
    body = verify_result(response)
    assert body["records"][0]["status"] == "ok"

    confirm_response = client.post(
        f"/api/runs/{body['run_id']}/confirm",
        json={"reference": "REF-OK", "resolution": "mismatch"},
    )
    assert confirm_response.status_code == 200
    record = confirm_response.json()["record"]
    assert record["status"] == "mismatch"
    assert record["human_confirmed"] is True


def test_confirming_a_row_with_no_linked_certificate_is_rejected(tmp_path, monkeypatch, isolated_confirmed_store, verify_result):
    bom_path = tmp_path / "bom.xlsx"
    _make_bom(bom_path, [("REF-MISSING", "S355MC")])

    # An unrelated certificate, so this reference resolves with no certificate linked -
    # nothing to confirm/reject a decision against.
    fake_cert = CertificateData(
        path="SUPPLIER/unrelated.pdf", supplier="SUPPLIER", text="", source=ExtractionSource.TEXT_LAYER, grade="AISI 304"
    )
    monkeypatch.setattr(pipeline, "extract_certificate", lambda *a, **k: fake_cert)

    response = client.post(
        "/api/verify",
        files={
            "bom": ("bom.xlsx", bom_path.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "certificates": ("SUPPLIER/unrelated.pdf", b"%PDF-1.4 fake", "application/pdf"),
        },
    )
    body = verify_result(response)
    assert body["records"][0]["certificate_filename"] is None

    confirm_response = client.post(
        f"/api/runs/{body['run_id']}/confirm",
        json={"reference": "REF-MISSING", "resolution": "ok"},
    )
    assert confirm_response.status_code == 400


def test_run_is_autosaved_and_resaved_after_confirmation(tmp_path, monkeypatch, isolated_confirmed_store, verify_result, exports_dir):
    bom_path = tmp_path / "bom.xlsx"
    _make_bom(bom_path, [("REF-EQUIV", "EN 10088-1:1995 X5CrNi18-10")])
    fake_cert = CertificateData(
        path="SUPPLIER/cert.pdf", supplier="SUPPLIER", text="", source=ExtractionSource.TEXT_LAYER, grade="AISI 304"
    )
    monkeypatch.setattr(pipeline, "extract_certificate", lambda *a, **k: fake_cert)

    response = client.post(
        "/api/verify",
        data={"lang": "es"},
        files={
            "bom": ("bom.xlsx", bom_path.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "certificates": ("SUPPLIER/cert.pdf", b"%PDF-1.4 fake", "application/pdf"),
        },
    )
    body = verify_result(response)
    assert body["saved_as"].startswith("verificacion_")
    history = client.get("/api/history").json()
    assert history["folder"] == str(exports_dir)
    assert [r["summary"]["needs_review"] for r in history["runs"]] == [1]

    client.post(f"/api/runs/{body['run_id']}/confirm", json={"reference": "REF-EQUIV", "resolution": "ok", "lang": "es"})

    runs = client.get("/api/history").json()["runs"]
    assert len(runs) == 1  # same file rewritten, not a second one
    assert runs[0]["summary"]["ok"] == 1
    assert runs[0]["records"][0]["human_confirmed"] is True


def test_bad_bom_is_reported_on_the_stream(tmp_path):
    response = client.post(
        "/api/verify",
        files={
            "bom": ("bom.csv", "Ref;Mat\nA;B\n".encode(), "text/csv"),
            "certificates": ("SUPPLIER/cert.pdf", b"%PDF-1.4 fake", "application/pdf"),
        },
    )
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert events[-1]["stage"] == "error"
    assert "Nº de pieza" in events[-1]["detail"]


def test_bom_and_correspondence_previews(tmp_path):
    bom_path = tmp_path / "bom.xlsx"
    _make_bom(bom_path, [("A-1", "S235JR"), ("A-2", "S355JR")])
    ok = client.post("/api/preview/bom", files={"bom": ("bom.xlsx", bom_path.read_bytes())})
    assert ok.status_code == 200 and ok.json()["parts"] == 2

    bad = client.post("/api/preview/bom", files={"bom": ("bom.csv", "Ref;Mat\nA;B\n".encode())})
    assert bad.status_code == 422
    assert bad.json()["detail"]["key"] == "bom.missing_columns"
    assert "Nº de pieza" in bad.json()["detail"]["message"]

    corr = client.post(
        "/api/preview/correspondence",
        files={"correspondence": ("c.csv", "ClientRef;Lote\nA-1;ZA1\nA-2;\n".encode())},
    )
    assert corr.json() == {"rows": 2, "without_lote": 1}


def test_undo_restores_previous_state_and_unpromotes_pair(tmp_path, monkeypatch, isolated_confirmed_store, verify_result):
    bom_path = tmp_path / "bom.xlsx"
    _make_bom(bom_path, [("REF-EQUIV", "EN 10088-1:1995 X5CrNi18-10")])
    fake_cert = CertificateData(
        path="SUPPLIER/cert.pdf", supplier="SUPPLIER", text="", source=ExtractionSource.TEXT_LAYER, grade="AISI 304"
    )
    monkeypatch.setattr(pipeline, "extract_certificate", lambda *a, **k: fake_cert)
    body = verify_result(client.post(
        "/api/verify",
        files={
            "bom": ("bom.xlsx", bom_path.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "certificates": ("SUPPLIER/cert.pdf", b"%PDF-1.4 fake", "application/pdf"),
        },
    ))
    run_id = body["run_id"]
    client.post(f"/api/runs/{run_id}/confirm", json={"reference": "REF-EQUIV", "resolution": "ok"})
    assert len(_confirmed_store._confirmed) == 1

    undone = client.post(f"/api/runs/{run_id}/undo", json={"reference": "REF-EQUIV"})

    assert undone.status_code == 200
    assert undone.json()["record"]["status"] == "needs_review"
    assert undone.json()["record"]["reason"] == "equivalence"
    assert undone.json()["record"]["human_confirmed"] is False
    assert len(_confirmed_store._confirmed) == 0
    assert client.get("/api/history").json()["runs"][0]["summary"]["needs_review"] == 1
    assert client.post(f"/api/runs/{run_id}/undo", json={"reference": "REF-EQUIV"}).status_code == 400
