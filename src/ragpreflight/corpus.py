"""Corpus-level analysis for ragpreflight.

Scans a directory of documents, detects near-duplicates, temporal staleness,
contradiction candidates, and generates aggregate statistics.

Usage:
    from ragpreflight.corpus import audit_corpus

    report = audit_corpus("./knowledge_base/")
    print(report.average_score)
    print(report.duplicate_groups)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from ragpreflight._constants import DEFAULT_STALE_MONTHS
from ragpreflight.models import CorpusReport, DocumentReport, Issue, IssueCategory, Severity
from ragpreflight.scanner import scan_document
from ragpreflight.utils import iter_supported_files

logger = logging.getLogger(__name__)

_CACHE_FILE = ".ragpreflight_cache.json"

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def audit_corpus(
    directory: str | Path,
    profile: dict | None = None,
    max_file_size_mb: float = 100.0,
    stale_months: int = DEFAULT_STALE_MONTHS,
    show_progress: bool = True,
    parallel: bool = True,
    max_workers: int = 4,
    use_cache: bool = False,
    detect_contradictions: bool = False,
) -> CorpusReport:
    """Audit an entire directory of documents.

    Args:
        directory: Root directory to walk for supported documents.
        profile: Optional threshold profile dict (from ``ragpreflight.profiles``).
        max_file_size_mb: Skip files larger than this (MB).
        stale_months: Flag documents older than this many months.
        show_progress: Show a rich progress bar for directories with 5+ files.
        parallel: Scan files concurrently using a thread pool (default True).
        max_workers: Thread pool size when parallel=True (default 4).
        use_cache: Re-use cached scan results for unchanged files (by mtime+size).
        detect_contradictions: Surface conflict candidate pairs — document pairs with
            high embedding similarity that warrant human review for conflicting content.
            Does not detect or confirm contradictions (requires sentence-transformers).

    Returns:
        CorpusReport with per-document results and cross-document issues.

    Raises:
        NotADirectoryError: If the path is not a directory.
    """
    root = Path(directory).resolve()
    if not root.is_dir():
        raise NotADirectoryError(
            f"'{directory}' is not a directory. Use scan_document() to scan a single file."
        )

    files = iter_supported_files(root)
    if not files:
        return CorpusReport(
            directory=str(directory),
            total_documents=0,
            average_score=0.0,
            corpus_issues=[
                Issue(
                    category=IssueCategory.CONTENT,
                    severity=Severity.WARNING,
                    message="No supported documents found in the directory.",
                    location=str(directory),
                    suggestion=(
                        "Ensure the directory contains .pdf, .docx, .txt, .csv, .html, "
                        ".md, .pptx, .xlsx, .ipynb, or .srt/.vtt files."
                    ),
                )
            ],
        )

    # Load incremental cache
    cache = _load_cache(root) if use_cache else {}

    doc_reports: list[DocumentReport] = []
    use_progress = show_progress and len(files) >= 5

    if parallel and len(files) > 1:
        doc_reports = _scan_parallel(files, max_file_size_mb, max_workers, cache, use_progress)
    else:
        doc_reports = _scan_sequential(files, max_file_size_mb, cache, use_progress)

    if use_cache:
        _save_cache(root, cache, doc_reports, files)

    corpus_issues: list[Issue] = []
    duplicate_groups: list[list[str]] = []

    # Near-duplicate detection
    dup_groups = _find_near_duplicates(doc_reports)
    duplicate_groups = [[r.filepath for r in g] for g in dup_groups]
    for group in dup_groups:
        paths = [r.filepath for r in group]
        corpus_issues.append(
            Issue(
                category=IssueCategory.DUPLICATION,
                severity=Severity.WARNING,
                message=f"Near-duplicate group detected ({len(group)} documents).",
                location=", ".join(Path(p).name for p in paths),
                suggestion=(
                    "Review and deduplicate these documents before ingestion. "
                    "Near-duplicates inflate retrieval noise and waste embedding budget."
                ),
            )
        )

    # Conflict candidate pairs (optional, requires sentence-transformers)
    if detect_contradictions:
        conflict_candidate_pairs = _find_conflict_candidates(doc_reports)
        for p1, p2 in conflict_candidate_pairs:
            corpus_issues.append(
                Issue(
                    category=IssueCategory.CONTENT,
                    severity=Severity.WARNING,
                    message=(
                        "Conflict candidate: two documents have high embedding similarity "
                        "and may cover the same topic with conflicting information. "
                        "This does not establish that a contradiction exists — human review required."
                    ),
                    location=f"{Path(p1).name}  ↔  {Path(p2).name}",
                    suggestion=(
                        "Review these documents for factual conflicts before ingestion. "
                        "Conflicting documents can cause inconsistent retrieval results."
                    ),
                )
            )

    # File-age freshness proxy (NOT proof of content staleness)
    stale_docs = _find_stale_documents(doc_reports, stale_months)
    if stale_docs:
        corpus_issues.append(
            Issue(
                category=IssueCategory.STALENESS,
                severity=Severity.INFO,
                message=(
                    f"File-age freshness proxy: {len(stale_docs)} file(s) have not been modified "
                    f"in over {stale_months} months. Content staleness is not established — "
                    f"filesystem mtime does not prove the content is outdated."
                ),
                location=", ".join(Path(r.filepath).name for r in stale_docs[:5]),
                suggestion=(
                    "Review these files to determine whether the content is still current. "
                    "File modification time alone cannot confirm content freshness."
                ),
            )
        )

    # Format distribution
    fmt_counts: dict[str, int] = {}
    for r in doc_reports:
        fmt_counts[r.file_format] = fmt_counts.get(r.file_format, 0) + 1
    logger.debug("Format distribution: %s", fmt_counts)

    # Low-scoring corpus alert
    scores = [r.score for r in doc_reports]
    average_score = sum(scores) / len(scores) if scores else 0.0
    min_score = (profile or {}).get("min_document_score", 60)

    failing = [r for r in doc_reports if r.score < min_score]
    if failing:
        corpus_issues.append(
            Issue(
                category=IssueCategory.CONTENT,
                severity=Severity.WARNING,
                message=(
                    f"{len(failing)}/{len(doc_reports)} documents score below the "
                    f"profile threshold ({min_score})."
                ),
                suggestion=(
                    "Remediate low-scoring documents before ingestion. "
                    "Run `ragpreflight scan <file>` on each for detailed guidance."
                ),
            )
        )

    return CorpusReport(
        directory=str(directory),
        total_documents=len(doc_reports),
        average_score=average_score,
        documents=doc_reports,
        corpus_issues=corpus_issues,
        duplicate_groups=duplicate_groups,
    )


# ---------------------------------------------------------------------------
# Parallel / sequential scanning helpers
# ---------------------------------------------------------------------------


def _scan_parallel(
    files: list[Path],
    max_file_size_mb: float,
    max_workers: int,
    cache: dict,
    show_progress: bool,
) -> list[DocumentReport]:
    """Scan files using a thread pool for speed on large corpora."""
    results: list[DocumentReport] = []
    pending: list[Path] = []

    # Serve cached results immediately
    for fpath in files:
        key = _cache_key(fpath)
        if key in cache:
            results.append(_deserialize_report(cache[key]))
        else:
            pending.append(fpath)

    if not pending:
        return results

    if show_progress and len(files) >= 5:
        try:
            from rich.progress import (
                BarColumn,
                Progress,
                SpinnerColumn,
                TaskProgressColumn,
                TextColumn,
            )

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
            ) as progress:
                task = progress.add_task("Scanning documents...", total=len(pending))
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {
                        executor.submit(_safe_scan, fpath, max_file_size_mb): fpath
                        for fpath in pending
                    }
                    for future in as_completed(futures):
                        results.append(future.result())
                        progress.advance(task)
            return results
        except ImportError:
            pass

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_safe_scan, fpath, max_file_size_mb): fpath for fpath in pending
        }
        for future in as_completed(futures):
            results.append(future.result())

    return results


def _scan_sequential(
    files: list[Path],
    max_file_size_mb: float,
    cache: dict,
    show_progress: bool,
) -> list[DocumentReport]:
    """Scan files one-by-one, serving from cache where available."""
    results: list[DocumentReport] = []
    pending = [f for f in files if _cache_key(f) not in cache]
    cached = [f for f in files if _cache_key(f) in cache]

    for fpath in cached:
        results.append(_deserialize_report(cache[_cache_key(fpath)]))

    if not pending:
        return results

    use_progress = show_progress and len(files) >= 5
    if use_progress:
        try:
            from rich.progress import (
                BarColumn,
                Progress,
                SpinnerColumn,
                TaskProgressColumn,
                TextColumn,
            )

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
            ) as progress:
                task = progress.add_task("Scanning documents...", total=len(pending))
                for fpath in pending:
                    results.append(_safe_scan(fpath, max_file_size_mb))
                    progress.advance(task)
            return results
        except ImportError:
            pass

    for fpath in pending:
        results.append(_safe_scan(fpath, max_file_size_mb))

    return results


# ---------------------------------------------------------------------------
# Incremental cache helpers
# ---------------------------------------------------------------------------


def _cache_key(path: Path) -> str:
    """Derive a cache key from file path + mtime + size."""
    try:
        stat = path.stat()
        return hashlib.md5(f"{path}:{stat.st_mtime}:{stat.st_size}".encode()).hexdigest()
    except OSError:
        return str(path)


def _load_cache(directory: Path) -> dict:
    """Load the incremental scan cache for a directory."""
    cache_path = directory / _CACHE_FILE
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_cache(
    directory: Path,
    existing_cache: dict,
    reports: list[DocumentReport],
    files: list[Path],
) -> None:
    """Persist scan results to the incremental cache."""
    updated = dict(existing_cache)
    path_to_key = {fpath: _cache_key(fpath) for fpath in files}
    path_to_report = {r.filepath: r for r in reports}

    for fpath in files:
        fp_str = str(fpath)
        if fp_str in path_to_report:
            updated[path_to_key[fpath]] = _serialize_report(path_to_report[fp_str])

    cache_path = directory / _CACHE_FILE
    import contextlib

    with contextlib.suppress(OSError):
        cache_path.write_text(json.dumps(updated, indent=2), encoding="utf-8")


def _serialize_report(report: DocumentReport) -> dict:
    """Serialize a DocumentReport to a cache-friendly dict."""
    return report.to_dict()


def _deserialize_report(data: dict) -> DocumentReport:
    """Deserialize a DocumentReport from cached dict data."""
    from ragpreflight.models import Issue, IssueCategory, Severity

    issues = [
        Issue(
            category=IssueCategory(i["category"]),
            severity=Severity(i["severity"]),
            message=i["message"],
            location=i.get("location"),
            suggestion=i.get("suggestion"),
        )
        for i in data.get("issues", [])
    ]
    return DocumentReport(
        filepath=data["filepath"],
        score=data["score"],
        issues=issues,
        page_count=data.get("page_count", 0),
        text_extractable_ratio=data.get("text_extractable_ratio", 0.0),
        file_format=data.get("file_format", ""),
        file_size_bytes=data.get("file_size_bytes", 0),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_scan(path: Path, max_file_size_mb: float) -> DocumentReport:
    """Scan a file without crashing the corpus audit.

    Args:
        path: File to scan.
        max_file_size_mb: Maximum file size in MB.

    Returns:
        DocumentReport — returns a score-0 error report on failure.
    """
    try:
        return scan_document(path, max_file_size_mb=max_file_size_mb)
    except Exception as exc:
        logger.warning("Failed to scan '%s': %s", path, exc)
        return DocumentReport(
            filepath=str(path),
            score=0,
            issues=[
                Issue(
                    category=IssueCategory.CONTENT,
                    severity=Severity.CRITICAL,
                    message=f"Scan failed: {exc}",
                    location=path.name,
                    suggestion="Verify the file is readable and not corrupted.",
                )
            ],
            file_format="unknown",
        )


def _find_near_duplicates(
    reports: list[DocumentReport],
    similarity_threshold: float = 0.85,
) -> list[list[DocumentReport]]:
    """Detect near-duplicate documents using MinHash LSH if available, else shingling.

    Args:
        reports: All document reports.
        similarity_threshold: Jaccard similarity above which docs are duplicates.

    Returns:
        List of duplicate groups (each group is a list of DocumentReport).
    """
    if len(reports) < 2:
        return []

    try:
        # datasketch >=2.0.0 changed default permutation scheme to 'affine32'.
        # We create all MinHash objects fresh each run so there's no cross-version
        # pickling issue — just import and use the current API.
        return _minhash_duplicates(reports, similarity_threshold)
    except ImportError:
        logger.debug(
            "datasketch not installed. Using basic shingle dedup. "
            "For faster dedup on large corpora: pip install ragpreflight[full]"
        )

    return _shingle_duplicates(reports, similarity_threshold)


def _find_conflict_candidates(
    reports: list[DocumentReport],
    similarity_threshold: float = 0.85,
) -> list[tuple[str, str]]:
    """Find document pairs with high embedding similarity that warrant conflict review.

    Uses sentence-transformers to embed each document, then surfaces pairs with
    cosine similarity above the threshold for human review. Does NOT detect or
    confirm contradictions — only surfaces candidates for domain-expert review.

    Args:
        reports: Document reports to compare.
        similarity_threshold: Embedding cosine similarity above which docs are
            flagged as conflict candidates.

    Returns:
        List of (filepath1, filepath2) pairs for human review.
    """
    try:
        import numpy as np  # type: ignore[import]
        from sentence_transformers import SentenceTransformer  # type: ignore[import]
    except ImportError:
        logger.debug(
            "sentence-transformers not installed. Conflict candidate detection skipped. "
            "Install with: pip install ragpreflight[full]"
        )
        return []

    from ragpreflight.utils import safe_read_text

    texts: list[str] = []
    paths: list[str] = []

    for r in reports:
        try:
            text, _ = safe_read_text(r.filepath, max_bytes=20_000)
            texts.append(text[:5000])
            paths.append(r.filepath)
        except Exception:
            continue

    if len(texts) < 2:
        return []

    try:
        model = SentenceTransformer("all-MiniLM-L6-v2")
        embeddings = model.encode(texts, show_progress_bar=False)
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        embeddings = embeddings / norms
        sim_matrix = embeddings @ embeddings.T

        candidates: list[tuple[str, str]] = []
        for i in range(len(paths)):
            for j in range(i + 1, len(paths)):
                if float(sim_matrix[i, j]) >= similarity_threshold:
                    candidates.append((paths[i], paths[j]))
        return candidates
    except Exception as exc:
        logger.debug("Contradiction detection failed: %s", exc)
        return []


def _extract_shingles(filepath: str, k: int = 5) -> set[str]:
    """Extract k-word shingles from a document file."""
    from ragpreflight.utils import safe_read_text

    try:
        text, _ = safe_read_text(filepath)
        words = re.findall(r"\b\w+\b", text.lower())
        if len(words) < k:
            return set(words)
        return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}
    except Exception:
        return set()


def _jaccard(a: set[str], b: set[str]) -> float:
    """Compute Jaccard similarity between two sets."""
    if not a and not b:
        return 1.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0


def _shingle_duplicates(
    reports: list[DocumentReport],
    threshold: float,
) -> list[list[DocumentReport]]:
    """O(n²) shingle-based near-duplicate detection."""
    shingles = {r.filepath: _extract_shingles(r.filepath) for r in reports}
    groups: list[list[DocumentReport]] = []
    visited: set[str] = set()

    for i, r1 in enumerate(reports):
        if r1.filepath in visited:
            continue
        group = [r1]
        for r2 in reports[i + 1 :]:
            if r2.filepath in visited:
                continue
            sim = _jaccard(shingles[r1.filepath], shingles[r2.filepath])
            if sim >= threshold:
                group.append(r2)
                visited.add(r2.filepath)
        if len(group) > 1:
            visited.add(r1.filepath)
            groups.append(group)

    return groups


def _minhash_duplicates(
    reports: list[DocumentReport],
    threshold: float,
) -> list[list[DocumentReport]]:
    """MinHash LSH near-duplicate detection via datasketch (>=2.0.0)."""
    from datasketch import MinHash, MinHashLSH  # type: ignore[import]

    num_perm = 128
    lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
    minhashes: dict[str, object] = {}

    for r in reports:
        shingles = _extract_shingles(r.filepath)
        m = MinHash(num_perm=num_perm)
        for s in shingles:
            m.update(s.encode("utf-8"))
        safe_key = r.filepath.replace(" ", "_")
        try:
            lsh.insert(safe_key, m)
            minhashes[safe_key] = (r, m)
        except Exception:
            pass

    groups: list[list[DocumentReport]] = []
    visited: set[str] = set()

    for key, (report, m) in minhashes.items():
        if key in visited:
            continue
        try:
            neighbours = lsh.query(m)
        except Exception:
            continue
        neighbours = [n for n in neighbours if n != key]
        if neighbours:
            group = [report] + [minhashes[n][0] for n in neighbours if n in minhashes]
            for n in neighbours:
                visited.add(n)
            visited.add(key)
            groups.append(group)

    return groups


def _find_stale_documents(
    reports: list[DocumentReport],
    stale_months: int,
) -> list[DocumentReport]:
    """Find documents with modification dates older than stale_months."""
    now = datetime.now(tz=timezone.utc)
    stale: list[DocumentReport] = []

    for r in reports:
        try:
            mtime = Path(r.filepath).stat().st_mtime
            modified = datetime.fromtimestamp(mtime, tz=timezone.utc)
            age_months = (now - modified).days / 30.44
            if age_months > stale_months:
                stale.append(r)
        except OSError:
            pass

    return stale
