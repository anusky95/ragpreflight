"""Utility functions: file detection, encoding helpers, size guards."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from charset_normalizer import from_bytes  # replaces chardet — faster, more accurate

from ragpreflight._constants import (
    BYTES_PER_MB,
    DEFAULT_MAX_FILE_SIZE_MB,
    HARD_MAX_FILE_SIZE_MB,
    MIN_ENCODING_CONFIDENCE,
    SUPPORTED_EXTENSIONS,
)

logger = logging.getLogger(__name__)


def detect_file_format(path: str | Path) -> str:
    """Detect file format by extension (and optionally magic bytes).

    Args:
        path: Path to the file.

    Returns:
        Lowercase format string, e.g. 'pdf', 'docx', 'pptx', 'xlsx'.
        Returns 'unknown' if the extension is not recognised.
    """
    suffix = Path(path).suffix.lower()
    mapping = {
        ".pdf": "pdf",
        ".docx": "docx",
        ".txt": "txt",
        ".csv": "csv",
        ".tsv": "tsv",
        ".html": "html",
        ".htm": "html",
        ".md": "markdown",
        ".markdown": "markdown",
        ".pptx": "pptx",
        ".xlsx": "xlsx",
        ".xls": "xlsx",
        ".ipynb": "ipynb",
        ".srt": "srt",
        ".vtt": "vtt",
    }
    fmt = mapping.get(suffix, "unknown")
    if fmt == "unknown":
        # Magic bytes fallback for PDF
        try:
            with open(path, "rb") as fh:
                header = fh.read(4)
            if header == b"%PDF":
                return "pdf"
        except OSError:
            pass
    return fmt


def is_supported(path: str | Path) -> bool:
    """Return True if the file format is supported by RAGCheck.

    Args:
        path: Path to the file.

    Returns:
        True if the extension is in the supported set.
    """
    return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS


def check_file_size(
    path: str | Path,
    max_mb: float = DEFAULT_MAX_FILE_SIZE_MB,
    hard_max_mb: float = HARD_MAX_FILE_SIZE_MB,
) -> tuple[int, str | None]:
    """Check file size and return (size_bytes, warning_message_or_None).

    Args:
        path: Path to the file.
        max_mb: Warn (but continue) above this size in megabytes.
        hard_max_mb: Refuse to process above this size in megabytes.

    Returns:
        Tuple of (file_size_bytes, warning_or_none).

    Raises:
        ValueError: If the file exceeds the hard maximum.
        FileNotFoundError: If the file does not exist.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {path}")

    size_bytes = p.stat().st_size
    size_mb = size_bytes / BYTES_PER_MB

    if size_mb > hard_max_mb:
        raise ValueError(
            f"File '{path}' is {size_mb:.1f} MB, which exceeds the hard maximum of "
            f"{hard_max_mb} MB. To raise this limit, pass max_file_size_mb= to the "
            f"scan function."
        )

    if size_mb > max_mb:
        return size_bytes, (
            f"File '{path}' is {size_mb:.1f} MB — this may cause slow processing. "
            f"Consider splitting large documents before ingestion."
        )

    return size_bytes, None


def detect_encoding(raw_bytes: bytes) -> tuple[str, float]:
    """Detect character encoding and confidence using charset-normalizer.

    Args:
        raw_bytes: Raw bytes sampled from the file (up to 64 KB is sufficient).

    Returns:
        Tuple of (encoding_name, confidence) where confidence is 0.0–1.0.
    """
    results = from_bytes(raw_bytes)
    best = results.best()
    if best is None:
        return "unknown", 0.0
    # charset-normalizer chaos score: 0.0 = clean, 1.0 = garbage.
    confidence = max(0.0, 1.0 - float(best.chaos))
    return str(best.encoding), confidence


def encoding_is_suspicious(confidence: float) -> bool:
    """Return True if encoding confidence is below the acceptable threshold.

    Args:
        confidence: Confidence value between 0.0 and 1.0.

    Returns:
        True if encoding is potentially wrong.
    """
    return confidence < MIN_ENCODING_CONFIDENCE


def safe_read_text(path: str | Path, max_bytes: int = 5 * 1024 * 1024) -> tuple[str, str]:
    """Safely read a text file, detecting encoding automatically.

    Args:
        path: Path to the text file.
        max_bytes: Maximum bytes to read (default 5 MB).

    Returns:
        Tuple of (text_content, detected_encoding).
    """
    with open(path, "rb") as fh:
        raw = fh.read(max_bytes)

    encoding, confidence = detect_encoding(raw)
    if encoding == "unknown":
        encoding = "utf-8"

    try:
        text = raw.decode(encoding, errors="replace")
    except (UnicodeDecodeError, LookupError):
        text = raw.decode("utf-8", errors="replace")
        encoding = "utf-8"

    if encoding_is_suspicious(confidence):
        logger.debug(
            "Low encoding confidence (%.2f) for %s — decoded as %s", confidence, path, encoding
        )

    return text, encoding


def iter_supported_files(directory: str | Path) -> list[Path]:
    """Walk a directory and return all supported file paths.

    Skips hidden files and directories (prefixed with '.').

    Args:
        directory: Root directory to walk.

    Returns:
        Sorted list of Path objects for supported files.
    """
    root = Path(directory)
    if not root.is_dir():
        raise NotADirectoryError(f"Not a directory: {directory}")

    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip hidden directories in-place
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fname in filenames:
            if fname.startswith("."):
                continue
            fpath = Path(dirpath) / fname
            if is_supported(fpath):
                found.append(fpath)

    return sorted(found)


def truncate_text(text: str, max_chars: int = 100) -> str:
    """Return first max_chars characters of text, stripping newlines.

    Args:
        text: Source text.
        max_chars: Maximum characters to return.

    Returns:
        Truncated single-line preview.
    """
    preview = " ".join(text.split())
    return preview[:max_chars]


def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp a float between lo and hi.

    Args:
        value: Input value.
        lo: Lower bound (default 0.0).
        hi: Upper bound (default 1.0).

    Returns:
        Clamped value.
    """
    return max(lo, min(hi, value))
