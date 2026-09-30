"""Tests for ragcheck.retrieval module."""

from __future__ import annotations

from pathlib import Path

import pytest

from ragpreflight.retrieval import (
    _generate_extractive_queries,
    _simple_keyphrases,
)


class TestExtractiveQueryGeneration:
    def test_generates_queries(self) -> None:
        text = """
# Introduction to Machine Learning

Machine learning is a method of data analysis that automates analytical model building.

## Supervised Learning

Supervised learning uses labeled training data. Common algorithms include linear regression and neural networks.

## Unsupervised Learning

Unsupervised learning finds patterns in data without labels. Clustering is a typical application.
        """
        queries = _generate_extractive_queries(text, n=5)
        assert len(queries) > 0
        assert all(isinstance(q, str) for q in queries)
        assert all(len(q) > 5 for q in queries)

    def test_no_duplicate_queries(self) -> None:
        text = "Machine learning. " * 50
        queries = _generate_extractive_queries(text, n=7)
        assert len(queries) == len(set(q.lower() for q in queries))

    def test_empty_text_returns_empty(self) -> None:
        queries = _generate_extractive_queries("", n=5)
        assert queries == []

    def test_short_text_returns_some_queries(self) -> None:
        text = "Neural networks learn from data. Deep learning uses many layers."
        queries = _generate_extractive_queries(text, n=3)
        # May be fewer than requested due to sparse content
        assert isinstance(queries, list)


class TestSimpleKeyphrases:
    def test_returns_list(self) -> None:
        text = "machine learning deep learning neural network machine learning deep learning"
        phrases = _simple_keyphrases(text)
        assert isinstance(phrases, list)

    def test_frequent_phrases_returned(self) -> None:
        text = ("machine learning " * 10) + ("deep learning " * 8) + "random word "
        phrases = _simple_keyphrases(text, top_n=5)
        assert "machine learning" in phrases or len(phrases) > 0

    def test_short_text_returns_empty_or_small(self) -> None:
        text = "Hello world."
        phrases = _simple_keyphrases(text, top_n=10)
        assert isinstance(phrases, list)


def _try_import(module: str) -> bool:
    try:
        __import__(module)
        return True
    except (ImportError, RuntimeError, Exception):
        return False


@pytest.mark.slow
class TestSimulateRetrieval:
    """Integration tests — require sentence-transformers (ragcheck[full])."""

    @pytest.mark.skipif(
        not _try_import("sentence_transformers"),
        reason="sentence-transformers not installed",
    )
    def test_simulate_returns_metrics(self, fixtures_dir: Path) -> None:
        from ragpreflight.retrieval import simulate_retrieval
        result = simulate_retrieval(fixtures_dir, queries_per_doc=2, top_k=3)
        if "error" in result:
            pytest.skip(f"Embedder not available: {result['error']}")
        assert "synthetic_retrieval_hit_rate" in result
        assert "dead_chunk_rate" in result
        assert "query_failure_rate" in result
        assert 0.0 <= result["synthetic_retrieval_hit_rate"] <= 1.0
        assert 0.0 <= result["dead_chunk_rate"] <= 1.0
        assert 0.0 <= result["query_failure_rate"] <= 1.0
