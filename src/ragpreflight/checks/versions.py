"""Version families, stale copies, conflicting statements and duplicates (F1, F14, F16).

The headline check. Finds documents that are versions or copies of one another, works out
which one is newest, and flags:

* ``duplicate``             exact copies (same normalized text)
* ``version-stale``         older versions still sitting next to the newer one
* ``version-conflict``      aligned sentences whose numbers or polarity disagree across versions
* ``version-period-twins``  recurring reports (2022 / 2023 10-K, Q1 / Q2) that a retriever can mix up

Design goals, in order: precision (a linter that cries wolf gets uninstalled), determinism,
speed (thousands of documents in seconds, stdlib only).

How documents are grouped into families:

1. Filename key: the stem with version tokens (v2, final, draft, copy, (1), old, rev ...) and period
   tokens (years, dates, quarters, FY, months) removed. Same key + different raw name + some shared
   text (``NAME_SIM_MIN``) => same family.
2. Text similarity: word 5-gram shingle Jaccard. Candidate pairs come from one-permutation MinHash
   with LSH banding (no all-pairs comparison); exact Jaccard is computed only for candidates.
   ``TEXT_SIM`` or more with *compatible* filenames => same family. Two files whose names share no
   stem (``nda-acme`` vs ``nda-globex``) are treated as separate documents built from one template,
   not versions, to avoid false conflicts.
3. Metadata title: same non-generic title + ``TEXT_SIM`` + a version/period token in a name.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from ..models import Document, Finding, Severity
from . import locate, rel

NAME = "versions"

# ---------------------------------------------------------------------------------------------
# Thresholds (kept here so they are easy to tune and to cite in docs)
# ---------------------------------------------------------------------------------------------
SHINGLE_SIZE = 5            # words per shingle
TEXT_SIM = 0.5              # shingle Jaccard for "same document" when filenames are merely compatible
NAME_SIM_MIN = 0.2          # shingle Jaccard needed when the filename key matches exactly
PERIOD_SIM_MIN = 0.1        # ... or this, when the names differ only by a period (year/quarter/FY)
PERIOD_REVISION_SIM = 0.5   # same-key period family at/above this (and no report keyword) => revisions
NAME_COMPAT_MIN = 0.5       # token Jaccard of filename keys for "compatible" names
MIN_DOC_WORDS = 20          # documents shorter than this are ignored for families
MIN_DUP_WORDS = 10          # and for exact-duplicate detection
OPH_BINS = 32               # one-permutation MinHash bins
LSH_ROWS = 2                # bins per LSH band  -> 16 bands; P(candidate | J=0.5) ~ 0.99
MAX_BUCKET = 50             # LSH buckets bigger than this are boilerplate; skipped
MAX_NAME_BLOCK = 50         # filename/title blocks bigger than this are skipped (e.g. daily logs)
MAX_FAMILY = 20             # revision families bigger than this are not analysed (structural, noisy)
SAMPLE_ABOVE_WORDS = 3_000_000  # corpus size above which shingles are hash-sampled (1 in SAMPLE_MOD)
SAMPLE_MOD = 4

SENT_MIN_WORDS = 5          # ignore shorter sentences for conflicts
SENT_ALIGN_SIM = 0.5        # content-word Jaccard to align two sentences
SENT_OVERLAP_SIM = 0.8      # ... or overlap coefficient with at least SENT_OVERLAP_MIN shared words
SENT_OVERLAP_MIN = 5
SEQ_MIN_RATIO = 0.5         # difflib word-sequence ratio an aligned pair needs to count as a rewrite
POLARITY_SEQ_RATIO = 0.5    # polarity-only conflicts (the interior-edit rule is the main guard)
POLARITY_EDIT_MAX = 6       # a polarity word must sit in an interior edit of at most this many words
MAX_CONFLICTS_PER_FAMILY = 5
MAX_SENTENCES = 4000        # per document, for conflict search
QUOTE_CHARS = 60

DATE_LINE_SCAN = 40         # first N lines searched for "Effective/Updated <date>"
MTIME_MIN_SPREAD = 86400.0  # seconds; mtimes closer than this (e.g. a git clone) are not trusted

SUPERSEDED_STATUSES = {"superseded", "deprecated", "archived", "obsolete", "retired", "inactive"}

FIX_STALE = ("Remove or archive the older version, or mark it superseded (e.g. frontmatter "
             "`status: superseded`, `superseded_by:`) so retrieval can filter it.")
FIX_CONFLICT = "Keep one source of truth; if both must stay, make the date/scope explicit in each document."
FIX_PERIOD = "Put the period in each chunk's metadata/text and filter by it at query time."
FIX_DUP = "Keep one copy; duplicates waste index space and double-weight the same passage in retrieval."

# ---------------------------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------------------------
_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9,
    "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_NB = r"(?<![a-z0-9])"   # not preceded by alnum
_NA = r"(?![a-z0-9])"    # not followed by alnum
_RE_FULLDATE = re.compile(r"(?<!\d)((?:19|20)\d\d)[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?![a-z0-9])")
_RE_YEARMONTH = re.compile(r"(?<!\d)((?:19|20)\d\d)[-_.](0[1-9]|1[0-2])(?![a-z0-9])")  # not "2021_10k"
_RE_FY = re.compile(_NB + r"fy[-_ ]?(\d{4}|\d{2})" + _NA)
_RE_QUARTER = re.compile(r"(?<![a-z0-9])q([1-4])(?![0-9])")
_RE_HALF = re.compile(_NB + r"h([12])" + _NA)
_RE_YEAR = re.compile(r"(?<!\d)((?:19|20)\d\d)(?!\d)")
_RE_COPY = re.compile(r"\(\d+\)|" + _NB + r"copy of" + _NA + "|" + _NB + r"copy\d*" + _NA)
_RE_VERSION = re.compile(_NB + r"(?:v|ver|version|rev|revision|r)[-_ .]?(\d+(?:\.\d+)*)" + _NA)
_REV_WORDS = {"final", "draft", "old", "new", "latest", "revised", "revision", "rev", "updated",
              "update", "backup", "bak", "orig", "original", "current", "copy", "wip", "edited",
              "clean", "redline", "tracked", "approved", "signed", "superseded", "archive", "archived"}
_REV_RANK = {"old": -2, "orig": -2, "original": -2, "backup": -2, "bak": -2, "archive": -2,
             "archived": -2, "superseded": -2, "new": 1, "latest": 2}

_REPORT_WORDS = {"report", "reports", "10k", "10q", "20f", "8k", "annual", "quarterly", "monthly",
                 "weekly", "daily", "statement", "statements", "minutes", "newsletter", "earnings",
                 "results", "budget", "forecast", "audit", "filing", "financials", "accounts",
                 "census", "survey", "review", "summary", "digest", "bulletin", "update", "outlook",
                 "proxy", "transcript", "deck", "roadmap", "plan", "kpi", "kpis", "metrics"}
_POLICY_WORDS = {"policy", "policies", "terms", "conditions", "tos", "handbook", "guide", "guidelines",
                 "procedure", "procedures", "sop", "manual", "agreement", "contract", "faq", "faqs",
                 "rules", "standard", "standards", "spec", "specification", "playbook", "charter",
                 "code", "regulation", "regulations", "privacy", "refund", "refunds", "pricing",
                 "tariff", "conduct", "process", "instructions", "requirements", "eligibility",
                 "benefits", "allowance", "fees", "fares"}
_GENERIC_TITLES = {"untitled", "document", "title", "none", "unknown", "draft", "slide 1", "presentation",
                   "powerpoint presentation", "microsoft word document", "report", "notes", "readme",
                   "index", "home", "page", "new document", "template"}


class _Name:
    __slots__ = ("key", "tokens", "display", "period_label", "period_date", "years", "rev_tags",
                 "version", "has_quarter")

    def __init__(self, stem: str):
        s = stem.lower()
        period: list[str] = []
        pdate: tuple[int, int, int] | None = None
        years: list[int] = []
        rev: list[str] = []
        version: tuple[int, ...] | None = None
        has_quarter = False

        def cut(m: re.Match) -> str:
            return " "

        for m in _RE_FULLDATE.finditer(s):
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            period.append(f"{y:04d}-{mo:02d}-{d:02d}")
            years.append(y)
            pdate = pdate or (y, mo, d)
        s = _RE_FULLDATE.sub(cut, s)
        for m in _RE_YEARMONTH.finditer(s):
            y, mo = int(m.group(1)), int(m.group(2))
            period.append(f"{y:04d}-{mo:02d}")
            years.append(y)
            pdate = pdate or (y, mo, 0)
        s = _RE_YEARMONTH.sub(cut, s)
        quarter = 0
        for m in _RE_QUARTER.finditer(s):
            quarter = int(m.group(1))
            period.append(f"Q{quarter}")
            has_quarter = True
        s = _RE_QUARTER.sub(cut, s)
        for m in _RE_HALF.finditer(s):
            period.append(f"H{m.group(1)}")
            has_quarter = True
        s = _RE_HALF.sub(cut, s)
        for m in _RE_FY.finditer(s):
            v = m.group(1)
            y = int(v) if len(v) == 4 else 2000 + int(v)
            period.append(f"FY{y}")
            years.append(y)
            has_quarter = True
            pdate = pdate or (y, 0, 0)
        s = _RE_FY.sub(cut, s)
        for m in _RE_VERSION.finditer(s):
            try:
                version = tuple(int(x) for x in m.group(1).split("."))
            except ValueError:
                pass
            rev.append("v" + m.group(1))
        s = _RE_VERSION.sub(cut, s)
        for _m in _RE_COPY.finditer(s):
            rev.append("copy")
        s = _RE_COPY.sub(cut, s)
        for m in _RE_YEAR.finditer(s):
            y = int(m.group(1))
            period.append(str(y))
            years.append(y)
            if pdate is None:
                pdate = (y, (quarter - 1) * 3 + 1 if quarter else 0, 0)
        s = _RE_YEAR.sub(cut, s)

        tokens: list[str] = []
        month = 0
        for t in re.split(r"[^a-z0-9]+", s):
            if not t:
                continue
            if t in _MONTHS and len(t) >= 3:
                period.append(t[:3])
                month = month or _MONTHS[t]
                continue
            if t in _REV_WORDS:
                rev.append(t)
                continue
            tokens.append(t)
        if pdate is not None and month and pdate[1] == 0:
            pdate = (pdate[0], month, 0)

        self.tokens = tokens
        self.key = " ".join(tokens)
        self.display = "-".join(tokens)
        self.period_label = " ".join(period)
        self.period_date = pdate
        self.years = years
        self.rev_tags = rev
        self.version = version
        self.has_quarter = has_quarter


def _names_compatible(a: _Name, b: _Name) -> bool:
    if not a.tokens or not b.tokens:
        return False
    sa, sb = set(a.tokens), set(b.tokens)
    if sa <= sb or sb <= sa:
        return True
    return len(sa & sb) / len(sa | sb) >= NAME_COMPAT_MIN


# ---------------------------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------------------------
_RE_ISO = re.compile(r"(?<!\d)((?:19|20)\d\d)[-/.](\d{1,2})(?:[-/.](\d{1,2}))?(?!\d)")
_RE_MDY_NAME = re.compile(r"\b([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+((?:19|20)\d\d)\b")
_RE_DMY_NAME = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3,9})\.?,?\s+((?:19|20)\d\d)\b")
_RE_MY_NAME = re.compile(r"\b([a-z]{3,9})\.?,?\s+((?:19|20)\d\d)\b")
_RE_NUMERIC_DMY = re.compile(r"(?<!\d)(\d{1,2})[/.-](\d{1,2})[/.-]((?:19|20)\d\d)(?!\d)")
_RE_DATE_LINE = re.compile(
    r"^\W*(?:last\s+)?(?:effective(?:\s+date)?|updated|revised|modified|reviewed|issued|published|"
    r"as\s+of|valid\s+from|version\s+date|date(?:d)?)\b\s*(?:on|from|date)?\s*[:\-–—]?\s*(.{4,60})",
    re.I,
)


def _parse_date(s: str) -> tuple[int, int, int] | None:
    """Best-effort date from a short string -> (year, month or 0, day or 0)."""
    if not s:
        return None
    t = str(s).strip().lower()
    m = _RE_ISO.search(t)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        d = int(m.group(3)) if m.group(3) else 0
        if 1 <= mo <= 12 and 0 <= d <= 31:
            return (y, mo, d)
        return (y, 0, 0)
    m = _RE_MDY_NAME.search(t)
    if m and m.group(1) in _MONTHS:
        return (int(m.group(3)), _MONTHS[m.group(1)], int(m.group(2)))
    m = _RE_DMY_NAME.search(t)
    if m and m.group(2) in _MONTHS:
        return (int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)))
    m = _RE_MY_NAME.search(t)
    if m and m.group(1) in _MONTHS:
        return (int(m.group(2)), _MONTHS[m.group(1)], 0)
    m = _RE_NUMERIC_DMY.search(t)
    if m:  # dd/mm/yyyy vs mm/dd/yyyy is ambiguous: keep the year only
        return (int(m.group(3)), 0, 0)
    m = _RE_YEAR.search(t)
    if m:
        return (int(m.group(1)), 0, 0)
    return None


def _cmp_dates(a: tuple, b: tuple) -> int:
    """Precision-aware compare: 2024 vs 2024-03-01 is a tie, not 'older'."""
    for i in range(len(a)):
        if a[i] == 0 or b[i] == 0:
            return 0
        if a[i] != b[i]:
            return -1 if a[i] < b[i] else 1
    return 0


def _fmt_date(d: tuple) -> str:
    y, m, dd = d[0], d[1], d[2]
    if m and dd:
        return f"{y:04d}-{m:02d}-{dd:02d}"
    if m:
        return f"{y:04d}-{m:02d}"
    return f"{y:04d}"


def _text_date(text: str) -> tuple[int, int, int] | None:
    for line in text.splitlines()[:DATE_LINE_SCAN]:
        m = _RE_DATE_LINE.match(line.strip())
        if m:
            d = _parse_date(m.group(1))
            if d:
                return d
    return None


# ---------------------------------------------------------------------------------------------
# Per-document preparation
# ---------------------------------------------------------------------------------------------
_RE_WORD = re.compile(r"[a-z0-9]+(?:['’][a-z]+)?")


class _Doc:
    __slots__ = ("i", "doc", "rel", "text", "words", "shingles", "sig", "name", "title", "status",
                 "date", "date_src", "mtime", "norm_hash")


def _prepare(doc: Document, i: int, root: Path) -> _Doc | None:
    if doc.read_error is not None:
        return None
    d = _Doc()
    d.i = i
    d.doc = doc
    d.rel = rel(doc.path, root)
    try:
        d.text = doc.text or ""
    except Exception:  # noqa: BLE001
        d.text = ""
    d.words = _RE_WORD.findall(d.text.lower())
    d.shingles = set()
    d.sig = None
    d.name = _Name(doc.path.stem)
    meta = doc.metadata or {}
    title = str(meta.get("title", "") or "").strip().lower()
    d.title = "" if (title in _GENERIC_TITLES or len(title) < 4) else re.sub(r"\s+", " ", title)
    d.status = str(meta.get("status", "") or "").strip().lower()
    d.norm_hash = (hashlib.sha1(" ".join(d.words).encode("utf-8")).hexdigest()
                   if len(d.words) >= MIN_DUP_WORDS else None)

    meta_date = _parse_date(str(meta.get("date", "") or ""))
    name_date = d.name.period_date
    binary = (doc.fmt or "").lower() in ("pdf", "docx", "html", "htm")
    d.date, d.date_src = None, ""
    # For binary formats metadata dates are written by tools (re-saves, exports); prefer the filename.
    order = ([(name_date, "filename"), (meta_date, "metadata")] if binary
             else [(meta_date, "metadata"), (name_date, "filename")])
    for val, src in order:
        if val:
            d.date, d.date_src = val, src
            break
    if d.date is None:
        td = _text_date(d.text)
        if td:
            d.date, d.date_src = td, "text"
    d.mtime = None
    try:
        if doc.path.exists():
            d.mtime = doc.path.stat().st_mtime
    except (OSError, ValueError):
        d.mtime = None
    return d


def _build_shingles(docs: list[_Doc]) -> None:
    total = sum(len(d.words) for d in docs)
    mod = SAMPLE_MOD if total > SAMPLE_ABOVE_WORDS else 1
    vocab: dict[str, int] = {}
    mask = 0xFFFFFFFFFFFFFFFF
    k = SHINGLE_SIZE
    for d in docs:
        if len(d.words) < max(k, MIN_DOC_WORDS):
            continue
        ids = [vocab.setdefault(w, len(vocab)) for w in d.words]
        # hash() of a tuple of ints is deterministic across runs (only str/bytes hashing is salted)
        hs = {hash(t) & mask for t in zip(*(ids[j:] for j in range(k)), strict=False)}
        if mod > 1:
            hs = {h for h in hs if h % mod == 0}
        d.shingles = hs
        mins = [mask + 1] * OPH_BINS
        for h in hs:
            x = (h * 0x9E3779B97F4A7C15) & mask
            b = x >> 59  # top 5 bits -> 32 bins
            if x < mins[b]:
                mins[b] = x
        d.sig = mins


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


# ---------------------------------------------------------------------------------------------
# Union-find
# ---------------------------------------------------------------------------------------------
class _UF:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            if ra < rb:
                self.p[rb] = ra
            else:
                self.p[ra] = rb


# ---------------------------------------------------------------------------------------------
# Sentences and conflicts
# ---------------------------------------------------------------------------------------------
_STOP = set(["a", "an", "the", "and", "or", "of", "to", "in", "on", "at", "by", "for", "from", "with", "as", "is", "are", "was", "were", "be", "been", "being", "this", "that", "these", "those", "it", "its", "it's", "into", "than", "then", "there", "their", "they", "them", "we", "our", "you", "your", "he", "she", "his", "her", "i", "me", "my", "which", "who", "whom", "whose", "what", "when", "where", "why", "how", "will", "would", "shall", "should", "can", "could", "has", "have", "had", "do", "does", "did", "also", "any", "all", "each", "per", "via", "such", "other", "more", "most", "less", "same", "so", "if", "up", "out", "about", "over", "under", "within", "upon", "onto", "off", "own", "very", "just", "both", "either", "etc", "may", "must", "might"])
_NEG = {"not", "no", "never", "cannot", "nor", "none", "non", "without", "neither", "nothing", "nobody"}
_LIMIT = {"only", "except", "unless", "excluding"}
_POLAR_PAIRS = [({"before", "prior"}, {"after"}), ({"may", "can"}, {"must", "shall", "required"}),
                ({"allowed", "permitted"}, {"prohibited", "forbidden"}),
                ({"required", "mandatory"}, {"optional"}), ({"eligible"}, {"ineligible"}),
                ({"include", "includes", "included"}, {"exclude", "excludes", "excluded"})]
_IDIOMS = re.compile(r"\b(?:no later than|not later than|no more than|not more than|no less than|"
                     r"not less than|not only|whether or not|if not|no\.\s*\d+|no matter|non-?stop|"
                     r"not applicable|n/a)\b", re.I)
_NUM_SKIP_PREV = {"section", "sections", "page", "pages", "chapter", "step", "article", "table", "figure",
                  "fig", "item", "part", "clause", "appendix", "annex", "schedule", "exhibit", "no",
                  "number", "rule", "paragraph", "para", "line", "version", "v", "rev", "phone", "tel",
                  "call", "fax", "flight", "gate", "room", "suite", "terminal", "id", "ref", "form"}
_UNIT_SKIP = {"and", "or", "to", "of", "the", "a", "an", "in", "on", "at", "for", "by", "with", "from",
              "is", "are", "was", "were", "be", "x", "am", "pm", "pin", "ext", "th", "st", "nd", "rd",
              "utc", "gmt", "est", "cst", "pst", "edt", "cdt", "pdt", "bst", "cet", "ist",
              # table-of-contents / cross-reference words: "Risk Factors 15 Item 1B" is a page number
              "item", "items", "part", "page", "pages", "section", "chapter", "exhibit", "note",
              "notes", "figure", "table", "schedule", "appendix", "article"}
_NUM_GLUE = set("-:/#=_.@+")
_CONDITIONAL = {"if", "when", "whether", "unless", "suppose", "imagine", "what", "why", "how"}
_CURRENCY = "$€£¥"
_RE_QTOKEN = re.compile(r"[$€£¥]\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?%|"
                        r"\d[\d,]*(?:\.\d+)?|[a-z]+(?:['’][a-z]+)?", re.I)
_RE_LIST_PREFIX = re.compile(r"^\s*(?:[#>*\-•|]+|\(?\d+(?:\.\d+)*[.)]|\(?[a-z][.)])\s+", re.I)
_RE_SENT_SPLIT = re.compile(r"(?<=[.!?;])\s+(?=[\"'(\[A-Z0-9$€£])")


def _norm_word(w: str) -> str:
    w = w.replace("’", "'")
    if w.endswith("n't"):
        return "not"
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


class _Sent:
    __slots__ = ("text", "page", "content", "quants", "neg", "markers", "norm", "nwords")


def _segments(text: str) -> list[str]:
    """Paragraph-aware segments: wrapped lines joined, bullets/headings kept separate."""
    segs: list[str] = []
    cur: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if cur:
                segs.append(" ".join(cur))
                cur = []
            continue
        starts_new = bool(_RE_LIST_PREFIX.match(line)) or line.startswith("#")
        if starts_new or line.startswith("|"):
            if cur:
                segs.append(" ".join(cur))
                cur = []
            if line.startswith("#") or line.startswith("|"):
                segs.append(line)
                continue
        cur.append(line)
    if cur:
        segs.append(" ".join(cur))
    return segs


def _quantities(text: str, ignore_years: set[int]) -> dict[str, set[float]]:
    """Numbers with a unit ($, %, or the following word). Skips ids, times, phone numbers, dates,
    section numbers, and years that are the family's own version years."""
    q: dict[str, set[float]] = {}
    toks = list(_RE_QTOKEN.finditer(text))
    for i, m in enumerate(toks):
        tok = m.group(0)
        if not tok or not (tok[0].isdigit() or tok[0] in _CURRENCY):
            continue
        if i == 0:
            continue  # list numbering / section number at sentence start
        before = text[m.start() - 1] if m.start() > 0 else " "
        after = text[m.end():m.end() + 2]
        if before in _NUM_GLUE or before.isalnum():
            continue  # 216-525-9227, 7:07, f:1856, id=42, abc123
        if after[:1] in _NUM_GLUE and after[1:2].isalnum():
            continue
        if after[:1].isalpha() and not tok.endswith("%"):
            continue  # 9_30pm, 3rd, 10k (glued suffix)
        prev = toks[i - 1].group(0).lower()
        if prev in _NUM_SKIP_PREV:
            continue
        raw = tok.replace(" ", "")
        unit = ""
        if raw[0] in _CURRENCY:
            unit = raw[0]
            raw = raw[1:]
        elif raw.endswith("%"):
            unit = "%"
            raw = raw[:-1]
        if raw.count(".") > 1 or len(raw.replace(",", "").split(".")[0]) > 7:
            continue
        try:
            val = float(raw.replace(",", ""))
        except ValueError:
            continue
        nxt = toks[i + 1].group(0).lower() if i + 1 < len(toks) else ""
        if re.fullmatch(r"\d+\.\d+", raw) and not unit and not nxt.isalpha():
            continue  # "3.2" section reference
        if not unit:
            if 1900 <= val <= 2100 and float(val).is_integer() and int(val) in ignore_years:
                continue
            if nxt in ("percent", "pct"):
                unit = "%"
            elif (nxt.isalpha() and nxt not in _UNIT_SKIP and len(nxt) > 1
                  and text[m.end():toks[i + 1].start()].strip() in ("", "-")):
                unit = _norm_word(nxt)
                if unit in ("hr", "hrs"):
                    unit = "hour"
            else:
                continue  # bare number: too noisy (ids, phone numbers, dates)
        q.setdefault(unit, set()).add(val)
    return q


