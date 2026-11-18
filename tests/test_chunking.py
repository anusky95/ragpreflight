from __future__ import annotations

import time
from pathlib import Path

import pytest

from ragpreflight.checks import chunking
from ragpreflight.checks.chunking import CHUNK_WORDS, is_heading, simulate_chunks
from ragpreflight.models import Document, Page, Severity

DEMO_POLICY = (Path(__file__).resolve().parents[1] / "src/ragpreflight/demo/refund-policy-2024.md").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _fixed_word_windows(monkeypatch):
    # These tests exercise the boundary logic with exact, predictable 380-word windows.
    monkeypatch.setenv("RAGPREFLIGHT_CHUNKER", "words:380")

# Every sentence is exactly 10 words, so with 380-word chunks every boundary that follows
# a multiple of 10 words falls cleanly between sentences. Some contain qualifiers on purpose.
SENTENCES = [
    "The airline publishes its customer service plan every single year.",
    "Passengers must not carry more than two bags onboard today.",
    "Refunds are never issued after the scheduled departure has passed.",
    "Staff review each complaint and reply within five working days.",
    "Seats can be changed online unless the flight is full.",
]


def prose(n_words: int) -> str:
    assert n_words % 10 == 0
    return " ".join(SENTENCES[i % len(SENTENCES)] for i in range(n_words // 10))


def doc(text: str, fmt: str = "md", name: str = "policy") -> Document:
    return Document(Path(f"/corpus/{name}.{fmt}"), fmt, [Page(1, text)])


def run(*docs: Document):
    return chunking.run(list(docs), Path("/corpus"))


def by_check(findings, check):
    return [f for f in findings if f.check == check]


# ---------------------------------------------------------------- simulate_chunks


def test_simulate_chunks_sizes_and_no_overlap():
    text = " ".join(f"w{i}" for i in range(1000))
    chunks = simulate_chunks(text, 380)
    assert [len(c.split()) for c in chunks] == [380, 380, 240]
    assert chunks[1].split()[0] == "w380"
    assert simulate_chunks("", 380) == []
    assert len(simulate_chunks(text, 100)) == 10
    assert CHUNK_WORDS == 380


def test_simulate_chunks_keeps_newlines():
    text = "# Title\n" + "word " * 10
    assert simulate_chunks(text, 5)[0].startswith("# Title\n")


# ---------------------------------------------------------------- chunk-split


def test_airline_bereavement_example_fires():
    # 372 words of clean prose + 8-word claim = 380 -> boundary right after "members"
    text = (prose(370) + " Read carefully. "
            + "Bereavement fares are offered for immediate family members "
            + "and must be requested before travel. Ask staff for help. " + prose(400))
    assert len(simulate_chunks(text)[0].split()) == 380
    assert simulate_chunks(text)[0].endswith("immediate family members")
    assert simulate_chunks(text)[1].startswith("and must be requested before travel.")
    fs = by_check(run(doc(text)), "chunk-split")
    assert len(fs) == 1
    f = fs[0]
    assert f.severity in (Severity.INFO, Severity.WARN) and f.f_codes == ["F7"]
    assert "Bereavement fares" in f.detail and "must be requested before travel" in f.detail
    assert "chunk 1 of" in f.detail and "ends mid-sentence" in f.detail and "✂" in f.detail
    assert f.location.startswith("line ")
    assert "--chunker" in f.fix and "overlap" in f.detail.lower()


def test_split_in_rule_like_text_is_a_warning():
    rules = ("Passengers must present valid ID. Refunds are permitted only per policy. "
             "Pets are prohibited in exit rows. ")
    text = (prose(370) + " Read carefully. "
            + "Bereavement fares are offered for immediate family members "
            + "and must be requested before travel. Ask staff for help. " + prose(400) + " " + rules * 20)
    fs = by_check(run(doc(text)), "chunk-split")
    assert fs and fs[0].severity is Severity.WARN


def test_clean_sentence_boundaries_do_not_fire():
    # boundaries land exactly between 10-word sentences, despite many qualifiers in the text
    assert run(doc(prose(3800))) == []


def test_split_without_qualifier_does_not_fire():
    text = (prose(370) + " Read carefully. "
            + "Bereavement fares are offered for immediate family members "
            + "and their spouses or partners as well. " + prose(400))
    assert by_check(run(doc(text)), "chunk-split") == []


def test_short_doc_skipped():
    text = "Fares are offered for family members and must be requested before travel. " * 5
    assert run(doc(text)) == []


def test_split_aggregated_per_doc_with_examples():
    claim1 = "Upgrades are available to members with gold status cards"  # 9 words
    claim2 = "Lounge access is included for travellers on business fares"  # 9 words
    clause = "unless the cabin is already full that day."  # 8 words
    text = (prose(370) + " Read. " + claim1 + " " + clause + " "
            + prose(360) + " Please read this. " + claim2 + " " + clause + " " + prose(400))
    chunks = simulate_chunks(text)
    assert chunks[1].startswith("unless") and chunks[2].startswith("unless")
    fs = by_check(run(doc(text)), "chunk-split")
    assert len(fs) == 1  # one finding per document, not per boundary
    assert fs[0].title.startswith("2 sentence(s)")
    assert "Upgrades are available" in fs[0].detail and "Lounge access" in fs[0].detail
    assert fs[0].location == "line 1"  # single-line test text: both cuts are on line 1


# ---------------------------------------------------------------- orphan headings


def test_is_heading():
    assert is_heading("## Refund policy")
    assert is_heading("REFUND POLICY")
    assert is_heading("3.2 Eligibility rules")
    assert not is_heading("Refunds are never issued after departure.")
    assert not is_heading("Plan | Price | Seats")
    assert not is_heading("")


def test_orphan_heading_fires():
    text = prose(370) + " Read carefully.\n\n## 4.1 Baggage allowance\n\n" + prose(400)
    # 372 words + 4 heading words = 376 -> heading ends within the last words of chunk 1
    fs = by_check(run(doc(text)), "chunk-orphan-heading")
    assert len(fs) == 1
    assert fs[0].severity is Severity.INFO
    assert "Baggage allowance" in fs[0].detail
    assert fs[0].location.startswith("line ")


def test_heading_mid_chunk_does_not_fire():
    text = prose(200) + "\n\n## Baggage allowance\n\n" + prose(600)
    assert by_check(run(doc(text)), "chunk-orphan-heading") == []


# ---------------------------------------------------------------- table split


TABLE = "\n".join(["Plan | Price | Seats"] + [f"Tier{i} | {i}0 | {i}" for i in range(1, 9)])


def test_table_split_fires():
    # header row at word ~370, 8 data rows of 5 words -> crosses the 380-word boundary
    text = prose(360) + " Pricing below.\n" + TABLE + "\n" + prose(400)
    fs = by_check(run(doc(text)), "chunk-table-split")
    assert len(fs) == 1
    assert fs[0].severity is Severity.INFO
    assert "Plan | Price | Seats" in fs[0].detail
    assert fs[0].location.startswith("line ")


def test_table_inside_one_chunk_does_not_fire():
    text = prose(100) + "\n" + TABLE + "\n" + prose(600)
    assert by_check(run(doc(text)), "chunk-table-split") == []


# ---------------------------------------------------------------- robustness


def test_code_doc_no_split_findings():
    code = "\n".join(
        f"def handler_{i}(event, context):\n    if not event.get('id'):\n        return None\n"
        f"    return process(event['id'], retries=3)\n"
        for i in range(120)
    )
    fs = run(doc(code, fmt="md", name="code"))
    assert by_check(fs, "chunk-split") == []
    assert by_check(fs, "chunk-table-split") == []


def test_never_raises():
    odd = Document(Path("/corpus/odd.pdf"), "pdf", [Page(1, None)])  # type: ignore[arg-type]
    assert isinstance(run(odd, doc(prose(800))), list)
    assert run(Document(Path("/corpus/x.pdf"), "pdf", [], read_error="boom")) == []


def test_deterministic():
    text = (prose(370) + " Read carefully. Bereavement fares are offered for immediate family "
            "members and must be requested before travel. " + prose(400))
    a = [f.to_dict() for f in run(doc(text))]
    assert a == [f.to_dict() for f in run(doc(text))]


def test_performance_2000_docs():
    words = (prose(3000)).split()
    text = "\n".join(" ".join(words[i:i + 12]) for i in range(0, 3000, 12))
    docs = [doc(text, name=f"d{i}") for i in range(2000)]
    t = time.perf_counter()
    run(*docs)
    assert time.perf_counter() - t < 30  # generous: CI runners are slow


# ---------------------------------------------------------------- real splitters


def test_default_is_langchain_recursive_with_its_real_defaults(monkeypatch):
    from ragpreflight import chunkers

    monkeypatch.delenv("RAGPREFLIGHT_CHUNKER", raising=False)
    ch = chunkers.chunk("para one.\n\n" + "word " * 2000)
    assert "RecursiveCharacterTextSplitter (chunk_size=4000 chars, overlap=200)" in ch.label
    assert all(e - s <= 4000 for s, e in ch.spans)


def test_bundled_port_matches_langchain_exactly():
    lc = pytest.importorskip("langchain_text_splitters")
    from ragpreflight import chunkers

    texts = [DEMO_POLICY, prose(3000), "\n\n".join(prose(400) for _ in range(12)),
             "# A\n\n" + ("Line one of a list item\n" * 300)]
    for size, overlap in [(4000, 200), (1000, 200), (300, 0), (500, 120)]:
        real = lc.RecursiveCharacterTextSplitter(chunk_size=size, chunk_overlap=overlap)
        for t in texts:
            assert chunkers._lc_recursive(t, size, overlap) == real.split_text(t), (size, overlap)


def test_spans_point_at_the_chunk_text():
    from ragpreflight import chunkers

    t = "\n\n".join(prose(300) for _ in range(8))
    ch = chunkers.chunk(t, "langchain:1000:200")
    for s, e in ch.spans:
        assert t[s:e].strip() and len(t[s:e]) <= 1000


def test_token_windows_and_validation():
    from ragpreflight import chunkers

    assert chunkers.is_valid("langchain:1000:200") and chunkers.is_valid("openai") and chunkers.is_valid("off")
    assert not chunkers.is_valid("fixed-512")
    ch = chunkers.chunk(prose(3000), "tokens:200:50")
    assert len(ch.spans) > 3
    assert chunkers.chunk("anything", "off") is None
