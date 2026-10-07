# Contributing to ragpreflight

Thanks for wanting to help — ragpreflight's goal is to become the pre-flight check every RAG pipeline runs, and that only happens with contributors. This guide gets you from zero to a merged PR.

## Dev setup

```bash
# 1. Fork and clone
git clone https://github.com/<your-username>/ragpreflight.git
cd ragpreflight

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install in editable mode with dev dependencies
pip install -e ".[dev]"

# 4. (Optional) Full extras — chunk analysis, retrieval simulation, contradiction detection
pip install -e ".[dev,full]"

# 5. Verify setup
ragpreflight --version
pytest -q -m "not slow"
```

## Running tests

```bash
pytest -q -m "not slow"        # fast suite (default for local dev)
pytest -q --slow               # everything, incl. slow model-loading tests
pytest tests/test_scanner.py   # one module
pytest -k "test_scan"          # by name pattern
pytest --cov=ragpreflight      # with coverage
```

Markers: `slow` = tests that load sentence-transformers models. They are skipped unless you pass `--slow`.

## Code style

We use **ruff** (line length 99) and **mypy** (strict). All run in CI — your PR must pass before merge.

```bash
ruff check src/ tests/     # lint
ruff format src/ tests/    # auto-format
mypy src/ragpreflight/     # type check
```

## Design principles (read before contributing)

1. **Zero API keys for core.** Everything in the base install runs offline. LLM-assisted features stay behind `--use-llm` flags in the `[llm]` extra.
2. **Lightweight base install.** Heavy deps go in `[full]` or `[llm]` — never in core.
3. **Never crash on bad input.** Wrap file I/O in try/except. Return errors, not tracebacks.
4. **Typed dataclasses for all outputs.** Public functions return typed dataclasses (`Issue`, `DocumentReport`, …), not dicts.
5. **Tests alongside code.** Every module has a corresponding test file in `tests/`.
6. **No `print()` in library code.** Use `logging` everywhere; `click.echo()` in the CLI only.
7. **Honest coverage claims.** Every issue links to a Garani 2026 failure mode with a declared relationship (`direct`, `proxy`, `risk_signal`). Don't claim detection you don't have — run `ragpreflight coverage` and update it.

## Adding a new check

Checks live in `src/ragpreflight/scanner.py`. There are two kinds:

**A. Cross-format check** (e.g. a new PII pattern, a new OCR artifact):
1. Find the shared check it belongs with — `_detect_pii_issues()`, the OCR detectors, `_build_language_issue()`, etc.
2. Emit an `Issue` with `category` (one of `IssueCategory`: `ocr`, `encoding`, `structure`, `content`, `metadata`, `chunking`, `duplication`, `staleness`), `severity` (`critical` / `warning` / `info`), a concrete `message`, an actionable `suggestion`, and `taxonomy_refs` linking the Garani 2026 mode(s) it maps to.
3. Add a test in `tests/test_scanner.py` with a fixture under `tests/fixtures/` if needed.

**B. Format-specific logic** (inside `_scan_pdf`, `_scan_docx`, `_scan_html`, …): follow the existing pattern in that function and add fixtures + tests the same way.

If your check maps to a SARIF-reportable category, it's picked up automatically — the SARIF rules (`OCR001`, `ENC001`, `STR001`, `CNT001`, `META001`, `CHK001`, `DUP001`, `STA001` in `report.py`) are keyed by category.

## Adding a new file format

1. Add the extension to `SUPPORTED_EXTENSIONS` in `src/ragpreflight/_constants.py`.
2. Write a `_scan_<format>()` function in `src/ragpreflight/scanner.py` following the existing pattern (return `_ScanResult`; never raise on bad input).
3. Register it in the format-dispatch map in `scanner.py`.
4. Add at least one fixture in `tests/fixtures/`.
5. Write tests in `tests/test_scanner.py`.
6. Add a row to the "Supported file formats" table in `README.md`.

## PR process

- One feature or fix per PR. Keep it focused.
- Update tests alongside your changes.
- Add a line to `CHANGELOG.md` under `[Unreleased]`.
- Run `pytest -q -m "not slow"`, `ruff check src/ tests/`, and `ruff format --check src/ tests/` locally first.

## Reporting bugs

Open an issue at [github.com/anusky95/ragpreflight/issues](https://github.com/anusky95/ragpreflight/issues) with:

- ragpreflight version (`ragpreflight --version`)
- Python version and OS
- The exact command (or minimal Python snippet) that triggered it
- Full error output (re-run with `--verbose` / `-v` if it's a scan failure)
- The problem file if you can share it (or a minimal reproducer — see `tests/fixtures/` for the pattern)

## Good first issues

New here? Start with an issue labelled [`good first issue`](https://github.com/anusky95/ragpreflight/labels/good%20first%20issue) — each one names the exact files to touch. If you get stuck, ask in the issue; maintainers answer.
