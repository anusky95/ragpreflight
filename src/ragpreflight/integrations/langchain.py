"""LangChain integration for ragpreflight.

Wraps ragpreflight's scanner as a LangChain DocumentLoader that gates ingestion
on a minimum readiness score. Documents below the threshold raise a ValueError
instead of being loaded.

Usage:
    from ragpreflight.integrations.langchain import RAGPreflightLoader

    loader = RAGPreflightLoader("document.pdf", min_score=70, profile="standard")
    docs = loader.load()   # raises ValueError if score < min_score

    # Or as a filter in a pipeline:
    loader = RAGPreflightLoader("document.pdf", min_score=60, raise_on_fail=False)
    docs = loader.load()   # returns [] if document fails quality check
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)


class RAGPreflightLoader:
    """A LangChain-compatible document loader that gates on ragpreflight score.

    Requires langchain-core to be installed:
        pip install langchain-core

    Args:
        file_path: Path to the document to load.
        min_score: Minimum acceptable readiness score (0–100). Default 60.
        profile: ragpreflight quality profile name. Default "standard".
        raise_on_fail: If True (default), raise ValueError on quality failure.
                       If False, return an empty list instead.
    """

    def __init__(
        self,
        file_path: str | Path,
        min_score: int = 60,
        profile: str = "standard",
        raise_on_fail: bool = True,
    ) -> None:
        self.file_path = Path(file_path)
        self.min_score = min_score
        self.profile = profile
        self.raise_on_fail = raise_on_fail

    def load(self) -> list:
        """Load and return documents if they pass the quality threshold.

        Returns:
            List of langchain Document objects.

        Raises:
            ImportError: If langchain-core is not installed.
            ValueError: If the document fails the quality threshold and
                        raise_on_fail is True.
        """
        try:
            from langchain_core.documents import Document  # type: ignore[import]
        except ImportError:
            raise ImportError(
                "langchain-core is required for RAGPreflightLoader. "
                "Install it with: pip install langchain-core"
            ) from None

        from ragpreflight import scan_document

        report = scan_document(self.file_path)

        if report.score < self.min_score:
            msg = (
                f"ragpreflight quality gate FAILED: '{self.file_path.name}' scored "
                f"{report.score}/100 (minimum {self.min_score}). "
                f"{len(report.critical_issues)} critical issue(s) detected."
            )
            if self.raise_on_fail:
                raise ValueError(msg)
            logger.warning(msg)
            return []

        # Attach ragpreflight metadata to each LangChain document
        metadata = {
            "source": str(self.file_path),
            "ragpreflight_score": report.score,
            "ragpreflight_issues": len(report.issues),
            "ragpreflight_critical": len(report.critical_issues),
            "file_format": report.file_format,
        }

        # Try to use an appropriate loader for the actual content
        try:
            return list(self._load_content(Document, metadata))
        except Exception as exc:
            logger.warning("Could not load content from '%s': %s", self.file_path, exc)
            return []

    def lazy_load(self) -> Iterator:
        """Lazy load interface for LangChain compatibility."""
        yield from self.load()

    def _load_content(self, doc_class: type, metadata: dict) -> Iterator:
        """Extract text and yield LangChain Documents."""
        suffix = self.file_path.suffix.lower()

        if suffix == ".pdf":
            try:
                import pymupdf as fitz  # type: ignore[import]

                doc = fitz.open(str(self.file_path))
                for page_num in range(doc.page_count):
                    page_text = doc[page_num].get_text("text")
                    if page_text.strip():
                        page_meta = dict(metadata, page=page_num + 1)
                        yield doc_class(page_content=page_text, metadata=page_meta)
                doc.close()
                return
            except ImportError:
                pass

        # Fallback: read as plain text
        try:
            from ragpreflight.utils import safe_read_text

            text, _ = safe_read_text(self.file_path)
            yield doc_class(page_content=text, metadata=metadata)
        except Exception as exc:
            raise ValueError(f"Cannot read '{self.file_path}': {exc}") from exc
