"""Before / after: ask the same questions of a knowledge base as-is, and after ragpreflight's fixes.

    ragpreflight ab ./docs --questions questions.json
    ragpreflight demo --chat

BEFORE  every file, chunked with your splitter (--chunker), retrieved with BM25.
AFTER   the same pipeline after applying what ragpreflight found:
          - older versions and exact duplicates removed (version-stale, duplicate)
          - personal data and credentials redacted (pii, secret)
          - files with no usable text left out and listed (unreadable, scanned, empty)
          - every chunk tagged with its source and date
          - sentence-safe chunking, so a condition stays with its rule (chunk-split)

Answers come from Claude when credentials are available (ANTHROPIC_API_KEY, or an `ant auth login`
profile; `pip install anthropic`). Without them, the page still shows what the retriever would hand
the model for each question, which is the part ragpreflight changes.
Only fixes that ragpreflight itself reports are applied: no hand-picked edits.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import chunkers
from .checks import pii, rel
from .models import Document, Severity

DEFAULT_MODEL = "claude-opus-5-5"
TOP_K = 3
SAFE_CHUNK_CHARS = 1000

SYSTEM = (
    "You are a customer-support assistant. Answer the question using only the context passages "
    "provided. When a passage names its source, cite it in square brackets after the claim. "
    "If the passages don't contain the answer, say you don't know. Keep the answer to 2-4 sentences."
)


@dataclass
class Chunk:
    source: str
    text: str
    note: str = ""  # e.g. "redacted 2 values"


@dataclass
class Side:
    chunks: list[Chunk]
    retrieved: list[list[tuple[Chunk, float]]] = field(default_factory=list)
    answers: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- retrieval (BM25, no dependencies)
_TOK = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are", "be", "can", "i",
         "my", "me", "what", "how", "do", "does", "it", "this", "that", "with", "at", "by", "if", "we"}


def _toks(t: str) -> list[str]:
    return [w for w in _TOK.findall(t.lower()) if w not in _STOP]


class BM25Index:
    """Okapi BM25 over chunks; term statistics computed once, so big real corpora stay fast."""

    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks, self.k1, self.b = chunks, k1, b
        self.tf = [Counter(_toks(c.text)) for c in chunks]
        self.len = [sum(t.values()) for t in self.tf]
        self.n = len(chunks)
        self.avg = (sum(self.len) / self.n) if self.n else 1
        self.df: Counter = Counter()
        self.post: dict[str, list[int]] = {}
        for i, t in enumerate(self.tf):
            for w in t:
                self.df[w] += 1
                self.post.setdefault(w, []).append(i)

    def search(self, query: str, k: int = TOP_K) -> list[tuple[Chunk, float]]:
        scores: dict[int, float] = {}
        for w in set(_toks(query)):
            if w not in self.df:
                continue
            idf = math.log(1 + (self.n - self.df[w] + 0.5) / (self.df[w] + 0.5))
            for i in self.post[w]:
                tf = self.tf[i][w]
                scores[i] = scores.get(i, 0.0) + idf * tf * (self.k1 + 1) / (
                    tf + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
        top = sorted(scores.items(), key=lambda x: -x[1])[:k]
        return [(self.chunks[i], s) for i, s in top]


def bm25(chunks: list[Chunk], query: str, k: int = TOP_K) -> list[tuple[Chunk, float]]:
    return BM25Index(chunks).search(query, k)


# ---------------------------------------------------------------- building the two indexes
def _chunk_texts(text: str) -> list[str]:
    ch = chunkers.chunk(text)
    if ch is None:
        return [text]
    return [text[s:e] for s, e in ch.spans if text[s:e].strip()]


_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9“\"(])|\n\s*\n")


def _sentence_safe(text: str, size: int = SAFE_CHUNK_CHARS) -> list[str]:
    """Pack whole sentences/paragraphs into chunks of about `size` chars; never cut a sentence."""
    parts = [p.strip() for p in _SENT.split(text) if p and p.strip()]
    out, cur = [], ""
    for p in parts:
        if cur and len(cur) + 1 + len(p) > size:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur} {p}".strip() if cur else p
    if cur:
        out.append(cur)
    return out


def _sentence_aware(text: str) -> list[str]:
    """The SAME splitter as BEFORE (size and overlap from --chunker), with sentence boundaries added
    to LangChain's separator list, so chunks end at a sentence instead of mid-clause."""
    spec = chunkers.current_spec()
    size, overlap = 1000, 200
    if spec.startswith("langchain"):
        size, overlap = chunkers._parse(spec, 4000, 200)
    seps = ("\n\n", "\n", ". ", "; ", " ", "")
    return [c for c in chunkers._lc_recursive(text, size, overlap, separators=seps) if c.strip()]


