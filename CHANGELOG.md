# Changelog

All notable changes to ragpreflight will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2026-09-29

First public release. Grounded in Garani 2026 (doi:10.18653/v1/2026.trustnlp-main.27).

### Added

- **Document Scanner** (`ragpreflight scan`) — score individual documents 0–100 across 5 quality dimensions:
  - Text extractability (30%)
  - OCR cleanliness (25%)
  - Structural integrity (20%)
  - Metadata completeness (10%)
  - Content density (15%)

- **Supported formats**: PDF (text + scanned), DOCX, TXT, CSV/TSV, HTML, Markdown, XLSX, PPTX, IPYNB, SRT

- **Corpus Auditor** (`ragpreflight audit`) — scan entire directories with:
  - Near-duplicate detection via MinHash LSH (datasketch) or shingle Jaccard fallback
  - File-age freshness proxy (not a content-staleness guarantee — see F1)
  - Conflict candidate detection for human review (high embedding similarity pairs)
  - Format distribution statistics and worst-offender list

- **Chunk Diagnostics** (`ragpreflight chunks`) — analyse chunked text for:
  - Semantic coherence via sentence-transformers (optional; returns `None` not fake `1.0` when absent)
  - Structural break detection (tables, lists, code blocks)
  - Boundary quality (mid-sentence cuts)
  - Information density

- **Retrieval Simulation** (`ragpreflight simulate`) — synthetic query generation + similarity hit rate:
  - Named `synthetic_retrieval_hit_rate` (not `precision@k` — no labeled relevance)
  - Dead chunk rate, query failure rate
  - Optional LLM-powered query generation (`--use-llm`)

- **Garani 2026 Taxonomy** (`ragpreflight taxonomy`, `ragpreflight coverage`):
  - All 33 failure modes across 7 pipeline stages, loaded from YAML
  - Evidence levels: 9 Strong, 12 Moderate, 12 Limited
  - `detector_status` per mode: direct / proxy / risk_signal / runtime_required / unsupported
  - `TaxonomyReference` on every `Issue` linking findings to F1–F33

- **Quality Profiles**: `permissive`, `standard`, `strict`

- **Output Formats**: rich terminal tables, JSON (`--json`), self-contained HTML (`--format html`), SARIF (`--format sarif`)

- **CI/CD friendly**: `ragpreflight score <file>` prints only the score; exit code 1 on critical issues

- **Config file**: `.ragpreflight.toml` in project root or home directory

- **Zero telemetry**: no tracking, no API keys for core functionality, fully offline-capable

### Fixed (vs original ragcheck codebase)

- `coherence_score` fallback was returning fake `1.0` when sentence-transformers absent — now returns `None`
- `precision_at_k` renamed to `synthetic_retrieval_hit_rate` (no labeled relevance existed)
- Staleness message now says "file-age proxy" — filesystem mtime does not prove content outdated
- `_find_contradiction_candidates` renamed to `_find_conflict_candidates` — these are not confirmed contradictions
- `_scan_xlsx` docstring falsely claimed `.xls` support — openpyxl only handles `.xlsx`
- `[cloud]` extra removed — no connector module existed
- `scikit-learn` removed from `[full]` — was unused
- Python minimum bumped to `>=3.10` (3.9 EOL)

[Unreleased]: https://github.com/anusky95/ragpreflight/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/anusky95/ragpreflight/releases/tag/v0.1.0
