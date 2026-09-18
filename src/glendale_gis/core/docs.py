"""User docs shipped with the package (from the repository's ``docs/`` folder).

Installed wheels carry them as ``glendale_gis/docs``. In a source checkout they are read from the
repository's ``docs/`` folder, so there is only one copy of each.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

REPO_DOCS = Path(__file__).resolve().parents[3] / "docs"


def read_doc(name: str) -> str:
    """Return a doc's text, e.g. ``read_doc("real-time-sources.md")``."""
    if "/" in name or "\\" in name or name.startswith("."):
        raise ValueError(f"Invalid doc name {name!r}")
    packaged = resources.files("glendale_gis") / "docs" / name
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")
    local = REPO_DOCS / name
    if local.is_file():
        return local.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Doc {name!r} is not installed")
