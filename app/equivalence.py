import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

DEFAULT_TABLE_PATH = Path(__file__).parent / "data" / "equivalence.yaml"


def _normalize(text: str) -> str:
    """Lowercase and collapse hyphen/space/underscore variance (e.g. "EN AW-5754"
    vs "EN AW 5754" are the same designation written two common ways - this is
    formatting noise, not a real equivalence judgment call).
    """
    return re.sub(r"[-_\s]+", " ", text.strip().lower())


@dataclass(frozen=True)
class Member:
    canonical: str
    value: str
    family: str


class EquivalenceTable:
    def __init__(self, groups: list[dict]):
        self._members: list[Member] = []
        for group in groups:
            canonical = group["canonical"]
            for member in group["members"]:
                self._members.append(Member(canonical=canonical, value=member["value"], family=member["family"]))
        # Longest value first, so e.g. "EN AW-5052" is preferred over a bare "5052" substring hit.
        self._members.sort(key=lambda m: len(m.value), reverse=True)

    @classmethod
    def load(cls, path: Path = DEFAULT_TABLE_PATH) -> "EquivalenceTable":
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(data.get("groups", []))

    def find_members(self, material_text: str) -> list[Member]:
        if not material_text:
            return []
        haystack = _normalize(material_text)
        matches = []
        seen_canonicals = set()
        for member in self._members:
            if _normalize(member.value) in haystack:
                if member.canonical in seen_canonicals:
                    continue
                matches.append(member)
                seen_canonicals.add(member.canonical)
        return matches

    def all_members(self) -> list[Member]:
        return list(self._members)


class MatchKind:
    EXACT = "exact"
    SAME_STANDARD = "same_standard"
    CROSS_STANDARD = "cross_standard"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


@dataclass
class EquivalenceResult:
    kind: str
    canonical: Optional[str] = None
    specified_value: Optional[str] = None
    extracted_value: Optional[str] = None


def classify(specified_material: str, extracted_material: str, table: EquivalenceTable) -> EquivalenceResult:
    """Classify how a BOM's specified material relates to a certificate's extracted material."""
    if _normalize(specified_material) == _normalize(extracted_material):
        return EquivalenceResult(kind=MatchKind.EXACT)

    spec_matches = table.find_members(specified_material)
    ext_matches = table.find_members(extracted_material)

    for s in spec_matches:
        for e in ext_matches:
            if s.canonical != e.canonical:
                continue
            if s.family == "external" or e.family == "external":
                return EquivalenceResult(
                    kind=MatchKind.CROSS_STANDARD,
                    canonical=s.canonical,
                    specified_value=s.value,
                    extracted_value=e.value,
                )
            return EquivalenceResult(kind=MatchKind.SAME_STANDARD, canonical=s.canonical)

    if spec_matches and ext_matches:
        # Both sides matched *something* in the table, but not the same group.
        return EquivalenceResult(kind=MatchKind.MISMATCH)

    return EquivalenceResult(kind=MatchKind.UNKNOWN)


# Standard, well-documented OCR confusion pairs - not a bespoke list. Covers
# letter/digit look-alikes and digit/digit shapes commonly confused at low
# scan quality. See design.md's "OCR-trust" decision.
_CONFUSABLE_PAIRS = ["0O", "1I", "1L", "2Z", "5S", "6G", "8B", "17", "38", "56", "08", "68"]


def _confusable_chars(ch: str) -> set[str]:
    ch = ch.upper()
    result = {ch}
    for pair in _CONFUSABLE_PAIRS:
        if ch in pair:
            result.update(pair)
    return result


def is_single_confusable_substitution(a: str, b: str) -> bool:
    """True if `a` and `b` are the same length, differ in exactly one
    position, and that position's two characters form a known OCR-confusable
    pair. Models a realistic single-glyph OCR misread - not general string
    similarity, which would flag far more pairs than are actually plausible
    OCR errors.
    """
    if len(a) != len(b) or a.upper() == b.upper():
        return False
    diff_positions = [i for i in range(len(a)) if a[i].upper() != b[i].upper()]
    if len(diff_positions) != 1:
        return False
    i = diff_positions[0]
    return b[i].upper() in _confusable_chars(a[i])


def has_confusable_alternate(value: str, table: EquivalenceTable) -> bool:
    """True if `value` sits exactly one confusable substitution away from a
    member of a *different* canonical material than any group `value` itself
    matches - meaning the reading could plausibly be a misread of that other
    material instead of the one it superficially matches.
    """
    if not value:
        return False
    own_canonicals = {m.canonical for m in table.find_members(value)}
    for member in table.all_members():
        if member.canonical in own_canonicals:
            continue
        if is_single_confusable_substitution(value, member.value):
            return True
    return False


# Chosen with a wide margin against the two real ground-truth readings: a
# clean S235JR read scored ~86.6, a genuinely noisy-but-correct EN AW-5754
# read (from a locally garbled region of a real certificate) scored ~15.6 -
# see tests/test_extraction.py's ground-truth confidence check.
OCR_TRUST_MIN_CONFIDENCE = 80.0


def is_reading_trustworthy(
    grade: Optional[str],
    confidence: Optional[float],
    table: EquivalenceTable,
    min_confidence: float = OCR_TRUST_MIN_CONFIDENCE,
) -> bool:
    """An OCR-derived grade is only trustworthy enough to skip manual review
    when it was read at high confidence AND isn't one confusable-digit-swap
    away from a different real material in the table. Either condition
    failing means "we can't rule out a misread" - see design.md.
    """
    if not grade or confidence is None:
        return False
    if confidence < min_confidence:
        return False
    return not has_confusable_alternate(grade, table)
