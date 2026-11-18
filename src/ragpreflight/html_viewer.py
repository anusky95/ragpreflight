"""Single-document visual viewer: the PDF's own pages, with every problem drawn where it is.

    ragpreflight view contract.pdf            # writes contract.ragpreflight.html and opens it

Highlights are computed from PDFium character boxes, so they sit on the exact words:
conflicting statements, personal data and secrets (blurred like a redaction), the place a chunk
splitter cuts a rule away from its condition (✂ line), garbled characters, table regions, image
regions that carry content, and pages with no text layer (the AI sees a blank page).
Everything is embedded: one offline HTML file.
"""

from __future__ import annotations

import base64
import html
import io
import json
import re
from pathlib import Path

from . import __version__
from .html_report import CAT_COLOR, CATEGORIES, category, pages_of
from .models import Finding, Report
from .taxonomy import MODES, PAPER_URL

FULL_SCALE = 1.4  # render scale for pages with findings (72 dpi * 1.4)
THUMB_SCALE = 0.45  # pages without findings
MAX_FULL_PAGES = 60
JPEG_QUALITY = 70


# ---------------------------------------------------------------- geometry helpers
class _PageGeo:
    """Raw PDFium text for a page plus helpers to turn char ranges into page-relative boxes."""

    def __init__(self, page):
        self.page = page
        self.w, self.h = page.get_size()
        self.tp = page.get_textpage()
        self.n = self.tp.count_chars()
        self.raw = self.tp.get_text_range(0, self.n) if self.n else ""
        # normalized text (what the checks saw) -> raw offsets
        self.norm, self.n2r = self._normalize(self.raw)

    @staticmethod
    def _normalize(raw: str):
        out, idx = [], []
        i = 0
        while i < len(raw):
            if raw[i] == "\r":
                out.append("\n")
                idx.append(i)
                i += 2 if raw[i + 1:i + 2] == "\n" else 1
                continue
            out.append(raw[i])
            idx.append(i)
            i += 1
        idx.append(len(raw))
        return "".join(out), idx

    def boxes(self, start: int, end: int) -> list[list[float]]:
        """Page-relative [x, y, w, h] (0..1, top-left origin) for raw char range [start, end)."""
        start, end = max(0, start), min(self.n, end)
        if end <= start:
            return []
        out = []
        try:
            k = self.tp.count_rects(start, end - start)
            for i in range(k):
                left, bottom, right, top = self.tp.get_rect(i)
                bw, bh = (right - left) / self.w, (top - bottom) / self.h
                if bw <= 0 or bh <= 0 or bh > 0.08 or bw > 0.95:
                    continue  # invisible/control glyphs can report page-sized boxes: not text
                out.append([left / self.w, 1 - top / self.h, bw, bh])
        except Exception:  # noqa: BLE001
            return []
        return out

    def norm_boxes(self, a: int, b: int) -> list[list[float]]:
        a, b = max(0, min(a, len(self.n2r) - 1)), max(0, min(b, len(self.n2r) - 1))
        return self.boxes(self.n2r[a], self.n2r[b])

    def find(self, snippet: str) -> tuple[int, int] | None:
        """Normalized-text span of a snippet (first ~10 words, flexible whitespace)."""
        words = re.findall(r"\S+", snippet.strip(' "“”…'))
        if not words:
            return None
        rx = re.compile(r"\s+".join(re.escape(w) for w in words[:12]))
        m = rx.search(self.norm)
        if not m and len(words) > 4:
            m = re.compile(r"\s+".join(re.escape(w) for w in words[:5])).search(self.norm)
        if not m:
            return None
        # extend to the full snippet length when the snippet was longer than the matched words
        end = m.end()
        if len(words) > 12:
            tail = re.compile(r"\s+".join(re.escape(w) for w in words[-4:])).search(self.norm, m.end())
            if tail and tail.end() - m.start() < 4 * len(snippet):
                end = tail.end()
        return m.start(), end


