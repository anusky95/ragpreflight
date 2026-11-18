"""Chunking checks: what does a real splitter cut?

We chunk each document with an actual production splitter (see ``ragpreflight.chunkers``; default:
LangChain's RecursiveCharacterTextSplitter with its shipped defaults) and inspect every chunk end:

    chunk-split           WARN* F7  a sentence is cut so its qualifier ("except", "unless", "must not",
                                    "before", "not" ...) sits in the next chunk; the chunk that ends
                                    at the cut states the rule without its condition
    chunk-orphan-heading  INFO  F7  a heading ends a chunk while its section body starts the next
    chunk-table-split     INFO  F7  a table crosses a chunk end: rows separate from their header

    * WARN in rule-dense text (policies, contracts); a note elsewhere.

For a team's own chunks (exported from the vector store), use ``ragpreflight chunks``, which runs
the same boundary checks on the real chunks instead of re-chunking.
One finding per document per type, with page/line and the exact text for every example.
"""

from __future__ import annotations

import re
from pathlib import Path

from .. import chunkers
from ..models import Document, Finding, Severity
from . import rel
from .extraction import find_table_runs

NAME = "chunking"

# kept for backwards compatibility (word-window helpers used in tests / experiments)
CHUNK_WORDS = 380
ENV_CHUNKER = chunkers.ENV
MAX_EXAMPLES = 3
MAX_DETAIL_EXAMPLES = 10

SPLIT_MIN_LEFT_WORDS = 4  # the claim left behind must be at least this long
SPLIT_QUALIFIER_WINDOW = 4  # qualifier must appear within the first N words after the cut
# Splits only matter enough to WARN in rule-like text (policies, procedures, contracts).
POLICY_DENSITY_WARN = 10.0  # rule words per 1,000 words (policies ~15+, 10-Ks ~2-8, papers <2)
_POLICY_WORDS = re.compile(r"(?i)\b(?:must|shall|may not|required|eligible|ineligible|prohibited|permitted|policy|entitled|mandatory|in accordance with|no later than)\b")
SPLIT_MAX_SENTENCE_WORDS = 80  # longer "sentences" mean we couldn't find real ones: skip

ORPHAN_MAX_TAIL_WORDS = 10  # heading + fewer than this many body words at chunk end
HEADING_MAX_WORDS = 12
TABLE_MIN_ROWS = 4

QUALIFIERS = (
    "not", "never", "no", "unless", "except", "excluding", "only", "before", "after", "must",
    "cannot", "can't", "won't", "don't", "doesn't", "isn't", "aren't", "until", "without",
    "provided that", "subject to",
)
_QUAL_RE = re.compile(
    r"(?<![\w'])(?:" + "|".join(re.escape(q).replace(r"\ ", r"\s+")
                                for q in sorted(QUALIFIERS, key=len, reverse=True))
    + r")(?![\w'])(?!\.\s*\d)",  # "no. 5" is a number, not a negation
    re.I,
)
# sentence ends: terminal punctuation (+ closing quote/bracket) then whitespace, or a blank line,
# or a newline that starts a bullet / heading
_SENT_END = re.compile(r"[.!?]+[\"')\]]*\s+|\n[ \t]*\n|\n(?=[ \t]*(?:[-*•#]|\d+[.)]\s))")
_MD_HEADING = re.compile(r"^#{1,6}\s+\S")
_NUM_HEADING = re.compile(r"^(?:\d{1,2}\.(?:\d{1,2}\.?)*|[IVX]+\.)\s+[A-Z]")  # "3." "3.2" "IV."
_WORD = re.compile(r"\S+")


# ---------------------------------------------------------------- helpers
def _word_spans(text: str) -> list[tuple[int, int]]:
    return [m.span() for m in _WORD.finditer(text)]


def simulate_chunks(text: str, size: int = CHUNK_WORDS) -> list[str]:
    """Fixed word windows (no overlap). Kept for experiments; real checks use ragpreflight.chunkers."""
    return [text[a:b] for a, b in chunkers._word_windows(text or "", max(1, int(size)), 0)]


def _clip(s: str, n: int = 90, left: bool = False) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) <= n:
        return s
    return "…" + s[-n:].lstrip() if left else s[:n].rstrip() + "…"


def is_heading(line: str, markdown: bool = True) -> bool:
    """Markdown '#' heading (when `markdown`), numbered '3.2 Title', or a short ALL-CAPS line."""
    s = line.strip()
    if not s or len(s) > 120:
        return False
    if s.startswith("#"):
        return markdown and bool(_MD_HEADING.match(s))
    words = s.split()
    if len(words) > HEADING_MAX_WORDS or s.endswith((".", ",", ";", ":", "?", "!")):
        return False
    if "|" in s:
        return False
    if _NUM_HEADING.match(s) and len(words) >= 2:
        return True
    letters = [c for c in s if c.isalpha()]
    if (2 <= len(words) <= 8 and len(letters) >= 6 and all(c.isupper() for c in letters)
            and sum(1 for c in s if c.isdigit()) <= 2):
        return True
    return False


