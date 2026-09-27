# Per-supplier matching configuration.
#
# NOTE (found during v1 validation): the example EBRO zip's folder names
# (e.g. "11703780001") do NOT match the CM4 BOM reference format (e.g.
# "I018-M108") - they look like an EBRO-internal order/delivery number
# instead. So direct-reference matching for EBRO never actually fires
# against the example data; verification correctly falls through to
# material-heuristic matching instead (which does find the right grade, but
# reports ambiguous_match when several certs share it - see README's known
# gaps). Keeping EBRO listed here is harmless (it's a no-op until a real
# reference-based naming convention is confirmed) but the assumption behind
# it is unverified - see design.md's open question on this.
DIRECT_LINK_SUPPLIERS: set[str] = {"EBRO"}

# Where every run's Excel export is saved automatically - this folder *is* the run history
# (see app/history.py). Defaults to a visible folder under Documents so it's backed up and
# easy to find in Finder; override with the CM4_EXPORTS_DIR environment variable.
import os
from pathlib import Path

EXPORTS_DIR: Path = Path(
    os.environ.get("CM4_EXPORTS_DIR") or Path.home() / "Documents" / "CM4 verificacions"
).expanduser()
