"""Core data types shared by readers, checks and reports."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path


class Severity(str, Enum):
    ERROR = "error"  # will cause wrong answers; fix before ingesting
    WARN = "warn"  # likely to hurt retrieval or answers; review
    INFO = "info"  # worth knowing; no action required

    @property
    def weight(self) -> int:
        return {"error": 12, "warn": 4, "info": 0}[self.value]


@dataclass
class Page:
    """One page (PDF) or one logical section (other formats)."""

    number: int  # 1-based
    text: str
    image_count: int = 0
    line_offset: int = 0  # lines before this text in the source file (e.g. stripped frontmatter)


@dataclass
class Document:
    path: Path
    fmt: str  # pdf | docx | md | txt | html
    pages: list[Page]
    metadata: dict[str, str] = field(default_factory=dict)  # title, author, date, owner, ...
    read_error: str | None = None

    @property
    def text(self) -> str:
        return "\n\n".join(p.text for p in self.pages)

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def word_count(self) -> int:
        return len(self.text.split())


@dataclass
class Finding:
    check: str  # stable id, e.g. "version-conflict"
    severity: Severity
    f_codes: list[str]  # taxonomy ids, e.g. ["F1", "F14"]
    title: str  # one line, shown in the terminal
    path: str  # file the finding is about (relative to scan root when possible)
    location: str = ""  # "p.3", "chunk 12", "line 40" ...
    detail: str = ""  # longer explanation / evidence
    fix: str = ""  # what to do about it
    related: list[str] = field(default_factory=list)  # other files involved

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


@dataclass
class Report:
    root: str
    documents: list[Document]
    findings: list[Finding]
    score: int
    verdict: str  # READY | REVIEW | NOT READY

    def counts(self) -> dict[str, int]:
        c = {s.value: 0 for s in Severity}
        for f in self.findings:
            c[f.severity.value] += 1
        return c

    def to_dict(self) -> dict:
        from . import __version__

        return {
            "tool": "ragpreflight",
            "version": __version__,
            "root": self.root,
            "score": self.score,
            "verdict": self.verdict,
            "counts": self.counts(),
            "documents": [
                {
                    "path": str(d.path),
                    "format": d.fmt,
                    "pages": len(d.pages),
                    "words": d.word_count,
                    "metadata": d.metadata,
                    "read_error": d.read_error,
                }
                for d in self.documents
            ],
            "findings": [f.to_dict() for f in self.findings],
        }