def _sentence_bounds(text: str, pos: int, window: int = 1500) -> tuple[int, int]:
    """(start, end) char offsets of the sentence containing `pos`."""
    lo = max(0, pos - window)
    start = lo if lo > 0 else 0
    for m in _SENT_END.finditer(text, lo, pos):
        if m.end() <= pos:
            start = m.end()
    m = _SENT_END.search(text, pos, min(len(text), pos + window))
    end = m.start() if m else min(len(text), pos + window)
    return start, end


def _page_line(doc: Document, pos: int) -> str:
    """'p.12, line 5' (PDF) / 'line 5' for a char offset into doc.text (pages joined by blank lines)."""
    off = 0
    for p in doc.pages:
        t = p.text or ""
        if pos <= off + len(t):
            line = t.count("\n", 0, max(0, pos - off)) + 1 + p.line_offset
            return f"p.{p.number}, line {line}" if doc.fmt == "pdf" else f"line {line}"
        off += len(t) + 2
    return ""


def chunk_ends(spans: list[tuple[int, int]], text_len: int) -> list[tuple[int, int]]:
    """(chunk_no, end_offset) for every chunk that ends before the document does."""
    out = []
    for i, (_s, e) in enumerate(spans):
        tail = e < text_len and i < len(spans) - 1
        if tail:
            out.append((i + 1, e))
    return out


def cut_examples(text: str, ends: list[tuple[int, int]]):
    """Yield (chunk_no, left_fragment, right_fragment, qualifier, cut_offset) for risky cuts:
    chunk `chunk_no` ends mid-sentence, keeping the claim but not its qualifier."""
    for no, b in ends:
        while b < len(text) and b > 0 and not text[b - 1].isspace() and not text[b].isspace():
            b -= 1  # snap a mid-word cut back to the word start
        while b < len(text) and text[b].isspace():
            b += 1  # the cut sits where the next chunk's text begins
        s_start, s_end = _sentence_bounds(text, b)
        if not (s_start < b < s_end):
            continue  # chunk ends between sentences: fine
        left, right = text[s_start:b], text[b:s_end]
        lw, rw = left.split(), right.split()
        if len(lw) < SPLIT_MIN_LEFT_WORDS or not rw:
            continue
        if len(lw) + len(rw) > SPLIT_MAX_SENTENCE_WORDS:
            continue
        if "|" in left or "|" in right:
            continue  # table rows are handled by chunk-table-split
        window = " ".join(rw[:SPLIT_QUALIFIER_WINDOW])
        m = next((x for x in _QUAL_RE.finditer(window) if x.group(0) not in ("No", "NO")), None)
        if not m:
            continue
        yield no, left, right, re.sub(r"\s+", " ", m.group(0)).lower(), b


def _lines_with_offsets(text: str) -> list[tuple[str, int]]:
    out, off = [], 0
    for line in text.split("\n"):
        out.append((line, off))
        off += len(line) + 1
    return out