def _merge_lines(boxes: list[list[float]]) -> list[list[float]]:
    """Merge character rects on the same line into one rect per line (cleaner highlights)."""
    lines: list[list[float]] = []
    for x, y, w, h in sorted(boxes, key=lambda b: (round(b[1], 3), b[0])):
        if lines and abs(lines[-1][1] - y) < 0.004 and x - (lines[-1][0] + lines[-1][2]) < 0.03:
            lx, ly, lw, lh = lines[-1]
            nx, ny = min(lx, x), min(ly, y)
            lines[-1] = [nx, ny, max(lx + lw, x + w) - nx, max(ly + lh, y + h) - ny]
        else:
            lines.append([x, y, w, h])
    return lines


# ---------------------------------------------------------------- marks per finding
def _marks_for(f: Finding, geos: dict[int, _PageGeo], doc_text_pages: list[str],
               mask: bool = False) -> list[dict]:
    """Highlights ({page, boxes, kind, label}) for one finding."""
    from .checks import chunking, pii
    from .checks.extraction import find_table_runs

    marks: list[dict] = []
    pages_hint = pages_of(f)  # expands ranges like "p.179–187, 190"
    cur = {"occ": -1, "ol": ""}  # current occurrence: one clickable row in the sidebar

    def occ(label: str) -> None:
        cur["occ"] += 1
        cur["ol"] = label

    def put(mark: dict) -> None:
        mark["occ"], mark["ol"] = cur["occ"], cur["ol"]
        marks.append(mark)

    def add(page: int, boxes, kind: str, label: str) -> None:
        if boxes:
            put({"page": page, "boxes": _merge_lines(boxes), "kind": kind, "label": label})

    def short(t: str, n: int = 60, left: bool = False) -> str:
        t = re.sub(r"\s+", " ", t).strip()
        return t if len(t) <= n else ("…" + t[-n:] if left else t[:n] + "…")

    if f.check in ("pii", "secret"):
        for no, g in geos.items():
            for kind, m in pii.find_all(g.norm):
                is_secret = kind in {k for k, _ in pii.SECRETS}
                if (f.check == "secret") == is_secret:
                    occ(f"p.{no} · {kind}" + ("" if mask else f": {m.group(0)}"))
                    if mask:
                        add(no, g.norm_boxes(m.start(), m.end()), "redact", kind)
                    else:  # developers fixing their own corpus need to see the value
                        add(no, g.norm_boxes(m.start(), m.end()), "pii", f"{kind}: {m.group(0)}")
    elif f.check == "version-conflict":
        for quote in re.findall(r'says: "(.*?)"(?:\n|$)', f.detail or ""):
            for no in pages_hint or list(geos):
                g = geos.get(no)
                span = g.find(quote) if g else None
                if span:
                    occ(f"p.{no} · “{short(quote, 70)}”")
                    add(no, g.norm_boxes(*span), "conflict", "Conflicts with another version")
                    break
    elif f.check == "chunk-split":
        from . import chunkers

        text = "\n\n".join(doc_text_pages)
        ch = chunkers.chunk(text)
        if ch is None:
            return marks
        _label = ch.label
        starts, off = [], 0
        for t in doc_text_pages:
            starts.append(off)
            off += len(t) + 2
        for cno, left, right, q, pos in chunking.cut_examples(text, chunking.chunk_ends(ch.spans, len(text.rstrip()))):
            pi = max(i for i, s in enumerate(starts) if s <= pos)
            g = geos.get(pi + 1)
            if not g:
                continue
            local = pos - starts[pi]
            line = g.norm.count("\n", 0, local) + 1
            occ(f"p.{pi + 1}, line {line} · chunk {cno}→{cno + 1} · '{q}' — “{short(left, 45, True)} ✂ {short(right, 45)}”")
            add(pi + 1, g.norm_boxes(local - len(left), local), "claim", f"Chunk ends here: claim without its '{q}' condition")
            add(pi + 1, g.norm_boxes(local, local + len(right)), "condition", f"Next chunk: the '{q}' condition")
            cut = g.norm_boxes(local, local + 1)
            if cut:
                x, y, w, h = cut[0]
                put({"page": pi + 1, "boxes": [[0, y - 0.004, 1, 0]], "kind": "cut",
                     "label": f"✂ chunk {cno} ends here ({_label})"})
    elif f.check == "extraction-garbled":
        bad = re.compile(r"�+|\(cid:\d+\)|[\x00-\x08\x0b\x0e-\x1f\x7f]+|Ã[\u0080-¿]|â€.")
        for no in pages_hint or list(geos):
            g = geos.get(no)
            if not g:
                continue
            hits = list(bad.finditer(g.norm))
            if not hits:
                continue
            occ(f"p.{no} · {len(hits)} garbled character run(s)")
            before = len(marks)
            for m in hits[:200]:
                add(no, g.norm_boxes(m.start(), m.end() + 1), "garbled", "Garbled characters")
            if hits and len(marks) == before:
                # invisible glyphs: nothing to point at on the page, so show what the AI reads instead
                m = hits[0]
                raw = g.norm[max(0, m.start() - 50): m.end() + 70]
                shown = "".join(c if c.isprintable() or c == " " else f"\\x{ord(c):02x}" for c in raw)
                shown = re.sub(r"\s+", " ", shown).strip()
                put({"page": no, "boxes": [[0, 0, 1, 1]], "kind": "garbledpage",
                              "label": f"Looks fine, but your AI reads: “…{shown[:150]}…”"})
    elif f.check in ("extraction-table", "chunk-table-split"):
        for no in pages_hint or list(geos):
            g = geos.get(no)
            if not g:
                continue
            lines = g.norm.split("\n")
            offs, o = [], 0
            for ln in lines:
                offs.append(o)
                o += len(ln) + 1
            for a, b in find_table_runs(lines):
                bx = g.norm_boxes(offs[a], offs[b] + len(lines[b]))
                if bx:
                    occ(f"p.{no}, line {a + 1} · table “{short(lines[a], 50)}” ({b - a + 1} rows)")
                    x0 = min(x for x, *_ in bx)
                    y0 = min(y for _, y, *_ in bx)
                    x1 = max(x + w for x, _, w, _ in bx)
                    y1 = max(y + h for _, y, _, h in bx)
                    put({"page": no, "boxes": [[x0, y0, x1 - x0, y1 - y0]], "kind": "table",
                                  "label": "Table: will be flattened into run-on text"})
    elif f.check in ("extraction-scanned", "extraction-empty", "extraction-images"):
        kind = "scanned" if f.check != "extraction-images" else "image"
        for no in pages_hint:
            occ(f"p.{no} · " + ("no text layer" if kind == "scanned" else "image-heavy page"))
            put({"page": no, "boxes": [[0, 0, 1, 1]], "kind": kind,
                          "label": "No text layer: your AI sees a blank page" if kind == "scanned"
                          else "Images carry content that becomes nothing as text"})
    elif f.check == "chunk-orphan-heading":
        for quote in re.findall(r"heading “(.*?)”", f.detail or ""):
            for no in pages_hint or list(geos):
                g = geos.get(no)
                span = g.find(quote.rstrip("…")) if g else None
                if span:
                    occ(f"p.{no} · heading “{short(quote, 60)}”")
                    add(no, g.norm_boxes(*span), "heading", "Heading ends a chunk; its body starts the next")
                    break
    return marks