_SENT_CACHE: dict[tuple[int, int, frozenset], list] = {}


def _sentences(d: _Doc, ignore_years: set[int]) -> list[_Sent]:
    key = (id(d), d.i, frozenset(ignore_years))
    hit = _SENT_CACHE.get(key)
    if hit is not None and hit[0] is d:
        return hit[1]
    out = _sentences_uncached(d, ignore_years)
    _SENT_CACHE[key] = [d, out]
    return out


def _sentences_uncached(d: _Doc, ignore_years: set[int]) -> list[_Sent]:
    out: list[_Sent] = []
    seen: set[str] = set()
    for page in d.doc.pages:
        try:
            ptext = page.text or ""
        except Exception:  # noqa: BLE001
            continue
        for seg in _segments(ptext):
            for s in _RE_SENT_SPLIT.split(seg):
                s = _RE_LIST_PREFIX.sub("", s.strip()).strip()
                if not s or _RE_DATE_LINE.match(s):
                    continue
                low = s.lower()
                words = _RE_WORD.findall(low)
                if len(words) < SENT_MIN_WORDS:
                    continue
                norm = " ".join(words)
                if norm in seen:
                    continue
                seen.add(norm)
                st = _Sent()
                st.text = s
                st.page = page.number
                st.norm = norm
                st.nwords = len(words)
                normed = [_norm_word(w) for w in words]
                st.content = frozenset(w for w in normed
                                       if w.isalpha() and len(w) > 1 and w not in _STOP
                                       and w not in _NEG and w not in _LIMIT)
                st.quants = _quantities(s, ignore_years)
                pol_words = [_norm_word(w) for w in _RE_WORD.findall(_IDIOMS.sub(" ", low))]
                st.neg = sum(1 for w in pol_words if w in _NEG)
                st.markers = frozenset(w for w in pol_words
                                       if w in _LIMIT or any(w in a or w in b for a, b in _POLAR_PAIRS))
                out.append(st)
                if len(out) >= MAX_SENTENCES:
                    return out
    return out


