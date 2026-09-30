"""Data models for the Garani 2026 RAG failure taxonomy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

DetectorStatus = Literal["direct", "proxy", "risk_signal", "runtime_required", "unsupported"]


@dataclass(frozen=True)
class TaxonomySource:
    """Provenance for a taxonomy entry."""

    author: str
    year: int
    venue: str
    doi: str
    url: str = ""


@dataclass(frozen=True)
class FailureMode:
    """One of the 33 failure modes from Garani 2026.

    Attributes:
        id: Canonical ID (F1–F33).
        stage: Pipeline stage name.
        name: Short human-readable name.
        definition: Paper-grounded formal definition.
        observable_manifestation: Paper-grounded observable manifestation.
        evidence_level: Strong, Moderate, or Limited (paper-assigned).
        source: Bibliographic provenance.
        detector_status: How ragpreflight relates to this mode.
        detector_ids: IDs of detectors that assess this mode.
    """

    id: str
    stage: str
    name: str
    definition: str
    observable_manifestation: str
    evidence_level: Literal["Strong", "Moderate", "Limited"]
    source: TaxonomySource
    detector_status: DetectorStatus
    detector_ids: list[str] = field(default_factory=list)
