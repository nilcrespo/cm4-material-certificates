"""Find where extracted values sit on a certificate page, for the review UI's highlights.

Works on the word list recorded at extraction (CertificateData.words) - PDF text-layer word
boxes or Tesseract's OCR word boxes - so it's the same code for text and scanned certificates.
Matching ignores case, spaces and punctuation ("EN AW-5754" finds "EN", "AW 5754" or
"ENAW-5754"), mirroring how extraction.py normalises what it reads.
"""

import re

MAX_HITS_PER_TERM = 5
_TYPE_WINDOW = 4  # words after "10204" in which the "2.2"/"3.1" type may appear


def _compress(text: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", text.upper())


def _find_term(words: list, term: str) -> list[list[int]]:
    """Word-index groups whose concatenated text contains `term` (normalised)."""
    target = _compress(term)
    if not target:
        return []
    joined, owner = "", []
    for index, word in enumerate(words):
        piece = _compress(word[0])
        joined += piece
        owner.extend([index] * len(piece))
    hits, start = [], 0
    while len(hits) < MAX_HITS_PER_TERM:
        i = joined.find(target, start)
        if i < 0:
            break
        group = sorted(set(owner[i : i + len(target)]))
        # A match spanning two pages is a coincidence of concatenation, not a real reading.
        if len({words[g][1] for g in group}) == 1:
            hits.append(group)
        start = i + len(target)
    return hits


def _find_certificate_type(words: list, cert_type: str) -> list[list[int]]:
    target = _compress(cert_type)
    hits = []
    for index, word in enumerate(words):
        if "10204" not in _compress(word[0]):
            continue
        for offset in range(0, _TYPE_WINDOW + 1):
            j = index + offset
            if j >= len(words) or words[j][1] != word[1]:
                break
            compressed = _compress(words[j][0])
            if compressed.endswith(target) and (j > index or compressed != "EN10204"):
                hits.append(list(range(index, j + 1)))
                break
    return hits[:MAX_HITS_PER_TERM]


def locate(words: list, *, grade=None, standard=None, certificate_type=None) -> list[dict]:
    """Boxes (page, x0, y0, x1, y1 as 0-1 fractions, kind) around each located value."""
    groups: list[tuple[str, list[int]]] = []
    if grade:
        groups += [("grade", g) for g in _find_term(words, grade)]
    if standard:
        groups += [("standard", g) for g in _find_term(words, standard)]
    if certificate_type:
        groups += [("type", g) for g in _find_certificate_type(words, certificate_type)]

    boxes = []
    for kind, group in groups:
        members = [words[i] for i in group]
        boxes.append({
            "kind": kind,
            "page": members[0][1],
            "x0": min(w[2] for w in members),
            "y0": min(w[3] for w in members),
            "x1": max(w[4] for w in members),
            "y1": max(w[5] for w in members),
        })
    return boxes


def mark_terms_in_html(html_text: str, terms: list[str]) -> str:
    """Wrap occurrences of `terms` in <mark> inside an HTML fragment's text (never inside tags),
    tolerating spaces/hyphens between characters - used for Word certificate previews."""
    patterns = []
    for term in terms:
        chars = [re.escape(c) for c in _compress(term)]
        if chars:
            patterns.append(r"[\s\-./:]*".join(chars))
    if not patterns:
        return html_text
    regex = re.compile("(" + "|".join(sorted(patterns, key=len, reverse=True)) + ")", re.IGNORECASE)
    parts = re.split(r"(<[^>]+>)", html_text)
    return "".join(part if part.startswith("<") else regex.sub(r"<mark>\1</mark>", part) for part in parts)
