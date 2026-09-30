"""Validation tests for the Garani 2026 RAG failure taxonomy.

These tests enforce the plan's requirements:
- exactly 33 IDs (F1–F33, each exactly once)
- exactly 7 pipeline stages matching the paper
- DOI present on every mode
- evidence levels constrained to Strong/Moderate/Limited
- detector_status values are valid
- coverage API is consistent
- unsupported/runtime modes never appear as detected
"""

from __future__ import annotations

import pytest

from ragpreflight.taxonomy import (
    detector_coverage,
    get_failure_mode,
    list_failure_modes,
    modes_by_stage,
)
from ragpreflight.taxonomy.models import FailureMode


EXPECTED_DOI = "10.18653/v1/2026.trustnlp-main.27"

VALID_EVIDENCE_LEVELS = {"Strong", "Moderate", "Limited"}

VALID_DETECTOR_STATUSES = {"direct", "proxy", "risk_signal", "runtime_required", "unsupported"}

EXPECTED_STAGES = {
    "ingestion",
    "representation",
    "retrieval",
    "generation",
    "evaluation",
    "deployment",
    "agentic_orchestration",
}

STAGE_MODE_COUNTS = {
    "ingestion": 4,          # F1–F4
    "representation": 2,     # F5–F6
    "retrieval": 6,          # F7–F12
    "generation": 5,         # F13–F17
    "evaluation": 2,         # F18–F19
    "deployment": 6,         # F20–F25
    "agentic_orchestration": 8,  # F26–F33
}


class TestTaxonomyStructure:
    def test_exactly_33_modes(self) -> None:
        assert len(list_failure_modes()) == 33

    def test_ids_are_f1_through_f33(self) -> None:
        ids = {m.id for m in list_failure_modes()}
        expected = {f"F{i}" for i in range(1, 34)}
        assert ids == expected, f"Missing: {expected - ids}, extra: {ids - expected}"

    def test_each_id_appears_exactly_once(self) -> None:
        ids = [m.id for m in list_failure_modes()]
        duplicates = {mid for mid in ids if ids.count(mid) > 1}
        assert not duplicates, f"Duplicate IDs: {duplicates}"

    def test_modes_in_f1_to_f33_order(self) -> None:
        ids = [m.id for m in list_failure_modes()]
        expected = [f"F{i}" for i in range(1, 34)]
        assert ids == expected

    def test_exactly_7_stages(self) -> None:
        stages = {m.stage for m in list_failure_modes()}
        assert stages == EXPECTED_STAGES

    def test_stage_mode_counts(self) -> None:
        for stage, count in STAGE_MODE_COUNTS.items():
            actual = len(modes_by_stage(stage))
            assert actual == count, f"Stage '{stage}': expected {count}, got {actual}"


class TestTaxonomyContent:
    def test_doi_present_on_every_mode(self) -> None:
        for m in list_failure_modes():
            assert m.source.doi == EXPECTED_DOI, (
                f"{m.id}: doi '{m.source.doi}' != '{EXPECTED_DOI}'"
            )

    def test_evidence_levels_are_valid(self) -> None:
        for m in list_failure_modes():
            assert m.evidence_level in VALID_EVIDENCE_LEVELS, (
                f"{m.id}: invalid evidence_level '{m.evidence_level}'"
            )

    def test_detector_statuses_are_valid(self) -> None:
        for m in list_failure_modes():
            assert m.detector_status in VALID_DETECTOR_STATUSES, (
                f"{m.id}: invalid detector_status '{m.detector_status}'"
            )

    def test_evidence_distribution_matches_paper(self) -> None:
        modes = list_failure_modes()
        strong = sum(1 for m in modes if m.evidence_level == "Strong")
        moderate = sum(1 for m in modes if m.evidence_level == "Moderate")
        limited = sum(1 for m in modes if m.evidence_level == "Limited")
        assert strong == 9, f"Expected 9 Strong, got {strong}"
        assert moderate == 12, f"Expected 12 Moderate, got {moderate}"
        assert limited == 12, f"Expected 12 Limited, got {limited}"

    def test_agentic_modes_all_limited(self) -> None:
        for m in modes_by_stage("agentic_orchestration"):
            assert m.evidence_level == "Limited", (
                f"{m.id}: agentic mode must be Limited, got '{m.evidence_level}'"
            )

    def test_all_modes_have_nonempty_definition(self) -> None:
        for m in list_failure_modes():
            assert m.definition.strip(), f"{m.id}: definition is empty"

    def test_all_modes_have_nonempty_manifestation(self) -> None:
        for m in list_failure_modes():
            assert m.observable_manifestation.strip(), (
                f"{m.id}: observable_manifestation is empty"
            )


class TestTaxonomyAPI:
    def test_get_failure_mode_exact_id(self) -> None:
        m = get_failure_mode("F7")
        assert m.id == "F7"
        assert m.name == "Chunking Boundary Errors"

    def test_get_failure_mode_case_insensitive(self) -> None:
        m = get_failure_mode("f7")
        assert m.id == "F7"

    def test_get_failure_mode_invalid_raises_key_error(self) -> None:
        with pytest.raises(KeyError):
            get_failure_mode("F99")

    def test_modes_by_stage_retrieval(self) -> None:
        modes = modes_by_stage("retrieval")
        ids = [m.id for m in modes]
        assert ids == ["F7", "F8", "F9", "F10", "F11", "F12"]

    def test_modes_by_stage_invalid_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            modes_by_stage("nonexistent_stage")

    def test_modes_by_stage_case_normalised(self) -> None:
        assert modes_by_stage("Retrieval") == modes_by_stage("retrieval")


class TestCoverageSemantics:
    def test_total_is_33(self) -> None:
        cov = detector_coverage()
        assert cov.total == 33

    def test_all_modes_accounted_for(self) -> None:
        cov = detector_coverage()
        total = (
            len(cov.direct)
            + len(cov.proxy)
            + len(cov.risk_signal)
            + len(cov.runtime_required)
            + len(cov.unsupported)
        )
        assert total == 33, f"Coverage buckets sum to {total}, expected 33"

    def test_f3_and_f7_are_direct(self) -> None:
        cov = detector_coverage()
        direct_ids = {m.id for m in cov.direct}
        assert "F3" in direct_ids, "F3 (Layout Parsing Errors) must be direct"
        assert "F7" in direct_ids, "F7 (Chunking Boundary Errors) must be direct"

    def test_unsupported_and_runtime_modes_not_in_direct(self) -> None:
        cov = detector_coverage()
        direct_ids = {m.id for m in cov.direct}
        for m in cov.runtime_required:
            assert m.id not in direct_ids, f"{m.id} is runtime_required but also in direct"
        for m in cov.unsupported:
            assert m.id not in direct_ids, f"{m.id} is unsupported but also in direct"

    def test_f23_is_risk_signal_not_direct(self) -> None:
        m = get_failure_mode("F23")
        assert m.detector_status == "risk_signal", (
            "F23 (PII/Compliance Leaks) must be risk_signal, not direct — "
            "finding PII in source text does not establish a runtime leak"
        )

    def test_f1_is_proxy_not_direct(self) -> None:
        m = get_failure_mode("F1")
        assert m.detector_status == "proxy", (
            "F1 (Outdated/Stale Data) must be proxy — "
            "filesystem mtime does not prove content staleness"
        )
