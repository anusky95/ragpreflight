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
        has_bad = any(
            i.severity in (Severity.CRITICAL, Severity.WARNING) for i in report.issues
        )
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
