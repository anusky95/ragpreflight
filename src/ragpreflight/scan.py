"""Scan a path: read every document, run every check, score the result."""

from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from .checks import rel, run_corpus, run_doc
from .models import Document, Finding, Report, Severity
from .readers import discover, read

# Per check id, a document loses at most this much (so 40 PII hits don't count 40 times).
CAP = {Severity.ERROR: 35, Severity.WARN: 12, Severity.INFO: 0}


def score(docs: list[Document], findings: list[Finding], root: Path) -> int:
    """Mean per-document score. A finding counts against its own document and the
    documents it names in `related`; corpus-level findings count once against the mean."""
    if not docs:
        return 0
    per_doc: dict[str, dict[str, int]] = defaultdict(dict)
    corpus_penalty = 0
    for f in findings:
        targets = [f.path] if f.path and f.path != "(corpus)" else []
        if f.check.startswith("version-conflict"):
            targets += f.related
        if not targets:
            corpus_penalty += min(f.severity.weight, CAP[f.severity]) // 2
            continue
        for t in targets:
            cur = per_doc[t].get(f.check, 0)
            per_doc[t][f.check] = min(CAP[f.severity], cur + f.severity.weight)
    scores = []
    for d in docs:
        if d.read_error:
            scores.append(0)
            continue
        scores.append(max(0, 100 - sum(per_doc.get(rel(d.path, root), {}).values())))
    return max(0, round(sum(scores) / len(scores)) - corpus_penalty)


def verdict(findings: list[Finding]) -> str:
    sev = {f.severity for f in findings}
    if Severity.ERROR in sev:
        return "NOT READY"
    if Severity.WARN in sev:
        return "REVIEW"
    return "READY"


ORDER = {Severity.ERROR: 0, Severity.WARN: 1, Severity.INFO: 2}
# Within a severity, show what does the most damage first.
IMPACT = ["version-conflict", "secret", "version-stale", "pii", "unreadable", "extraction-scanned",
          "extraction-empty", "duplicate", "version-period-twins", "extraction-garbled", "chunk-split",
          "extraction-table", "extraction-images", "chunk-table-split"]
PARALLEL_MIN_FILES = 8


def _process(path: Path, root: Path, only, skip) -> tuple[Document, list[Finding]]:
    """Read one file and run the per-document checks on it (runs inside a worker process)."""
    doc = read(path)
    return doc, run_doc(doc, root, only, skip)


def process_all(paths: list[Path], root: Path, only: set[str] | None = None, skip: set[str] | None = None,
                progress: Callable[[int, int], None] | None = None) -> list[tuple[Document, list[Finding]]]:
    """Read + check files, in parallel processes when there are enough of them."""
    total = len(paths)
    results: dict[int, tuple[Document, list[Finding]]] = {}

    def serial(pending: list[int]) -> None:
        for n, i in enumerate(pending, total - len(pending) + 1):
            results[i] = _process(paths[i], root, only, skip)
            if progress:
                progress(n, total)

    if total < PARALLEL_MIN_FILES or (os.cpu_count() or 1) < 2:
        serial(list(range(total)))
    else:
        try:
            with ProcessPoolExecutor(max_workers=min(os.cpu_count() or 2, 16)) as ex:
                futures = {ex.submit(_process, p, root, only, skip): i for i, p in enumerate(paths)}
                for done, fut in enumerate(as_completed(futures), 1):
                    results[futures[fut]] = fut.result()  # _process never raises; a broken pool does
                    if progress:
                        progress(done, total)
        except Exception:  # noqa: BLE001 - no usable worker processes (stdin script, sandbox, frozen app)
            serial([i for i in range(total) if i not in results])
    return [results[i] for i in range(total)]


def read_all(paths: list[Path], progress: Callable[[int, int], None] | None = None) -> list[Document]:
    """Just read the files (no checks)."""
    return [d for d, _ in process_all(paths, Path("."), only={"__none__"}, progress=progress)]


def scan(path: str | Path, only: set[str] | None = None, skip: set[str] | None = None,
         progress: Callable[[int, int], None] | None = None) -> Report:
    root = Path(path).resolve()
    if not root.exists():
        raise FileNotFoundError(f"No such file or folder: {path}")
    processed = process_all(discover(root), root, only, skip, progress)
    docs = [d for d, _ in processed]
    findings = []
    for d in docs:
        if d.read_error:
            findings.append(Finding("unreadable", Severity.ERROR, ["F2"], f"Could not read file: {d.read_error}",
                                    rel(d.path, root), fix="Fix or convert the file; as-is, its content is invisible to your AI."))
    for _, fs in processed:
        findings += fs
    findings += run_corpus(docs, root, only, skip)
    rank = {c: i for i, c in enumerate(IMPACT)}
    findings.sort(key=lambda f: (ORDER[f.severity], rank.get(f.check, len(IMPACT)), f.check, f.path))
    v = verdict(findings)
    s = score(docs, findings, root)
    # Keep the number consistent with the verdict: errors can't look like a passing grade.
    s = min(s, 59) if v == "NOT READY" else min(s, 89) if v == "REVIEW" else s
    return Report(str(root), docs, findings, s, v)
