"""Tests for ragcheck.scanner module."""

from __future__ import annotations

from pathlib import Path

import pytest

from ragpreflight.models import DocumentReport, IssueCategory, Severity
from ragpreflight.scanner import scan_document


class TestScanMarkdown:
    def test_clean_markdown_returns_report(self, sample_md: Path) -> None:
        report = scan_document(sample_md)
        assert isinstance(report, DocumentReport)
        assert report.score > 0
        assert report.file_format == "markdown"

    def test_clean_markdown_has_no_critical_issues(self, sample_md: Path) -> None:
        report = scan_document(sample_md)
        assert len(report.critical_issues) == 0

    def test_markdown_score_reflects_structure(self, sample_md: Path) -> None:
        report = scan_document(sample_md)
        # Good markdown with headings should score reasonably
        assert report.score >= 50


class TestScanTxt:
    def test_clean_txt_returns_report(self, sample_txt: Path) -> None:
        report = scan_document(sample_txt)
        assert isinstance(report, DocumentReport)
        assert report.score > 0
        assert report.file_format == "txt"

    def test_empty_txt_scores_zero(self, empty_txt: Path) -> None:
        report = scan_document(empty_txt)
        assert report.score == 0
        assert any(i.severity == Severity.CRITICAL for i in report.issues)

    def test_ocr_errors_detected(self, ocr_errors_txt: Path) -> None:
        report = scan_document(ocr_errors_txt)
        ocr_issues = [i for i in report.issues if i.category == IssueCategory.OCR]
        assert len(ocr_issues) > 0

    def test_ocr_errors_lower_score(self, sample_txt: Path, ocr_errors_txt: Path) -> None:
        clean = scan_document(sample_txt)
        dirty = scan_document(ocr_errors_txt)
        assert dirty.score <= clean.score


class TestScanCsv:
    def test_clean_csv_returns_report(self, sample_csv: Path) -> None:
        report = scan_document(sample_csv)
        assert isinstance(report, DocumentReport)
        assert report.file_format == "csv"
        assert report.score > 0

    def test_messy_csv_has_structure_issues(self, messy_csv: Path) -> None:
        report = scan_document(messy_csv)
        structure_issues = [i for i in report.issues if i.category == IssueCategory.STRUCTURE]
        assert len(structure_issues) > 0

    def test_messy_csv_scores_lower(self, sample_csv: Path, messy_csv: Path) -> None:
        clean = scan_document(sample_csv)
        messy = scan_document(messy_csv)
        assert messy.score <= clean.score


class TestScanHtml:
    def test_clean_html_returns_report(self, sample_html: Path) -> None:
        report = scan_document(sample_html)
        assert isinstance(report, DocumentReport)
        assert report.file_format == "html"
        assert report.score > 0

    def test_html_metadata_scored(self, sample_html: Path) -> None:
        report = scan_document(sample_html)
        # sample.html has <title> and <meta description>
        assert report.score > 40


class TestScanErrors:
    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            scan_document(tmp_path / "nonexistent.txt")

    def test_unsupported_extension_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "file.xyz"
        f.write_text("content")
        with pytest.raises(ValueError, match="Unsupported"):
            scan_document(f)

    def test_directory_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="directory"):
            scan_document(tmp_path)

    def test_file_too_large_raises(self, tmp_path: Path) -> None:
        # Create a fake 1-byte file and pretend the limit is 0 MB
        f = tmp_path / "big.txt"
        f.write_bytes(b"x" * 1025)  # 1 KB
        with pytest.raises(ValueError, match="exceeds"):
            scan_document(f, hard_max_file_size_mb=0.0009)


class TestScanPdf:
    def test_clean_pdf_returns_report(self, clean_pdf: Path) -> None:
        report = scan_document(clean_pdf)
        assert isinstance(report, DocumentReport)
        assert report.file_format == "pdf"
        assert report.score > 0

    def test_clean_pdf_has_pages(self, clean_pdf: Path) -> None:
        report = scan_document(clean_pdf)
        assert report.page_count >= 1

    def test_clean_pdf_high_extractability(self, clean_pdf: Path) -> None:
        report = scan_document(clean_pdf)
        assert report.text_extractable_ratio > 0.5

    def test_clean_pdf_scores_above_threshold(self, clean_pdf: Path) -> None:
        # A clean, well-structured PDF with metadata should score reasonably high
        report = scan_document(clean_pdf)
        assert report.score >= 60

    def test_empty_pdf_low_score(self, empty_pdf: Path) -> None:
        # Blank PDF: no text layer, so score must be below the standard threshold
        report = scan_document(empty_pdf)
        assert report.score < 60

    def test_empty_pdf_has_critical_or_warning(self, empty_pdf: Path) -> None:
        report = scan_document(empty_pdf)
        has_bad = any(i.severity in (Severity.CRITICAL, Severity.WARNING) for i in report.issues)
        assert has_bad

    def test_ocr_errors_pdf_detected(self, ocr_errors_pdf: Path) -> None:
        report = scan_document(ocr_errors_pdf)
        assert report.score >= 0

    def test_ocr_pdf_scores_lower_than_clean(self, clean_pdf: Path, ocr_errors_pdf: Path) -> None:
        clean = scan_document(clean_pdf)
        dirty = scan_document(ocr_errors_pdf)
        assert dirty.score <= clean.score

    def test_scanned_pdf_low_extractability(self, scanned_pdf: Path) -> None:
        report = scan_document(scanned_pdf)
        # Scanned PDF has no text layer — low extractability
        assert report.text_extractable_ratio < 0.5

    def test_messy_table_pdf_detects_structure(self, messy_table_pdf: Path) -> None:
        report = scan_document(messy_table_pdf)
        assert isinstance(report, DocumentReport)
        assert report.score > 0


