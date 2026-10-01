"""Shared pytest fixtures for ragpreflight tests."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add --slow flag to opt-in to sentence-transformers tests."""
    parser.addoption(
        "--slow",
        action="store_true",
        default=False,
        help="Run slow tests that load sentence-transformer models.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip @pytest.mark.slow tests unless --slow is passed."""
    if not config.getoption("--slow", default=False):
        skip_slow = pytest.mark.skip(
            reason="Skipped by default (loads ML models). Run with --slow to include."
        )
        for item in items:
            if "slow" in item.keywords:
                item.add_marker(skip_slow)


@pytest.fixture()
def fixtures_dir() -> Path:
    """Return the path to the test fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture()
def sample_md(fixtures_dir: Path) -> Path:
    """Path to a clean Markdown fixture."""
    return fixtures_dir / "sample.md"


@pytest.fixture()
def sample_txt(fixtures_dir: Path) -> Path:
    """Path to a clean plain-text fixture."""
    return fixtures_dir / "sample.txt"


@pytest.fixture()
def sample_csv(fixtures_dir: Path) -> Path:
    """Path to a clean CSV fixture."""
    return fixtures_dir / "sample.csv"


@pytest.fixture()
def sample_html(fixtures_dir: Path) -> Path:
    """Path to a clean HTML fixture."""
    return fixtures_dir / "sample.html"


@pytest.fixture()
def ocr_errors_txt(fixtures_dir: Path) -> Path:
    """Path to a text file with OCR substitution errors."""
    return fixtures_dir / "ocr_errors.txt"


@pytest.fixture()
def empty_txt(fixtures_dir: Path) -> Path:
    """Path to an empty text file."""
    return fixtures_dir / "empty.txt"


@pytest.fixture()
def messy_csv(fixtures_dir: Path) -> Path:
    """Path to a CSV with inconsistent columns."""
    return fixtures_dir / "messy.csv"


@pytest.fixture()
def clean_pdf(fixtures_dir: Path) -> Path:
    """Path to a clean, well-structured PDF fixture."""
    return fixtures_dir / "clean.pdf"


@pytest.fixture()
def empty_pdf(fixtures_dir: Path) -> Path:
    """Path to a blank PDF with no text layer."""
    return fixtures_dir / "empty.pdf"


@pytest.fixture()
def ocr_errors_pdf(fixtures_dir: Path) -> Path:
    """Path to a PDF with OCR substitution artifacts."""
    return fixtures_dir / "ocr_errors.pdf"


@pytest.fixture()
def scanned_pdf(fixtures_dir: Path) -> Path:
    """Path to a scanned (image-only) PDF with no text layer."""
    return fixtures_dir / "scanned.pdf"


@pytest.fixture()
def messy_table_pdf(fixtures_dir: Path) -> Path:
    """Path to a PDF containing tables."""
    return fixtures_dir / "messy_table.pdf"


@pytest.fixture()
def sample_docx(fixtures_dir: Path) -> Path:
    """Path to a clean DOCX fixture."""
    return fixtures_dir / "sample.docx"