def _numeric_conflict(a: _Sent, b: _Sent) -> str | None:
    """Unit whose values disagree. A value only added or only removed (subset) is not a conflict."""
    for unit in sorted(set(a.quants) & set(b.quants)):
        va, vb = a.quants[unit], b.quants[unit]
        if not (va <= vb or vb <= va):
            return unit
    return None


def _interior_changes(a: _Sent, b: _Sent) -> tuple[float, set[str], set[str], set[str]]:
    """Word-sequence similarity, plus the words each side changed *inside* the sentence (an edit
    with unchanged text on both sides). Leading/trailing additions such as a glued heading or an
    extra clause are not edits of the shared statement."""
    wa = [_norm_word(w) for w in a.norm.split()]
    wb = [_norm_word(w) for w in b.norm.split()]
    sm = difflib.SequenceMatcher(None, wa, wb, autojunk=False)
    ops = sm.get_opcodes()
    ca: set[str] = set()
    cb: set[str] = set()
    edge: set[str] = set()
    for k, (tag, i1, i2, j1, j2) in enumerate(ops):
        if tag == "equal":
            continue
        if k == 0 or k == len(ops) - 1 or ops[k - 1][0] != "equal" or ops[k + 1][0] != "equal":
            edge.update(wa[i1:i2])
            edge.update(wb[j1:j2])
            continue
        if (i2 - i1) <= POLARITY_EDIT_MAX:
            ca.update(wa[i1:i2])
        if (j2 - j1) <= POLARITY_EDIT_MAX:
            cb.update(wb[j1:j2])
    return sm.ratio(), ca, cb, edge