class TestScanDocx:
    def test_docx_returns_report(self, sample_docx: Path) -> None:
        report = scan_document(sample_docx)
        assert isinstance(report, DocumentReport)
        assert report.file_format == "docx"
        assert report.score > 0

    def test_docx_has_no_critical_issues(self, sample_docx: Path) -> None:
        report = scan_document(sample_docx)
        assert len(report.critical_issues) == 0

    def test_docx_metadata_scored(self, sample_docx: Path) -> None:
        # sample.docx has title and author set
        report = scan_document(sample_docx)
        assert report.score >= 50

    def test_docx_score_in_range(self, sample_docx: Path) -> None:
        report = scan_document(sample_docx)
        assert 0 <= report.score <= 100


class TestDocumentReportContract:
    def test_report_has_required_fields(self, sample_txt: Path) -> None:
        report = scan_document(sample_txt)
        assert hasattr(report, "filepath")
        assert hasattr(report, "score")
        assert hasattr(report, "issues")
        assert hasattr(report, "file_format")
        assert hasattr(report, "file_size_bytes")
        assert hasattr(report, "text_extractable_ratio")
        assert hasattr(report, "page_count")

    def test_score_in_range(self, sample_txt: Path) -> None:
        report = scan_document(sample_txt)
        assert 0 <= report.score <= 100

    def test_to_dict_serialisable(self, sample_txt: Path) -> None:
        import json

        report = scan_document(sample_txt)
        d = report.to_dict()
        # Must be JSON serialisable
        json.dumps(d)
        assert "score" in d
        assert "issues" in d


def _make_pdf(path: Path, page_texts: list[str]) -> Path:
    import pymupdf

    doc = pymupdf.open()
    for text in page_texts:
        page = doc.new_page()
        if text == "IMG":
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 600, 800), 0)
            pix.clear_with(200)
            page.insert_image(page.rect, pixmap=pix)
        elif text:
            page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()
    return path


class TestPreLaunchRegressions:
    def test_tiny_text_pdf_not_flagged_as_scanned(self, tmp_path: Path) -> None:
        pdf = _make_pdf(tmp_path / "tiny.pdf", ["one <a@b.com>"])
        report = scan_document(pdf)
        assert report.text_extractable_ratio == 1.0
        assert not any("scanned PDF" in i.message for i in report.issues)
        assert any("Near-empty page" in i.message for i in report.issues)

    def test_scanned_pages_with_page_number_stamps_still_flagged(self, tmp_path: Path) -> None:
        import pymupdf

        doc = pymupdf.open()
        for n in range(1, 4):
            page = doc.new_page()
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 600, 800), 0)
            pix.clear_with(200)
            page.insert_image(page.rect, pixmap=pix)
            page.insert_text((280, 820), f"Page {n}", fontsize=8)
        pdf = tmp_path / "stamped_scan.pdf"
        doc.save(str(pdf))
        doc.close()
        report = scan_document(pdf)
        assert report.text_extractable_ratio == 0.0
        assert any("scanned PDF" in i.message for i in report.issues)
        assert report.score <= 20

    def test_image_only_pages_still_flagged(self, tmp_path: Path) -> None:
        pdf = _make_pdf(tmp_path / "half.pdf", ["IMG", "IMG", "IMG", "word " * 20])
        report = scan_document(pdf)
        assert report.text_extractable_ratio == 0.25
        assert any(i.severity == Severity.CRITICAL for i in report.issues)

    def test_half_image_only_pdf_is_critical_and_capped(self, tmp_path: Path) -> None:
        body = "The committee reviewed the quarterly operations report in detail. " * 5
        pdf = _make_pdf(tmp_path / "half.pdf", ["IMG", "IMG", body, body])
        report = scan_document(pdf)
        assert report.text_extractable_ratio == 0.5
        assert any(i.severity == Severity.CRITICAL for i in report.issues)
        assert report.score <= 50

    def test_blank_back_page_not_flagged_as_scanned(self, tmp_path: Path) -> None:
        body = "The committee reviewed the quarterly operations report in detail. " * 5
        pdf = _make_pdf(tmp_path / "letter.pdf", [body, ""])
        report = scan_document(pdf)
        assert report.text_extractable_ratio == 1.0
        assert not any(i.severity == Severity.CRITICAL for i in report.issues)
        assert any("Blank page" in i.message for i in report.issues)
        assert report.score >= 60

    @pytest.mark.parametrize(
        "text",
        ["Contact jane@example.com", "SSN 123-45-6789 and 987-65-4321"],
    )
    def test_pii_fires_below_three_hits(self, tmp_path: Path, text: str) -> None:
        pdf = _make_pdf(tmp_path / "pii.pdf", [text])
        report = scan_document(pdf)
        assert any(i.message.startswith("Possible PII") for i in report.issues)

    def test_reversed_table_preview_detected(self) -> None:
        from ragpreflight.scanner import _preview_looks_reversed

        assert _preview_looks_reversed("ehT | waL | lliw")
        assert _preview_looks_reversed("tI | si | ni")
        assert not _preview_looks_reversed("Model | BLEU | Training Cost")
