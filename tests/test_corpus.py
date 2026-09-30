"""Tests for ragcheck.corpus module."""

from __future__ import annotations

from pathlib import Path

import pytest

from ragpreflight.corpus import audit_corpus, _jaccard, _extract_shingles
from ragpreflight.models import CorpusReport, IssueCategory


class TestAuditCorpus:
    def test_audit_returns_corpus_report(self, fixtures_dir: Path) -> None:
        report = audit_corpus(fixtures_dir, show_progress=False)
        assert isinstance(report, CorpusReport)

    def test_all_supported_files_scanned(self, fixtures_dir: Path) -> None:
        report = audit_corpus(fixtures_dir, show_progress=False)
        # Should find at least the fixture files we created
        assert report.total_documents >= 4

    def test_average_score_in_range(self, fixtures_dir: Path) -> None:
        report = audit_corpus(fixtures_dir, show_progress=False)
        assert 0 <= report.average_score <= 100

    def test_empty_directory_returns_zero_docs(self, tmp_path: Path) -> None:
        report = audit_corpus(tmp_path, show_progress=False)
        assert report.total_documents == 0
        assert len(report.corpus_issues) > 0

    def test_not_a_directory_raises(self, sample_txt: Path) -> None:
        with pytest.raises(NotADirectoryError):
            audit_corpus(sample_txt, show_progress=False)

    def test_to_dict_serialisable(self, fixtures_dir: Path) -> None:
        import json
        report = audit_corpus(fixtures_dir, show_progress=False)
        json.dumps(report.to_dict())

    def test_worst_documents_property(self, fixtures_dir: Path) -> None:
        report = audit_corpus(fixtures_dir, show_progress=False)
        worst = report.worst_documents
        if worst:
            for doc in worst:
                assert doc.score <= max(d.score for d in report.documents)

    def test_documents_list_populated(self, fixtures_dir: Path) -> None:
        report = audit_corpus(fixtures_dir, show_progress=False)
        assert len(report.documents) == report.total_documents

    def test_custom_profile_applied(self, fixtures_dir: Path) -> None:
        from ragpreflight.profiles import get_profile
        profile = get_profile("strict")
        report = audit_corpus(fixtures_dir, profile=profile, show_progress=False)
        # Strict profile may flag more documents
        assert isinstance(report, CorpusReport)


class TestDuplicateDetection:
    def test_jaccard_identical(self) -> None:
        a = {"the", "quick", "brown", "fox"}
        assert _jaccard(a, a) == 1.0

    def test_jaccard_disjoint(self) -> None:
        a = {"the", "quick", "brown"}
        b = {"lazy", "dog", "jumped"}
        assert _jaccard(a, b) == 0.0

    def test_jaccard_partial(self) -> None:
        a = {"a", "b", "c"}
        b = {"b", "c", "d"}
        sim = _jaccard(a, b)
        assert 0.0 < sim < 1.0

    def test_jaccard_empty(self) -> None:
        assert _jaccard(set(), set()) == 1.0

    def test_extract_shingles_returns_set(self, sample_txt: Path) -> None:
        shingles = _extract_shingles(str(sample_txt))
        assert isinstance(shingles, set)
        assert len(shingles) > 0

    def test_near_duplicate_detected(self, tmp_path: Path) -> None:
        """Two nearly identical files should be flagged as duplicates."""
        text = "The quick brown fox jumps over the lazy dog. " * 50
        (tmp_path / "doc_a.txt").write_text(text, encoding="utf-8")
        (tmp_path / "doc_b.txt").write_text(text, encoding="utf-8")
        report = audit_corpus(tmp_path, show_progress=False)
        dup_issues = [i for i in report.corpus_issues if i.category == IssueCategory.DUPLICATION]
        assert len(dup_issues) > 0

    def test_different_files_not_duplicates(self, tmp_path: Path) -> None:
        """Files with very different content should not be flagged."""
        (tmp_path / "doc_a.txt").write_text("Machine learning is a subset of artificial intelligence. " * 30)
        (tmp_path / "doc_b.txt").write_text("Pizza is made from dough, cheese, and tomato sauce. " * 30)
        report = audit_corpus(tmp_path, show_progress=False)
        dup_issues = [i for i in report.corpus_issues if i.category == IssueCategory.DUPLICATION]
        assert len(dup_issues) == 0
