import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree
from typing import Optional

from .models import CertificateData, ExtractionSource

TEXT_LAYER_MIN_CHARS = 200

CERTIFICATE_EXTENSIONS = (".pdf", ".docx", ".doc")
_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_OCR_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif"}

# Standards that appear on these certificates but describe the certificate type
# itself (EN 10204 = "Inspection documents for metallic products"), not the
# material grade/standard we're trying to extract. Normalized (spaces removed).
_NON_MATERIAL_STANDARDS = {"EN10204"}

#  A real certificate's OCR/text layer sometimes inserts a stray hyphen or extra space right
# after a prefix letter - real examples: "EN- 10025" and "S-235JR" (examples/certificats/EBRO/
# unzipped/11703780005/ZA34876.pdf), "EN AW 5754" with a space instead of the usual hyphen
# (.../11703780014/ZA32562.pdf and .../11703780007/ZA33404.pdf). `[\s-]{0,3}` tolerates that
# noise without over-matching; the separator is then stripped back out by `_clean_match` so the
# returned value stays in the canonical form app/data/equivalence.yaml and the BOM itself use
# (no embedded hyphen/space where equivalence.find_members wouldn't expect one).
_STANDARD_RE = re.compile(r"EN[\s-]{0,3}\d{3,6}(?:-\d{1,2})?")

_GRADE_PATTERNS = [
    re.compile(r"\bX\d[A-Za-z]{2,4}\d{1,2}-\d{1,2}\b"),  # X5CrNi18-10
    re.compile(r"\bS[\s-]{0,3}\d{3}[A-Z]{0,3}\b"),  # S355MC, S235JR, OCR'd "S-235JR"
    re.compile(r"\bAISI[\s-]{0,3}\d{3}L?\b", re.IGNORECASE),  # AISI 304, AISI 316L
    re.compile(r"\b1\.\d{4}\b"),  # EN Werkstoff number, e.g. 1.4301
    re.compile(r"\bEN[\s-]{0,3}AW[\s-]{0,3}\d{4}\b", re.IGNORECASE),  # EN AW-5052, "EN AW 5754"
    re.compile(r"\b[567]\d{3}[\s-]?[TH]\d{1,3}\b"),  # aluminum series + temper: 6082 T6, 5754 H111
]