def _polarity_conflict(a: _Sent, b: _Sent, ca: set[str], cb: set[str]) -> str | None:
    if (a.neg > 0) != (b.neg > 0) and ((ca | cb) & _NEG):
        return "negation"
    for side_a, side_b in _POLAR_PAIRS:
        a_has_a, a_has_b = bool(a.markers & side_a), bool(a.markers & side_b)
        b_has_a, b_has_b = bool(b.markers & side_a), bool(b.markers & side_b)
        flipped = (a_has_a and not a_has_b and b_has_b and not b_has_a) or \
                  (a_has_b and not a_has_a and b_has_a and not b_has_b)
        if flipped and ((ca | cb) & (side_a | side_b)):
            return f"{'/'.join(sorted(side_a))} vs {'/'.join(sorted(side_b))}"
    la, lb = a.markers & _LIMIT, b.markers & _LIMIT
    if la != lb and (not la or not lb) and ((ca | cb) & _LIMIT):
        return f"'{next(iter(sorted(la or lb)))}' qualifier"
    return None


def _align_score(a: _Sent, b: _Sent) -> float:
    if not a.content or not b.content:
        return 0.0
    inter = len(a.content & b.content)
    if not inter:
        return 0.0
    j = inter / len(a.content | b.content)
    if j >= SENT_ALIGN_SIM:
        return j
    if inter >= SENT_OVERLAP_MIN and inter / min(len(a.content), len(b.content)) >= SENT_OVERLAP_SIM:
        return SENT_ALIGN_SIM  # one sentence extends the other; aligned at the minimum score
    return 0.0


