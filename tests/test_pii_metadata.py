from pathlib import Path

from ragpreflight.checks import metadata, pii
from ragpreflight.models import Document, Page, Severity

ROOT = Path("/kb")


def doc(name, text, **meta):
    return Document(ROOT / name, "md", [Page(1, text)], dict(meta))


def test_pii_finds_personal_data_and_masks_it():
    d = doc("notes.txt", "Call jordan.reyes84@gmail.com or 415-555-0187. SSN 219-09-9999.")
    [f] = pii.run([d], ROOT)
    assert f.check == "pii" and f.severity == Severity.ERROR  # SSN makes it an error
    assert "jordan.reyes84" not in f.detail and "j***@gmail.com" in f.detail


def test_pii_ignores_role_mailboxes_and_example_domains():
    d = doc("contact.md", "Write to support@skyfare-air.com or jane@example.com. Version 1.2.3.4 shipped.")
    assert pii.run([d], ROOT) == []


def test_card_requires_luhn():
    good = doc("a.txt", "card 4111 1111 1111 1111")
    bad = doc("b.txt", "order 1234 5678 9012 3456")
    assert pii.run([good], ROOT)[0].title.startswith("Personal data: 1 payment card")
    assert pii.run([bad], ROOT) == []


def test_secrets_are_errors():
    d = doc("cfg.md", "use key sk-proj-Z3x9Q2mVb7Lw4Kp8Rt6Yn1Hs5Df0Gj2Ac4 and AKIAABCDEFGHIJKLMNOP")
    [f] = pii.run([d], ROOT)
    assert f.check == "secret" and f.severity == Severity.ERROR and "F25" in f.f_codes


def test_metadata_summarises_at_corpus_level():
    docs = [doc(f"d{i}.md", "plain text without any date") for i in range(6)]
    found = metadata.run(docs, ROOT)
    no_date = [f for f in found if f.check == "metadata-no-date"]
    assert len(no_date) == 1 and no_date[0].path == "(corpus)" and "6 of 6" in no_date[0].title


def test_metadata_reads_dates_from_text_and_flags_placeholders():
    d = doc("p.md", "Effective date: March 2024. Policy text.", title="Document1", author="user")
    found = {f.check for f in metadata.run([d], ROOT)}
    assert "metadata-no-date" not in found
    assert "metadata-placeholder" in found
    assert "metadata-no-owner" in found


def test_findings_say_exactly_where_with_values_masked():
    d = Document(ROOT / "f.pdf", "pdf", [Page(1, "intro"), Page(2, "line one\nPortal (Login: acme, Password: hunter22x); call us")])
    [f] = pii.run([d], ROOT)
    assert f.check == "secret" and f.location == "p.2, line 2"
    assert "p.2, line 2" in f.detail and "hunter22x" not in f.detail and "Password: ******" in f.detail
