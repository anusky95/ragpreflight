"""Tests for ragcheck.chunker module."""

from __future__ import annotations

from pathlib import Path

import pytest

from ragpreflight.chunker import (
    analyze_chunks,
    _split_recursive,
    _split_sentences,
    _split_paragraphs,
    _info_density,
)
from ragpreflight.models import ChunkReport


class TestSplitters:
    def test_recursive_split_produces_chunks(self) -> None:
        text = "Hello world. " * 100
        chunks = _split_recursive(text, chunk_size=100, overlap=10, separators=[". ", " ", ""])
        assert len(chunks) > 1
        assert all(isinstance(c, str) for c in chunks)

    def test_recursive_split_respects_size(self) -> None:
        text = "word " * 500
        chunks = _split_recursive(text, chunk_size=50, overlap=0, separators=[" ", ""])
        for chunk in chunks:
            assert len(chunk) <= 60  # Allow small overruns at boundaries

    def test_sentence_split(self) -> None:
        text = "First sentence. Second sentence. Third sentence. " * 20
        chunks = _split_sentences(text, chunk_size=100, overlap=10)
        assert len(chunks) > 1

    def test_paragraph_split(self) -> None:
        text = "Paragraph one.\n\nParagraph two.\n\nParagraph three.\n\n" * 10
        chunks = _split_paragraphs(text, chunk_size=50, overlap=5)
        assert len(chunks) > 1

    def test_empty_text_returns_empty(self) -> None:
        chunks = _split_recursive("", chunk_size=512, overlap=50, separators=["\n\n", " ", ""])
        assert chunks == []

    def test_short_text_returns_single_chunk(self) -> None:
        text = "Short text."
        chunks = _split_recursive(text, chunk_size=512, overlap=50, separators=["\n\n", " ", ""])
        assert len(chunks) == 1
        assert chunks[0] == text


class TestInfoDensity:
    def test_meaningful_text_high_density(self) -> None:
        text = "Machine learning algorithms process information from training datasets."
        density = _info_density(text)
        assert density > 0.5

    def test_stopword_heavy_text_low_density(self) -> None:
        text = "the and or but if it is as to for"
        density = _info_density(text)
        assert density < 0.5

    def test_empty_text_zero_density(self) -> None:
        assert _info_density("") == 0.0


@pytest.mark.slow
class TestAnalyzeChunks:
    def test_analyze_returns_list(self, sample_txt: Path) -> None:
        chunks = analyze_chunks(sample_txt)
        assert isinstance(chunks, list)
        assert len(chunks) > 0

    def test_each_result_is_chunk_report(self, sample_txt: Path) -> None:
        chunks = analyze_chunks(sample_txt)
        for chunk in chunks:
            assert isinstance(chunk, ChunkReport)
            assert isinstance(chunk.chunk_index, int)
            assert isinstance(chunk.text_preview, str)
            assert chunk.coherence_score is None or 0.0 <= chunk.coherence_score <= 1.0
            assert chunk.coherence_status in ("evaluated", "not_evaluated")

    def test_custom_splitter_used(self, sample_txt: Path) -> None:
        """Custom splitter should override strategy."""
        def my_splitter(text: str) -> list[str]:
            return [text[:100], text[100:200]]

        chunks = analyze_chunks(sample_txt, splitter=my_splitter)
        assert len(chunks) <= 2  # At most 2 non-empty chunks

    def test_chunk_indices_sequential(self, sample_txt: Path) -> None:
        chunks = analyze_chunks(sample_txt, chunk_size=200)
        indices = [c.chunk_index for c in chunks]
        assert indices == list(range(len(chunks)))

    def test_empty_file_returns_error_chunk(self, empty_txt: Path) -> None:
        chunks = analyze_chunks(empty_txt)
        assert len(chunks) == 1
        assert chunks[0].score_is_zero_or_critical()  # via issues

    def test_to_dict_serialisable(self, sample_txt: Path) -> None:
        import json
        chunks = analyze_chunks(sample_txt)
        for chunk in chunks:
            json.dumps(chunk.to_dict())

    def test_strategy_paragraph(self, sample_md: Path) -> None:
        chunks = analyze_chunks(sample_md, strategy="paragraph", chunk_size=300)
        assert len(chunks) > 0

    def test_strategy_sentence(self, sample_txt: Path) -> None:
        chunks = analyze_chunks(sample_txt, strategy="sentence", chunk_size=200)
        assert len(chunks) > 0


# Monkey-patch helper for the empty test
def _score_is_zero_or_critical(self: ChunkReport) -> bool:
    from ragpreflight.models import Severity
    return (
        self.coherence_score is None
        or self.coherence_score == 0.0
        or any(i.severity == Severity.CRITICAL for i in self.issues)
    )


ChunkReport.score_is_zero_or_critical = _score_is_zero_or_critical  # type: ignore[attr-defined]
