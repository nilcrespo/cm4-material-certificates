import json
from pathlib import Path

DEFAULT_STORE_PATH = Path(__file__).parent / "data" / "confirmed_pairs.json"


def _pair_key(canonical: str, value_a: str, value_b: str) -> str:
    # Order-independent: "X5CrNi18-10 confirmed vs AISI 304" is the same pair
    # regardless of which side was "specified" and which was "extracted".
    a, b = sorted([value_a.strip().lower(), value_b.strip().lower()])
    return f"{canonical.strip().lower()}::{a}::{b}"


class ConfirmedPairsStore:
    """Persists material-equivalence pairs a person has confirmed as OK.

    Once a specific cross-standard pair (e.g. "X5CrNi18-10 = AISI 304") has been
    confirmed once, future matches on that same pair auto-resolve to `ok` instead
    of needing review again - see design.md's equivalence decision.
    """

    def __init__(self, path: Path = DEFAULT_STORE_PATH):
        self.path = Path(path)
        self._confirmed: set[str] = set()
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                self._confirmed = set(json.load(f))
        else:
            self._confirmed = set()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(sorted(self._confirmed), f, indent=2)

    def is_confirmed(self, canonical: str, value_a: str, value_b: str) -> bool:
        return _pair_key(canonical, value_a, value_b) in self._confirmed

    def confirm(self, canonical: str, value_a: str, value_b: str) -> None:
        self._confirmed.add(_pair_key(canonical, value_a, value_b))
        self._save()
