"""Can an answer built from this document be dated and traced? F21 (and F1 for very old docs)."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from ..models import Document, Finding, Severity
from . import rel

NAME = "metadata"

PLACEHOLDERS = re.compile(
    r"^(untitled|document\d*|microsoft word - .*|presentation\d*|book\d*|user|admin|author|owner|"
    r"unknown|n/?a|none|null|tbd|todo|-|\?+|default|test|new document)$",
    re.I,
)
OLD_YEARS = 3
CORPUS_SUMMARY_MIN = 5  # with this many docs missing a field, report once for the corpus
DATE_IN_TEXT = re.compile(
    r"(?i)\b(?:effective|updated|last updated|revised|reviewed|as of|dated)\b[^\n]{0,20}?"
    r"((?:19|20)\d{2})")


def _year(doc: Document) -> int | None:
    m = re.search(r"((?:19|20)\d{2})", doc.metadata.get("date", ""))
    if m:
        return int(m.group(1))
    m = DATE_IN_TEXT.search(doc.text[:4000])
    return int(m.group(1)) if m else None


def _has(doc: Document, key: str) -> bool:
    v = doc.metadata.get(key, "").strip()
    return bool(v) and not PLACEHOLDERS.match(v)


def run(docs: list[Document], root: Path) -> list[Finding]:
    out: list[Finding] = []
    no_date = [d for d in docs if _year(d) is None]
    no_owner = [d for d in docs if not (_has(d, "author") or _has(d, "owner"))]
    placeholder = [d for d in docs for k in ("title", "author") if d.metadata.get(k) and PLACEHOLDERS.match(d.metadata[k].strip())]

    def report(group: list[Document], check: str, what: str, fix: str) -> None:
        if not group:
            return
        if len(group) >= CORPUS_SUMMARY_MIN:
            names = ", ".join(rel(d.path, root) for d in group[:5])
            out.append(Finding(check, Severity.WARN, ["F21"],
                               f"{len(group)} of {len(docs)} documents have no {what}",
                               "(corpus)", detail=f"e.g. {names}" + (" …" if len(group) > 5 else ""),
                               fix=fix, related=[rel(d.path, root) for d in group]))
        else:
            for d in group:
                out.append(Finding(check, Severity.INFO, ["F21"], f"No {what}", rel(d.path, root), fix=fix))

    report(no_date, "metadata-no-date", "date (metadata or 'Effective/Updated <date>' line)",
           "Add an effective/updated date (PDF/DOCX properties, frontmatter `updated:`), so stale answers can be detected and filtered.")
    report(no_owner, "metadata-no-owner", "owner or author",
           "Record an owner (frontmatter `owner:` or document author), so someone is accountable when an answer is wrong.")
    seen = set()
    for d in placeholder:
        if d.path in seen:
            continue
        seen.add(d.path)
        bad = {k: d.metadata[k] for k in ("title", "author") if d.metadata.get(k) and PLACEHOLDERS.match(d.metadata[k].strip())}
        out.append(Finding("metadata-placeholder", Severity.INFO, ["F21"],
                           "Placeholder metadata: " + ", ".join(f'{k}="{v}"' for k, v in bad.items()),
                           rel(d.path, root),
                           fix="Placeholder titles/authors look like real metadata to filters and citations; set real values or clear them."))
    this_year = date.today().year
    for d in docs:
        y = _year(d)
        if y and this_year - y > OLD_YEARS:
            out.append(Finding("metadata-old", Severity.INFO, ["F1"],
                               f"Last dated {y} ({this_year - y} years ago)", rel(d.path, root),
                               fix="Confirm it is still current, or mark it archived so retrieval can down-rank it."))
    return out
