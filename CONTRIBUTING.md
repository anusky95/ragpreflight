# Contributing to RAGCheck

Thank you for your interest in contributing! This guide will help you set up your development environment and understand our conventions.

## Dev Setup

```bash
# 1. Fork and clone
git clone https://github.com/your-fork/ragcheck.git
cd ragcheck

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install in editable mode with dev dependencies
pip install -e ".[dev]"

# 4. (Optional) Full extras for all features
pip install -e ".[dev,full,llm]"

# 5. Verify setup
ragcheck --version
pytest -q
```

## Running Tests

```bash
pytest                      # all tests
pytest tests/test_scanner.py  # one module
pytest -k "test_scan"       # by name pattern
pytest --cov=ragcheck        # with coverage
```

## Code Style

We use **ruff** for formatting and linting. Line length is 99 characters.

```bash
ruff check src/ tests/      # lint
ruff format src/ tests/     # auto-format
mypy src/ragcheck/           # type check
```

All of the above run automatically in CI. Your PR must pass lint + tests before it can be merged.

## Design Principles (Read Before Contributing)

1. **Zero API keys for core functionality.** LLM-powered features must be behind `--use-llm` flags.
2. **Lightweight base install.** Heavy deps go in `[full]` or `[llm]` extras — never in core.
3. **Never crash on bad input.** Wrap all file I/O in try/except. Return errors, not tracebacks.
4. **Typed dataclasses for all outputs.** Public functions return typed dataclasses, not dicts.
5. **Tests alongside code.** Every module has a corresponding test file.
6. **No `print()` in library code.** Use `logging` everywhere; `click.echo()` in CLI only.

## PR Guidelines

- Keep PRs focused: one feature or fix per PR
- Update tests alongside your changes
- Add your change to `CHANGELOG.md` under `[Unreleased]`
- Make sure `pytest` and `ruff check` pass locally before opening a PR

## Adding a New File Format

1. Add the extension to `SUPPORTED_EXTENSIONS` in `_constants.py`
2. Write a `_scan_<format>` function in `scanner.py` following the existing pattern
3. Register it in `_FORMAT_SCANNERS` in `scanner.py`
4. Add at least one test fixture in `tests/fixtures/`
5. Write tests in `tests/test_scanner.py`

## Reporting Bugs

Open an issue at [github.com/ragcheck/ragcheck/issues](https://github.com/ragcheck/ragcheck/issues) with:
- RAGCheck version (`ragcheck --version`)
- Python version
- OS
- Command or code that triggered the bug
- Full error output (with `--verbose` if applicable)