def before_index(docs: list[Document], root: Path) -> list[Chunk]:
    out = []
    for d in docs:
        if d.read_error or not d.text.strip():
            continue
        for t in _chunk_texts(d.text):
            out.append(Chunk(rel(d.path, root), t))
    return out


def _redact(text: str, contacts: bool = True) -> tuple[str, int]:
    """Redact credentials, SSNs and card numbers always; emails/phones only when `contacts`
    (documents ragpreflight rated a privacy error). Public company phone lines stay usable."""
    found = [(k, m) for k, m in pii.find_all(text)
             if contacts or k not in ("email address", "phone number")]
    found.sort(key=lambda km: -km[1].start())
    for kind, m in found:
        text = text[:m.start()] + f"[REDACTED {kind}]" + text[m.end():]
    return text, len(found)


def after_index(docs: list[Document], findings, root: Path) -> tuple[list[Chunk], list[str]]:
    """Apply what ragpreflight found, and return (chunks, human-readable list of changes)."""
    drop: dict[str, str] = {}
    period_docs: set[str] = set()
    severe_pii: set[str] = set()
    for f in findings:
        if f.check == "version-stale" and f.severity != Severity.INFO:
            drop[f.path] = f"removed: older version (newer: {', '.join(f.related[:2])})"
        elif f.check == "duplicate":
            drop.setdefault(f.path, "removed: exact duplicate")
        elif f.check in ("unreadable", "extraction-empty") or (
                f.check == "extraction-scanned" and f.severity == Severity.ERROR):
            drop.setdefault(f.path, "left out: no usable text (OCR it, then re-run)")
        elif f.check == "version-period-twins":
            period_docs.update([f.path, *f.related])
        elif f.check == "pii" and f.severity == Severity.ERROR:
            severe_pii.add(f.path)
    changes = [f"{p} — {why}" for p, why in sorted(drop.items())]
    out: list[Chunk] = []
    tagged_periods = 0
    for d in docs:
        rp = rel(d.path, root)
        if rp in drop or d.read_error or not d.text.strip():
            continue
        if _garbage_share(d.text) >= GARBAGE_SHARE:
            changes.append(f"{rp} — left out: text extracts as garbage (both PDF readers fail; OCR it)")
            continue
        text, n_red = _redact(d.text, contacts=rp in severe_pii)
        if n_red:
            changes.append(f"{rp} — redacted {n_red} credential / sensitive value(s)")
        period = _period(d) if rp in period_docs else ""
        if period:
            tagged_periods += 1
        date = d.metadata.get("date", "") if d.fmt != "pdf" else ""  # PDF dates are usually re-save dates
        header = (f"[Source: {rp}" + (f" | period {period}" if period else "")
                  + (f" | updated {date}" if date else "") + "]")
        for t in _sentence_aware(text):
            out.append(Chunk(rp, f"{header}\n{t}", note=f"redacted {n_red}" if n_red else ""))
    if tagged_periods:
        changes.append(f"{tagged_periods} recurring report(s): every chunk tagged with its period (period twins)")
    changes.append("all remaining chunks: tagged with their source; same splitter, plus sentence boundaries as separators")
    return out, changes


GARBAGE_SHARE = 0.05  # control/replacement chars as a share of all characters


