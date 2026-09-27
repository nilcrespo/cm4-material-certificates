import subprocess

from app.extraction import extract_certificate, extract_docx_text
from app.models import ExtractionSource
from app.pipeline import is_certificate_path
from app.preview import render_certificate_preview
from docx_helpers import make_docx

TEXT_CERT = [
    "CERTIFICADO DE INSPECCIÓN EN 10204 3.1",
    "Cliente: CM4 Sistemas de Fabricación S.L.",
    "Material: EN 10025-2 S235JR  Colada: 123456",
    "Espesor 6 mm - Chapa laminada en caliente",
    "Composición química y ensayos mecánicos conformes a la norma indicada.",
]


def test_docx_text_is_read_without_ocr(tmp_path):
    path = tmp_path / "cert.docx"
    path.write_bytes(make_docx(TEXT_CERT))

    cert = extract_certificate(path, supplier="SUP", relative_path="SUP/cert.docx")

    assert cert.source == ExtractionSource.TEXT_LAYER
    assert cert.grade == "S235JR"
    assert cert.certificate_type == "3.1"
    assert "Colada" in extract_docx_text(path)


def test_image_only_docx_is_ocrd_like_a_scanned_pdf(tmp_path, examples_dir):
    # A real scanned EBRO certificate page pasted into Word - the case where a supplier sends
    # a "Word certificate" that is really just a scan.
    scan = examples_dir / "EBRO" / "unzipped" / "11703780002" / "ZA34683.pdf"
    subprocess.run(["pdftoppm", "-png", "-r", "300", "-f", "1", "-l", "1", str(scan), str(tmp_path / "p")], check=True)
    png = next(tmp_path.glob("p*.png")).read_bytes()
    path = tmp_path / "scan.docx"
    path.write_bytes(make_docx([], image_png=png))

    cert = extract_certificate(path, supplier="EBRO", relative_path="EBRO/scan.docx")

    assert cert.source == ExtractionSource.OCR
    assert cert.grade == "S235JR"
    assert cert.grade_confidence is not None


def test_certificate_extensions_and_lock_files():
    assert is_certificate_path("EBRO/a.pdf")
    assert is_certificate_path("EBRO/a.DOCX")
    assert is_certificate_path("EBRO/a.doc")
    assert not is_certificate_path("EBRO/~$a.docx")
    assert not is_certificate_path("EBRO/notes.txt")


def test_docx_preview_has_text_and_images_and_no_scripts():
    content = make_docx(["Material <S235JR>"], image_png=b"\x89PNG\r\n\x1a\nfake")
    page = render_certificate_preview("SUP/cert.docx", content)
    assert "Material &lt;S235JR&gt;" in page
    assert "data:image/png;base64," in page
    assert "<script" not in page


def test_unreadable_word_file_falls_back_to_extracted_text():
    page = render_certificate_preview("SUP/cert.docx", b"not a zip", fallback_text="S235JR")
    assert "S235JR" in page


def test_certificate_layout_and_page_for_a_real_scan(examples_dir, verify_result):
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    bom = examples_dir / "EBRO" / "M009_BOM.xlsx"
    cert = examples_dir / "EBRO" / "unzipped" / "11703780002" / "ZA34683.pdf"
    body = verify_result(client.post(
        "/api/verify",
        files=[("bom", ("M009_BOM.xlsx", bom.read_bytes())), ("certificates", ("EBRO/x/ZA34683.pdf", cert.read_bytes()))],
    ))
    layout = client.get(f"/api/runs/{body['run_id']}/certificate/layout", params={"path": "EBRO/x/ZA34683.pdf"}).json()
    assert layout["pages"] >= 1
    assert {h["kind"] for h in layout["highlights"]} >= {"grade", "type"}
    page = client.get(f"/api/runs/{body['run_id']}/certificate/page", params={"path": "EBRO/x/ZA34683.pdf", "n": 1})
    assert page.status_code == 200 and page.content.startswith(b"\x89PNG")
