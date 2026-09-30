"""Load and parse the Garani 2026 taxonomy YAML into FailureMode objects."""

from __future__ import annotations

import importlib.resources
from functools import lru_cache
from typing import Any

from ragpreflight.taxonomy.models import FailureMode, TaxonomySource

_YAML_FILENAME = "garani_2026.yaml"


def _load_yaml(text: str) -> Any:
    """Parse YAML text without importing yaml at module level."""
    try:
        import yaml  # type: ignore[import]
        return yaml.safe_load(text)
    except ImportError:
        import tomllib  # noqa: F401 — tomllib is stdlib in 3.11+
        raise ImportError(
            "PyYAML is required to load the taxonomy. "
            "Install with: pip install pyyaml"
        ) from None


def _read_taxonomy_yaml() -> str:
    """Read the bundled garani_2026.yaml file."""
    try:
        ref = importlib.resources.files("ragpreflight.taxonomy").joinpath(_YAML_FILENAME)
        return ref.read_text(encoding="utf-8")
    except Exception:
        # Fallback: resolve relative to this file
        import os
        here = os.path.dirname(__file__)
        with open(os.path.join(here, _YAML_FILENAME), encoding="utf-8") as f:
            return f.read()


def _parse_mode(raw: dict) -> FailureMode:
    src = raw.get("source", {})
    source = TaxonomySource(
        author=src.get("author", ""),
        year=int(src.get("year", 0)),
        venue=src.get("venue", ""),
        doi=src.get("doi", ""),
        url=src.get("url", ""),
    )
    return FailureMode(
        id=raw["id"],
        stage=raw["stage"],
        name=raw["name"],
        definition=raw.get("definition", "").strip(),
        observable_manifestation=raw.get("observable_manifestation", "").strip(),
        evidence_level=raw["evidence_level"],
        source=source,
        detector_status=raw.get("detector_status", "unsupported"),
        detector_ids=list(raw.get("detector_ids", [])),
    )


@lru_cache(maxsize=1)
def load_taxonomy() -> list[FailureMode]:
    """Return all 33 FailureMode objects from the canonical YAML.

    Results are cached after first call.

    Returns:
        List of FailureMode objects in paper order (F1–F33).
    """
    text = _read_taxonomy_yaml()
    data = _load_yaml(text)
    return [_parse_mode(m) for m in data["failure_modes"]]
