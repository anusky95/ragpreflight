"""Check registry.

Every check module exposes:

    NAME: str                     # short id shown in --only / --skip
    def run(docs: list[Document], root: Path) -> list[Finding]

Per-document checks (PER_DOC) only look at one document at a time, so the scanner runs them in
the same worker process that read the file. Corpus checks (versions, metadata) compare documents
with each other and run once at the end.

Checks must be deterministic, offline, and must never raise on odd input
(skip the document instead). Finding.path should be relative to `root`
(use `rel(doc.path, root)`).
"""

from __future__ import annotations

from pathlib import Path

from ..models import Document, Finding, Severity

PER_DOC = ("extraction", "chunking", "pii")
CORPUS = ("versions", "metadata")


def rel(path: Path, root: Path) -> str:
    try:
        base = root if root.is_dir() else root.parent
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def where_in(doc: Document, start: int, page_text: str, page_no: int) -> str:
    """Human location for a character offset in one page: 'p.12, line 5' (PDF) or 'line 5'."""
    page = next((p for p in doc.pages if p.number == page_no), None)
    line = page_text.count("\n", 0, start) + 1 + (page.line_offset if page else 0)
    return f"p.{page_no}, line {line}" if doc.fmt == "pdf" else f"line {line}"


def locate(doc: Document, snippet: str) -> str:
    """Find where a quoted sentence/snippet sits in a document ('p.12, line 5' or 'line 5').
    Matches the first few words with flexible whitespace; '' if not found."""
    import re

    words = re.findall(r"\S+", snippet.strip(' "“”…'))[:8]
    if not words:
        return ""
    rx = re.compile(r"\s+".join(re.escape(w) for w in words))
    for page in doc.pages:
        m = rx.search(page.text or "")
        if m:
            return where_in(doc, m.start(), page.text, page.number)
    return ""


def all_checks():
    from . import chunking, extraction, metadata, pii, versions

    return [versions, extraction, chunking, pii, metadata]


def _selected(names, only, skip):
    mods = {m.NAME: m for m in all_checks()}
    return [mods[n] for n in names if (not only or n in only) and not (skip and n in skip)]


def _safe(mod, docs, root) -> list[Finding]:
    try:
        return mod.run(docs, root)
    except Exception as e:  # noqa: BLE001 - one broken check must not hide the others
        return [Finding(f"{mod.NAME}-crashed", Severity.INFO, [], f"check '{mod.NAME}' failed: {e}", "",
                        fix="Please report this at github.com/anusky95/ragpreflight/issues")]


def run_doc(doc: Document, root: Path, only: set[str] | None = None,
            skip: set[str] | None = None) -> list[Finding]:
    if doc.read_error is not None:
        return []
    out: list[Finding] = []
    for mod in _selected(PER_DOC, only, skip):
        out.extend(_safe(mod, [doc], root))
    return out


def run_corpus(docs: list[Document], root: Path, only: set[str] | None = None,
               skip: set[str] | None = None) -> list[Finding]:
    readable = [d for d in docs if d.read_error is None]
    out: list[Finding] = []
    for mod in _selected(CORPUS, only, skip):
        out.extend(_safe(mod, readable, root))
    return out


def run_all(docs: list[Document], root: Path, only: set[str] | None = None,
            skip: set[str] | None = None) -> list[Finding]:
    out: list[Finding] = []
    for d in docs:
        out.extend(run_doc(d, root, only, skip))
    return out + run_corpus(docs, root, only, skip)