# ---------------------------------------------------------------- render
def _render(page, scale: float) -> str:
    """Page image as a data URI: JPEG via Pillow when installed, else a dependency-free PNG."""
    bitmap = page.render(scale=scale, rev_byteorder=True)  # RGB(A) byte order
    try:
        img = bitmap.to_pil().convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except ImportError:
        return "data:image/png;base64," + base64.b64encode(_png(bitmap)).decode()


def _png(bitmap) -> bytes:
    import struct
    import zlib

    w, h, stride = bitmap.width, bitmap.height, bitmap.stride
    n = bitmap.n_channels
    buf = bytes(bitmap.buffer)
    rows = []
    for y in range(h):
        row = buf[y * stride: y * stride + w * n]
        if n == 4:  # drop alpha
            row = b"".join(row[i:i + 3] for i in range(0, len(row), 4))
        rows.append(b"\x00" + row)
    raw = zlib.compress(b"".join(rows), 6)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


MAX_ROWS = 200


def _occurrence_list(fid: int, marks: list[dict], e) -> str:
    """One clickable row per occurrence (each chunk cut, each value, each table, each page)."""
    seen: dict[int, str] = {}
    for m in marks:
        if m.get("occ", -1) >= 0 and m["occ"] not in seen:
            seen[m["occ"]] = m.get("ol", "")
    if not seen:
        return ""
    rows = [f"<li onclick='event.stopPropagation();goOcc({fid},{o})' data-occ='{fid}:{o}'>{e(lbl)}</li>"
            for o, lbl in list(seen.items())[:MAX_ROWS]]
    more = (f"<li class='more'>+{len(seen) - MAX_ROWS} more (use the page strip)</li>"
            if len(seen) > MAX_ROWS else "")
    return f"<ol class='occ'>{''.join(rows)}{more}</ol>"


