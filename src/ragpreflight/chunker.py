"""Chunk analysis module for ragpreflight.

Analyses chunked text for coherence, structural breaks, and boundary quality.

Usage:
    from ragpreflight.chunker import analyze_chunks

    reports = analyze_chunks("document.pdf", chunk_size=512, overlap=50)
    for r in reports:
        print(r.chunk_index, r.coherence_score, r.issues)
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path

from ragpreflight._constants import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    STRUCTURAL_BREAK_PATTERNS,
)
from ragpreflight.models import ChunkReport, Issue, IssueCategory, Severity
from ragpreflight.scanner import scan_document
from ragpreflight.utils import clamp, truncate_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_chunks(
    filepath: str | Path,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    strategy: str = "recursive",
    splitter: Callable[[str], list[str]] | None = None,
    profile: dict | None = None,
) -> list[ChunkReport]:
    """Analyse document chunks for RAG readiness.

    Args:
        filepath: Path to the document.
        chunk_size: Target chunk size in characters (default 512).
        overlap: Number of overlapping characters between chunks (default 50).
        strategy: Chunking strategy name — 'recursive', 'sentence', or 'paragraph'.
                  Ignored if ``splitter`` is provided.
        splitter: Optional custom split function ``(text) -> [chunk, ...]``.
                  If provided, ``strategy``, ``chunk_size``, and ``overlap`` are ignored.
        profile: Optional threshold profile dict (from ``ragpreflight.profiles``).

    Returns:
        List of ChunkReport objects, one per chunk.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file format is unsupported.
    """
    path = Path(filepath)
    doc_report = scan_document(path)

    full_text = _extract_text(path, doc_report.file_format)
    if not full_text.strip():
        return [
            ChunkReport(
                chunk_index=0,
                text_preview="(empty)",
                coherence_score=None,
                coherence_status="not_evaluated",
                issues=[
                    Issue(
                        category=IssueCategory.CONTENT,
                        severity=Severity.CRITICAL,
                        message="Document has no extractable text — cannot produce chunks.",
                        suggestion="Fix document extractability first (run ragpreflight scan).",
                    )
                ],
            )
        ]

    if splitter is not None:
        chunks = splitter(full_text)
    else:
        chunks = _split(full_text, chunk_size=chunk_size, overlap=overlap, strategy=strategy)

    if not chunks:
        return []

    # Try loading sentence-transformers for coherence scoring
    embed_fn = _get_embedder()
    min_coherence = (profile or {}).get("min_chunk_coherence", 0.65)

    reports: list[ChunkReport] = []
    for i, chunk in enumerate(chunks):
        chunk_issues: list[Issue] = []

        # 1. Structural break detection
        _check_structural_breaks(chunk, i, chunk_issues)

        # 2. Boundary quality
        _check_boundary_quality(chunk, i, chunk_issues)

        # 3. Information density
        density = _info_density(chunk)
        if density < 0.3:
            chunk_issues.append(
                Issue(
                    category=IssueCategory.CHUNKING,
                    severity=Severity.WARNING,
                    message=f"Chunk {i}: low information density ({density:.0%}) — mostly stopwords or whitespace.",
                    location=f"chunk {i}",
                    suggestion="Adjust chunk boundaries or increase chunk size.",
                )
            )

        # 4. Coherence scoring (requires sentence-transformers)
        coherence = _score_coherence(chunk, embed_fn)

        if coherence is not None and coherence < min_coherence:
            chunk_issues.append(
                Issue(
                    category=IssueCategory.CHUNKING,
                    severity=Severity.WARNING,
                    message=f"Chunk {i}: low semantic coherence ({coherence:.2f} < {min_coherence}).",
                    location=f"chunk {i}",
                    suggestion=(
                        "This chunk may mix unrelated topics. Consider adjusting chunk boundaries "
                        "or using a semantic/sentence chunking strategy."
                    ),
                )
            )

        reports.append(
            ChunkReport(
                chunk_index=i,
                text_preview=truncate_text(chunk),
                coherence_score=coherence,
                coherence_status="evaluated" if coherence is not None else "not_evaluated",
                issues=chunk_issues,
            )
        )

    # 5. Semantic boundary analysis across adjacent chunks
    if embed_fn is not None and len(chunks) >= 2:
        boundary_issues = _check_semantic_boundaries(chunks, embed_fn)
        if boundary_issues:
            # Attach boundary issues to the corpus-level summary on the first chunk
            reports[0].issues.extend(boundary_issues)

    return reports


# ---------------------------------------------------------------------------
# Text extraction helper (reuses scanner internals)
# ---------------------------------------------------------------------------


def _extract_text(path: Path, fmt: str) -> str:
    """Extract full text from a document for chunking.

    Args:
        path: Path to the document.
        fmt: File format string (e.g. 'pdf', 'docx').

    Returns:
        Plain text content.
    """
    if fmt == "pdf":
        return _extract_pdf_text(path)
    if fmt == "docx":
        return _extract_docx_text(path)
    if fmt in ("txt", "csv", "tsv", "markdown"):
        from ragpreflight.utils import safe_read_text

        text, _ = safe_read_text(path)
        return text
    if fmt == "html":
        return _extract_html_text(path)
    from ragpreflight.utils import safe_read_text

    text, _ = safe_read_text(path)
    return text


def _extract_pdf_text(path: Path) -> str:
    try:
        import pymupdf as fitz  # type: ignore[import]

        doc = fitz.open(str(path))
        pages = [doc[i].get_text("text") for i in range(doc.page_count)]
        doc.close()
        return "\n".join(pages)
    except Exception as exc:
        logger.debug("PDF text extraction failed: %s", exc)
        return ""


def _extract_docx_text(path: Path) -> str:
    try:
        from docx import Document  # type: ignore[import]

        doc = Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception as exc:
        logger.debug("DOCX text extraction failed: %s", exc)
        return ""


def _extract_html_text(path: Path) -> str:
    try:
        from bs4 import BeautifulSoup  # type: ignore[import]

        from ragpreflight.utils import safe_read_text

        text, _ = safe_read_text(path)
        soup = BeautifulSoup(text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        return soup.get_text(separator=" ", strip=True)
    except Exception as exc:
        logger.debug("HTML text extraction failed: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Chunking strategies
# ---------------------------------------------------------------------------


def _split(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    strategy: str = "recursive",
) -> list[str]:
    """Split text into chunks using the selected strategy.

    Args:
        text: Full document text.
        chunk_size: Target chunk size in characters.
        overlap: Character overlap between adjacent chunks.
        strategy: 'recursive', 'sentence', or 'paragraph'.

    Returns:
        List of text chunks.
    """
    strategy = strategy.lower()
    if strategy == "sentence":
        return _split_sentences(text, chunk_size, overlap)
    if strategy == "paragraph":
        return _split_paragraphs(text, chunk_size, overlap)
    return _split_recursive(text, chunk_size, overlap)


def _split_recursive(
    text: str,
    chunk_size: int,
    overlap: int,
    separators: list[str] | None = None,
) -> list[str]:
    """Recursive character splitter — tries to respect sentence and paragraph boundaries.

    Args:
        text: Source text.
        chunk_size: Target size in characters.
        overlap: Overlap in characters.
        separators: Ordered list of separator strings to try. Defaults to a
                    sensible set that respects paragraphs, sentences, and words.

    Returns:
        List of chunks.
    """
    _seps = separators if separators is not None else ["\n\n", "\n", ". ", "! ", "? ", " ", ""]
    return _recursive_split(text, chunk_size, overlap, _seps)


def _recursive_split(text: str, chunk_size: int, overlap: int, separators: list[str]) -> list[str]:
    if len(text) <= chunk_size:
        return [text] if text.strip() else []

    sep = separators[0] if separators else ""
    remaining_seps = separators[1:] if len(separators) > 1 else []

    if sep and sep in text:
        parts = text.split(sep)
        chunks: list[str] = []
        current = ""
        for part in parts:
            candidate = (current + sep + part).strip() if current else part.strip()
            if len(candidate) <= chunk_size:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                if len(part) > chunk_size and remaining_seps:
                    chunks.extend(_recursive_split(part, chunk_size, overlap, remaining_seps))
                else:
                    current = part.strip()
        if current:
            chunks.append(current)
        # Apply overlap
        return _apply_overlap(chunks, overlap)

    # Fallback: hard split
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end])
        start = end - overlap if overlap else end
    return [c for c in chunks if c.strip()]


def _apply_overlap(chunks: list[str], overlap: int) -> list[str]:
    """Add overlap between consecutive chunks.

    Args:
        chunks: Non-overlapping chunks.
        overlap: Number of characters to prepend from the previous chunk.

    Returns:
        Chunks with overlap applied.
    """
    if overlap <= 0 or len(chunks) <= 1:
        return chunks
    result = [chunks[0]]
    for i in range(1, len(chunks)):
        tail = chunks[i - 1][-overlap:]
        result.append(tail + " " + chunks[i])
    return result


def _split_sentences(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split on sentence boundaries.

    Args:
        text: Source text.
        chunk_size: Target chunk size in characters.
        overlap: Overlap in characters.

    Returns:
        List of chunks.
    """
    sentence_endings = re.compile(r"(?<=[.!?])\s+")
    sentences = sentence_endings.split(text)

    chunks: list[str] = []
    current = ""
    for sent in sentences:
        if len(current) + len(sent) + 1 <= chunk_size:
            current = (current + " " + sent).strip()
        else:
            if current:
                chunks.append(current)
            current = sent.strip()
    if current:
        chunks.append(current)

    return _apply_overlap(chunks, overlap)