def _clean_match(raw: str) -> str:
    """Strip a stray separator glued right after a prefix letter/letters by OCR noise, e.g.
    "S-235JR" -> "S235JR", "EN- 10025" -> "EN 10025", "EN AW 5754" -> "EN AW-5754" (the
    hyphenated form equivalence.yaml and the BOM's own Material column use). Leaves an
    already-clean match unchanged.
    """
    cleaned = re.sub(r"^S[\s-]+(\d)", r"S\1", raw)
    cleaned = re.sub(r"^(EN)[\s-]+(\d)", r"\1 \2", cleaned)
    cleaned = re.sub(r"^(EN[\s-]{0,3}AW)[\s-]+(\d)", r"EN AW-\2", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^AISI[\s-]+(\d)", r"AISI \1", cleaned, flags=re.IGNORECASE)
    return cleaned

_COIL_LABEL_RE = re.compile(r"(coil no|bobina|colada|heat no)", re.IGNORECASE)
_COIL_VALUE_RE = re.compile(r"^\s*([A-Z]{1,3}\d{3,7})\b")

# EN 10204 inspection-document type (3.1, 2.2, ...) - distinct from the material
# standard itself. Matches both a clean text-layer "EN 10204 3.1" and an OCR'd
# "EN 10204 TIPO 2.2" (real example: examples/certificats/EBRO/.../ZA34852.pdf).
_CERT_TYPE_RE = re.compile(r"EN\s?10204[^\d]{0,20}?([234]\.[12])")


def extract_text_layer(pdf_path: Path) -> str:
    result = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def has_text_layer(text: str) -> bool:
    return len(re.sub(r"\s+", "", text)) >= TEXT_LAYER_MIN_CHARS


def extract_via_ocr(pdf_path: Path, lang: str = "eng+spa") -> str:
    with tempfile.TemporaryDirectory() as tmpdir:
        prefix = str(Path(tmpdir) / "page")
        subprocess.run(
            ["pdftoppm", "-png", "-r", "300", str(pdf_path), prefix],
            capture_output=True,
            check=True,
        )
        pages = sorted(Path(tmpdir).glob("page*.png"))
        texts = []
        for page in pages:
            result = subprocess.run(
                ["tesseract", str(page), "stdout", "-l", lang],
                capture_output=True,
                text=True,
                check=True,
            )
            texts.append(result.stdout)
        return "\n".join(texts)


def extract_via_ocr_with_confidence(pdf_path: Path, lang: str = "eng+spa") -> tuple[str, list[tuple[int, int, float]]]:
    """Like extract_via_ocr, but also returns per-word (start, end, confidence)
    spans into the returned text, so a caller can look up how confidently OCR
    read a specific substring (e.g. the extracted grade) - see
    `confidence_for_span`. Confidence is Tesseract's own 0-100 word-level
    score; words Tesseract didn't score (conf < 0, e.g. non-text detections)
    are excluded from the spans list.

    Word separators are reconstructed as a single space within the same OCR
    line and a newline between lines, matching extract_via_ocr's line-based
    output closely enough that the existing line-based parsers (e.g.
    parse_lot_number) still work unchanged.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        prefix = str(Path(tmpdir) / "page")
        subprocess.run(
            ["pdftoppm", "-png", "-r", "300", str(pdf_path), prefix],
            capture_output=True,
            check=True,
        )
        return ocr_images_with_confidence(sorted(Path(tmpdir).glob("page*.png")), lang)


def ocr_images_with_confidence(images: list[Path], lang: str = "eng+spa") -> tuple[str, list[tuple[int, int, float]]]:
    """OCR each image in order with Tesseract's TSV output, returning the joined text and
    per-word (start, end, confidence) spans into it - the shared core of the scanned-PDF
    path and the image-only Word document path (see extract_via_ocr_with_confidence)."""
    text_parts: list[str] = []
    spans: list[tuple[int, int, float]] = []
    offset = 0
    for page in images:
        result = subprocess.run(
            ["tesseract", str(page), "stdout", "-l", lang, "tsv"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            continue  # an image Tesseract can't read (e.g. a vector EMF logo) just contributes nothing
        lines = result.stdout.splitlines()
        if not lines:
            continue
        header = lines[0].split("\t")
        prev_line_key = None
        for raw_line in lines[1:]:
            if not raw_line.strip():
                continue
            fields = raw_line.split("\t")
            row = dict(zip(header, fields))
            word = row.get("text", "").strip()
            if not word:
                continue
            line_key = (row.get("block_num"), row.get("par_num"), row.get("line_num"))
            if prev_line_key is not None:
                sep = "\n" if line_key != prev_line_key else " "
                text_parts.append(sep)
                offset += len(sep)
            try:
                conf = float(row.get("conf", "-1"))
            except ValueError:
                conf = -1.0
            start = offset
            text_parts.append(word)
            offset += len(word)
            if conf >= 0:
                spans.append((start, offset, conf))
            prev_line_key = line_key
        text_parts.append("\n")
        offset += 1
    return "".join(text_parts), spans


def extract_docx_text(path: Path) -> str:
    """Paragraph text of a .docx (body, tables, headers/footers), one paragraph per line -
    a .docx is a zip of XML parts, so no extra dependency is needed."""
    with zipfile.ZipFile(path) as zf:
        parts = ["word/document.xml"] + sorted(
            n for n in zf.namelist() if re.match(r"word/(header|footer)\d*\.xml$", n)
        )
        lines: list[str] = []
        for part in parts:
            if part not in zf.namelist():
                continue
            root = ElementTree.fromstring(zf.read(part))
            for paragraph in root.iter(f"{_WORD_NS}p"):
                pieces = []
                for node in paragraph.iter():
                    if node.tag == f"{_WORD_NS}t" and node.text:
                        pieces.append(node.text)
                    elif node.tag == f"{_WORD_NS}tab":
                        pieces.append("\t")
                    elif node.tag in (f"{_WORD_NS}br", f"{_WORD_NS}cr"):
                        pieces.append("\n")
                lines.append("".join(pieces))
    return "\n".join(lines)


def docx_images(path: Path, dest: Path) -> list[Path]:
    """Write every raster image embedded in a .docx to `dest`, in archive order."""
    written = []
    with zipfile.ZipFile(path) as zf:
        for index, name in enumerate(n for n in zf.namelist() if n.startswith("word/media/")):
            suffix = Path(name).suffix.lower()
            if suffix not in _OCR_IMAGE_EXTENSIONS:
                continue
            target = dest / f"img{index:03d}{suffix}"
            target.write_bytes(zf.read(name))
            written.append(target)
    return written


def extract_doc_text(path: Path) -> str:
    """Text of a legacy binary .doc via macOS `textutil`. Empty when textutil isn't available
    (non-macOS) or can't read the file - the certificate then simply lands in review."""
    if shutil.which("textutil") is None:
        return ""
    result = subprocess.run(
        ["textutil", "-convert", "txt", "-stdout", str(path)], capture_output=True, text=True
    )
    return result.stdout if result.returncode == 0 else ""


def confidence_for_span(text: str, target: str, spans: list[tuple[int, int, float]]) -> Optional[float]:
    """Minimum OCR confidence among words overlapping the first occurrence of
    `target` in `text`. None if `target` isn't found in `text`, or no OCR word
    data overlaps it (e.g. `text` came from the text layer, not OCR).
    """
    if not target:
        return None
    idx = text.find(target)
    if idx < 0:
        return None
    end = idx + len(target)
    overlapping = [c for (s, e, c) in spans if e > idx and s < end]
    if not overlapping:
        return None
    return min(overlapping)


def parse_standard(text: str) -> str | None:
    for match in _STANDARD_RE.finditer(text):
        normalized = match.group(0).replace(" ", "").replace("-", "")
        if normalized not in _NON_MATERIAL_STANDARDS:
            return _clean_match(match.group(0).strip())
    return None


def _match_grade(text: str) -> Optional[re.Match]:
    for pattern in _GRADE_PATTERNS:
        match = pattern.search(text)
        if match:
            return match
    return None


def parse_grade(text: str) -> str | None:
    match = _match_grade(text)
    return _clean_match(match.group(0).strip()) if match else None


def parse_certificate_type(text: str) -> str | None:
    match = _CERT_TYPE_RE.search(text)
    return match.group(1) if match else None


def split_norma_and_grade(specified_material: str) -> tuple[str | None, str | None]:
    """Split a BOM `Material` cell (e.g. "EN 10025-2:2004 S235JR") into its norma and
    grade, for display purposes only. Reuses the same grade patterns extraction uses on
    certificates, so a BOM cell and a certificate reading are recognized consistently.

    This is not used for comparison logic - app.equivalence.classify() already matches
    the grade through the equivalence table, which correctly handles cross-standard
    equivalences that a plain split would lose.
    """
    if not specified_material:
        return None, None
    match = _match_grade(specified_material)
    if not match:
        return None, None
    grade = _clean_match(match.group(0).strip())
    norma = specified_material[: match.start()].strip() or None
    return norma, grade


def parse_lot_number(text: str) -> str | None:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _COIL_LABEL_RE.search(line):
            for candidate_line in lines[i + 1 : i + 6]:
                match = _COIL_VALUE_RE.match(candidate_line)
                if match:
                    return match.group(1)
    return None


def _read_certificate_text(path: Path) -> tuple[str, ExtractionSource, list[tuple[int, int, float]]]:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        text = extract_docx_text(path)
        if has_text_layer(text):
            return text, ExtractionSource.TEXT_LAYER, []
        # A scan pasted into Word: OCR the embedded images, exactly like a scanned PDF's pages.
        with tempfile.TemporaryDirectory() as tmpdir:
            images = docx_images(path, Path(tmpdir))
            if not images:
                return text, ExtractionSource.TEXT_LAYER, []
            ocr_text, spans = ocr_images_with_confidence(images)
            return ocr_text, ExtractionSource.OCR, spans
    if suffix == ".doc":
        return extract_doc_text(path), ExtractionSource.TEXT_LAYER, []

    text = extract_text_layer(path)
    if has_text_layer(text):
        return text, ExtractionSource.TEXT_LAYER, []
    ocr_text, spans = extract_via_ocr_with_confidence(path)
    return ocr_text, ExtractionSource.OCR, spans


def extract_certificate(pdf_path: Path, supplier: str, relative_path: str) -> CertificateData:
    """Extract material data from one certificate (PDF, .docx or .doc).

    Tries direct text extraction first (PDF text layer / Word text); falls back to OCR only
    when that text is too thin - rendered PDF pages, or a .docx's embedded images. The same field parser runs on whichever text was
    obtained, so matching/verification logic doesn't need to know which path ran -
    only the recorded `source` differs, which downstream logic uses to force any
    OCR-derived result into review.
    """
    text, source, word_spans = _read_certificate_text(pdf_path)

    grade_match = _match_grade(text)
    grade = _clean_match(grade_match.group(0).strip()) if grade_match else None
    grade_confidence = None
    if source == ExtractionSource.OCR and grade_match:
        # Confidence is looked up against the raw matched text, not the cleaned `grade` -
        # e.g. a reading of "S-235JR" is cleaned to "S235JR" for comparison/display, but the
        # hyphenated form is what's actually present at that position in `text`.
        grade_confidence = confidence_for_span(text, grade_match.group(0).strip(), word_spans)

    return CertificateData(
        path=relative_path,
        supplier=supplier,
        text=text,
        source=source,
        standard=parse_standard(text),
        grade=grade,
        lot_number=parse_lot_number(text),
        grade_confidence=grade_confidence,
        certificate_type=parse_certificate_type(text),
    )
