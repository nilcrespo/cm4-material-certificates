import io
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


def test_verify_with_correspondence_document_uses_it(examples_dir, verify_result):
    bom_path = examples_dir / "EBRO" / "M009_BOM.xlsx"
    cert_pdf = examples_dir / "EBRO" / "unzipped" / "11703780002" / "ZA34683.pdf"
    correspondence_path = examples_dir / "EBRO" / "correspondencia_1170378.xlsx"

    response = client.post(
        "/api/verify",
        files={
            "bom": ("M009_BOM.xlsx", bom_path.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "certificates": ("EBRO/11703780002.zip", _zip_bytes(cert_pdf), "application/zip"),
            "correspondence": (
                "correspondencia_1170378.xlsx",
                correspondence_path.read_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
        },
    )

    assert response.status_code == 200
    body = verify_result(response)
    by_ref = {r["reference"]: r for r in body["records"]}
    # I018-L211 is linked via correspondence to ZA34683 - confirmed even though the
    # underlying status stays needs_review/ocr_source (see design.md OCR-safety rule).
    assert by_ref["I018-L211"]["certificate_filename"].endswith("ZA34683.pdf")


def test_verify_without_correspondence_document_behaves_as_before(examples_dir, verify_result):
    bom_path = examples_dir / "EBRO" / "M009_BOM.xlsx"
    cert_pdf = examples_dir / "EBRO" / "unzipped" / "11703780002" / "ZA34683.pdf"

    response = client.post(
        "/api/verify",
        files={
            "bom": ("M009_BOM.xlsx", bom_path.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "certificates": ("EBRO/11703780002.zip", _zip_bytes(cert_pdf), "application/zip"),
        },
    )

    assert response.status_code == 200
    body = verify_result(response)
    by_ref = {r["reference"]: r for r in body["records"]}
    # Without correspondence, I018-L211 falls to material heuristic - it's the only
    # S235JR certificate in this small upload, so it still resolves via heuristic
    # (not an error), just via a different, less certain strategy than 5.1's case.
    assert by_ref["I018-L211"]["certificate_filename"].endswith("ZA34683.pdf")
