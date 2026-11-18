import json
import subprocess
import sys
from pathlib import Path

from ragpreflight import scan
from ragpreflight.readers import discover, read

DEMO = Path(__file__).resolve().parents[1] / "src" / "ragpreflight" / "demo"


def run(*args):
    return subprocess.run([sys.executable, "-m", "ragpreflight", *args], capture_output=True, text=True,
                          encoding="utf-8", env={"PYTHONIOENCODING": "utf-8", "COLUMNS": "120", **__import__("os").environ})


def test_readers_handle_every_demo_format():
    docs = [read(p) for p in discover(DEMO)]
    assert {d.fmt for d in docs} == {"md", "txt", "html", "pdf", "docx"}
    assert all(d.read_error is None for d in docs)
    pdf = next(d for d in docs if d.fmt == "pdf")
    assert pdf.pages[0].image_count >= 1 and len(pdf.pages[0].text.strip()) < 25


def test_demo_scan_catches_the_planted_failures():
    report = scan(DEMO)
    checks = {f.check for f in report.findings}
    for expected in ("version-conflict", "version-stale", "extraction-scanned", "pii", "secret"):
        assert expected in checks, expected
    assert report.verdict == "NOT READY"
    conflict = next(f for f in report.findings if f.check == "version-conflict")
    assert "refund-policy" in conflict.path


def test_cli_json_and_exit_codes(tmp_path):
    r = run(str(DEMO), "--format", "json")
    assert r.returncode == 1, r.stderr
    data = json.loads(r.stdout)
    assert data["verdict"] == "NOT READY" and data["findings"]

    clean = tmp_path / "kb"
    clean.mkdir()
    (clean / "a.md").write_text("---\nowner: Docs team\nupdated: 2026-01-10\n---\n# Hello\n\n" + "Plain helpful text. " * 40)
    r = run(str(clean))
    assert r.returncode == 0, r.stdout + r.stderr


def test_cli_bare_path_and_html_report(tmp_path):
    out = tmp_path / "r.html"
    r = run(str(DEMO), "--out", str(out), "--fail-on", "never")
    assert r.returncode == 0
    assert "<html" in out.read_text(encoding="utf-8")


def test_unreadable_and_missing_paths(tmp_path):
    (tmp_path / "broken.pdf").write_bytes(b"%PDF-1.4 garbage")
    report = scan(tmp_path)
    assert any(f.check == "unreadable" for f in report.findings)
    assert run(str(tmp_path / "nope")).returncode == 2


def test_explain_and_coverage():
    assert "Conflicting" in run("explain", "F14").stdout
    assert "of 33 checked" in run("coverage").stdout


def test_html_links_every_finding_and_document_to_its_source(tmp_path):
    from ragpreflight.report import to_html

    report = scan(DEMO)
    page = to_html(report, link_base=DEMO)
    assert 'href="baggage-claim-form-scanned.pdf#page=1"' in page  # PDF opens at the flagged page
    assert 'href="refund-policy-2024.md"' in page  # related file of a conflict
    assert page.count("<tr>") == len(report.documents) + 1  # one row per scanned file + header
    absolute = to_html(report)
    assert "file:///" in absolute


def test_view_writes_a_page_viewer_with_highlights(tmp_path):
    out = tmp_path / "v.html"
    r = run("view", str(DEMO / "baggage-claim-form-scanned.pdf"), "-o", str(out), "--no-open")
    assert r.returncode == 0, r.stderr
    page = out.read_text(encoding="utf-8")
    assert "data:image/" in page and '"kind": "scanned"' in page
    assert "No text layer" in page


def test_view_shows_values_unless_masked(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "notes.txt").write_text("call 415-555-0187")
    # non-PDF falls back to the report; PII values stay masked there
    r = run("view", str(kb / "notes.txt"), "-o", str(tmp_path / "v.html"), "--no-open")
    assert r.returncode == 0 and "415-555-0187" not in (tmp_path / "v.html").read_text(encoding="utf-8")


def test_chunks_lints_a_real_index_export(tmp_path):
    rows = [
        {"text": "Refund policy\n\nBereavement fares are offered to immediate family members", "metadata": {"source": "policy.pdf", "page": 3}, "chunk_index": 0},
        {"text": "except when the ticket was bought with miles. Call 415-555-0187 for help.", "metadata": {"source": "policy.pdf", "page": 3}, "chunk_index": 1},
        {"text": "Bags are free.", "chunk_index": 2},
    ]
    f = tmp_path / "export.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    r = run("chunks", str(f), "--format", "json", "--fail-on", "never")
    assert r.returncode == 0, r.stderr
    checks = {x["check"] for x in json.loads(r.stdout)["findings"]}
    assert {"chunk-split", "pii", "chunk-no-source"} <= checks


def test_mask_hides_secret_values_in_bot_answers():
    from ragpreflight.html_viewer import _mask_answer

    out = _mask_answer("Use login pgebrand and the password onepge.", ["onepge"])
    assert "onepge" not in out and "[REDACTED]" in out