def _split_paragraphs(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split on paragraph boundaries (\n\n).

    Args:
        text: Source text.
        chunk_size: Target chunk size in characters.
        overlap: Overlap in characters.

    Returns:
        List of chunks.
    """
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 2 <= chunk_size:
            current = (current + "\n\n" + para).strip()
        else:
            if current:
                chunks.append(current)
            if len(para) > chunk_size:
                # Split long paragraphs further
                chunks.extend(_split_recursive(para, chunk_size, overlap))
                current = ""
            else:
                current = para
    if current:
        chunks.append(current)

    return _apply_overlap(chunks, overlap)


# ---------------------------------------------------------------------------
# Chunk quality checks
# ---------------------------------------------------------------------------


def _check_structural_breaks(chunk: str, index: int, issues: list[Issue]) -> None:
    """Detect if the chunk cuts through a structural element.

    Args:
        chunk: Chunk text.
        index: Chunk index (for reporting).
        issues: Issues list to mutate.
    """
    for pattern in STRUCTURAL_BREAK_PATTERNS:
        if re.search(pattern, chunk, re.MULTILINE):
            issues.append(
                Issue(
                    category=IssueCategory.CHUNKING,
                    severity=Severity.WARNING,
                    message=f"Chunk {index}: contains a structural element boundary (table/list/code).",
                    location=f"chunk {index}",
                    suggestion=(
                        "Adjust chunk boundaries to avoid splitting tables, lists, or code blocks. "
                        "Use a structure-aware splitter."
                    ),
                )
            )
            return  # One report per chunk is enough


def _check_boundary_quality(chunk: str, index: int, issues: list[Issue]) -> None:
    """Check if the chunk starts or ends mid-sentence.

    Args:
        chunk: Chunk text.
        index: Chunk index (for reporting).
        issues: Issues list to mutate.
    """
    stripped = chunk.strip()
    if not stripped:
        return

    # Starts mid-sentence: first char is lowercase (not a list item or code)
    first_char = stripped[0]
    if first_char.islower():
        issues.append(
            Issue(
                category=IssueCategory.CHUNKING,
                severity=Severity.INFO,
                message=f"Chunk {index}: starts with a lowercase character — may be cut mid-sentence.",
                location=f"chunk {index}",
                suggestion="Increase overlap or use sentence-aware chunking.",
            )
        )

    # Ends without sentence terminator
    last_char = stripped[-1]
    if last_char not in ".!?\"'":
        issues.append(
            Issue(
                category=IssueCategory.CHUNKING,
                severity=Severity.INFO,
                message=f"Chunk {index}: does not end with sentence punctuation — may be cut mid-sentence.",
                location=f"chunk {index}",
                suggestion="Use sentence-level chunking or increase overlap.",
            )
        )


def _info_density(chunk: str) -> float:
    """Compute information density as ratio of non-stopword tokens.

    Args:
        chunk: Chunk text.

    Returns:
        Float in [0.0, 1.0].
    """
    # Basic English stopword list (no external dependency)
    stopwords = {
        "the",
        "a",
        "an",
        "is",
        "it",
        "in",
        "on",
        "at",
        "to",
        "of",
        "and",
        "or",
        "for",
        "with",
        "as",
        "by",
        "from",
        "that",
        "this",
        "be",
        "are",
        "was",
        "were",
        "has",
        "have",
        "had",
        "not",
        "but",
        "if",
        "its",
        "can",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "do",
        "did",
        "does",
        "so",
        "up",
        "out",
        "no",
        "we",
        "he",
        "she",
        "they",
        "their",
        "our",
        "your",
        "my",
        "his",
        "her",
        "i",
        "you",
        "us",
        "me",
        "him",
        "them",
    }
    tokens = re.findall(r"\b[a-zA-Z]+\b", chunk.lower())
    if not tokens:
        return 0.0
    meaningful = sum(1 for t in tokens if t not in stopwords and len(t) > 2)
    return clamp(meaningful / len(tokens))


# ---------------------------------------------------------------------------
# Coherence scoring (optional: sentence-transformers)
# ---------------------------------------------------------------------------

_embedder_cache: object | None = None
_embedder_tried = False


def _get_embedder() -> object | None:
    """Return a sentence-transformers model, or None if not installed.

    Returns:
        SentenceTransformer model instance, or None.
    """
    global _embedder_cache, _embedder_tried
    if _embedder_tried:
        return _embedder_cache
    _embedder_tried = True
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore[import]

        _embedder_cache = SentenceTransformer("all-MiniLM-L6-v2")
        logger.debug("Loaded sentence-transformers for coherence scoring.")
    except ImportError:
        logger.info(
            "sentence-transformers not installed. Coherence scoring disabled. "
            "Install with: pip install ragpreflight[full]"
        )
        _embedder_cache = None
    except Exception as exc:
        logger.warning("Could not load sentence-transformers model: %s", exc)
        _embedder_cache = None
    return _embedder_cache


def _score_coherence(chunk: str, embedder: object | None) -> float | None:
    """Compute semantic coherence of a chunk.

    Splits the chunk into sentences, embeds each, and computes mean pairwise
    cosine similarity. Returns None when embedder is unavailable — never returns
    a synthetic 1.0 sentinel that would mislead callers.

    Args:
        chunk: Chunk text.
        embedder: SentenceTransformer model (or None to skip).

    Returns:
        Coherence score 0.0–1.0, or None when evaluation is not possible.
    """
    if embedder is None:
        return None

    sentences = re.split(r"(?<=[.!?])\s+", chunk.strip())
    sentences = [s for s in sentences if len(s.split()) >= 3]

    if len(sentences) < 2:
        return 1.0

    try:
        import numpy as np  # type: ignore[import]

        embeddings = embedder.encode(sentences, show_progress_bar=False)  # type: ignore[attr-defined]
        # Normalise
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        normalised = embeddings / norms
        # Mean pairwise cosine similarity (upper triangle)
        sim_matrix = normalised @ normalised.T
        n = len(sentences)
        if n == 1:
            return 1.0
        total = 0.0
        count = 0
        for i in range(n):
            for j in range(i + 1, n):
                total += float(sim_matrix[i, j])
                count += 1
        return clamp(total / count if count > 0 else 1.0)
    except Exception as exc:
        logger.debug("Coherence scoring failed: %s", exc)
        return None


def _check_semantic_boundaries(chunks: list[str], embedder: object) -> list[Issue]:
    """Detect poor semantic chunk boundaries by comparing adjacent chunk embeddings.

    A very high similarity between adjacent chunks (>0.92) suggests the split was
    unnecessary — they cover the same micro-topic and should be merged. A very low
    similarity (<0.15) between adjacent chunks suggests a hard topic break landed
    mid-sentence, which fixed-size chunking often causes.

    Args:
        chunks: List of chunk text strings.
        embedder: SentenceTransformer model instance.

    Returns:
        List of Issues summarising boundary quality problems.
    """
    try:
        import numpy as np  # type: ignore[import]

        # Use only the last ~200 chars of chunk N and first ~200 chars of chunk N+1
        # to measure the boundary region specifically
        boundary_texts = []
        for i in range(len(chunks) - 1):
            tail = chunks[i][-200:].strip()
            head = chunks[i + 1][:200].strip()
            boundary_texts.append(tail)
            boundary_texts.append(head)

        embeddings = embedder.encode(boundary_texts, show_progress_bar=False)  # type: ignore[attr-defined]
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        embeddings = embeddings / norms

        over_split: list[int] = []  # adjacent chunks too similar
        hard_break: list[int] = []  # adjacent chunks too different

        for i in range(len(chunks) - 1):
            tail_emb = embeddings[i * 2]
            head_emb = embeddings[i * 2 + 1]
            sim = float(tail_emb @ head_emb)
            if sim > 0.92:
                over_split.append(i)
            elif sim < 0.15:
                hard_break.append(i)

        issues: list[Issue] = []
        if over_split:
            issues.append(
                Issue(
                    category=IssueCategory.CHUNKING,
                    severity=Severity.INFO,
                    message=(
                        f"{len(over_split)} adjacent chunk pair(s) are highly similar "
                        f"(cosine > 0.92) — these chunks may be over-split."
                    ),
                    suggestion=(
                        "Consider increasing chunk size or using paragraph-based splitting "
                        "to avoid splitting tightly related sentences."
                    ),
                )
            )
        if hard_break:
            issues.append(
                Issue(
                    category=IssueCategory.CHUNKING,
                    severity=Severity.WARNING,
                    message=(
                        f"{len(hard_break)} chunk boundary/ies land at a hard topic break "
                        f"(cosine < 0.15) — the split separates semantically unrelated content."
                    ),
                    suggestion=(
                        "Use semantic chunking (split on embedding similarity drops) or "
                        "paragraph-based splitting to align chunk boundaries with topic changes."
                    ),
                )
            )
        return issues
    except Exception as exc:
        logger.debug("Semantic boundary check failed: %s", exc)
        return []