def _best_matches(src: list[_Sent], dst: list[_Sent], only: set[int] | None = None,
                  skip_norms: set[str] | None = None) -> dict[int, tuple[int, float]]:
    """Best-aligned dst sentence for each src sentence (inverted index on content words)."""
    index: dict[str, list[int]] = {}
    for j, s in enumerate(dst):
        for w in s.content:
            index.setdefault(w, []).append(j)
    best: dict[int, tuple[int, float]] = {}
    limit = max(50, len(dst) // 5)
    for i, s in enumerate(src):
        if only is not None and i not in only:
            continue
        if skip_norms is not None and s.norm in skip_norms:
            continue
        cand: dict[int, int] = {}
        counted = 0
        for w in s.content:
            post = index.get(w)
            if post is None or len(post) > limit:
                continue
            counted += 1
            for j in post:
                cand[j] = cand.get(j, 0) + 1
        # Jaccard >= SENT_ALIGN_SIM needs at least that share of our words; overlap needs SENT_OVERLAP_MIN.
        need = max(1, min(SENT_OVERLAP_MIN, int(counted * SENT_ALIGN_SIM)))
        top_score, tops = 0.0, []
        for j in sorted(j for j, c in cand.items() if c >= need):
            sc = _align_score(s, dst[j])
            if sc <= 0:
                continue
            if sc > top_score:
                top_score, tops = sc, [j]
            elif sc == top_score:
                tops.append(j)
        if len(tops) > 1:  # tie on content words: break it on the full word sequence
            ws = s.norm.split()
            tops.sort(key=lambda j: -difflib.SequenceMatcher(None, ws, dst[j].norm.split(),
                                                             autojunk=False).ratio())
        if tops:
            best[i] = (tops[0], top_score)
    return best


def _focus_spans(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group(0).lower()) for m in _RE_WORD.finditer(text.lower())]


