from __future__ import annotations

import sys
import time
from pathlib import Path

try:
    import ragpreflight  # noqa: F401
except ImportError:  # running from a source checkout without install
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ragpreflight.checks import extraction
from ragpreflight.checks.extraction import format_pages, is_table_row
from ragpreflight.models import Document, Page, Severity

PROSE = (
    "The quarterly report describes how the support team handled customer requests during "
    "the spring. Most tickets were resolved within two days, and the team expects to keep "
    "that pace next year. Managers reviewed the backlog every week and moved staff between "
    "queues when the volume changed. "
)

MATH = (
    "Let α, β ∈ ℝ and consider the estimator θ̂ = ∑_{i=1}^{n} w_i x_i with weights w_i ≥ 0. "
    "We show that E[θ̂] ≤ \\frac{a}{b} + ε whenever λ > 0, and that ‖x‖₂ ≤ √n · ‖x‖_∞. "
    "The gradient ∂L/∂θ = −2 ∑ (y_i − θ x_i) x_i vanishes at the optimum, so "
    "\\mathcal{O}(n \\log n) time suffices. For all δ ∈ (0, 1), with probability 1 − δ, "
    "|θ̂ − θ| ≤ σ √(2 \\log(2/δ) / n). Here μ denotes the mean, Σ the covariance and "
    "π(x) ∝ exp(−βH(x)) the Gibbs measure; ∀x ∃y: f(x) ≠ g(y) ⇒ h(x) ≈ 0. "
)

CODE = '''
def load_config(path: str) -> dict:
    """Read the YAML config and merge defaults."""
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    cfg = {**DEFAULTS, **raw}
    if not cfg.get("output_dir"):
        raise ValueError("output_dir is required")
    for key, val in cfg.items():
        logger.debug("cfg %s=%r", key, val)
    return cfg

class Cache:
    def __init__(self, maxsize=128):
        self._data = OrderedDict()
        self.maxsize = maxsize

    def get(self, key):
        if key in self._data:
            self._data.move_to_end(key)
            return self._data[key]
        return None

    def put(self, key, value):
        self._data[key] = value
        if len(self._data) > self.maxsize:
            self._data.popitem(last=False)
'''


def doc(text: str = "", fmt: str = "txt", pages: list[Page] | None = None, name: str = "doc") -> Document:
    if pages is None:
        pages = [Page(1, text)]
    return Document(Path(f"/corpus/{name}.{fmt}"), fmt, pages)


def run(*docs: Document):
    return extraction.run(list(docs), Path("/corpus"))


def checks(findings) -> set[str]:
    return {f.check for f in findings}


# ---------------------------------------------------------------- helpers


def test_format_pages():
    assert format_pages([4, 5, 6, 7, 9]) == "p.4–7, 9"
    assert format_pages([2]) == "p.2"
    assert format_pages([]) == ""


def test_is_table_row():
    assert is_table_row("Revenue 120 340 560 12%")
    assert is_table_row("Region  |  Q1  |  Q2")
    assert is_table_row("North    1,200    3,400    5.6")
    assert not is_table_row("In 2023 we hired 120 people across the company offices.")


# ---------------------------------------------------------------- negatives


def test_clean_prose_no_findings():
    assert run(doc(PROSE * 10)) == []


def test_math_is_not_garbled():
    fs = run(doc(MATH * 6, fmt="md"), doc(pages=[Page(i, MATH * 3) for i in range(1, 4)], fmt="pdf", name="paper"))
    assert "extraction-garbled" not in checks(fs), fs


def test_code_is_not_garbled_or_table():
    fs = run(doc(CODE * 4, fmt="md"))
    assert fs == [], fs


def test_short_doc_is_only_empty():
    fs = run(doc("Draft. TODO"))
    assert checks(fs) == {"extraction-empty"}
    assert fs[0].severity is Severity.ERROR
    assert fs[0].f_codes == ["F2", "F3"]
    assert fs[0].path.endswith("doc.txt")


def test_non_english_prose_not_garbled():
    fr = ("Le contrat est signé par les deux parties et il entre en vigueur dès la date de "
          "signature. Les frais de dossier sont à la charge du client pour toute la durée. ") * 20
    de = ("Die Reise muss vor dem Abflug beantragt werden und ist nicht übertragbar. "
          "Der Kunde erhält eine Bestätigung per E-Mail mit allen Details. ") * 20
    assert "extraction-garbled" not in checks(run(doc(fr), doc(de, name="de")))


def test_bullet_list_with_numbers_is_not_table():
    md = PROSE + "\n- Text extractability (30%)\n- OCR quality (25%)\n- Tables (20%)\n- Metadata (15%)\n- PII (10%)\n"
    assert run(doc(md, fmt="md")) == []


def test_lost_bullet_glyphs_are_not_garbled():
    # pypdf often turns bullet glyphs into \x7f at line starts
    text = "\n".join(f"\x7f Point {i}: the team reviewed the backlog and moved staff between queues." for i in range(30))
    assert "extraction-garbled" not in checks(run(doc(fmt="pdf", pages=[Page(1, text)])))


def test_markdown_pipe_table_is_not_extraction_problem():
    md = PROSE + "\n| Plan | Price | Seats |\n|---|---|---|\n| Basic | 10 | 1 |\n| Pro | 20 | 5 |\n| Team | 50 | 20 |\n"
    assert "extraction-table" not in checks(run(doc(md, fmt="md")))


# ---------------------------------------------------------------- positives


