"""Tests for ragcheck.utils module."""

from __future__ import annotations

from pathlib import Path

import pytest

from ragpreflight.utils import (
    clamp,
    detect_encoding,
    detect_file_format,
    encoding_is_suspicious,
    is_supported,
    iter_supported_files,
    safe_read_text,
    truncate_text,
    check_file_size,
)


class TestDetectFileFormat:
    def test_pdf(self, tmp_path: Path) -> None:
        assert detect_file_format(tmp_path / "file.pdf") == "pdf"

    def test_docx(self, tmp_path: Path) -> None:
        assert detect_file_format(tmp_path / "file.docx") == "docx"

    def test_txt(self, tmp_path: Path) -> None:
        assert detect_file_format(tmp_path / "file.txt") == "txt"

    def test_csv(self, tmp_path: Path) -> None:
        assert detect_file_format(tmp_path / "file.csv") == "csv"

    def test_tsv(self, tmp_path: Path) -> None:
        assert detect_file_format(tmp_path / "file.tsv") == "tsv"

    def test_html(self, tmp_path: Path) -> None:
        assert detect_file_format(tmp_path / "file.html") == "html"
        assert detect_file_format(tmp_path / "file.htm") == "html"

    def test_markdown(self, tmp_path: Path) -> None:
        assert detect_file_format(tmp_path / "file.md") == "markdown"
        assert detect_file_format(tmp_path / "file.markdown") == "markdown"

    def test_unknown(self, tmp_path: Path) -> None:
        assert detect_file_format(tmp_path / "file.xyz") == "unknown"

    def test_case_insensitive(self, tmp_path: Path) -> None:
        assert detect_file_format(tmp_path / "FILE.PDF") == "pdf"


class TestIsSupported:
    def test_supported_extensions(self, tmp_path: Path) -> None:
        for ext in [".pdf", ".docx", ".txt", ".csv", ".tsv", ".html", ".md"]:
            assert is_supported(tmp_path / f"file{ext}")

    def test_unsupported_extension(self, tmp_path: Path) -> None:
        assert not is_supported(tmp_path / "file.xyz")
        assert not is_supported(tmp_path / "file.exe")


class TestClamp:
    def test_within_range(self) -> None:
        assert clamp(0.5) == 0.5

    def test_below_zero(self) -> None:
        assert clamp(-1.0) == 0.0

    def test_above_one(self) -> None:
        assert clamp(1.5) == 1.0

    def test_custom_bounds(self) -> None:
        assert clamp(15.0, lo=0.0, hi=10.0) == 10.0
        assert clamp(-5.0, lo=0.0, hi=10.0) == 0.0


class TestDetectEncoding:
    def test_utf8_text(self) -> None:
        raw = "Hello, world!".encode("utf-8")
        enc, conf = detect_encoding(raw)
        assert conf > 0.5

    def test_ascii_text(self) -> None:
        raw = b"Simple ASCII text."
        enc, conf = detect_encoding(raw)
        assert conf > 0.5


class TestEncodingIsSuspicious:
    def test_high_confidence_not_suspicious(self) -> None:
        assert not encoding_is_suspicious(0.99)

    def test_low_confidence_suspicious(self) -> None:
        assert encoding_is_suspicious(0.5)

    def test_boundary(self) -> None:
        assert not encoding_is_suspicious(0.8)
        assert encoding_is_suspicious(0.79)


class TestSafeReadText:
    def test_reads_utf8(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("Hello, UTF-8 world!", encoding="utf-8")
        text, enc = safe_read_text(f)
        assert "Hello" in text

    def test_reads_latin1(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_bytes("Héllo".encode("latin-1"))
        text, enc = safe_read_text(f)
        assert len(text) > 0


class TestTruncateText:
    def test_short_text_unchanged(self) -> None:
        assert truncate_text("hello", 100) == "hello"

    def test_long_text_truncated(self) -> None:
        text = "word " * 100
        result = truncate_text(text, 50)
        assert len(result) <= 50

    def test_newlines_collapsed(self) -> None:
        text = "hello\nworld\nfoo"
        result = truncate_text(text)
        assert "\n" not in result


class TestIterSupportedFiles:
    def test_finds_supported_files(self, fixtures_dir: Path) -> None:
        files = iter_supported_files(fixtures_dir)
        assert len(files) > 0
        for f in files:
            assert is_supported(f)

    def test_skips_hidden_files(self, tmp_path: Path) -> None:
        (tmp_path / ".hidden.txt").write_text("hidden")
        (tmp_path / "visible.txt").write_text("visible")
        files = iter_supported_files(tmp_path)
        names = [f.name for f in files]
        assert ".hidden.txt" not in names
        assert "visible.txt" in names

    def test_not_a_directory_raises(self, sample_txt: Path) -> None:
        with pytest.raises(NotADirectoryError):
            iter_supported_files(sample_txt)


class TestCheckFileSize:
    def test_normal_file_ok(self, tmp_path: Path) -> None:
        f = tmp_path / "small.txt"
        f.write_bytes(b"x" * 1000)
        size, warning = check_file_size(f)
        assert warning is None
        assert size == 1000

    def test_large_file_warns(self, tmp_path: Path) -> None:
        f = tmp_path / "big.txt"
        f.write_bytes(b"x" * (1024 * 1024 * 5))  # 5 MB
        size, warning = check_file_size(f, max_mb=1.0)
        assert warning is not None
        assert "MB" in warning

    def test_over_hard_limit_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "huge.txt"
        f.write_bytes(b"x" * (1024 * 1024))  # 1 MB
        with pytest.raises(ValueError, match="exceeds"):
            check_file_size(f, max_mb=0.5, hard_max_mb=0.9)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            check_file_size(tmp_path / "nonexistent.txt")
