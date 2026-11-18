"""Real chunk boundaries from the splitters production RAG stacks use.

ragpreflight doesn't invent a chunk size. It runs your splitter (or the most common one) and checks
what lands on each boundary. Specs (``--chunker``):

    langchain[:SIZE[:OVERLAP]]    LangChain RecursiveCharacterTextSplitter. Defaults 4000 chars / 200,
                                  exactly as the library ships. Uses the installed library when present,
                                  otherwise a bundled port of the same algorithm (MIT, see below).
    llamaindex[:SIZE[:OVERLAP]]   LlamaIndex SentenceSplitter. Defaults 1024 tokens / 200. Needs
                                  ``pip install llama-index-core``.
    openai                        OpenAI file search defaults: 800-token windows, 400 overlap. OpenAI's
                                  exact boundaries are not public; token windows with tiktoken.
    tokens:SIZE[:OVERLAP]         fixed token windows (tiktoken o200k_base when installed).
    words:SIZE[:OVERLAP]          fixed word windows (tests, quick experiments).
    off                           skip chunk checks.

Every spec returns character spans [(start, end)] into the text, so findings can say exactly where
a chunk ends.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

ENV = "RAGPREFLIGHT_CHUNKER"
DEFAULT = "langchain"


@dataclass
class Chunking:
    spans: list[tuple[int, int]]
    label: str  # human description, e.g. "LangChain RecursiveCharacterTextSplitter (4000 chars, 200 overlap)"
    source: str  # "library", "bundled port", "approximation"


class ChunkerUnavailable(RuntimeError):
    pass


def current_spec() -> str:
    return (os.environ.get(ENV) or DEFAULT).strip().lower()


def _parse(spec: str, default_size: int, default_overlap: int) -> tuple[int, int]:
    parts = spec.split(":")[1:]
    size = int(parts[0]) if parts and parts[0] else default_size
    overlap = int(parts[1]) if len(parts) > 1 and parts[1] else default_overlap
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError(f"bad chunker size/overlap in '{spec}'")
    return size, overlap


def is_valid(spec: str) -> bool:
    return bool(re.fullmatch(r"(?i)off|openai|(langchain|llamaindex)(:\d+(:\d+)?)?|(tokens|words):\d+(:\d+)?", spec))


def chunk(text: str, spec: str | None = None) -> Chunking | None:
    """Chunk spans for `text` under `spec` (default: $RAGPREFLIGHT_CHUNKER or 'langchain')."""
    spec = (spec or current_spec()).strip().lower()
    if spec == "off" or not text:
        return None
    kind = spec.split(":")[0]
    if kind == "langchain":
        size, overlap = _parse(spec, 4000, 200)
        return _langchain(text, size, overlap)
    if kind == "llamaindex":
        size, overlap = _parse(spec, 1024, 200)
        return _llamaindex(text, size, overlap)
    if kind == "openai":
        c = _token_windows(text, 800, 400)
        c.label = "OpenAI file search defaults (800-token windows, 400 overlap; exact boundaries not public)"
        return c
    if kind == "tokens":
        size, overlap = _parse(spec, 512, 0)
        return _token_windows(text, size, overlap)
    if kind == "words":
        size, overlap = _parse(spec, 380, 0)
        return Chunking(_word_windows(text, size, overlap), f"{size}-word windows, {overlap} overlap", "approximation")
    raise ValueError(f"unknown chunker '{spec}'")


# ---------------------------------------------------------------- LangChain
def _langchain(text: str, size: int, overlap: int) -> Chunking:
    label = f"LangChain RecursiveCharacterTextSplitter (chunk_size={size} chars, overlap={overlap})"
    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
    except ImportError:
        chunks = _lc_recursive(text, size, overlap)
        return Chunking(_lc_starts(text, chunks, overlap), label, "bundled port")
    splitter = RecursiveCharacterTextSplitter(chunk_size=size, chunk_overlap=overlap)
    chunks = splitter.split_text(text)
    return Chunking(_lc_starts(text, chunks, overlap), label, "library")


def _lc_starts(text: str, chunks: list[str], overlap: int) -> list[tuple[int, int]]:
    """Start offsets exactly as LangChain's create_documents(add_start_index=True) finds them."""
    spans, index, prev = [], 0, 0
    for c in chunks:
        offset = index + prev - overlap
        found = text.find(c, max(0, offset))
        index = found if found >= 0 else text.find(c)
        if index < 0:
            index = spans[-1][1] if spans else 0
        spans.append((index, index + len(c)))
        prev = len(c)
    return spans