def test_scanned_majority_is_error_and_aggregated():
    pages = [Page(1, PROSE)] + [Page(i, "", 1) for i in range(2, 6)]
    fs = [f for f in run(doc(fmt="pdf", pages=pages)) if f.check == "extraction-scanned"]
    assert len(fs) == 1
    assert fs[0].severity is Severity.ERROR
    assert fs[0].location == "p.2–5"
    assert "ocr" in fs[0].fix.lower()


def test_scanned_minority_is_warn():
    pages = [Page(i, PROSE) for i in range(1, 10)] + [Page(10, "  12 ", 2)]
    fs = [f for f in run(doc(fmt="pdf", pages=pages)) if f.check == "extraction-scanned"]
    assert len(fs) == 1 and fs[0].severity is Severity.WARN
    assert fs[0].location == "p.10"


def test_fully_scanned_pdf_reports_scanned_not_empty():
    fs = run(doc(fmt="pdf", pages=[Page(1, "", 1), Page(2, "", 1)]))
    assert checks(fs) == {"extraction-scanned"}
    assert "All pages" in fs[0].title


def test_images_pdf_page():
    pages = [Page(1, PROSE * 3), Page(2, "Figure 2: revenue by region over five years", 4)]
    fs = [f for f in run(doc(fmt="pdf", pages=pages)) if f.check == "extraction-images"]
    assert len(fs) == 1 and fs[0].location == "p.2" and fs[0].f_codes == ["F4"]


def test_images_non_pdf():
    d = Document(Path("/corpus/deck.docx"), "docx", [Page(1, PROSE, 6)])
    assert "extraction-images" in checks(run(d))
    d2 = Document(Path("/corpus/report.docx"), "docx", [Page(1, PROSE * 10, 3)])
    assert "extraction-images" not in checks(run(d2))


def test_garbled_replacement_chars():
    bad = PROSE + "The f\ufffde for the \ufffd\ufffdrvice is \ufffd\ufffd per month. " + PROSE
    fs = [f for f in run(doc(bad)) if f.check == "extraction-garbled"]
    assert len(fs) == 1
    assert "U+FFFD" in fs[0].detail
    assert fs[0].detail.count('"') >= 2  # quoted evidence snippets
    assert fs[0].location.startswith("line ")


def test_garbled_cid_on_pdf_pages():
    bad = "(cid:72)(cid:101)(cid:108)(cid:108)(cid:111) " * 3 + PROSE
    pages = [Page(1, PROSE), Page(2, bad), Page(3, bad), Page(4, PROSE)]
    fs = [f for f in run(doc(fmt="pdf", pages=pages)) if f.check == "extraction-garbled"]
    assert len(fs) == 1 and fs[0].location == "p.2–3"
    assert "(cid:" in fs[0].detail


def test_garbled_mojibake():
    bad = PROSE + "Le cafÃ© est ouvert. Weâ€™re open. RÃ©sumÃ© attached. â€œQuoteâ€\x9d " + PROSE
    fs = [f for f in run(doc(bad)) if f.check == "extraction-garbled"]
    assert len(fs) == 1 and "mojibake" in fs[0].title


def test_garbled_letter_spacing():
    bad = PROSE + "T h i s  p o l i c y  a p p l i e s  t o  a l l  s t a f f  m e m b e r s . " + PROSE
    fs = [f for f in run(doc(bad)) if f.check == "extraction-garbled"]
    assert len(fs) == 1 and "letter-spaced" in fs[0].title


def test_garbled_low_dictionary():
    junk = "Tlhs plcy rqrs thrt mnth ntc bfr trmntn wth crtfd mll dlvry " * 30
    fs = [f for f in run(doc(junk)) if f.check == "extraction-garbled"]
    assert len(fs) == 1 and "dictionary" in fs[0].title


def test_table_pdf_numeric_rows():
    table = "\n".join(["Region Q1 Q2 Q3 Q4"] + [f"R{i} {i}20 {i}40 {i}60 {i}80" for i in range(1, 8)])
    pages = [Page(1, PROSE), Page(2, PROSE + "\n" + table + "\n" + PROSE)]
    fs = [f for f in run(doc(fmt="pdf", pages=pages)) if f.check == "extraction-table"]
    assert len(fs) == 1
    assert fs[0].location == "p.2"
    assert fs[0].title == "Table-heavy content on p.2 will likely extract as run-on text"


def test_table_docx_pipe_rows():
    rows = "\n".join(["Plan | Price | Seats", "Basic | 10 | 1", "Pro | 20 | 5", "Team | 50 | 20"])
    d = Document(Path("/corpus/pricing.docx"), "docx", [Page(1, PROSE + "\n" + rows)])
    fs = [f for f in run(d) if f.check == "extraction-table"]
    assert len(fs) == 1 and "4 rows" in fs[0].title


def test_never_raises_on_odd_input():
    odd = Document(Path("/corpus/odd.pdf"), "pdf", [Page(1, None, None)])  # type: ignore[arg-type]
    assert isinstance(run(odd, doc(PROSE * 5)), list)
    assert run(Document(Path("/corpus/x.pdf"), "pdf", [], read_error="boom")) == []


def test_deterministic():
    d = doc(fmt="pdf", pages=[Page(1, ""), Page(2, "", 3), Page(3, PROSE + "(cid:1) " * 5)])
    assert [f.to_dict() for f in run(d)] == [f.to_dict() for f in run(d)]


def test_performance_2000_docs():
    words = (PROSE * 80).split()[:3000]
    assert len(words) == 3000
    page_text = "\n".join(" ".join(words[i:i + 12]) for i in range(0, 3000, 12))
    docs = [Document(Path(f"/corpus/d{i}.pdf"), "pdf", [Page(1, page_text)]) for i in range(2000)]
    t = time.perf_counter()
    run(*docs)
    assert time.perf_counter() - t < 30  # generous: CI runners are slow
