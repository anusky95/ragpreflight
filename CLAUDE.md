# CLAUDE.md — ragpreflight

Offline lint for documents before RAG, LLM wikis or agents read them. v0.3 is a ground-up rewrite (the v0.2 brief that used to live here described the old architecture).

## Principles
1. Zero config, zero API keys, deterministic. `uvx ragpreflight ./docs` must just work.
2. Precision over recall. Every new check needs a negative test (clean prose, math, code) that must stay silent.
3. Never crash on bad input; a broken file becomes an `unreadable` finding.
4. Architecture-neutral: never pitch it as RAG-only.
5. Findings lead; the score is secondary. Aggregate per document (one finding with a location list, not one per page).

## Layout
- `src/ragpreflight/readers.py` — files → `Document` (pdf, docx, md + frontmatter, txt, html)
- `src/ragpreflight/checks/` — one module per check family; each exposes `NAME` and `run(docs, root) -> list[Finding]`
- `src/ragpreflight/scan.py` — orchestration, scoring, verdict
- `src/ragpreflight/report.py` — terminal / JSON / Markdown / HTML
- `src/ragpreflight/cli.py` — `ragpreflight PATH | demo | explain | coverage`
- `src/ragpreflight/demo/` — fictional SkyFare Air corpus (regenerate with `scripts/make_demo.py`)
- `action.yml`, `.pre-commit-hooks.yaml`, `skills/ragpreflight/SKILL.md` — distribution surfaces

## Dev
`pip install -e ".[dev]" && pytest && ruff check src tests`
