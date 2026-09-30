"""Core data models for ragpreflight.

All public functions return typed dataclasses — never raw dicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Optional


class Severity(str, Enum):
    """Issue severity levels."""

    CRITICAL = "critical"  # Will definitely cause RAG failures
    WARNING = "warning"  # Likely to cause issues
    INFO = "info"  # Worth knowing, may not cause problems


class IssueCategory(str, Enum):
    """Classification of detected issues."""

    OCR = "ocr"
    ENCODING = "encoding"
    STRUCTURE = "structure"
    CONTENT = "content"
    METADATA = "metadata"
    CHUNKING = "chunking"
    DUPLICATION = "duplication"
    STALENESS = "staleness"


@dataclass
class TaxonomyReference:
    """Link between a detected issue and a Garani 2026 failure mode.

    Attributes:
        mode_id: Canonical failure mode ID (e.g. "F7").
        relationship: How strongly the issue relates to the mode.
            - "direct": The detector directly measures this failure mode.
            - "proxy": The detector uses a proxy signal (e.g. file age for staleness).
            - "risk_signal": The finding is a risk indicator, not a confirmed instance.
        confidence: Optional 0.0–1.0 confidence; None when not quantifiable.
        explanation: Why this issue maps to this mode, including what the finding
            does NOT prove (required for proxy and risk_signal relationships).
    """

    mode_id: str
    relationship: Literal["direct", "proxy", "risk_signal"]
    mode_name: str = ""
    definition: str = ""
    confidence: Optional[float] = None
    explanation: Optional[str] = None


@dataclass
class Issue:
    """A single detected quality issue.

    Attributes:
        category: Which aspect of document quality is affected.
        severity: How badly this will impact RAG performance.
        message: Human-readable description of the problem.
        location: Where in the document (e.g. "page 3", "rows 12-15").
        suggestion: Actionable recommendation for fixing this issue.
        context: Short text snippet showing the actual offending content.
        taxonomy_refs: Links to Garani 2026 failure modes this issue relates to.
    """

    category: IssueCategory
    severity: Severity
    message: str
    location: Optional[str] = None
    suggestion: Optional[str] = None
    context: Optional[str] = None
    taxonomy_refs: list[TaxonomyReference] = field(default_factory=list)

    def __str__(self) -> str:
        parts = [f"[{self.severity.value.upper()}][{self.category.value}] {self.message}"]
        if self.location:
            parts.append(f"  Location: {self.location}")
        if self.context:
            parts.append(f"  Context:  {self.context}")
        if self.suggestion:
            parts.append(f"  Fix: {self.suggestion}")
        return "\n".join(parts)


@dataclass
class DocumentReport:
    """Quality report for a single document.

    Attributes:
        filepath: Absolute or relative path to the scanned document.
        score: Overall readiness score 0–100.
        issues: List of detected issues sorted by severity.
        page_count: Number of pages (0 for non-paginated formats).
        text_extractable_ratio: Fraction of document that yielded text (0.0–1.0).
        file_format: Detected format string (e.g. "pdf", "docx").
        file_size_bytes: Size on disk.
    """

    filepath: str
    score: int
    issues: list[Issue] = field(default_factory=list)
    page_count: int = 0
    text_extractable_ratio: float = 0.0
    file_format: str = ""
    file_size_bytes: int = 0

    @property
    def critical_issues(self) -> list[Issue]:
        """Return only CRITICAL severity issues."""
        return [i for i in self.issues if i.severity == Severity.CRITICAL]

    @property
    def warnings(self) -> list[Issue]:
        """Return only WARNING severity issues."""
        return [i for i in self.issues if i.severity == Severity.WARNING]

    @property
    def passed(self) -> bool:
        """True when there are no CRITICAL issues."""
        return len(self.critical_issues) == 0

    def to_dict(self) -> dict:
        """Serialize to a plain dict suitable for JSON output."""
        return {
            "filepath": self.filepath,
            "score": self.score,
            "file_format": self.file_format,
            "file_size_bytes": self.file_size_bytes,
            "page_count": self.page_count,
            "text_extractable_ratio": round(self.text_extractable_ratio, 4),
            "issues": [_issue_to_dict(i) for i in self.issues],
        }


def _issue_to_dict(i: Issue) -> dict:
    return {
        "category": i.category.value,
        "severity": i.severity.value,
        "message": i.message,
        "location": i.location,
        "context": i.context,
        "suggestion": i.suggestion,
        "taxonomy_refs": [
            {
                "mode_id": ref.mode_id,
                "mode_name": ref.mode_name,
                "definition": ref.definition,
                "relationship": ref.relationship,
                "confidence": ref.confidence,
                "explanation": ref.explanation,
            }
            for ref in i.taxonomy_refs
        ],
    }


@dataclass
class ChunkReport:
    """Quality report for a single chunk produced by a chunking strategy.

    Attributes:
        chunk_index: Zero-based position in the chunk sequence.
        text_preview: First 100 characters of the chunk.
        coherence_score: Semantic coherence 0.0–1.0, or None when sentence-transformers
            is not installed. Never returns 1.0 as a synthetic "unavailable" sentinel.
        coherence_status: "evaluated" when score is real, "not_evaluated" when
            sentence-transformers was absent or evaluation failed.
        issues: Issues detected in this specific chunk.
    """

    chunk_index: int
    text_preview: str
    coherence_score: Optional[float]
    coherence_status: str = "not_evaluated"
    issues: list[Issue] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize to a plain dict suitable for JSON output."""
        return {
            "chunk_index": self.chunk_index,
            "text_preview": self.text_preview,
            "coherence_score": round(self.coherence_score, 4) if self.coherence_score is not None else None,
            "coherence_status": self.coherence_status,
            "issues": [_issue_to_dict(i) for i in self.issues],
        }


@dataclass
class CorpusReport:
    """Quality report for a directory of documents.

    Attributes:
        directory: Path that was audited.
        total_documents: Number of files processed.
        average_score: Mean score across all documents.
        documents: Individual DocumentReport per file.
        corpus_issues: Cross-document issues (e.g. duplicates, contradictions).
        duplicate_groups: Lists of near-duplicate filepath groups.
    """

    directory: str
    total_documents: int
    average_score: float
    documents: list[DocumentReport] = field(default_factory=list)
    corpus_issues: list[Issue] = field(default_factory=list)
    duplicate_groups: list[list[str]] = field(default_factory=list)

    @property
    def worst_documents(self) -> list[DocumentReport]:
        """Bottom 10% of documents by score."""
        if not self.documents:
            return []
        cutoff = max(1, len(self.documents) // 10)
        return sorted(self.documents, key=lambda d: d.score)[:cutoff]

    def to_dict(self) -> dict:
        """Serialize to a plain dict suitable for JSON output."""
        return {
            "directory": self.directory,
            "total_documents": self.total_documents,
            "average_score": round(self.average_score, 2),
            "duplicate_groups": self.duplicate_groups,
            "corpus_issues": [_issue_to_dict(i) for i in self.corpus_issues],
            "documents": [d.to_dict() for d in self.documents],
        }
