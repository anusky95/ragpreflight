# Changelog

All notable changes to ragpreflight will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.2] — 2026-10-07

### Changed

- **Scoring (behavior change):** a PDF where half or more of the pages have no usable text layer is now reported as CRITICAL and its score is capped at 50 (previously WARNING, uncapped — a 50%-image PDF scored 74). Such documents now fail the `standard` profile (min 60), so CI gates may start failing on partially scanned PDFs.
- **PII detection threshold lowered:** emails, SSNs, phone numbers and credit card numbers are flagged from the first hit (previously 3+). IP addresses still need 2 hits, since dotted version strings resemble IPs.
- **README rewritten** with real CLI output; **CONTRIBUTING.md** updated (correct `--slow` flag, ragpreflight naming).

### Fixed

- **Short text-only PDFs misreported as scanned:** a page with fewer than 10 words was counted as having no text, so a 1-page, 2-word PDF got "0% extractable … likely a scanned PDF" alongside "Near-empty page (2 words)". Sparse pages are now treated as scanned only when images cover at least half the page, so scans carrying page-number or Bates stamps are still caught. Blank pages (no text, no images) are reported as INFO and excluded from the extractable ratio.
- **Garbled table previews:** previews with reversed glyph order (e.g. `ehT | waL | lliw`) are no longer shown, and table previews moved out of the Location column.
- **Truncated fix text:** the Fix column no longer cuts suggestions off with `[...]`.
- **Pre-commit hooks ran on every file:** `types_or: [pdf, file]` matched all files, including source code; hooks now only match supported document extensions.

## [0.2.1] — 2026-10-01

### Changed

- **HTML report redesigned**: SVG circular gauge, expandable issue cards with taxonomy links, dark mode support, IBM Plex typography, pip install suggestions per issue category.
- **README revised per reviewer feedback**: olmOCR-bench section rewritten with Spearman rank correlation (ρ = 0.21 all categories, ρ = 0.60 excluding table_tests) and honest caveats about statistical power; removed "near-exact" claim and Match column; "first open-source tool" softened to "to our knowledge"; sample report fixed (0% extractable with OCR findings → 34% extractable with explanation); added "How scoring works" section documenting scoring weights and floor/ceiling rules; F7 coverage clarified as direct via `chunks`, risk signal via `scan`; comparison table scoped to pre-ingestion features; relative links replaced with absolute GitHub URLs.
- **Defensive language removed**: dropped "honest strength, not a weakness" and "a tool claiming 33/33 is lying" from taxonomy section.

### Added

- **Trusted publishing workflow** (`.github/workflows/publish.yml`): GitHub Actions OIDC-based PyPI publishing — no long-lived API tokens.
- **Benchmark reproducibility** (`scripts/olmocr_bench/`): sampling script, audit script, and full list of 175 PDF filenames (seed=42) for independent reproduction of the olmOCR-bench validation.
- **Pip install suggestions**: each issue card in HTML reports suggests relevant tools (pytesseract for OCR, camelot-py for tables, ftfy for encoding, etc.).

### Fixed

- **OCR issue card title overflow**: multi-line OCR messages no longer dump full details into the card summary; `_summary_line()` extracts the first line.
- **Sample report internal inconsistency** (M4): 0% extractable text now correctly shows 34% with explanation of OCR text layer.

## [0.2.0] — 2026-10-01

### Fixed

- **OCR false-positive elimination**: `broken_ligatures` regex matched every `f` and `i` in English; `mid_word_spaces` matched every space between lowercase words; `rn_m` matched legitimate words like "learning" and "government". All three now use dictionary-aware detection via a bundled 234K-word English wordlist (nltk corpus, gzipped). Only flags OCR artifacts when the corrected form IS a real word and the original IS NOT.
- **Score inflation for unextractable documents**: A scanned PDF with 0% extractable text could score 70 due to metadata/structure scores. Added floor/ceiling logic: 0% extractability caps score at 20, OCR error rate >10% caps at 40, empty documents score 0.
- **mid_word_space non-overlapping regex**: `re.finditer` consumed words greedily, missing adjacent broken words. Switched to token-pair iteration.
- **rn_m double replacement**: Words like "cornrnittee" with two rn sequences now try both single and all-occurrence replacement.

### Changed

- **JSON output redesigned**: `DocumentReport.to_dict()` now returns flat, CI-friendly format with `verdict` ("ingest_ready"/"needs_review"/"reject"), `counts` summary, `failure_modes` as flat F-code list, `summary` instead of verbose prose. Filepath is basename-only.
- **Class renames**: `RAGCheckLoader` → `RAGPreflightLoader`, `RAGCheckNodePostprocessor` → `RAGPreflightNodePostprocessor`. All docstrings updated from "RAGCheck" to "ragpreflight".

### Added

- **Dictionary-aware OCR detection** (`_wordlist.py`): Bundled gzipped English wordlist (234K words from nltk corpus). Falls back to nltk runtime, then to hardcoded common-word set.
- **Header/footer noise detection**: Compares first/last lines across pages, flags when >=60% share the same text (F3 proxy).
- **Page number artifact detection**: Regex matches "Page N of M", "- N -", standalone page numbers matching page position.
- **Enhanced table detection**: Per-table page number, dimensions (rows x cols), content preview of first cells.
- **GitHub Actions workflow example** in README for CI/CD integration with SARIF upload.
- **olmOCR-bench external validation**: 175 PDFs across 7 difficulty categories confirm ragpreflight scores correlate with known OCR model difficulty. Added to README.

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

[Unreleased]: https://github.com/anusky95/ragpreflight/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/anusky95/ragpreflight/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/anusky95/ragpreflight/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/anusky95/ragpreflight/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/anusky95/ragpreflight/releases/tag/v0.1.0