def to_viewer_html(report: Report, pdf_path: Path, source_href: str = "", mask: bool = False) -> str:
    """mask=False shows PII/secret values on the page (local dev use); mask=True blurs them."""
    import pypdfium2 as pdfium

    from .report import tag

    doc = next(d for d in report.documents if Path(d.path).resolve() == Path(pdf_path).resolve())
    pdf = pdfium.PdfDocument(str(pdf_path))
    n_pages = len(pdf)
    geos = {i + 1: _PageGeo(pdf[i]) for i in range(n_pages)}
    doc_text_pages = [p.text or "" for p in doc.pages]

    items = []
    flagged: set[int] = set()
    for k, f in enumerate(report.findings):
        marks = _marks_for(f, geos, doc_text_pages, mask) if f.path != "(corpus)" else []
        for m in marks:
            m["fid"] = k
            flagged.add(m["page"])
        items.append({"k": k, "f": f, "marks": marks})
    full = set(sorted(flagged)[:MAX_FULL_PAGES])
    pages = []
    for i in range(n_pages):
        no = i + 1
        g = geos[no]
        pages.append({"no": no, "img": _render(pdf[i], FULL_SCALE if no in full else THUMB_SCALE),
                      "full": no in full, "ar": g.h / g.w})
    pdf.close()

    e = html.escape
    marks_json = [m for it in items for m in it["marks"]]
    finding_cards = []
    for key, title, *_rest in CATEGORIES + [("other", "Other", "", "", "", ())]:
        fs = [it for it in items if category(it["f"]) == key]
        if not fs:
            continue
        cards = []
        for it in fs:
            f = it["f"]
            first = it["marks"][0]["page"] if it["marks"] else None
            codes = " ".join(f'<a href="{PAPER_URL}" title="{e(MODES[c][1]) if c in MODES else ""}">{c}</a>' for c in f.f_codes)
            values = _occurrence_list(it["k"], it["marks"], e)
            cards.append(
                f"<div class='card {f.severity.value}' data-fid='{it['k']}' data-page='{first or ''}' onclick='go({it['k']})'>"
                f"<div class='t'><span class='sev'>{f.severity.value}</span> <b>{e(tag(f))}</b></div>"
                f"<div>{e(f.title)}</div><div class='loc'>{e(f.location or 'whole document')}"
                f"{'' if it['marks'] else ' · not drawn on a page'}</div>{values}"
                f"<details><summary>Evidence & fix</summary><pre>{e(f.detail)}</pre><div class='fix'>→ {e(f.fix)}</div>"
                f"<div class='codes'>{codes}</div></details></div>")
        finding_cards.append(f"<section style='--c:{CAT_COLOR[key]}'><h3><span class='dot'></span>{e(title)} "
                             f"<span class='cnt'>{len(fs)}</span></h3>{''.join(cards)}</section>")

    strip = "".join(
        f"<a href='#p{p['no']}' class='s{' hot' if p['no'] in flagged else ''}' title='page {p['no']}'"
        f" style='--c:{_page_color(p['no'], items)}'></a>" for p in pages)
    page_divs = "".join(
        f"<div class='page{' thumb' if not p['full'] else ''}' id='p{p['no']}' style='--ar:{p['ar']:.4f}'>"
        f"<img src='{p['img']}' alt='page {p['no']}' loading='lazy'><div class='ov' data-page='{p['no']}'></div>"
        f"<div class='pno'>p.{p['no']}{'' if p['full'] else ' · no issues'}</div></div>"
        for p in pages)
    n = report.counts()
    vclass = report.verdict.split()[0]
    payload = json.dumps({"marks": marks_json, "colors": KIND_COLOR}).replace("</", "<\\/")
    src_link = (f"<a href='{e(source_href)}' target='_blank' rel='noopener'>open original</a>"
                if source_href else "")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(Path(pdf_path).name)} · ragpreflight</title>