def _snippet(text: str, pos: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= QUOTE_CHARS + 15:
        return text
    start = max(0, min(pos - QUOTE_CHARS // 3, len(text) - QUOTE_CHARS))
    end = min(len(text), start + QUOTE_CHARS)
    if start > 0:
        sp = text.find(" ", start)
        start = sp + 1 if 0 <= sp < pos else start
    if end < len(text):
        sp = text.rfind(" ", pos, end)
        end = sp if sp > pos else end
    return ("…" if start > 0 else "") + text[start:end].strip() + ("…" if end < len(text) else "")


def _diff_focus(a: str, b: str) -> tuple[int, int]:
    """Char positions of the first differing region (non-empty on each side if possible)."""
    sa, sb = _focus_spans(a), _focus_spans(b)
    wa, wb = [t[2] for t in sa], [t[2] for t in sb]
    pa = pb = None
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, wa, wb, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if pa is None and i2 > i1:
            pa = sa[i1][0]
        if pb is None and j2 > j1:
            pb = sb[j1][0]
        if pa is not None and pb is not None:
            break
    return pa or 0, pb or 0


def _word_pos(text: str, words: set[str]) -> int | None:
    for s, _e, w in _focus_spans(text):
        if _norm_word(w) in words:
            return s
    return None


def _num_pos(text: str, unit: str, val: float) -> int | None:
    for m in _RE_QTOKEN.finditer(text):
        tok = m.group(0)
        if not tok or not (tok[0].isdigit() or tok[0] in "$€£¥"):
            continue
        raw = tok.replace(" ", "").lstrip("$€£¥").rstrip("%").replace(",", "")
        try:
            if float(raw) == val:
                return m.start()
        except ValueError:
            continue
    return None


def _fmt_num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def _find_conflicts(old: _Doc, new: _Doc, ignore_years: set[int]) -> list[dict]:
    so, sn = _sentences(old, ignore_years), _sentences(new, ignore_years)
    if not so or not sn:
        return []
    new_norms = {s.norm for s in sn}
    fwd = _best_matches(so, sn, skip_norms=new_norms)
    back = _best_matches(sn, so, only={j for j, _ in fwd.values()})
    out: list[dict] = []
    for i in sorted(fwd):
        a = so[i]
        if a.norm in new_norms:
            continue  # sentence survives unchanged in the newer version
        j, score = fwd[i]
        if back.get(j, (None,))[0] != i:
            continue  # not a mutual best match
        b = sn[j]
        ratio, ca, cb, edge = _interior_changes(a, b)
        if ratio < SEQ_MIN_RATIO:
            continue  # same topic words, but not the same statement rewritten
        num = _numeric_conflict(a, b)
        reason = ""
        if num:
            unit = num
            va = sorted(a.quants[unit] - b.quants[unit])
            vb = sorted(b.quants[unit] - a.quants[unit])
            pa = _num_pos(a.text, unit, va[0]) if va else None
            pb = _num_pos(b.text, unit, vb[0]) if vb else None
            label = {"%": "percentage", "$": "amount ($)", "€": "amount (€)",
                     "£": "amount (£)", "¥": "amount (¥)"}.get(unit, f"'{unit}'")
            reason = (f"different values for {label}: {', '.join(_fmt_num(v) for v in va)} vs "
                      f"{', '.join(_fmt_num(v) for v in vb)}")
            da, db = _diff_focus(a.text, b.text)
            pa = da if pa is None else pa
            pb = db if pb is None else pb
        else:
            pol = None
            if ratio >= POLARITY_SEQ_RATIO and not (edge & _CONDITIONAL):
                pol = _polarity_conflict(a, b, ca, cb)  # "if the app never checks" reframes, not negates
            if not pol:
                continue
            reason = f"polarity differs ({pol})"
            da, db = _diff_focus(a.text, b.text)
            focus_words = _NEG | _LIMIT | {w for pair in _POLAR_PAIRS for side in pair for w in side}
            wa = _word_pos(a.text, (a.markers | ({"not", "no", "never", "cannot", "non", "without"}
                                                 if a.neg else set())) & focus_words)
            wb = _word_pos(b.text, (b.markers | ({"not", "no", "never", "cannot", "non", "without"}
                                                 if b.neg else set())) & focus_words)
            # Put the quote on the polarity word for the side that has it, else on the changed text.
            pa = wa if (wa is not None and (a.neg > b.neg or a.markers - b.markers)) else da
            pb = wb if (wb is not None and (b.neg > a.neg or b.markers - a.markers)) else db
        out.append({"old": a, "new": b, "reason": reason,
                    "q_old": _snippet(a.text, pa), "q_new": _snippet(b.text, pb)})
    return out


# ---------------------------------------------------------------------------------------------
# Family building
# ---------------------------------------------------------------------------------------------
def _edges(ds: list[_Doc]) -> dict[tuple[int, int], float]:
    """Candidate pairs (local indices) -> shingle Jaccard, via filename/title blocks and LSH."""
    pairs: set[tuple[int, int]] = set()
    blocks: dict[str, list[int]] = {}
    for k, d in enumerate(ds):
        if d.name.key and len(d.name.key) >= 3:
            blocks.setdefault("n:" + d.name.key, []).append(k)
        if d.title:
            blocks.setdefault("t:" + d.title, []).append(k)
    for members in blocks.values():
        if 1 < len(members) <= MAX_NAME_BLOCK:
            for x in range(len(members)):
                for y in range(x + 1, len(members)):
                    pairs.add((members[x], members[y]))
    buckets: dict[tuple, list[int]] = {}
    for k, d in enumerate(ds):
        if d.sig is None:
            continue
        for b in range(0, OPH_BINS, LSH_ROWS):
            band = tuple(d.sig[b:b + LSH_ROWS])
            if any(v > 0xFFFFFFFFFFFFFFFF for v in band):
                continue  # empty bin
            buckets.setdefault((b,) + band, []).append(k)
    for members in buckets.values():
        if 1 < len(members) <= MAX_BUCKET:
            for x in range(len(members)):
                for y in range(x + 1, len(members)):
                    pairs.add((members[x], members[y]))
    return {p: _jaccard(ds[p[0]].shingles, ds[p[1]].shingles) for p in sorted(pairs)}


def _accept(a: _Doc, b: _Doc, sim: float) -> bool:
    na, nb = a.name, b.name
    same_key = bool(na.key) and na.key == nb.key and len(na.key) >= 3
    differs = a.doc.path.stem.lower() != b.doc.path.stem.lower()
    if same_key and differs and sim >= NAME_SIM_MIN:
        return True
    if (same_key and sim >= PERIOD_SIM_MIN and na.period_label and nb.period_label
            and na.period_label != nb.period_label and not na.rev_tags and not nb.rev_tags):
        return True
    if sim >= TEXT_SIM and _names_compatible(na, nb):
        return True
    if (sim >= TEXT_SIM and a.title and a.title == b.title
            and (na.rev_tags or nb.rev_tags or na.period_label or nb.period_label)):
        return True
    return False


def _is_period_family(members: list[_Doc], sims: dict[tuple[int, int], float]) -> bool:
    keys = {m.name.key for m in members}
    if len(keys) != 1 or not next(iter(keys)):
        return False
    labels = [m.name.period_label for m in members]
    if any(not lb for lb in labels):
        return False
    if len(set(labels)) < 2:
        return False
    toks = set(members[0].name.tokens)
    if any(m.name.has_quarter for m in members) or toks & _REPORT_WORDS:
        return True
    if toks & _POLICY_WORDS:
        return False
    vals = list(sims.values())
    return bool(vals) and max(vals) < PERIOD_REVISION_SIM


# ---------------------------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------------------------
def _order(members: list[_Doc]) -> tuple[dict[int, tuple], dict[int, str]]:
    dates: dict[int, tuple] = {}
    srcs: dict[int, str] = {}
    for m in members:
        if m.date:
            dates[m.i], srcs[m.i] = m.date, m.date_src
    if not dates:
        # Fall back to explicit version numbers (v1 < v2) when every member has one.
        if all(m.name.version for m in members) and len({m.name.version for m in members}) == len(members):
            for m in members:
                dates[m.i], srcs[m.i] = m.name.version + (0,) * (3 - len(m.name.version)), "version number"
            return dates, srcs
        ranks = {m.i: _REV_RANK.get(next((t for t in m.name.rev_tags if t in _REV_RANK), ""), 0)
                 for m in members}
        if len(set(ranks.values())) > 1:
            for m in members:
                dates[m.i], srcs[m.i] = (ranks[m.i] + 10, 1, 1), "filename (old/new)"
            return dates, srcs
        mt = [m.mtime for m in members]
        if all(t is not None for t in mt) and max(mt) - min(mt) >= MTIME_MIN_SPREAD:
            for m in members:
                dt = datetime.fromtimestamp(m.mtime, tz=timezone.utc)
                dates[m.i], srcs[m.i] = (dt.year, dt.month, dt.day), "file modification time"
    return dates, srcs


def _date_desc(m: _Doc, dates: dict, srcs: dict) -> str:
    if m.i not in dates:
        return f"{m.rel} (no date found)"
    src = srcs[m.i]
    if src == "version number":
        return f"{m.rel} (version {'.'.join(str(x) for x in m.name.version)})"
    if src.startswith("filename (old"):
        return f"{m.rel} (marked {'/'.join(m.name.rev_tags)} in filename)"
    note = ""
    if src == "file modification time":
        note = "; no date in metadata, filename or text, so this can be wrong after a copy or git clone"
    return f"{m.rel} (dated {_fmt_date(dates[m.i])} from {src}{note})"


def _revision_findings(members: list[_Doc], sims: dict, family_years: set[int]) -> list[Finding]:
    out: list[Finding] = []
    dates, srcs = _order(members)
    dated = [m for m in members if m.i in dates]
    newest = [m for m in dated if not any(_cmp_dates(dates[o.i], dates[m.i]) > 0 for o in dated)]
    newest_ids = {m.i for m in newest}
    older = [m for m in dated if m.i not in newest_ids and any(_cmp_dates(dates[n.i], dates[m.i]) > 0
                                                              for n in newest)]
    sim_pct = max(sims.values()) if sims else 0.0

    # Conflicts: each older member vs the newest; if no order, all unordered pairs.
    pairs: list[tuple[_Doc, _Doc, bool]] = []
    if older and len(newest) == 1:
        for m in older:
            pairs.append((m, newest[0], True))
    elif not older:
        for x in range(len(members)):
            for y in range(x + 1, len(members)):
                pairs.append((members[x], members[y], False))
    else:
        for m in older:
            for n in newest:
                pairs.append((m, n, True))

    conflicts: list[tuple[_Doc, _Doc, bool, dict]] = []
    for a, b, ordered in pairs:
        if a.status in SUPERSEDED_STATUSES or b.status in SUPERSEDED_STATUSES:
            conflicts.extend((a, b, ordered, {"muted": True, **c})
                             for c in _find_conflicts(a, b, family_years))
            continue
        for c in _find_conflicts(a, b, family_years):
            conflicts.append((a, b, ordered, c))
    live = [c for c in conflicts if not c[3].get("muted")]
    conflict_docs = {c[0].i for c in live}

    newest_rel = ", ".join(n.rel for n in sorted(newest, key=lambda n: n.rel))
    for m in sorted(older, key=lambda d: d.rel):
        newer = sorted([o for o in dated if _cmp_dates(dates[o.i], dates[m.i]) > 0],
                       key=lambda o: (tuple(-x for x in dates[o.i]), o.rel))
        n_conf = sum(1 for c in conflicts if c[0].i == m.i)
        detail = (f"These files look like versions of the same document "
                  f"(up to {sim_pct:.0%} shared text). Older: {_date_desc(m, dates, srcs)}. Newer: "
                  + "; ".join(_date_desc(o, dates, srcs) for o in newer) + ". "
                  "A retriever can return either one, and the model cannot tell which is current.")
        if n_conf:
            detail += f" {n_conf} statement(s) disagree with the newer version (see version-conflict)."
        if m.status in SUPERSEDED_STATUSES:
            out.append(Finding(
                "version-stale", Severity.INFO, ["F1"],
                f"Older version already marked {m.status}: {m.rel} (newer: {newest_rel})",
                m.rel, detail=detail + f" It is already marked `status: {m.status}`; make sure your "
                "pipeline filters on that field.", fix=FIX_STALE, related=[o.rel for o in newer]))
            continue
        sev = Severity.ERROR if m.i in conflict_docs else Severity.WARN
        out.append(Finding(
            "version-stale", sev, ["F1"],
            f"Older version still in corpus: {m.rel} (newer: {newest_rel})",
            m.rel, detail=detail, fix=FIX_STALE, related=[o.rel for o in newer]))

    total = len(live)
    for k, (a, b, ordered, c) in enumerate(live[:MAX_CONFLICTS_PER_FAMILY]):
        sa, sb = c["old"], c["new"]
        la, lb = locate(a.doc, sa.text), locate(b.doc, sb.text)
        at_a = f" [{la}]" if la else ""
        at_b = f" [{lb}]" if lb else ""
        if ordered:
            who = (f'{a.rel}{at_a} (older) says: "{sa.text}"\n'
                   f'{b.rel}{at_b} (newer) says: "{sb.text}"')
        else:
            who = (f'{a.rel}{at_a} says: "{sa.text}"\n{b.rel}{at_b} says: "{sb.text}"\n'
                   "Could not tell which file is newer.")
        detail = f"{who}\nWhy flagged: {c['reason']}."
        if k == min(total, MAX_CONFLICTS_PER_FAMILY) - 1 and total > MAX_CONFLICTS_PER_FAMILY:
            detail += (f"\n{total} conflicting statements found in this family in total; "
                       f"showing the first {MAX_CONFLICTS_PER_FAMILY}.")
        loc = la or (f"p.{sa.page}" if (a.doc.fmt or "").lower() == "pdf" else "")
        out.append(Finding(
            "version-conflict", Severity.ERROR, ["F14", "F1"],
            f'Conflicting statements: "{c["q_old"]}" vs "{c["q_new"]}"',
            a.rel, location=loc, detail=detail, fix=FIX_CONFLICT, related=[b.rel]))
    return out


def _period_finding(members: list[_Doc]) -> Finding:
    ms = sorted(members, key=lambda m: (m.name.period_date or (0, 0, 0), m.rel))
    years = sorted({y for m in ms for y in m.name.years})
    if years:
        span = f"{years[0]}–{years[-1]}" if years[0] != years[-1] else str(years[0])
    else:
        span = ", ".join(m.name.period_label for m in ms)
    base = ms[0].name.display or ms[0].doc.path.stem
    n = len(ms)
    detail = ("These look like the same recurring document for different periods ("
              + ", ".join(f"{m.rel} [{m.name.period_label}]" for m in ms)
              + "). They are not stale, but chunks from different periods read alike, so a question "
              "about one period can retrieve another period's numbers.")
    return Finding(
        "version-period-twins", Severity.WARN if n >= 3 else Severity.INFO, ["F1", "F16"],
        f"{base}: {n} period versions ({span}) — tag every chunk with its period, or answers mix years",
        ms[-1].rel, detail=detail, fix=FIX_PERIOD, related=[m.rel for m in ms[:-1]])


def _analyse_family(members: list[_Doc], sims: dict[tuple[int, int], float]) -> list[Finding]:
    family_years = {y for m in members for y in m.name.years}
    family_years |= {m.date[0] for m in members if m.date}
    if _is_period_family(members, sims):
        out = [_period_finding(_distinct_periods(members))]
        by_label: dict[str, list[_Doc]] = {}
        for m in members:
            by_label.setdefault(m.name.period_label, []).append(m)
        for label in sorted(by_label):
            group = by_label[label]
            if len(group) > 1:
                sub = {k: v for k, v in sims.items() if k[0] in {g.i for g in group} and k[1] in {g.i for g in group}}
                out.extend(_revision_findings(group, sub, family_years))
        return out
    if len(members) > MAX_FAMILY:
        return []
    return _revision_findings(members, sims, family_years)


def _distinct_periods(members: list[_Doc]) -> list[_Doc]:
    seen: dict[str, _Doc] = {}
    for m in sorted(members, key=lambda m: m.rel):
        seen.setdefault(m.name.period_label, m)
    return list(seen.values())


# ---------------------------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------------------------
def run(docs: list[Document], root: Path) -> list[Finding]:
    try:
        return _run(docs, root)
    except Exception as e:  # noqa: BLE001 - a check must never take the scan down
        return [Finding("versions-crashed", Severity.INFO, [], f"versions check failed: {e}", "",
                        fix="Please report this at github.com/anusky95/ragpreflight/issues")]


def _run(docs: list[Document], root: Path) -> list[Finding]:
    _SENT_CACHE.clear()
    try:
        return _run_inner(docs, root)
    finally:
        _SENT_CACHE.clear()


def _run_inner(docs: list[Document], root: Path) -> list[Finding]:
    prepared: list[_Doc] = []
    for i, doc in enumerate(sorted(docs, key=lambda d: str(d.path))):
        try:
            d = _prepare(doc, i, root)
        except Exception:  # noqa: BLE001 - skip odd documents
            d = None
        if d is not None:
            prepared.append(d)
    for k, d in enumerate(prepared):
        d.i = k

    findings: list[Finding] = []

    # 1. Exact duplicates; keep one representative per group for family analysis.
    groups: dict[str, list[_Doc]] = {}
    for d in prepared:
        if d.norm_hash:
            groups.setdefault(d.norm_hash, []).append(d)
    dup_of: set[int] = set()
    for h in sorted(groups, key=lambda h: groups[h][0].rel):
        g = sorted(groups[h], key=lambda d: d.rel)
        for d in g[1:]:
            dup_of.add(d.i)
            findings.append(Finding(
                "duplicate", Severity.WARN, ["F1"], f"Exact duplicate of {g[0].rel}", d.rel,
                detail=f"{d.rel} has the same text as {g[0].rel}"
                       + (f" (and {len(g) - 2} other file(s))" if len(g) > 2 else "")
                       + ". Duplicates waste index space and double-weight this content in retrieval.",
                fix=FIX_DUP, related=[x.rel for x in g if x is not d]))
    reps = [d for d in prepared if d.i not in dup_of and len(d.words) >= MIN_DOC_WORDS]

    # 2. Similarity and families.
    _build_shingles(reps)
    sims_local = _edges(reps)
    uf = _UF(len(reps))
    accepted: dict[tuple[int, int], float] = {}
    for (x, y), sim in sims_local.items():
        if _accept(reps[x], reps[y], sim):
            uf.union(x, y)
            accepted[(reps[x].i, reps[y].i)] = sim
    fams: dict[int, list[_Doc]] = {}
    for k, d in enumerate(reps):
        fams.setdefault(uf.find(k), []).append(d)

    for root_k in sorted(fams):
        members = sorted(fams[root_k], key=lambda d: d.rel)
        if len(members) < 2:
            continue
        ids = {m.i for m in members}
        sims = {k: v for k, v in accepted.items() if k[0] in ids and k[1] in ids}
        try:
            findings.extend(_analyse_family(members, sims))
        except Exception:  # noqa: BLE001 - one odd family must not hide the rest
            continue
    return findings
