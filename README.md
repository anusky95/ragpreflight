# ragpreflight

[![PyPI version](https://badge.fury.io/py/ragpreflight.svg)](https://badge.fury.io/py/ragpreflight)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Downloads](https://img.shields.io/pypi/dm/ragpreflight.svg)](https://pypistats.org/packages/ragpreflight)
[![DOI](https://img.shields.io/badge/paper-doi%3A10.18653%2Fv1%2F2026.trustnlp--main.27-blue)](https://doi.org/10.18653/v1/2026.trustnlp-main.27)

**The pre-ingestion RAG audit tool. Catch document failures before you embed them — not after your chatbot starts hallucinating.**

```bash
pip install ragpreflight
ragpreflight scan my_document.pdf
```

```
╭──────────────────────────────────────────────────────────────────────╮
│ ragpreflight — Document Readiness Report                             │
│ File: quarterly_report.pdf                                           │
│ Score: 44/100  █████████░░░░░░░░░░░                                  │
│ Format: PDF  ·  Size: 2.1 MB  ·  Pages: 34  ·  Extractable: 0%      │
╰──────────────────────────────────────────────────────────────────────╯

  CRITICAL  ocr      [pages 3, 7, 11] l / I / 1 confusion — 23 matches
                     Scanner misread lowercase l, capital I, and digit 1 as each other.
                     Examples: "cIinical" → "clinical", "1evel" → "level"
                     Keyword search and semantic retrieval both fail on corrupted tokens.
                     → Re-run OCR at higher DPI, or apply post-correction (pyspellchecker).

  CRITICAL  ocr      [pages 5, 9] mid-word spaces — 14 matches
                     Spaces inserted inside words during scan: "pati ent" → "patient"
                     Each broken word becomes two meaningless tokens in the index.
                     → Apply OCR post-correction before ingestion.

  CRITICAL  ocr      [pages 2, 14–17] rn / m split — 8 matches
                     The letter m was split into rn: "inforrnation" → "information"
                     Common in low-DPI scans of serif fonts.
                     → Re-scan at 300 DPI minimum or run post-OCR cleanup.

  CRITICAL  content  0% of pages have extractable text. This is a scanned PDF.
                     → Apply OCR (Tesseract, AWS Textract, Google Document AI) before ingestion.

  WARNING   structure 8 table(s) detected — will chunk as garbled text without special handling.
                     → Use a table-aware extractor (pdfplumber, Camelot, LlamaParse).

  WARNING   content  Possible PII: 12 email addresses found on pages 4, 18, 22.
                     → Review and redact before ingestion into a shared RAG corpus.

  INFO      metadata No title, author, or creation date in document metadata.
                     → Add metadata to improve retrieval ranking and attribution.

7 issue(s) found  ·  Score: 44  ·  4 critical

RAG Failure Taxonomy  (doi:10.18653/v1/2026.trustnlp-main.27)
  OCR artifacts  → F3  Document Quality Failure       [direct]
  Scanned PDF    → F3  Document Quality Failure       [direct]
  Tables         → F7  Structure-Unaware Chunking     [risk signal]
  PII            → F23 PII / Compliance Leak          [direct]
  No metadata    → F11 Low Recall / Ranking Failure   [risk signal]
```

---

## The gap nobody talks about

Every RAG evaluation tool — RAGAS, DeepEval, TruLens, RAGChecker — runs **after** you build your system. They need a live retriever, real queries, LLM-generated outputs, and an LLM judge to score them. By that point, bad documents are already embedded. You're debugging a production system, not preventing the problem.

ragpreflight runs **before** you embed anything. Give it a folder of files. It tells you which ones will cause failures and why — in seconds, with no API keys, no internet connection, no running model.

```
Your documents  →  [ragpreflight]  →  fix issues  →  embed  →  RAG system
                         ↑
                  This is the gap.
            Nothing else runs here.
```

It is grounded in peer-reviewed research: 33 failure modes across 7 pipeline stages from [Garani 2026](https://doi.org/10.18653/v1/2026.trustnlp-main.27) — the first systematic taxonomy of RAG failure modes published at TrustNLP 2026 (ACL). ragpreflight is the first open-source tool that links every detected issue to a named failure mode from that published taxonomy.

---

## How it compares

| | **ragpreflight** | RAGAS | DeepEval | TruLens | RAGChecker | Unstructured |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **When it runs** | **pre-ingestion** | post-gen | post-gen | post-gen | post-gen | ingestion |
| Needs a live RAG system | ❌ | ✅ | ✅ | ✅ | ✅ | ❌ |
| Needs an LLM to run | ❌ | ✅ | ✅ | ✅ | ✅ | ⚠️ optional |
| Needs labeled queries / golden sets | ❌ | ⚠️ some metrics | ⚠️ some metrics | ⚠️ some metrics | ✅ | ❌ |
| Fully offline | ✅ | ❌ | ❌ | ❌ | ❌ | ⚠️ OSS only |
| OCR artifact detection | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Chunking boundary quality | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Near-duplicate detection | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| PII in source documents | ✅ | ❌ | ⚠️ | ❌ | ❌ | ❌ |
| Staleness detection | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Taxonomy-grounded (peer-reviewed) | ✅ 33 modes | ❌ | ❌ | ❌ | ❌ | ❌ |
| Multi-format (PDF/DOCX/CSV/HTML/MD…) | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ |
| Readiness score 0–100 | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| HTML audit report | ✅ | ❌ | ⚠️ cloud dashboard | ✅ | ❌ | ❌ |
| CI/CD exit code gating | ✅ | ⚠️ | ✅ | ❌ | ❌ | ❌ |
| SARIF output (GitHub Code Scanning) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |

> **These tools are complementary, not competing.** ragpreflight cleans and validates your corpus before ingestion. RAGAS/DeepEval/TruLens evaluate your live RAG system after deployment. Use both.

---

## Quickstart

### Scan a single document

```bash
ragpreflight scan quarterly_report.pdf
ragpreflight scan contract.pdf --profile strict   # higher thresholds
ragpreflight scan report.pdf --json               # machine-readable
ragpreflight scan report.pdf --format html --output report.html
```

### Audit your entire knowledge base

```bash
ragpreflight audit ./knowledge_base/
ragpreflight audit ./knowledge_base/ --format html --output audit.html
```

```
╭──────────────────────────────────────────────────────────────╮
│ ragpreflight — Corpus Readiness Report                       │
│ Directory: ./knowledge_base/                                 │
│ Documents: 17  ·  Avg Score: 77.9  ███████████████░░░░░      │
│ Duplicate Groups: 0                                          │
╰──────────────────────────────────────────────────────────────╯

 Score  File                   Format    Issues  Critical
──────────────────────────────────────────────────────────
     0  empty_file.txt         TXT            1         1   ← fix first
    44  scanned_old.pdf        PDF            5         1   ← needs OCR
    85  contract_draft.pdf     PDF            2         0
    90  data_export.csv        CSV            0         0
    92  technical_spec.pdf     PDF            2         0
    94  meeting_notes.docx     DOCX           2         0
```

### Gate your ingestion pipeline (CI/CD)

```bash
# Fail the pipeline if any document scores below 60
ragpreflight score my_doc.pdf          # prints: 92

score=$(ragpreflight score my_doc.pdf)
[ "$score" -ge 60 ] || exit 1
```

### GitHub Actions integration

```yaml
# .github/workflows/rag-quality.yml
name: RAG Document Quality Gate

on:
  pull_request:
    paths: ['knowledge_base/**', 'docs/**']

jobs:
  ragpreflight:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - run: pip install ragpreflight

      - name: Audit knowledge base
        run: |
          ragpreflight audit ./knowledge_base/ --json > audit.json
          ragpreflight audit ./knowledge_base/ --format sarif --output results.sarif

      - name: Quality gate — fail on critical issues
        run: |
          score=$(ragpreflight score ./knowledge_base/ 2>/dev/null)
          echo "Corpus average score: $score"
          if [ "$score" -lt 60 ]; then
            echo "::error::RAG corpus score $score is below threshold (60)"
            exit 1
          fi

      - name: Upload SARIF to GitHub Code Scanning
        if: always()
        uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: results.sarif
```

### SARIF output for GitHub Code Scanning

```bash
ragpreflight scan my_doc.pdf --format sarif --output results.sarif
```

Upload `results.sarif` to GitHub Code Scanning and document issues appear as pull request annotations.

### Explore the taxonomy

```bash
ragpreflight taxonomy list                    # all 33 failure modes
ragpreflight taxonomy list --stage retrieval  # filter by pipeline stage
ragpreflight taxonomy show F7                 # full detail for one mode
ragpreflight coverage                         # what this tool can/cannot detect
```

---

## Python API

```python
from ragpreflight import scan_document, audit_corpus

# Single document
report = scan_document("my_doc.pdf")
print(report.score)         # 83
print(report.issues)        # list[Issue] — each with category, severity, suggestion

for issue in report.issues:
    print(issue.severity.value, issue.category.value, issue.message)
    if issue.taxonomy_refs:
        # Each issue linked to Garani 2026 failure modes
        for ref in issue.taxonomy_refs:
            print(f"  → {ref.mode_id} ({ref.relationship})")

# Corpus
corpus = audit_corpus("./knowledge_base/")
print(corpus.average_score)        # 77.9
print(corpus.duplicate_groups)     # [[file1, file2], ...]

# Programmatic fail gate
critical = [i for d in corpus.documents for i in d.issues if i.severity.value == "critical"]
if critical:
    raise ValueError(f"{len(critical)} critical issues — fix before ingesting")
```

All outputs are typed dataclasses, not dicts. Full type hints. Zero global state.

---

## Supported file formats

| Format | What's checked |
|--------|----------------|
| PDF (text-based) | OCR artifacts, structure, tables, metadata, PII, content density |
| PDF (scanned) | Flags 0% extractability, recommends OCR — score penalised |
| DOCX | Structure, encoding, tables, metadata |
| TXT / Markdown | Encoding, OCR patterns, header hierarchy |
| CSV / TSV | Column consistency, encoding, structural integrity |
| HTML | Boilerplate ratio, structure |
| XLSX | Sheet structure, encoding |
| PPTX | Slide content, structure |
| IPYNB | Cell content, code/text ratio |
| SRT | Transcript length, artifact detection |

---

## Quality profiles

```bash
ragpreflight scan doc.pdf --profile permissive  # chatbots, internal tools
ragpreflight scan doc.pdf --profile standard    # enterprise search (default)
ragpreflight scan doc.pdf --profile strict      # medical, legal, financial
```

| Profile | Min score | OCR tolerance | Similarity threshold |
|---------|-----------|---------------|---------------------|
| `permissive` | 40 | < 10% | 0.95 |
| `standard` | 60 | < 5% | 0.90 |
| `strict` | 80 | < 2% | 0.85 |

---

## Optional extras

```bash
pip install ragpreflight           # core — fully offline, no API keys
pip install ragpreflight[full]     # adds: semantic chunk coherence (sentence-transformers)
                                   #       near-duplicate detection (datasketch MinHash)
pip install ragpreflight[llm]      # adds: LLM-powered query generation for retrieval sim
```

---

## The taxonomy: 33 failure modes, 7 pipeline stages

ragpreflight is the first open-source tool grounded in a peer-reviewed RAG failure taxonomy. Each issue it raises is linked to one of 33 named failure modes across 7 pipeline stages:

| Stage | Modes | ragpreflight coverage |
|-------|-------|----------------------|
| Ingestion | F1–F4 | F1 proxy · F3 direct · F4 risk signal |
| Representation | F5–F6 | — (runtime required) |
| Retrieval | F7–F12 | F7 direct · F11 proxy |
| Generation | F13–F17 | — (requires LLM outputs) |
| Evaluation | F18–F19 | — |
| Deployment | F20–F25 | F23 risk signal |
| Agentic Orchestration | F26–F33 | — (requires agent traces) |

**"2 direct + 4 proxy/risk + 27 runtime/unsupported" is honest strength, not a weakness. A tool claiming 33/33 detection is lying.**

```bash
ragpreflight coverage   # see exactly what is and isn't detected
```

For runtime coverage (hallucination, faithfulness, latency): [DeepEval](https://deepeval.com) · [Ragas](https://docs.ragas.io) · [RAGChecker](https://pypi.org/project/ragchecker/) · [Phoenix](https://github.com/Arize-ai/openinference)

---

## External validation — olmOCR-bench

ragpreflight scores were validated against [olmOCR-bench](https://huggingface.co/datasets/allenai/olmOCR-bench) (Poznanski et al., 2025), a public benchmark of 1,403 PDFs across 7 difficulty categories with known OCR accuracy from state-of-the-art models.

**175 PDFs (25 per category, stratified random seed=42) were audited with no ground truth labels, no OCR outputs, and no LLM calls — purely static analysis.**

| Category | ragpreflight mean score | olmOCR best accuracy | Match |
|---|:---:|:---:|:---|
| `old_scans` (historical LoC scans) | **45** | 44.5% | ✅ Near-exact |
| `old_scans_math` | 59.7 | 75.1% | ✅ Hard, scored hard |
| `long_tiny_text` | 73.0 | 81.7% | ✅ Medium difficulty |
| `multi_column` | 82.4 | 79.4% | ✅ Layout complexity |
| `headers_footers` | 84.7 | 93.4% | ✅ Easy, scored easy |
| `arxiv_math` | 85.2 | 75.6% | ✅ Typeset, extractable |
| `table_tests` | 86.4 | 70.2% | ⚠ ragpreflight detects text quality; table *structure reconstruction* requires runtime |

**Key finding:** The hardest category for OCR models (`old_scans`, 44.5% accuracy) is also the lowest scored by ragpreflight (mean 45). The ordering is consistent across all categories. Static pre-ingestion analysis predicts OCR and RAG difficulty without running a model.

→ [View the full HTML audit report](docs/olmocr-bench-report.html) — 175 PDFs with per-document scores, issue breakdowns, and Garani 2026 taxonomy links.

---

## Config file

Create `.ragpreflight.toml` in your project root or `~`:

```toml
[ragpreflight]
profile = "standard"
max_file_size_mb = 100
output_format = "terminal"
chunk_size = 512
chunk_overlap = 50
```

---

## Research

This tool implements the taxonomy from:

```bibtex
@inproceedings{garani-2026-systematic,
  title     = {A Systematic Taxonomy of Failure Modes in Retrieval-Augmented Generation Systems},
  author    = {Garani, Anupama},
  booktitle = {Proceedings of the 6th Workshop on Trustworthy Natural Language Processing (TrustNLP 2026)},
  year      = {2026},
  publisher = {Association for Computational Linguistics},
  doi       = {10.18653/v1/2026.trustnlp-main.27},
  url       = {https://aclanthology.org/2026.trustnlp-main.27/}
}
```

If you use ragpreflight in research, please cite the paper above.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Issues and PRs welcome.

---

## License

MIT — see [LICENSE](LICENSE).

---

*Built on the Garani 2026 taxonomy · [ACL Anthology](https://aclanthology.org/2026.trustnlp-main.27/) · [doi:10.18653/v1/2026.trustnlp-main.27](https://doi.org/10.18653/v1/2026.trustnlp-main.27)*
