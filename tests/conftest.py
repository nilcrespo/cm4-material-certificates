import json
import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

EXAMPLES_DIR = APP_ROOT / "examples" / "certificats"


@pytest.fixture
def examples_dir() -> Path:
    # The real supplier certificates / BOM are confidential client data and are not in the
    # public repository - tests that need them run only on a machine that has them.
    if not EXAMPLES_DIR.is_dir():
        pytest.skip("confidential example data (examples/certificats) not present")
    return EXAMPLES_DIR


@pytest.fixture
def verify_result():
    """POST /api/verify streams newline-delimited progress events ending in a "done" event -
    this fixture returns a parser for that final event, shaped like the endpoint's old
    single-JSON response (run_id/summary/records/config), for tests that only care about
    the end result rather than the progress events themselves.
    """

    def _parse(response) -> dict:
        events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
        return next(e for e in events if e["stage"] == "done")

    return _parse


@pytest.fixture(autouse=True)
def exports_dir(tmp_path, monkeypatch) -> Path:
    """Every test saves auto-exports into its own temp folder - never the user's real
    ~/Documents history folder."""
    from app import config

    target = tmp_path / "exports"
    monkeypatch.setattr(config, "EXPORTS_DIR", target)
    return target
