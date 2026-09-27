import base64
import html
import mimetypes
import posixpath
import shutil
import subprocess
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

from .locate import mark_terms_in_html

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"

MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
}

_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
body{{font:13px/1.45 -apple-system,"Segoe UI",Helvetica,Arial,sans-serif;color:#1d1d1b;background:#fff;margin:16px}}
p{{margin:0 0 6px}} img{{max-width:100%;height:auto;display:block;margin:8px 0}}
table{{border-collapse:collapse;margin:8px 0}} td{{border:1px solid #c9c9c4;padding:2px 6px;vertical-align:top}}
pre{{white-space:pre-wrap;font:12px/1.4 ui-monospace,Menlo,monospace}}
mark{{background:#fff1a6;outline:2px solid #e2b400;font-weight:700}}
</style></head><body>{body}</body></html>"""


def media_type_for(path: str) -> str:
    return MEDIA_TYPES.get(Path(path).suffix.lower(), "application/octet-stream")


def _docx_body_html(content: bytes) -> str:
    with zipfile.ZipFile(BytesIO(content)) as zf:
        rels = {}
        if "word/_rels/document.xml.rels" in zf.namelist():
            for rel in ElementTree.fromstring(zf.read("word/_rels/document.xml.rels")).iter(f"{_REL}Relationship"):
                rels[rel.get("Id")] = posixpath.normpath(posixpath.join("word", rel.get("Target", "")))

        def image_html(rel_id: str) -> str:
            name = rels.get(rel_id)
            if not name or name not in zf.namelist():
                return ""
            mime = mimetypes.guess_type(name)[0] or ""
            if not mime.startswith("image/") or mime in ("image/x-emf", "image/x-wmf", "image/emf", "image/wmf"):
                return ""
            data = base64.b64encode(zf.read(name)).decode("ascii")
            return f'<img src="data:{mime};base64,{data}" alt="">'

        def paragraph_html(p) -> str:
            out = []
            for node in p.iter():
                if node.tag == f"{_W}t" and node.text:
                    out.append(html.escape(node.text))
                elif node.tag in (f"{_W}br", f"{_W}cr"):
                    out.append("<br>")
                elif node.tag == f"{_W}tab":
                    out.append("&emsp;")
                elif node.tag == f"{_A}blip":
                    out.append(image_html(node.get(f"{_R}embed")))
            return "<p>" + "".join(out) + "</p>" if out else ""

        root = ElementTree.fromstring(zf.read("word/document.xml"))
        body = root.find(f"{_W}body")
        parts = []
        for child in list(body) if body is not None else []:
            if child.tag == f"{_W}p":
                parts.append(paragraph_html(child))
            elif child.tag == f"{_W}tbl":
                rows = []
                for tr in child.iter(f"{_W}tr"):
                    cells = "".join(
                        "<td>" + "".join(paragraph_html(p) for p in tc.iter(f"{_W}p")) + "</td>"
                        for tc in tr.iter(f"{_W}tc")
                    )
                    rows.append(f"<tr>{cells}</tr>")
                parts.append("<table>" + "".join(rows) + "</table>")
        return "".join(parts)


def _doc_body_html(content: bytes) -> str:
    if shutil.which("textutil") is not None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "cert.doc"
            src.write_bytes(content)
            result = subprocess.run(
                ["textutil", "-convert", "txt", "-stdout", str(src)], capture_output=True, text=True
            )
            if result.returncode == 0:
                return f"<pre>{html.escape(result.stdout)}</pre>"
    return ""


def render_certificate_preview(path: str, content: bytes, fallback_text: str = "", terms: list[str] | None = None) -> str:
    """A self-contained HTML page showing a Word certificate's text and images, for the review
    panel (browsers can't embed .docx/.doc the way they embed PDFs). Falls back to the text
    extracted during verification when the document can't be rendered."""
    suffix = Path(path).suffix.lower()
    body = ""
    try:
        if suffix == ".docx":
            body = _docx_body_html(content)
        elif suffix == ".doc":
            body = _doc_body_html(content)
    except (zipfile.BadZipFile, ElementTree.ParseError, KeyError):
        body = ""
    if not body.strip():
        body = f"<pre>{html.escape(fallback_text)}</pre>"
    if terms:
        body = mark_terms_in_html(body, terms)
    return _PAGE.format(body=body)