def _garbage_share(text: str) -> float:
    bad = len(re.findall(r"[�\x00-\x08\x0b\x0e-\x1f\x7f]", text))
    return bad / max(1, len(text))


def _period(d: Document) -> str:
    """Period label from the filename: '2018', '2023 Q2', 'FY2022'."""
    stem = d.path.stem.lower()
    y = re.findall(r"(?<!\d)((?:19|20)\d\d)(?!\d)", stem)
    q = re.search(r"(?<![a-z0-9])q([1-4])(?![0-9])", stem)
    if not y:
        return ""
    return f"{y[-1]}" + (f" Q{q.group(1)}" if q else "")


# ---------------------------------------------------------------- answering
class NoCredentials(RuntimeError):
    pass


def make_client():
    """An Anthropic client if the SDK and credentials are available, else None (evidence-only mode)."""
    try:
        import anthropic
    except ImportError:
        return None, "pip install anthropic to see the bot's answers"
    try:
        client = anthropic.Anthropic()
        return client, ""
    except Exception as e:  # noqa: BLE001 - no credentials configured
        return None, f"no Anthropic credentials ({type(e).__name__}); set ANTHROPIC_API_KEY or run `ant auth login`"


def answer(client, model: str, question: str, hits: list[tuple[Chunk, float]]) -> str:
    import anthropic

    # Passage text only, like LangChain's default stuff-documents prompt ("{page_content}"). The
    # AFTER side's chunks carry ragpreflight's [Source | period] tag inside their text.
    context = "\n\n".join(f"<passage>\n{c.text}\n</passage>" for c, _ in hits)
    try:
        resp = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": f"{context}\n\nQuestion: {question}"}],
        )
    except TypeError as e:  # the SDK found no credentials at all
        raise NoCredentials(str(e)) from e
    except anthropic.AuthenticationError:
        return "⚠ Anthropic credentials were rejected."
    except anthropic.RateLimitError:
        return "⚠ Rate limited; try again shortly."
    except anthropic.APIStatusError as e:
        return f"⚠ API error {e.status_code}: {e.message}"
    except anthropic.APIConnectionError:
        return "⚠ Could not reach the Anthropic API."
    if resp.stop_reason == "refusal":
        return "⚠ The model declined to answer."
    return "\n".join(b.text for b in resp.content if b.type == "text").strip()


# ---------------------------------------------------------------- run
@dataclass
class ABResult:
    questions: list[str]
    before: Side
    after: Side
    changes: list[str]
    model: str
    answers_note: str
    chunker: str
    report: object


def run(path: str | Path, questions: list[str], model: str = DEFAULT_MODEL, top_k: int = TOP_K,
        with_answers: bool = True) -> ABResult:
    from .scan import scan

    root = Path(path).resolve()
    report = scan(root)
    docs = report.documents
    b = Side(before_index(docs, root))
    after_chunks, changes = after_index(docs, report.findings, root)
    a = Side(after_chunks)
    client, note = make_client() if with_answers else (None, "answers turned off (--no-answers)")
    indexes = {id(b): BM25Index(b.chunks), id(a): BM25Index(a.chunks)}
    for q in questions:
        for side in (b, a):
            hits = indexes[id(side)].search(q, top_k)
            side.retrieved.append(hits)
            text = ""
            if client:
                try:
                    text = answer(client, model, q, hits)
                except NoCredentials:
                    client, note = None, ("no Anthropic credentials: set ANTHROPIC_API_KEY or run "
                                          "`ant auth login`, then re-run")
            side.answers.append(text)
    ch = chunkers.chunk("x" * 10)
    return ABResult(questions, b, a, changes, model, note, ch.label if ch else "off", report)


def load_questions(path: str | Path | None) -> list[str]:
    if path is None:
        from importlib import resources

        return json.loads((resources.files("ragpreflight") / "demo" / "_questions.json").read_text(encoding="utf-8"))
    p = Path(path)
    if p.suffix.lower() == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        return [q if isinstance(q, str) else q.get("question", "") for q in data]
    return [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
