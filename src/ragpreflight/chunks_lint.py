"""Lint the chunks a pipeline actually produced (exported from the vector store / index).

    ragpreflight chunks export.jsonl

No re-chunking and no simulation: these are the exact texts your retriever can return.
Accepts JSONL (one chunk per line) or a JSON array. Field names are matched loosely:

    text      text | content | page_content | chunk | chunk_text | document
    source    source | doc_id | document_id | file | filename | file_path | url | metadata.<same>
    order     start_index | chunk_index | index | seq | position | chunk_id | metadata.<same>
    page      page | page_number | metadata.page

Checks:
    chunk-split           a chunk ends mid-sentence and the next chunk of the same source opens
                          with the condition ("except", "unless", "not" ...)
    chunk-orphan-heading  a chunk ends with a heading; the section body is in the next chunk
    chunk-table-split     a chunk continues a table without its header row
    chunk-oversize        longer than your embedding model reads (--max-tokens, default 8191):
                          the tail is silently truncated and never embedded
    chunk-tiny            near-empty chunks (noise that crowds out real results)
    chunk-duplicate       identical chunks (double-weighting, wasted top-k slots)
    chunk-no-source       chunks without a source: answers can't cite or be traced
    pii / secret          personal data and credentials inside retrievable chunks
    extraction-garbled    replacement chars, (cid:NN), control characters inside chunks
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from .checks import chunking, pii
from .models import Document, Finding, Page, Report, Severity
from .scan import verdict

TEXT_KEYS = ("text", "content", "page_content", "chunk", "chunk_text", "document", "body")
SOURCE_KEYS = ("source", "doc_id", "document_id", "file", "filename", "file_name", "file_path", "path",
               "url", "title")
ORDER_KEYS = ("start_index", "chunk_index", "index", "seq", "position", "chunk_id", "chunk_number")
PAGE_KEYS = ("page", "page_number", "page_label")
TINY_WORDS = 5
MAX_TOKENS_DEFAULT = 8191  # OpenAI text-embedding-3-*; many open models read only 512
_GARBLED = re.compile(r"�|\(cid:\d+\)|[\x00-\x08\x0b\x0e-\x1f\x7f]")
_TERMINAL = re.compile(r"[.!?:;)\]\"'”’]\s*$")


def _get(rec: dict, keys: tuple[str, ...]):
    for k in keys:
        if k in rec and rec[k] not in (None, ""):
            return rec[k]
    meta = rec.get("metadata") or rec.get("meta") or {}
    if isinstance(meta, dict):
        for k in keys:
            if k in meta and meta[k] not in (None, ""):
                return meta[k]
    return None


def load(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8-sig")
    stripped = raw.lstrip()
    if stripped.startswith("["):
        data = json.loads(raw)
    else:
        data = [json.loads(line) for line in raw.splitlines() if line.strip()]
    out = []
    for i, rec in enumerate(data):
        if isinstance(rec, str):
            rec = {"text": rec}
        text = _get(rec, TEXT_KEYS)
        if not isinstance(text, str):
            continue
        order = _get(rec, ORDER_KEYS)
        try:
            order = float(order)
        except (TypeError, ValueError):
            order = None
        out.append({"i": i, "text": text, "source": _get(rec, SOURCE_KEYS), "order": order,
                    "page": _get(rec, PAGE_KEYS)})
    return out


def _tokens(text: str) -> int:
    try:
        import tiktoken

        return len(tiktoken.get_encoding("cl100k_base").encode(text, disallowed_special=()))
    except ImportError:
        return round(len(text.split()) * 1.33)


def _where(c: dict) -> str:
    s = f"chunk {c['n']}"
    if c.get("page") not in (None, ""):
        s += f" (p.{c['page']})"
    return s


def _continuation(prev: str, nxt: str) -> str:
    """The part of `nxt` that continues `prev` (skips an overlap that repeats prev's tail)."""
    for k in (120, 60, 30):
        tail = prev.rstrip()[-k:]
        if len(tail) >= 20:
            j = nxt.find(tail)
            if j != -1:
                return nxt[j + len(tail):]
    return nxt


def lint(path: str | Path, max_tokens: int = MAX_TOKENS_DEFAULT) -> Report:
    p = Path(path)
    chunks = load(p)
    findings: list[Finding] = []
    by_src: dict[str, list[dict]] = defaultdict(list)
    no_source = 0
    for c in chunks:
        src = str(c["source"]) if c["source"] is not None else "(no source)"
        if c["source"] is None:
            no_source += 1
        by_src[src].append(c)
    docs = []
    for src, cs in by_src.items():
        cs.sort(key=lambda c: (c["order"] is None, c["order"] if c["order"] is not None else 0, c["i"]))
        for n, c in enumerate(cs, 1):
            c["n"] = n
        docs.append(Document(Path(src), "chunks", [Page(c["n"], c["text"]) for c in cs]))

        # ---- boundary checks between consecutive chunks of one source
        splits, orphans, tables = [], [], []
        for a, b in zip(cs, cs[1:], strict=False):
            at = a["text"].rstrip()
            if not at:
                continue
            cont = _continuation(a["text"], b["text"])
            if not _TERMINAL.search(at):
                rw = cont.split()[: chunking.SPLIT_QUALIFIER_WINDOW]
                m = next((x for x in chunking._QUAL_RE.finditer(" ".join(rw)) if x.group(0) not in ("No", "NO")), None)
                if m and rw:
                    left = re.split(r"(?<=[.!?])\s+", at)[-1]
                    right = re.split(r"(?<=[.!?])\s", cont.strip(), maxsplit=1)[0]
                    splits.append((a, b, left, right, m.group(0).lower()))
            last_lines = [ln for ln in at.split("\n") if ln.strip()]
            if last_lines and chunking.is_heading(last_lines[-1]):
                orphans.append((a, last_lines[-1].strip()))
            b_lines = [ln for ln in b["text"].split("\n") if ln.strip()]
            from .checks.extraction import find_table_runs, is_table_row

            if len(b_lines) >= 2 and is_table_row(b_lines[0]) and is_table_row(b_lines[1]):
                a_runs = find_table_runs(last_lines, min_rows=2) if last_lines else []
                if a_runs and a_runs[-1][1] >= len(last_lines) - 1:
                    header = last_lines[a_runs[-1][0]].strip()
                    if header and header not in b["text"]:
                        tables.append((b, header))
        sev_split = Severity.INFO
        if splits:
            text = " ".join(c["text"] for c in cs)
            dens = 1000 * len(chunking._POLICY_WORDS.findall(text)) / max(1, len(text.split()))
            sev_split = Severity.WARN if dens >= chunking.POLICY_DENSITY_WARN else Severity.INFO
            shown = [f"{_where(a)} → {_where(b)}: chunk {a['n']} ends “{chunking._clip(left, 200, left=True)}” and "
                     f"the '{q}' condition opens chunk {b['n']}: “{chunking._clip(right, 200)}”"
                     for a, b, left, right, q in splits[:10]]
            findings.append(Finding(
                "chunk-split", sev_split, ["F7"],
                f"{len(splits)} chunk(s) end mid-sentence; the condition is in the next chunk "
                f"({', '.join(sorted({q for *_, q in splits})[:3])})",
                src, "; ".join(_where(a) for a, *_ in splits[:3]) + (f" (+{len(splits) - 3} more)" if len(splits) > 3 else ""),
                detail="These are your real chunks. A retriever can return the first one alone and the "
                       "model answers without the condition.\n\n" + "\n\n".join(shown),
                fix="Re-chunk on sentence/section boundaries, or merge neighbouring chunks at retrieval "
                    "time (parent/neighbour expansion)."))
        if orphans:
            findings.append(Finding(
                "chunk-orphan-heading", Severity.INFO, ["F7"],
                f"{len(orphans)} chunk(s) end with a heading whose section is in the next chunk", src,
                "; ".join(_where(a) for a, _ in orphans[:3]),
                detail="\n".join(f"{_where(a)} ends with “{chunking._clip(h, 80)}”" for a, h in orphans[:10]),
                fix="Split on headings, or prepend the section heading to every chunk."))
        if tables:
            findings.append(Finding(
                "chunk-table-split", Severity.WARN, ["F7"],
                f"{len(tables)} chunk(s) continue a table without its header row", src,
                "; ".join(_where(b) for b, _ in tables[:3]),
                detail="\n".join(f"{_where(b)} has table rows; header “{chunking._clip(h, 80)}” is only in the previous chunk"
                                 for b, h in tables[:10]),
                fix="Repeat the table header in every chunk that holds table rows."))

        # ---- per-chunk checks
        big = [(c, t) for c in cs if (t := _tokens(c["text"])) > max_tokens]
        if big:
            findings.append(Finding(
                "chunk-oversize", Severity.WARN, ["F7", "F20"],
                f"{len(big)} chunk(s) exceed {max_tokens} tokens: the embedding model truncates the rest", src,
                "; ".join(_where(c) for c, _ in big[:3]),
                detail="\n".join(f"{_where(c)}: ~{t:,} tokens" for c, t in big[:10]),
                fix=f"Split these chunks; text past {max_tokens} tokens is never embedded, so it can't be retrieved."))
        tiny = [c for c in cs if len(c["text"].split()) < TINY_WORDS]
        if tiny:
            findings.append(Finding(
                "chunk-tiny", Severity.INFO, ["F11"], f"{len(tiny)} near-empty chunk(s)", src,
                "; ".join(_where(c) for c in tiny[:3]),
                detail="\n".join(f"{_where(c)}: “{chunking._clip(c['text'], 60)}”" for c in tiny[:10]),
                fix="Drop or merge near-empty chunks; they match many queries weakly and waste top-k slots."))
        hits = []
        for c in cs:
            for kind, m in pii.find_all(c["text"]):
                hits.append((c, kind, m.group(0)))
        if hits:
            secret_kinds = {k for k, _ in pii.SECRETS}
            for is_secret in (True, False):
                grp = [h for h in hits if (h[1] in secret_kinds) == is_secret]
                if not grp:
                    continue
                kinds = sorted({k for _, k, _ in grp})
                findings.append(Finding(
                    "secret" if is_secret else "pii",
                    Severity.ERROR if is_secret or set(kinds) & pii.SEVERE else Severity.WARN,
                    ["F23", "F25"] if is_secret else ["F23"],
                    f"{'Credentials' if is_secret else 'Personal data'} in indexed chunks: {', '.join(kinds)}", src,
                    "; ".join(dict.fromkeys(_where(c) for c, _, _ in grp[:3])),
                    detail="\n".join(f"{_where(c)}: {k}: {pii._mask_value(k, v)}" for c, k, v in grp[:10]),
                    fix="These are already retrievable. Remove/redact them from the index (and rotate credentials)."))
        garbled = [c for c in cs if len(_GARBLED.findall(c["text"])) >= 3]
        if garbled:
            findings.append(Finding(
                "extraction-garbled", Severity.WARN, ["F3"], f"{len(garbled)} chunk(s) contain garbled characters",
                src, "; ".join(_where(c) for c in garbled[:3]),
                detail="\n".join(f"{_where(c)}: {chunking._clip(repr(c['text'][:120]), 120)}" for c in garbled[:5]),
                fix="Re-extract the source with another parser or OCR, then re-index these chunks."))

    # ---- corpus-level
    seen: dict[str, dict] = {}
    dups = []
    for c in chunks:
        key = hashlib.sha1(re.sub(r"\s+", " ", c["text"]).strip().lower().encode()).hexdigest()
        if len(c["text"].split()) >= TINY_WORDS and key in seen:
            dups.append((c, seen[key]))
        seen.setdefault(key, c)
    if dups:
        findings.append(Finding(
            "chunk-duplicate", Severity.WARN, ["F1"], f"{len(dups)} duplicate chunk(s) in the index", "(index)",
            detail="\n".join(f"{c['source']} {_where(c)} = {o['source']} {_where(o)}" for c, o in dups[:10]),
            fix="Deduplicate before indexing; duplicates take several top-k slots with the same text."))
    if no_source:
        findings.append(Finding(
            "chunk-no-source", Severity.WARN, ["F21", "F33"], f"{no_source} chunk(s) have no source / document id",
            "(index)", fix="Store the source document id (and page) on every chunk so answers can cite and be audited."))

    from .scan import IMPACT, ORDER
    rank = {c: i for i, c in enumerate(IMPACT)}
    findings.sort(key=lambda f: (ORDER[f.severity], rank.get(f.check, len(IMPACT)), f.check, f.path))
    v = verdict(findings)
    n_err = sum(f.severity == Severity.ERROR for f in findings)
    n_warn = sum(f.severity == Severity.WARN for f in findings)
    score = max(0, 100 - 12 * n_err - 3 * n_warn)
    score = min(score, 59) if v == "NOT READY" else min(score, 89) if v == "REVIEW" else score
    rep = Report(str(p), docs, findings, score, v)
    return rep
