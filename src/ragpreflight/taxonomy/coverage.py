"""Taxonomy coverage reporting — what ragpreflight can and cannot assess."""

from __future__ import annotations

from dataclasses import dataclass, field

from ragpreflight.taxonomy.loader import load_taxonomy
from ragpreflight.taxonomy.models import FailureMode


@dataclass
class CoverageSummary:
    """Summary of detector coverage across the 33 failure modes.

    Attributes:
        direct: Modes directly assessed by a detector.
        proxy: Modes assessed indirectly via a proxy signal.
        risk_signal: Modes flagged by a risk indicator (not confirmed).
        runtime_required: Modes that need runtime traces to assess.
        unsupported: Modes not currently assessed.
        total: Total number of modes (always 33).
    """

    direct: list[FailureMode] = field(default_factory=list)
    proxy: list[FailureMode] = field(default_factory=list)
    risk_signal: list[FailureMode] = field(default_factory=list)
    runtime_required: list[FailureMode] = field(default_factory=list)
    unsupported: list[FailureMode] = field(default_factory=list)
    total: int = 33


def detector_coverage() -> CoverageSummary:
    """Return a CoverageSummary showing how each F1–F33 mode is addressed.

    Returns:
        CoverageSummary grouped by detector_status.
    """
    summary = CoverageSummary()
    for mode in load_taxonomy():
        bucket = getattr(summary, mode.detector_status, summary.unsupported)
        bucket.append(mode)
    return summary
