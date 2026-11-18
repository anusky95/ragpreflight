"""Turn files into Documents. Never raises on bad input: problems land in Document.read_error."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from .models import Document, Page

SUPPORTED = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".md": "md",
    ".markdown": "md",
    ".txt": "txt",
    ".text": "txt",
    ".html": "html",
    ".htm": "html",
}
MAX_BYTES = 100 * 1024 * 1024
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".ragpreflight"}


def discover(root: Path) -> list[Path]:
    """All supported files under root (or root itself if it is a file), sorted."""
    if root.is_file():
        return [root] if root.suffix.lower() in SUPPORTED else []
    out = []
    for p in root.rglob("*"):
        if any(part in SKIP_DIRS or part.startswith(".") for part in p.relative_to(root).parts[:-1]):
            continue
        if p.is_file() and p.suffix.lower() in SUPPORTED:
            out.append(p)
    return sorted(out)


def read(path: Path) -> Document:
    fmt = SUPPORTED.get(path.suffix.lower(), "txt")
    try:
        size = path.stat().st_size
        if size > MAX_BYTES:
            return Document(path, fmt, [], read_error=f"file is {size // 2**20} MB; limit is 100 MB")
        if size == 0:
            return Document(path, fmt, [], read_error="file is empty")
        return {"pdf": _pdf, "docx": _docx, "md": _md, "txt": _txt, "html": _html}[fmt](path)
    except Exception as e:  # noqa: BLE001 - a bad file must never crash a corpus scan
        return Document(path, fmt, [], read_error=f"could not read: {type(e).__name__}: {e}")


# ---------------------------------------------------------------- PDF
def _pdf(path: Path) -> Document:
    """PDFium (fast, C) first; pure-Python pypdf as the fallback for files PDFium rejects."""
    try:
        return _pdf_pdfium(path)
    except ImportError:
        return _pdf_pypdf(path)
    except Exception:  # noqa: BLE001
        return _pdf_pypdf(path)


def _pdf_pdfium(path: Path) -> Document:
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    pdf = pdfium.PdfDocument(str(path))
    try:
        if len(pdf) == 0:
            return Document(path, "pdf", [], read_error="PDF has no readable pages")
        pages = []
        for i in range(len(pdf)):
            page = pdf[i]
            try:
                tp = page.get_textpage()
                text = tp.get_text_range().replace("\r\n", "\n").replace("\r", "\n")
                tp.close()
            except Exception:  # noqa: BLE001
                text = ""
            try:
                images = sum(1 for _ in page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE], max_depth=3))
            except Exception:  # noqa: BLE001
                images = 0
            page.close()
            pages.append(Page(i + 1, text, images))
        info = pdf.get_metadata_dict(skip_empty=True)
    finally:
        pdf.close()
    meta: dict[str, str] = {}
    if info.get("Title"):
        meta["title"] = info["Title"]
    if info.get("Author"):
        meta["author"] = info["Author"]
    date = info.get("ModDate") or info.get("CreationDate")
    if date:
        meta["date"] = _pdf_date(date)
    return Document(path, "pdf", pages, meta)


def _pdf_pypdf(path: Path) -> Document:
    import logging

    from pypdf import PdfReader

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:  # noqa: BLE001
            return Document(path, "pdf", [], read_error="PDF is password-protected")
    pages = []
    if len(reader.pages) == 0:
        return Document(path, "pdf", [], read_error="PDF has no readable pages")
    for i, page in enumerate(reader.pages, 1):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            text = ""
        pages.append(Page(i, text, _pdf_image_count(page)))
    meta: dict[str, str] = {}
    info = reader.metadata or {}
    for key, name in (("/Title", "title"), ("/Author", "author"), ("/ModDate", "date"), ("/CreationDate", "created")):
        val = info.get(key)
        if val:
            meta[name] = _pdf_date(str(val)) if "date" in name or name == "created" else str(val)
    if "date" not in meta and "created" in meta:
        meta["date"] = meta["created"]
    return Document(path, "pdf", pages, meta)


def _pdf_image_count(page) -> int:
    try:
        res = page.get("/Resources") or {}
        res = res.get_object() if hasattr(res, "get_object") else res
        xobj = res.get("/XObject")
        if not xobj:
            return 0
        xobj = xobj.get_object()
        return sum(1 for k in xobj if xobj[k].get_object().get("/Subtype") == "/Image")
    except Exception:  # noqa: BLE001
        return 0


def _pdf_date(s: str) -> str:
    m = re.match(r"D?:?(\d{4})(\d{2})?(\d{2})?", s)
    if not m:
        return s
    y, mo, d = m.group(1), m.group(2) or "01", m.group(3) or "01"
    return f"{y}-{mo}-{d}"


# ---------------------------------------------------------------- DOCX
def _docx(path: Path) -> Document:
    import docx

    d = docx.Document(str(path))
    parts = [p.text for p in d.paragraphs]
    for t in d.tables:
        for row in t.rows:
            parts.append(" | ".join(c.text.strip() for c in row.cells))
    cp = d.core_properties
    meta = {}
    if cp.title:
        meta["title"] = cp.title
    if cp.author:
        meta["author"] = cp.author
    dt = cp.modified or cp.created
    if dt:
        meta["date"] = dt.date().isoformat()
    images = sum(1 for r in d.part.rels.values() if "image" in r.reltype)
    return Document(path, "docx", [Page(1, "\n".join(parts), images)], meta)


# ---------------------------------------------------------------- Markdown / text
_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


def _decode(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Minimal YAML frontmatter (key: value lines). Works for LLM-wiki and OKF files."""
    m = _FRONTMATTER.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t", "-")):
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip().strip("'\"")
    return meta, text[m.end():]


def _md(path: Path) -> Document:
    text = _decode(path)
    meta, body = parse_frontmatter(text)
    for alias, canon in (("updated", "date"), ("last_updated", "date"), ("last_reviewed", "date"),
                         ("effective_date", "date"), ("modified", "date"), ("owner", "author")):
        if alias in meta and canon not in meta:
            meta[canon] = meta[alias]
    images = len(re.findall(r"!\[[^\]]*\]\([^)]+\)", body))
    offset = text[: len(text) - len(body)].count("\n")  # frontmatter lines, so line numbers match the file
    return Document(path, "md", [Page(1, body, images, offset)], meta)


def _txt(path: Path) -> Document:
    return Document(path, "txt", [Page(1, _decode(path))])


# ---------------------------------------------------------------- HTML
class _HTMLText(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article"}

    def __init__(self) -> None:
        super().__init__()
        self.out: list[str] = []
        self.meta: dict[str, str] = {}
        self.images = 0
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "img":
            self.images += 1
        elif tag == "meta" and a.get("name") and a.get("content"):
            self.meta[a["name"].lower()] = a["content"]
        elif tag in ("td", "th"):
            self.out.append(" | ")
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.meta.setdefault("title", data.strip())
            return
        self.out.append(data)


def _html(path: Path) -> Document:
    p = _HTMLText()
    p.feed(_decode(path))
    text = re.sub(r"[ \t]+", " ", "".join(p.out))
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    meta = dict(p.meta)
    for alias in ("last-modified", "date", "dc.date"):
        if alias in meta:
            meta.setdefault("date", meta[alias])
    return Document(path, "html", [Page(1, text, p.images)], meta)
