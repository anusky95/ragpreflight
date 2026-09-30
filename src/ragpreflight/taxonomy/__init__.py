"""Garani 2026 RAG failure taxonomy — standalone, no heavy dependencies.

Usage::

    from ragpreflight.taxonomy import get_failure_mode, list_failure_modes
    from ragpreflight.taxonomy import modes_by_stage, detector_coverage

    mode = get_failure_mode("F7")
    print(mode.name, mode.evidence_level)

    retrieval_modes = modes_by_stage("retrieval")
    coverage = detector_coverage()
"""

from __future__ import annotations

from ragpreflight.taxonomy.coverage import CoverageSummary, detector_coverage
from ragpreflight.taxonomy.loader import load_taxonomy
from ragpreflight.taxonomy.models import DetectorStatus, FailureMode, TaxonomySource

__all__ = [
    "FailureMode",
    "TaxonomySource",
    "DetectorStatus",
    "CoverageSummary",
    "get_failure_mode",
    "list_failure_modes",
    "modes_by_stage",
    "detector_coverage",
]

_VALID_STAGES = {
    "ingestion",
    "representation",
    "retrieval",
    "generation",
    "evaluation",
    "deployment",
    "agentic_orchestration",
}


def list_failure_modes() -> list[FailureMode]:
    """Return all 33 failure modes in paper order (F1–F33).

    Returns:
        List of FailureMode objects.
    """
    return load_taxonomy()


def get_failure_mode(mode_id: str) -> FailureMode:
    """Return a single failure mode by its canonical ID.

    Args:
        mode_id: Canonical mode ID, e.g. "F7" or "f7" (case-insensitive).

    Returns:
        FailureMode for the given ID.

    Raises:
        KeyError: If the mode ID is not found in the taxonomy.
    """
    normalised = mode_id.upper()
    for mode in load_taxonomy():
        if mode.id == normalised:
            return mode
    valid = ", ".join(m.id for m in load_taxonomy())
    raise KeyError(f"Unknown failure mode '{mode_id}'. Valid IDs: {valid}")


def modes_by_stage(stage: str) -> list[FailureMode]:
    """Return all failure modes for a given pipeline stage.

    Args:
        stage: One of 'ingestion', 'representation', 'retrieval', 'generation',
               'evaluation', 'deployment', 'agentic_orchestration'.

    Returns:
        List of FailureMode objects for the requested stage.

    Raises:
        ValueError: If the stage name is not recognised.
    """
    normalised = stage.lower().replace(" ", "_").replace("-", "_")
    if normalised not in _VALID_STAGES:
        valid = ", ".join(sorted(_VALID_STAGES))
        raise ValueError(f"Unknown stage '{stage}'. Valid stages: {valid}")
    return [m for m in load_taxonomy() if m.stage == normalised]