# Port of langchain_text_splitters.RecursiveCharacterTextSplitter (MIT License, (c) LangChain, Inc.)
# with its defaults: separators ["\n\n", "\n", " ", ""], keep_separator="start", strip_whitespace,
# length_function=len. Kept in step with langchain-text-splitters 1.x; tests compare both when installed.
def _lc_split_regex(text: str, separator: str) -> list[str]:
    if separator:
        parts = re.split(f"({separator})", text)
        splits = [parts[i] + parts[i + 1] for i in range(1, len(parts), 2)]
        if len(parts) % 2 == 0:
            splits += parts[-1:]
        splits = [parts[0], *splits]
    else:
        splits = list(text)
    return [s for s in splits if s]


def _lc_merge(splits: list[str], separator: str, size: int, overlap: int) -> list[str]:
    sep_len = len(separator)
    docs: list[str] = []
    cur: list[str] = []
    total = 0
    for d in splits:
        n = len(d)
        if total + n + (sep_len if cur else 0) > size:
            if cur:
                doc = separator.join(cur).strip()
                if doc:
                    docs.append(doc)
                while total > overlap or (total + n + (sep_len if cur else 0) > size and total > 0):
                    total -= len(cur[0]) + (sep_len if len(cur) > 1 else 0)
                    cur = cur[1:]
        cur.append(d)
        total += n + (sep_len if len(cur) > 1 else 0)
    doc = separator.join(cur).strip()
    if doc:
        docs.append(doc)
    return docs


def _lc_recursive(text: str, size: int, overlap: int,
                  separators: tuple[str, ...] = ("\n\n", "\n", " ", "")) -> list[str]:
    final: list[str] = []
    separator = separators[-1]
    rest: tuple[str, ...] = ()
    for i, s in enumerate(separators):
        if not s:
            separator = s
            break
        if re.search(re.escape(s), text):
            separator = s
            rest = separators[i + 1:]
            break
    splits = _lc_split_regex(text, re.escape(separator))
    good: list[str] = []
    for s in splits:
        if len(s) < size:
            good.append(s)
        else:
            if good:
                final.extend(_lc_merge(good, "", size, overlap))
                good = []
            if not rest:
                final.append(s)
            else:
                final.extend(_lc_recursive(s, size, overlap, rest))
    if good:
        final.extend(_lc_merge(good, "", size, overlap))
    return final


# ---------------------------------------------------------------- LlamaIndex
def _llamaindex(text: str, size: int, overlap: int) -> Chunking:
    try:
        from llama_index.core import Document as LIDoc
        from llama_index.core.node_parser import SentenceSplitter
    except ImportError as e:
        raise ChunkerUnavailable("--chunker llamaindex needs: pip install llama-index-core") from e
    nodes = SentenceSplitter(chunk_size=size, chunk_overlap=overlap).get_nodes_from_documents([LIDoc(text=text)])
    spans = []
    for n in nodes:
        s, e = getattr(n, "start_char_idx", None), getattr(n, "end_char_idx", None)
        if s is None or e is None:
            s = text.find(n.get_content())
            e = s + len(n.get_content()) if s >= 0 else s
        if s is not None and s >= 0:
            spans.append((s, e))
    return Chunking(spans, f"LlamaIndex SentenceSplitter (chunk_size={size} tokens, overlap={overlap})", "library")


# ---------------------------------------------------------------- token / word windows
def _token_windows(text: str, size: int, overlap: int) -> Chunking:
    try:
        import tiktoken

        enc = tiktoken.get_encoding("o200k_base")
        toks = enc.encode(text, disallowed_special=())
        _, offs = enc.decode_with_offsets(toks)
        step = max(1, size - overlap)
        spans = []
        for i in range(0, len(toks), step):
            j = min(len(toks), i + size)
            spans.append((offs[i], offs[j] if j < len(toks) else len(text)))
            if j >= len(toks):
                break
        return Chunking(spans, f"{size}-token windows, {overlap} overlap (tiktoken o200k_base)", "library")
    except ImportError:
        w = max(1, round(size * 0.75))
        ow = min(round(overlap * 0.75), w - 1)
        return Chunking(_word_windows(text, w, ow),
                        f"~{size}-token windows, {overlap} overlap (≈{w} words; pip install tiktoken for exact)",
                        "approximation")


def _word_windows(text: str, size: int, overlap: int) -> list[tuple[int, int]]:
    words = [m.span() for m in re.finditer(r"\S+", text)]
    step = max(1, size - overlap)
    spans = []
    for i in range(0, len(words), step):
        j = min(len(words), i + size) - 1
        spans.append((words[i][0], words[j][1]))
        if j >= len(words) - 1:
            break
    return spans