<style>{VCSS}</style></head><body>
<header class="top"><div class="brand">ragpreflight <span>{__version__}</span></div>
<div class="file">{e(Path(pdf_path).name)} <span class="sub">· {n_pages} pages · {doc.word_count:,} words {src_link}</span></div>
<div class="kpi"><b class="v-{vclass}">{e(report.verdict)}</b> <span class="sub">{report.score}/100 · {n['error']} errors · {n['warn']} warnings · {n['info']} notes</span></div></header>
<nav class="strip" title="Pages: highlighted ones have problems">{strip}</nav>
<div class="wrap"><aside>{''.join(finding_cards) or '<p>No issues found.</p>'}
<p class="sub foot">F-codes: failure modes from <a href="{PAPER_URL}">Garani (2026)</a>. Highlights come from the PDF's own character positions.</p></aside>
<main id="pages">{page_divs}</main></div>
<div id="tip"></div>
<script type="application/json" id="data">{payload}</script><script>{VJS}</script></body></html>"""


KIND_COLOR = {"conflict": "#e05a3a", "redact": "#d6336c", "pii": "#d6336c", "claim": "#d9a23a", "condition": "#3d8bd9",
              "cut": "#d9a23a", "garbled": "#8a63d2", "garbledpage": "#8a63d2", "table": "#8a63d2", "scanned": "#e05a3a",
              "image": "#8a63d2", "heading": "#d9a23a"}


def _page_color(no: int, items) -> str:
    sev_rank = {"error": 0, "warn": 1, "info": 2}
    best = None
    for it in items:
        if any(m["page"] == no for m in it["marks"]):
            r = sev_rank[it["f"].severity.value]
            if best is None or r < best[0]:
                best = (r, CAT_COLOR.get(category(it["f"]), "#888"))
    return best[1] if best else "transparent"


VCSS = """
:root{--ink:#14213d;--muted:#66728f;--line:#e3e8f0;--card:#fff;--bg:#eef1f6;--red:#d64527;--amber:#c48a12;--green:#1f8a6e}
@media (prefers-color-scheme:dark){:root{--ink:#e8ecf4;--muted:#98a3bd;--line:#26314a;--card:#161f35;--bg:#0b1120}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:inherit}.sub{color:var(--muted);font-size:12.5px}
.top{position:sticky;top:0;z-index:5;display:flex;flex-wrap:wrap;gap:6px 18px;align-items:center;padding:10px 16px;background:var(--card);border-bottom:1px solid var(--line)}
.brand{font-weight:700}.brand span{font-weight:400;color:var(--muted);font-size:12px}.file{font-weight:600;flex:1;min-width:200px}
.v-NOT{color:var(--red)}.v-REVIEW{color:var(--amber)}.v-READY{color:var(--green)}
.strip{position:sticky;top:46px;z-index:4;display:flex;gap:1px;padding:6px 16px;background:var(--card);border-bottom:1px solid var(--line);overflow-x:auto}
.strip a{flex:1 0 3px;min-width:3px;height:14px;background:var(--line);border-radius:2px}.strip a.hot{background:var(--c);height:14px}
.wrap{display:grid;grid-template-columns:minmax(280px,380px) 1fr;gap:18px;padding:16px;align-items:start}
@media (max-width:860px){.wrap{grid-template-columns:1fr}aside{position:static!important;max-height:none!important}}
aside{position:sticky;top:96px;max-height:calc(100vh - 110px);overflow:auto;padding-right:4px}
aside h3{font-size:14px;margin:14px 0 6px;display:flex;align-items:center;gap:8px}.cnt{font-weight:400;color:var(--muted)}
.dot{width:10px;height:10px;border-radius:50%;background:var(--c);display:inline-block}
.card{background:var(--card);border-radius:10px;padding:9px 11px;margin:6px 0;border-left:4px solid var(--c);cursor:pointer;font-size:13px}
.card:hover{outline:2px solid var(--c)}.card .t{font-size:12px}.sev{font-size:10.5px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:var(--muted)}
.card.error .sev{color:var(--red)}.card.warn .sev{color:var(--amber)}.loc{color:var(--muted);font:12px ui-monospace,Consolas,monospace;margin-top:2px}
details{margin-top:4px}summary{color:var(--muted);font-size:12px}pre{white-space:pre-wrap;font:12px/1.5 ui-monospace,Consolas,monospace;background:var(--bg);border-radius:6px;padding:8px;margin:4px 0}
.occ{list-style:none;margin:7px 0 2px;padding:0;display:flex;flex-direction:column;gap:3px;max-height:340px;overflow:auto}
.occ li{font:12px/1.4 ui-monospace,Consolas,monospace;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:4px 7px;cursor:pointer;word-break:break-word}
.occ li:hover,.occ li.on{border-color:var(--c);background:color-mix(in srgb,var(--c) 14%,transparent)}.occ li.more{cursor:default;color:var(--muted)}
.vals{display:flex;flex-direction:column;gap:2px;margin-top:5px;font:12px ui-monospace,Consolas,monospace}.vals span{background:#d6336c1f;border-radius:5px;padding:1px 6px;word-break:break-all}
.fix{font-size:12.5px}.codes a{font-size:11px;color:var(--muted);margin-right:6px}.foot{margin-top:16px}
main{display:flex;flex-direction:column;align-items:center;gap:18px}
.page{position:relative;width:min(100%,900px);aspect-ratio:1/var(--ar);background:#fff;box-shadow:0 2px 14px #0003;border-radius:4px;overflow:hidden}
.page.thumb{width:min(60%,420px);opacity:.75}.page img{width:100%;height:100%;display:block}
.pno{position:absolute;right:8px;bottom:6px;font-size:11px;background:#0d1424cc;color:#fff;border-radius:6px;padding:1px 7px}
.ov{position:absolute;inset:0}
.m{position:absolute;border-radius:2px;cursor:pointer;transition:box-shadow .2s}
.m.conflict{background:#e05a3a33;border-bottom:2px solid #e05a3a}
.m.pii{background:#d6336c26;outline:2px solid #d6336c;outline-offset:1px}
.m.redact{backdrop-filter:blur(5px);-webkit-backdrop-filter:blur(5px);background:#d6336c55;outline:2px solid #d6336c}
.m.claim{background:#d9a23a33;border-bottom:2px solid #d9a23a}.m.condition{background:#3d8bd933;border-bottom:2px dashed #3d8bd9}
.m.cut{border-top:2px dashed #d9a23a;height:0!important;pointer-events:none}.m.cut::before{content:"✂ chunk boundary";position:absolute;left:-1px;top:-9px;color:#fff;background:#d9a23a;font:600 10px system-ui;padding:1px 6px;border-radius:0 6px 6px 0}
.m.garbled{background:#8a63d255;outline:1px solid #8a63d2}.m.table{outline:2px dashed #8a63d2;background:#8a63d211}
.m.heading{background:#d9a23a33;border-bottom:2px solid #d9a23a}
.m.scanned{background:repeating-linear-gradient(45deg,#e05a3a22 0 12px,#e05a3a10 12px 24px);outline:3px solid #e05a3a;outline-offset:-3px}
.m.garbledpage{outline:3px dashed #8a63d2;outline-offset:-3px;background:#8a63d20d}
.m.image{outline:3px dashed #8a63d2;outline-offset:-3px}
.tag.wide{white-space:normal;max-width:calc(100% - 24px);font:500 12px/1.4 ui-monospace,Consolas,monospace}
.tag{position:absolute;font:600 11px system-ui;color:#fff;padding:2px 7px;border-radius:6px;white-space:nowrap;transform:translateY(-100%);pointer-events:none;box-shadow:0 2px 6px #0004}
.m.pulse{animation:pulse 1.4s ease-out 2}@keyframes pulse{0%{box-shadow:0 0 0 0 var(--k)}100%{box-shadow:0 0 0 14px transparent}}
#tip{position:fixed;pointer-events:none;background:#0d1424ee;color:#fff;font-size:12px;padding:7px 9px;border-radius:8px;max-width:320px;display:none;z-index:9}
"""

VJS = r"""
const D=JSON.parse(document.getElementById('data').textContent);const tip=document.getElementById('tip');
const ov={};document.querySelectorAll('.ov').forEach(o=>ov[o.dataset.page]=o);
const byF={};const byO={};const tagged=new Set();
for(const m of D.marks){const o=ov[m.page];if(!o)continue;const col=D.colors[m.kind]||'#888';
 m.boxes.forEach((b,i)=>{const d=document.createElement('div');d.className='m '+m.kind;d.style.cssText=`left:${b[0]*100}%;top:${b[1]*100}%;width:${b[2]*100}%;height:${b[3]*100}%;--k:${col}`;
  d.dataset.fid=m.fid;const ok=m.fid+':'+m.occ;(byO[ok]=byO[ok]||[]).push(d);d.onmouseenter=ev=>{tip.textContent=m.label;tip.style.display='block'};d.onmousemove=ev=>{tip.style.left=Math.min(ev.clientX+12,innerWidth-330)+'px';tip.style.top=ev.clientY+12+'px'};
  d.onmouseleave=()=>tip.style.display='none';d.onclick=()=>{const r=document.querySelector(`li[data-occ="${m.fid}:${m.occ}"]`)||document.querySelector(`.card[data-fid="${m.fid}"]`);if(r){document.querySelectorAll('.occ li.on').forEach(x=>x.classList.remove('on'));r.classList.add('on');r.scrollIntoView({behavior:'smooth',block:'center'})}};
  o.appendChild(d);(byF[m.fid]=byF[m.fid]||[]).push(d);
  const key=m.kind==='garbled'?m.page+':garbled':m.page+':'+m.kind+':'+Math.round(b[1]*40);
  if(i===0&&!tagged.has(key)&&['conflict','redact','pii','garbled','table'].includes(m.kind)){tagged.add(key);const t=document.createElement('div');t.className='tag';t.textContent=m.label;t.style.cssText=`left:${Math.min(b[0]*100,70)}%;top:${b[1]*100}%;background:${col}`;o.appendChild(t)}
  if(m.kind==='scanned'||m.kind==='image'||m.kind==='garbledpage'){const t=document.createElement('div');t.className='tag'+(m.kind==='garbledpage'?' wide':'');t.textContent=m.label;t.style.cssText=`left:12px;top:36px;background:${col};transform:none`;o.appendChild(t)}})}
function goOcc(fid,o){const els=byO[fid+':'+o];document.querySelectorAll('.occ li.on').forEach(x=>x.classList.remove('on'));const r=document.querySelector(`li[data-occ="${fid}:${o}"]`);if(r)r.classList.add('on');if(!els||!els.length)return;els[0].scrollIntoView({behavior:'smooth',block:'center'});els.forEach(x=>{x.classList.remove('pulse');void x.offsetWidth;x.classList.add('pulse')})}
function go(fid){const els=byF[fid];if(!els||!els.length){return}els[0].scrollIntoView({behavior:'smooth',block:'center'});els.forEach(x=>{x.classList.remove('pulse');void x.offsetWidth;x.classList.add('pulse')})}
"""
