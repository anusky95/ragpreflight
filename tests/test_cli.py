"""Tests for ragcheck CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from ragpreflight.cli import main


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


class TestVersion:
    def test_version_flag(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "RAGCheck" in result.output or "0.1.0" in result.output

    def test_short_version_flag(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["-V"])
        assert result.exit_code == 0


class TestScanCommand:
    def test_scan_txt_succeeds(self, runner: CliRunner, sample_txt: Path) -> None:
        result = runner.invoke(main, ["scan", str(sample_txt)])
        assert result.exit_code in (0, 1)  # 1 is OK if there are critical issues
        assert "RAGCheck" in result.output or "Score" in result.output

    def test_scan_json_output(self, runner: CliRunner, sample_txt: Path) -> None:
        result = runner.invoke(main, ["scan", str(sample_txt), "--json"])
        assert result.exit_code in (0, 1)
        data = json.loads(result.output)
        assert "score" in data
        assert "issues" in data

    def test_scan_quiet_prints_number(self, runner: CliRunner, sample_txt: Path) -> None:
        result = runner.invoke(main, ["scan", str(sample_txt), "--quiet"])
        assert result.exit_code in (0, 1)
        score = result.output.strip()
        assert score.isdigit()
        assert 0 <= int(score) <= 100

    def test_scan_missing_file_gives_error(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(main, ["scan", str(tmp_path / "nonexistent.txt")])
        assert result.exit_code != 0

    def test_scan_invalid_profile_gives_error(self, runner: CliRunner, sample_txt: Path) -> None:
        result = runner.invoke(main, ["scan", str(sample_txt), "--profile", "bogus"])
        assert result.exit_code != 0

    def test_scan_strict_profile(self, runner: CliRunner, sample_txt: Path) -> None:
        result = runner.invoke(main, ["scan", str(sample_txt), "--profile", "strict"])
        assert result.exit_code in (0, 1)

    def test_scan_html_format(self, runner: CliRunner, sample_txt: Path, tmp_path: Path) -> None:
        output_file = tmp_path / "report.html"
        result = runner.invoke(
            main,
            ["scan", str(sample_txt), "--format", "html", "--output", str(output_file)],
        )
        assert result.exit_code in (0, 1)
        assert output_file.exists()
        content = output_file.read_text()
        assert "RAGCheck" in content
        assert "<html" in content


class TestAuditCommand:
    def test_audit_directory_succeeds(self, runner: CliRunner, fixtures_dir: Path) -> None:
        result = runner.invoke(main, ["audit", str(fixtures_dir), "--no-progress"])
        assert result.exit_code in (0, 1)

    def test_audit_json_output(self, runner: CliRunner, fixtures_dir: Path) -> None:
        result = runner.invoke(main, ["audit", str(fixtures_dir), "--json", "--no-progress"])
        assert result.exit_code in (0, 1)
        data = json.loads(result.output)
        assert "total_documents" in data
        assert "average_score" in data

    def test_audit_empty_directory(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(main, ["audit", str(tmp_path), "--no-progress"])
        assert result.exit_code in (0, 1)

    def test_audit_quiet(self, runner: CliRunner, fixtures_dir: Path) -> None:
        result = runner.invoke(main, ["audit", str(fixtures_dir), "--quiet", "--no-progress"])
        assert result.exit_code in (0, 1)
        score = result.output.strip()
        assert score.isdigit()


class TestScoreCommand:
    def test_score_prints_integer(self, runner: CliRunner, sample_txt: Path) -> None:
        result = runner.invoke(main, ["score", str(sample_txt)])
        assert result.exit_code in (0, 1)
        score = result.output.strip()
        assert score.isdigit()
        assert 0 <= int(score) <= 100

    def test_score_empty_file_prints_zero(self, runner: CliRunner, empty_txt: Path) -> None:
        result = runner.invoke(main, ["score", str(empty_txt)])
        assert result.output.strip() == "0"

    def test_score_missing_file_gives_error(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(main, ["score", str(tmp_path / "missing.txt")])
        assert result.exit_code != 0


class TestChunksCommand:
    def test_chunks_produces_output(self, runner: CliRunner, sample_txt: Path) -> None:
        result = runner.invoke(main, ["chunks", str(sample_txt)])
        assert result.exit_code in (0, 1)

    def test_chunks_json_output(self, runner: CliRunner, sample_txt: Path) -> None:
        result = runner.invoke(main, ["chunks", str(sample_txt), "--json"])
        assert result.exit_code in (0, 1)
        data = json.loads(result.output)
        assert isinstance(data, list)
        if data:
            assert "chunk_index" in data[0]
            assert "coherence_score" in data[0]

    def test_chunks_sentence_strategy(self, runner: CliRunner, sample_txt: Path) -> None:
        result = runner.invoke(main, ["chunks", str(sample_txt), "--strategy", "sentence"])
        assert result.exit_code in (0, 1)


class TestHelpOutput:
    def test_main_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "scan" in result.output
        assert "audit" in result.output

    def test_scan_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["scan", "--help"])
        assert result.exit_code == 0
        assert "--profile" in result.output

    def test_audit_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["audit", "--help"])
        assert result.exit_code == 0