# ---------------------------------------------------------------- per document
def _check_doc(doc: Document, path: str, ch: chunkers.Chunking, text: str) -> list[Finding]:
    ends = chunk_ends(ch.spans, len(text.rstrip()))
    if not ends:
        return []  # one chunk: nothing to split
    out: list[Finding] = []
    n_chunks = len(ch.spans)
    how = f"{ch.label} → {n_chunks} chunks" + ("" if ch.source != "approximation" else " (approximate)")

    # --- chunk-split
    examples = list(cut_examples(text, ends))
    if examples:
        quals = []
        for *_, q, _b in examples:
            if q not in quals:
                quals.append(q)
        where = []
        for *_, pos in examples:
            loc = _page_line(doc, pos)
            if loc and loc not in where:
                where.append(loc)
        shown = []
        for no, left, right, q, pos in examples[:MAX_DETAIL_EXAMPLES]:
            loc = _page_line(doc, pos)
            shown.append(
                f"{loc + ' — ' if loc else ''}chunk {no} of {n_chunks} ends mid-sentence: it keeps the "
                f"claim, the '{q}' condition is only in chunk {no + 1}:\n"
                f"    “{_clip(left, 220, left=True)} ✂ {_clip(right, 220)}”")
        n = len(examples)
        if n > MAX_DETAIL_EXAMPLES:
            shown.append(f"… and {n - MAX_DETAIL_EXAMPLES} more.")
        loc_field = "; ".join(where[:3]) + (f" (+{len(where) - 3} more)" if len(where) > 3 else "")
        rule_density = 1000 * len(_POLICY_WORDS.findall(text)) / max(1, len(text.split()))
        sev = Severity.WARN if rule_density >= POLICY_DENSITY_WARN else Severity.INFO
        out.append(Finding(
            "chunk-split", sev, ["F7"],
            f"{n} sentence(s) cut mid-way: the chunk keeps the claim, the next one gets its "
            f"condition ({', '.join(repr(q) for q in quals[:3])})",
            path, loc_field,
            detail=(f"Chunked with {how}. ✂ marks where a chunk ends. If a retriever returns that "
                    f"chunk alone, the model sees the rule without its condition (overlap repeats the "
                    f"start of the next chunk, it doesn't complete this one).\n\n" + "\n\n".join(shown)),
            fix="Keep conditions with their claim: split on sentences/sections (e.g. LlamaIndex "
                "SentenceSplitter, a markdown/heading splitter), or attach neighbouring chunks at "
                "retrieval time. Not your splitter? Re-run with --chunker, or lint your real chunks "
                "with `ragpreflight chunks export.jsonl`.",
        ))

    lines = _lines_with_offsets(text)
    md = doc.fmt in ("md", "txt")

    # --- chunk-orphan-heading: a heading sits at the very end of a chunk
    orphans: list[tuple[int, str, str]] = []
    for no, e in ends:
        tail = text[max(0, e - 400):e]
        tail_lines = [ln for ln in tail.split("\n") if ln.strip()]
        for k in range(len(tail_lines) - 1, max(-1, len(tail_lines) - 3), -1):
            if is_heading(tail_lines[k], markdown=md):
                after = " ".join(tail_lines[k + 1:]).split()
                if len(after) < ORPHAN_MAX_TAIL_WORDS:
                    pos = text.rfind(tail_lines[k], 0, e)
                    orphans.append((no, tail_lines[k].strip(), _page_line(doc, max(0, pos))))
                break
    if orphans:
        shown = "\n".join(f"{loc + ' — ' if loc else ''}chunk {c} ends with the heading “{_clip(h, 80)}”"
                          for c, h, loc in orphans[:MAX_DETAIL_EXAMPLES])
        more = f" (+{len(orphans) - MAX_EXAMPLES} more)" if len(orphans) > MAX_EXAMPLES else ""
        out.append(Finding(
            "chunk-orphan-heading", Severity.INFO, ["F7"],
            f"{len(orphans)} heading(s) end a chunk while their section body starts the next",
            path, "; ".join(loc for _, _, loc in orphans[:MAX_EXAMPLES] if loc) + more,
            detail=f"Chunked with {how}. The next chunk loses the heading that says what it is about:\n{shown}",
            fix="Split on headings (markdown/structure-aware splitter) or prepend the section heading "
                "to every chunk.",
        ))

    # --- chunk-table-split: a table crosses a chunk end (header before, rows after)
    line_texts = [ln for ln, _ in lines]
    splits: list[tuple[int, str, int, str]] = []
    for a, b in find_table_runs(line_texts, min_rows=TABLE_MIN_ROWS):
        head_end = lines[a][1] + len(lines[a][0])
        last_end = lines[b][1] + len(lines[b][0])
        for no, e in ends:
            if head_end <= e < last_end:
                rows_after = sum(1 for ln, off in lines[a:b + 1] if ln.strip() and off >= e)
                if rows_after:
                    splits.append((no, _clip(lines[a][0], 80), rows_after, _page_line(doc, lines[a][1])))
                break
    if splits:
        shown = "\n".join(f"{loc + ' — ' if loc else ''}table starting “{h}”: {r} row(s) land in chunk "
                          f"{c + 1} without this header" for c, h, r, loc in splits[:MAX_DETAIL_EXAMPLES])
        more = f" (+{len(splits) - MAX_EXAMPLES} more)" if len(splits) > MAX_EXAMPLES else ""
        out.append(Finding(
            "chunk-table-split", Severity.INFO, ["F7"],
            f"{len(splits)} table(s) cross a chunk boundary: rows end up without their header",
            path, "; ".join(loc for *_, loc in splits[:MAX_EXAMPLES] if loc) + more,
            detail=f"Chunked with {how}.\n{shown}",
            fix="Keep each table in one chunk, or repeat the header row in every chunk that holds "
                "table rows (table-aware splitter).",
        ))
    return out


def run(docs: list[Document], root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for doc in docs:
        try:
            if doc.read_error is not None:
                continue
            text = doc.text or ""
            ch = chunkers.chunk(text)
            if ch is None:
                continue
            findings.extend(_check_doc(doc, rel(doc.path, root), ch, text))
        except chunkers.ChunkerUnavailable as e:
            return [Finding("chunking-unavailable", Severity.INFO, [], str(e), "(corpus)")]
        except Exception:  # noqa: BLE001 - never let one odd document break the scan
            continue
    return findings
