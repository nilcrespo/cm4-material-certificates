from pathlib import Path
from typing import Optional

import openpyxl

from .tabular import read_csv_rows

CLIENT_REF_COLUMN = "ClientRef"
LOTE_COLUMN = "Lote"


def _normalize_cell(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_correspondence_xlsx(path: Path, sheet_name: Optional[str] = None) -> dict[str, Optional[str]]:
    """Parse a correspondence document mapping BOM reference (ClientRef) to
    certificate identifier (Lote). Extra columns (Albarán, Pos, Cantidad, ...)
    are ignored - only ClientRef and Lote are required, matching the real
    emailed table's column names.

    A blank Lote for a present ClientRef is kept as None (not dropped): it
    means the supplier has confirmed no certificate exists for that line yet -
    see design.md's "correspondence-document matching" decision.
    """
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[sheet_name] if sheet_name else wb.active

    rows_iter = ws.iter_rows(values_only=True)
    header = next(rows_iter)
    col_index = {name: i for i, name in enumerate(header) if name is not None}

    if CLIENT_REF_COLUMN not in col_index or LOTE_COLUMN not in col_index:
        raise ValueError(
            f"Correspondence document is missing expected columns "
            f"'{CLIENT_REF_COLUMN}' / '{LOTE_COLUMN}'"
        )

    ref_idx = col_index[CLIENT_REF_COLUMN]
    lote_idx = col_index[LOTE_COLUMN]

    result: dict[str, Optional[str]] = {}
    for row in rows_iter:
        if row is None or ref_idx >= len(row):
            continue
        ref = _normalize_cell(row[ref_idx])
        if ref is None:
            continue
        lote = _normalize_cell(row[lote_idx]) if lote_idx < len(row) else None
        result[ref] = lote

    return result


def parse_correspondence_csv(content: bytes) -> dict[str, Optional[str]]:
    rows = read_csv_rows(content)
    header = [cell.strip() for cell in rows[0]] if rows else []
    if CLIENT_REF_COLUMN not in header or LOTE_COLUMN not in header:
        raise ValueError(
            f"Correspondence document is missing expected columns "
            f"'{CLIENT_REF_COLUMN}' / '{LOTE_COLUMN}'"
        )
    ref_idx = header.index(CLIENT_REF_COLUMN)
    lote_idx = header.index(LOTE_COLUMN)

    result: dict[str, Optional[str]] = {}
    for row in rows[1:]:
        ref = _normalize_cell(row[ref_idx]) if ref_idx < len(row) else None
        if ref is None:
            continue
        result[ref] = _normalize_cell(row[lote_idx]) if lote_idx < len(row) else None
    return result


def parse_correspondence(path: Path) -> dict[str, Optional[str]]:
    """Parse a correspondence document, dispatching by file extension."""
    if path.suffix.lower() == ".csv":
        return parse_correspondence_csv(path.read_bytes())
    return parse_correspondence_xlsx(path)
