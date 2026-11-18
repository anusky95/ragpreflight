"""Extraction checks: will this document's text survive parsing?

Findings (one per document per type, pages aggregated):

    extraction-empty    ERROR  F2/F3  readable file that yields almost no words
    extraction-scanned  ERROR/WARN F3 PDF pages that are images with no text layer
    extraction-images   WARN   F4     image-heavy pages/docs with little text
    extraction-garbled  WARN   F3     text that extracted badly (U+FFFD, (cid:NN), mojibake ...)
    extraction-table    WARN   F3     tabular content likely to chunk as run-on text

Design rule: precision over recall. Math symbols, Greek letters, LaTeX and code are
NOT signals of bad extraction; only concrete corruption markers are.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..models import Document, Finding, Page, Severity
from . import rel

NAME = "extraction"

# ---------------------------------------------------------------- thresholds
EMPTY_MAX_WORDS = 20  # fewer words than this in the whole file -> extraction-empty

SCANNED_MAX_CHARS = 25  # a PDF page with images and fewer text chars has no text layer
SCANNED_ERROR_SHARE = 0.5  # >= this share of pages scanned -> ERROR, else WARN

IMAGES_MIN_PER_PAGE = 3  # PDF page: >= this many images ...
IMAGES_MAX_WORDS_PAGE = 80  # ... and fewer words than this -> extraction-images
IMAGES_MIN_DOC = 3  # non-PDF doc: >= this many images ...
IMAGES_MAX_WORDS_PER_IMAGE = 30  # ... and fewer words per image than this

# garbled-text signals (per page; any one crossing its threshold flags the page)
GARBLED_MIN_FFFD = 5  # U+FFFD replacement characters
GARBLED_MIN_CID = 3  # "(cid:123)" tokens (font without a ToUnicode map)
GARBLED_MIN_CONTROL = 8  # C0 control chars other than \t \n \r \f (not lost bullets) ...
GARBLED_MIN_CONTROL_SHARE = 0.005  # ... and at least this share of characters
GARBLED_MIN_MOJIBAKE = 3  # UTF-8-read-as-Latin-1 sequences (Ã©, â€™, Â )
GARBLED_MIN_SPACED_LETTERS = 24  # letters inside "T h i s  i s" runs of >= 8 letters
GARBLED_DICT_MIN_TOKENS = 200  # low-dictionary signal needs this many ASCII words ...
GARBLED_DICT_MAX_STOPWORD_SHARE = 0.03  # ... a stopword share below this ...
GARBLED_DICT_MIN_JUNK_SHARE = 0.10  # ... AND this share of junk tokens (no vowels, l1ke th1s)
GARBLED_MAX_SNIPPETS = 2

# tables
TABLE_MIN_ROWS = 4  # consecutive table-like lines
TABLE_NUMERIC_MIN_TOKENS = 3
TABLE_NUMERIC_MIN_NUMBERS = 2
TABLE_NUMERIC_MIN_SHARE = 0.5
TABLE_PAGE_MIN_NUMBERS = 40  # PDF page where numbers dominate ...
TABLE_PAGE_MIN_NUMBER_SHARE = 0.4  # ... share of all tokens
TABLE_WARN_LINE_SHARE = 0.2  # WARN only when table rows are this share of non-empty lines ...
TABLE_WARN_PAGE_SHARE = 0.3  # ... or this share of PDF pages are table pages; otherwise INFO

MAX_RANGES_SHOWN = 8

# ---------------------------------------------------------------- helpers


def format_pages(pages: list[int]) -> str:
    """[1,2,3,4,6] -> 'p.1–4, 6' (truncated after MAX_RANGES_SHOWN ranges)."""
    ps = sorted(set(pages))
    if not ps:
        return ""
    ranges: list[tuple[int, int]] = []
    start = prev = ps[0]
    for p in ps[1:]:
        if p == prev + 1:
            prev = p
            continue
        ranges.append((start, prev))
        start = prev = p
    ranges.append((start, prev))
    parts = [f"{a}" if a == b else f"{a}–{b}" for a, b in ranges[:MAX_RANGES_SHOWN]]
    more = len(ranges) - MAX_RANGES_SHOWN
    s = "p." + ", ".join(parts)
    if more > 0:
        s += f" (+{more} more ranges)"
    return s


def _snippet(text: str, start: int, end: int, ctx: int = 30) -> str:
    a = max(0, start - ctx)
    b = min(len(text), end + ctx)
    s = text[a:b].replace("\n", " ").replace("\r", " ")
    s = "".join(ch if (ch >= " " or ch == "\t") else f"\\x{ord(ch):02x}" for ch in s)
    s = re.sub(r"\s+", " ", s).strip()
    return ("…" if a > 0 else "") + s + ("…" if b < len(text) else "")


# --- table heuristics (shared with chunking) --------------------------------
_NUM_TOKEN = re.compile(
    r"^[(\[]?[-+−±~<>≈]?[$€£¥]?\d[\d,.:/]*(?:%|[kKmMbB]n?|x)?[)\]]?[,;]?$|^[-–—]$|^n/?a$",
    re.I,
)
_GAP_SPLIT = re.compile(r"\t+| {2,}")
_BULLET = re.compile(r"^(?:[-*•▪◦]|\d{1,2}[.)])\s+")


def is_number_token(tok: str) -> bool:
    return bool(_NUM_TOKEN.match(tok))


def is_table_row(line: str, allow_pipes: bool = True) -> bool:
    """Does this single line look like a row of a flattened table?"""
    s = line.strip()
    if not s or len(s) > 400:
        return False
    if "|" not in s and not _HAS_DIGIT.search(s):
        return False
    s = _BULLET.sub("", s)  # "- Text extractability (30%)" is a list item, not a row
    if allow_pipes and "|" in s and "||" not in s:
        cells = [c for c in s.strip("|").split("|")]
        if len(cells) >= 2 and sum(1 for c in cells if c.strip()) >= 1:
            return True
    if "\t" in s or "  " in s:
        cells = [c for c in _GAP_SPLIT.split(s) if c]
        if len(cells) >= 3 and any(is_number_token(c.strip()) for c in cells):
            return True
    toks = s.split()
    if len(toks) >= TABLE_NUMERIC_MIN_TOKENS:
        nums = sum(1 for t in toks if is_number_token(t))
        if nums >= TABLE_NUMERIC_MIN_NUMBERS and nums / len(toks) >= TABLE_NUMERIC_MIN_SHARE:
            return True
    return False


def find_table_runs(lines: list[str], min_rows: int = TABLE_MIN_ROWS,
                    allow_pipes: bool = True) -> list[tuple[int, int]]:
    """Runs of >= min_rows table-like lines: list of (first_line_idx, last_line_idx).

    Blank lines inside a run are tolerated; any other non-row line ends it.
    """
    runs: list[tuple[int, int]] = []
    start = last = -1
    rows = 0
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if is_table_row(line, allow_pipes):
            if start < 0:
                start = i
                rows = 0
            rows += 1
            last = i
        else:
            if start >= 0 and rows >= min_rows:
                runs.append((start, last))
            start = -1
            rows = 0
    if start >= 0 and rows >= min_rows:
        runs.append((start, last))
    return runs


# --- garbled-text signals ---------------------------------------------------
_CID = re.compile(r"\(cid:\d+\)")
# a control char right after a newline/space is usually a lost bullet glyph, not garble
_CONTROL = re.compile(r"(?<![\n ])[\x00-\x08\x0b\x0e-\x1f\x7f]")
_FFFD = re.compile("�")
# UTF-8 bytes decoded as Latin-1 / cp1252: Ã© Ã¨ Ã¼, â€™ â€œ â€", Â followed by a Latin-1 symbol
_MOJIBAKE = re.compile(r"Ã[\u0080-¿]|â€[\u0080-¿‐-™Œ-ž]|Â[ -¿]")
_SPACED = re.compile(r"(?<![A-Za-z])(?:[A-Za-z] {1,2}){7,}[A-Za-z](?![A-Za-z])")
_STOPWORDS = (
    # en
    "the of and to in is that for it as was with be by on are this or from at which have not"
    " an but they his her were their has will would can been we you all one there if more"
    # fr / es / pt / it / de / nl (most frequent function words)
    " le la les des du et est une dans pour que qui pas sur au el los las del en por con para"
    " una es lo se da do em os um uma il di che non per della der die das und ist nicht mit"
    " ein eine den von zu het een van op dat"
).split()
_STOP_RE = re.compile(r"\b(?:" + "|".join(sorted(set(_STOPWORDS), key=len, reverse=True)) + r")\b", re.I)
_ASCII_WORD = re.compile(r"(?<![\w\\])[A-Za-z][A-Za-z0-9]*(?![\w(])")
_JUNK_SCAN = re.compile(
    r"(?<![\w\\])(?:[b-df-hj-np-tv-xzB-DF-HJ-NP-TV-XZ]{4,}|[A-Za-z]+\d+[A-Za-z]+[A-Za-z0-9]*)(?![\w(])"
)
_HAS_DIGIT = re.compile(r"\d")
_NUMLIKE_START = re.compile(r"(?<!\S)[(\[]?[-+−]?[$€£¥]?\d")


def _garbled_signals(text: str) -> list[tuple[str, list[str], int]]:
    """Return [(signal description, evidence snippets, count)] for signals over threshold."""
    hits: list[tuple[str, list[str], int]] = []
    n_chars = len(text)
    if n_chars == 0:
        return hits

    n = text.count("�")
    if n >= GARBLED_MIN_FFFD:
        snips = []
        pos = -1
        for _ in range(GARBLED_MAX_SNIPPETS):
            pos = text.find("�", pos + 1)
            if pos < 0:
                break
            snips.append(_snippet(text, pos, pos + 1))
            pos += 40  # don't repeat the same neighbourhood
        hits.append((f"{n} replacement characters (U+FFFD)", snips, n))

    if "(cid:" in text:
        ms = _CID.findall(text)
        if len(ms) >= GARBLED_MIN_CID:
            snips = [_snippet(text, m.start(), m.end())
                     for m in list(_CID.finditer(text))[:GARBLED_MAX_SNIPPETS]]
            hits.append((f"{len(ms)} '(cid:NN)' glyph codes (font has no Unicode map)", snips, len(ms)))

    ctrl = _CONTROL.findall(text)
    if len(ctrl) >= GARBLED_MIN_CONTROL and len(ctrl) / n_chars >= GARBLED_MIN_CONTROL_SHARE:
        snips = [_snippet(text, m.start(), m.end())
                 for m in list(_CONTROL.finditer(text))[:GARBLED_MAX_SNIPPETS]]
        hits.append((f"{len(ctrl)} control characters", snips, len(ctrl)))

    moj = list(_MOJIBAKE.finditer(text))
    if len(moj) >= GARBLED_MIN_MOJIBAKE:
        snips = [_snippet(text, m.start(), m.end()) for m in moj[:GARBLED_MAX_SNIPPETS]]
        hits.append((f"{len(moj)} mojibake sequences (UTF-8 read as Latin-1, e.g. 'Ã©', 'â€™')",
                     snips, len(moj)))

    spaced = list(_SPACED.finditer(text))
    if spaced:
        letters = sum(sum(1 for c in m.group(0) if c.isalpha()) for m in spaced)
        if letters >= GARBLED_MIN_SPACED_LETTERS:
            snips = [_snippet(text, m.start(), m.end(), 10) for m in spaced[:GARBLED_MAX_SNIPPETS]]
            hits.append((f"{len(spaced)} letter-spaced runs ('T h i s  i s'), {letters} letters",
                         snips, len(spaced)))

    # Low dictionary share: only for Latin-script text, and only with a second, independent
    # junk-token signal, so non-English prose, tables and code don't trip it.
    # (cheap junk pre-scan first: the stopword regex is the slowest step of this check)
    junk = [w for w in _JUNK_SCAN.findall(text) if not w.isupper()]
    min_junk = GARBLED_DICT_MIN_TOKENS * GARBLED_DICT_MIN_JUNK_SHARE
    words = _ASCII_WORD.findall(text) if len(junk) >= min_junk else []
    if len(words) >= GARBLED_DICT_MIN_TOKENS and len(junk) / len(words) >= GARBLED_DICT_MIN_JUNK_SHARE:
        stop = len(_STOP_RE.findall(text))
        if stop / len(words) < GARBLED_DICT_MAX_STOPWORD_SHARE:
            hits.append((
                f"few dictionary words: {stop}/{len(words)} common words, "
                f"{len(junk)} junk tokens",
                [" ".join(junk[:8])],
                len(junk),
            ))
    return hits


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


# ---------------------------------------------------------------- per-document checks


def _check_doc(doc: Document, path: str) -> list[Finding]:
    out: list[Finding] = []
    pages: list[Page] = list(doc.pages or [])
    is_pdf = doc.fmt == "pdf"
    total_words = sum(len(p.text.split()) for p in pages)
    total_images = sum(max(0, p.image_count or 0) for p in pages)

    # --- scanned (PDF only)
    scanned = [p.number for p in pages
               if is_pdf and (p.image_count or 0) >= 1 and len(p.text.strip()) < SCANNED_MAX_CHARS]
    if scanned:
        share = len(scanned) / max(1, len(pages))
        sev = Severity.ERROR if share >= SCANNED_ERROR_SHARE else Severity.WARN
        loc = format_pages(scanned)
        what = "All pages" if len(scanned) == len(pages) else f"{len(scanned)} of {len(pages)} pages"
        out.append(Finding(
            "extraction-scanned", sev, ["F3"],
            f"{what} scanned with no text layer ({loc}); content invisible to AI unless OCR'd",
            path, loc,
            detail=(f"{len(scanned)}/{len(pages)} page(s) contain images but fewer than "
                    f"{SCANNED_MAX_CHARS} characters of extractable text: {loc}."),
            fix="Run OCR before ingesting (e.g. `ocrmypdf --skip-text in.pdf out.pdf`), "
                "then re-scan to confirm the text layer.",
        ))

    # --- empty
    all_scanned = bool(scanned) and len(scanned) == len(pages)
    if total_words < EMPTY_MAX_WORDS:
        if not all_scanned:
            img_note = (f" It contains {total_images} image(s): content may be in pictures."
                        if total_images else "")
            out.append(Finding(
                "extraction-empty", Severity.ERROR, ["F2", "F3"],
                f"Only {total_words} word(s) of text extracted; this file adds nothing to the index",
                path, "",
                detail=(f"{doc.fmt.upper()} file with {len(pages)} page(s)/section(s) yields "
                        f"{total_words} words (threshold {EMPTY_MAX_WORDS}).{img_note}"),
                fix="Check the source: re-export from the original, OCR it if it is a scan, "
                    "or remove it from the corpus.",
            ))
        return out  # nothing else meaningful to say about near-empty text

    # --- images
    if is_pdf:
        img_pages = [p.number for p in pages
                     if p.number not in scanned
                     and (p.image_count or 0) >= IMAGES_MIN_PER_PAGE
                     and len(p.text.split()) < IMAGES_MAX_WORDS_PAGE]
        if img_pages:
            loc = format_pages(img_pages)
            n_img = sum(p.image_count for p in pages if p.number in img_pages)
            out.append(Finding(
                "extraction-images", Severity.WARN, ["F4"],
                f"Image-heavy pages ({loc}): charts/figures carry content that becomes nothing as text",
                path, loc,
                detail=(f"{len(img_pages)} page(s) have >= {IMAGES_MIN_PER_PAGE} images "
                        f"({n_img} in total) and fewer than {IMAGES_MAX_WORDS_PAGE} words of text."),
                fix="Add text descriptions/captions for key figures, or use a multimodal parser "
                    "(e.g. Docling, a VLM captioner) that turns charts into text.",
            ))
    elif total_images >= IMAGES_MIN_DOC and total_words / total_images < IMAGES_MAX_WORDS_PER_IMAGE:
        out.append(Finding(
            "extraction-images", Severity.WARN, ["F4"],
            f"{total_images} images vs {total_words} words: figures carry content that becomes nothing as text",
            path, "",
            detail=(f"{total_words / total_images:.0f} words per image "
                    f"(threshold {IMAGES_MAX_WORDS_PER_IMAGE})."),
            fix="Add alt text / captions describing what each image shows, or use a "
                "multimodal parser that converts figures to text.",
        ))

    # --- garbled
    garbled_pages: list[int] = []
    evidence: list[str] = []
    signal_names: list[str] = []
    for p in pages:
        sigs = _garbled_signals(p.text or "")
        if not sigs:
            continue
        garbled_pages.append(p.number)
        for desc, snips, _ in sigs:
            short = desc.split(" (")[0]
            if short not in signal_names:
                signal_names.append(short)
            if len(evidence) < GARBLED_MAX_SNIPPETS:
                where = f"p.{p.number}" if is_pdf else "text"
                for s in snips:
                    if len(evidence) >= GARBLED_MAX_SNIPPETS:
                        break
                    evidence.append(f"[{where}] {desc}: \"{s}\"")
    if garbled_pages:
        loc = format_pages(garbled_pages) if is_pdf else ""
        if not is_pdf:
            # point at the first evidence line for non-paged formats
            for p in pages:
                txt = p.text or ""
                for rx in (_FFFD, _CID, _CONTROL, _MOJIBAKE, _SPACED):
                    m = rx.search(txt)
                    if m:
                        loc = f"line {_line_of(txt, m.start())}"
                        break
                if loc:
                    break
        where = f" on {loc}" if is_pdf else ""
        out.append(Finding(
            "extraction-garbled", Severity.WARN, ["F3"],
            f"Garbled text{where}: {', '.join(signal_names[:3])}",
            path, loc,
            detail="Evidence: " + " | ".join(evidence),
            fix="Re-extract with a different parser (pdfplumber, PyMuPDF, Docling) or OCR the "
                "affected pages; for mojibake re-read the source as UTF-8.",
        ))

    # --- tables
    table_pages: list[int] = []
    rows_total = 0
    sample = ""
    for p in pages:
        txt = p.text or ""
        lines = txt.splitlines()
        runs = find_table_runs(lines, allow_pipes=doc.fmt != "md")
        hit = False
        if runs:
            hit = True
            for a, b in runs:
                rows_total += sum(1 for ln in lines[a:b + 1] if ln.strip())
            if not sample:
                a, _ = runs[0]
                sample = re.sub(r"\s+", " ", lines[a].strip())[:120]
        elif is_pdf and len(_NUMLIKE_START.findall(txt)) >= TABLE_PAGE_MIN_NUMBERS:
            toks = txt.split()
            nums = sum(1 for t in toks if is_number_token(t)) if toks else 0
            if nums >= TABLE_PAGE_MIN_NUMBERS and nums / len(toks) >= TABLE_PAGE_MIN_NUMBER_SHARE:
                hit = True
                if not sample:
                    sample = " ".join(toks[:15])
        if hit:
            table_pages.append(p.number)
    if table_pages:
        nonempty = sum(1 for p in pages for ln in (p.text or "").splitlines() if ln.strip()) or 1
        heavy = (rows_total / nonempty >= TABLE_WARN_LINE_SHARE
                 or (is_pdf and len(table_pages) / max(1, len(pages)) >= TABLE_WARN_PAGE_SHARE))
        if is_pdf:
            loc = format_pages(table_pages)
            title = f"Table-heavy content on {loc} will likely extract as run-on text"
        else:
            loc = ""
            title = (f"Table content ({rows_total} rows) will be chunked as flat text "
                     "without its header row")
        out.append(Finding(
            "extraction-table", Severity.WARN if heavy else Severity.INFO, ["F3"],
            title, path, loc,
            detail=(f"Detected {rows_total or 'numeric-dense'} table-like row(s)"
                    f"{' on ' + loc if loc else ''}. First row: \"{sample}\"."),
            fix="Extract tables with a table-aware parser (Docling, pdfplumber) and store them "
                "as markdown/rows with the header repeated in every chunk.",
        ))
    return out


def run(docs: list[Document], root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for doc in docs:
        try:
            if doc.read_error is not None:
                continue
            findings.extend(_check_doc(doc, rel(doc.path, root)))
        except Exception:  # noqa: BLE001 - never let one odd document break the scan
            continue
    return findings
