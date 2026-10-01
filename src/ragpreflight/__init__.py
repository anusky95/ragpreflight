"""ragpreflight — Pre-ingestion RAG document quality audit.

Grounded in Garani 2026 (doi:10.18653/v1/2026.trustnlp-main.27).
Zero API keys required. Everything runs locally.

Quick start:
    from ragpreflight import scan_document, audit_corpus, analyze_chunks

    report = scan_document("document.pdf")
    print(report.score)   # 0-100
    print(report.issues)  # list[Issue]
"""

from __future__ import annotations

__version__ = "0.2.0"
__author__ = "Anupama Garani"
__license__ = "MIT"

from ragpreflight.chunker import analyze_chunks
from ragpreflight.config import load_config
from ragpreflight.corpus import audit_corpus
from ragpreflight.models import (
    ChunkReport,
    CorpusReport,
    DocumentReport,
    Issue,
    IssueCategory,
    Severity,
)
from ragpreflight.profiles import PROFILES, get_profile
from ragpreflight.scanner import scan_document

__all__ = [
    "__version__",
    # Data models
    "DocumentReport",
    "ChunkReport",
    "CorpusReport",
    "Issue",
    "IssueCategory",
    "Severity",
    # Core functions
    "scan_document",
    "analyze_chunks",
    "audit_corpus",
    # Profiles
    "get_profile",
    "PROFILES",
    # Config
    "load_config",
]
