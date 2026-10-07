# ragpreflight

[![PyPI version](https://badge.fury.io/py/ragpreflight.svg)](https://badge.fury.io/py/ragpreflight)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Downloads](https://img.shields.io/pypi/dm/ragpreflight.svg)](https://pypistats.org/packages/ragpreflight)
[![DOI](https://img.shields.io/badge/paper-doi%3A10.18653%2Fv1%2F2026.trustnlp--main.27-blue)](https://doi.org/10.18653/v1/2026.trustnlp-main.27)

**The pre-flight check every RAG pipeline runs.** Audit documents *before* you embed them — OCR artifacts, extractability, tables, PII, metadata, chunking — with a 0–100 readiness score and fix suggestions. Fully offline. No API keys. No model.

<!-- ![demo](demo.gif) -->


```bash
pip install ragpreflight
ragpreflight scan my_document.pdf
```

## Why

Every RAG eval tool — Ragas, DeepEval, TruLens — runs **after** you build your system. They need a live retriever, real queries, and an LLM judge. By then, bad documents are already embedded. You're debugging production, not preventing the problem.

ragpreflight runs **before** ingestion. It's the eslint/pytest of RAG data: point it at a file or a folder, get a readiness score and a list of exactly what's wrong and how to fix it.

```
Your documents  →  [ragpreflight]  →  fix issues  →  embed  →  RAG system
                         ↑
                  This is the gap.
            Nothing else runs here.
```

Grounded in peer-reviewed research: 33 failure modes across 7 pipeline stages from [Garani 2026](https://doi.org/10.18653/v1/2026.trustnlp-main.27) (TrustNLP 2026, ACL). Every detected issue links to a named failure mode in the taxonomy.

## What it checks

| Category | Examples |
|---|---|
| OCR artifacts | l/I/1 confusion, mid-word spaces, rn/m splits |
| Extractability | Scanned pages with no text layer, extraction coverage |
| Tables | Table detection — flags content that will chunk as garbled text |
| PII | Emails, SSNs, credit cards, phone numbers, IPs (masked in reports) |
| Metadata | Missing title, author, creation date |
| Chunking | Boundary errors, coherence (`chunks` command, requires `ragpreflight[full]`) |
| Duplication & staleness | Near-duplicate documents, outdated files |

10 formats: PDF, DOCX, TXT, Markdown, CSV/TSV, HTML, XLSX, PPTX, IPYNB, SRT.

## Sample output

Real output (excerpt) from a 4-page report with two image-only pages, OCR noise, and contact details:

```
$ ragpreflight scan quarterly_report.pdf
╭────────────────────────────────────────────────────────────────╮
│ ragpreflight — Document Readiness Report                       │
│ File: quarterly_report.pdf                                     │
│ Score: 50/100  ██████████░░░░░░░░░░                            │
│ Format: PDF  ·  Size: 1.4 MB  ·  Pages: 4  ·  Extractable: 50% │
╰────────────────────────────────────────────────────────────────╯
╭────────────┬──────────────┬──────────────────────────┬────────────────┬──────────────────────────╮
│ Severity   │ Category     │ Issue                    │ Location       │ Fix                      │
├────────────┼──────────────┼──────────────────────────┼────────────────┼──────────────────────────┤
│ CRITICAL   │ content      │ Only 50% of pages have   │ —              │ Apply OCR (Tesseract,    │
│            │              │ extractable text. This   │                │ AWS Textract, Google     │
│            │              │ document is likely a     │                │ Document AI) to the full │
│            │              │ scanned PDF.             │                │ document before          │
│            │              │                          │                │ ingestion.               │
│ WARNING    │ content      │ Page yields no           │ page 1         │ Run OCR (e.g. Tesseract) │
│            │              │ extractable text — may   │                │ on image-only pages      │
│            │              │ be a scanned image.      │                │ before ingestion.        │
│ WARNING    │ content      │ Page yields no           │ page 2         │ Run OCR (e.g. Tesseract) │
│            │              │ extractable text — may   │                │ on image-only pages      │
│            │              │ be a scanned image.      │                │ before ingestion.        │
│ WARNING    │ content      │ Possible PII: 2 email    │ pages 4        │ Review whether email     │
│            │              │ value(s) across 1        │                │ values should be         │
│            │              │ page(s).                 │                │ redacted before          │
│            │              │ ↳ e.g. j.s***@corp.com · │                │ ingestion into your RAG  │
│            │              │ a.l***@corp.com          │                │ corpus.                  │
│ ⋮  4 more rows: phone-number PII, OCR artifacts on pages 3–4, missing metadata                   │
╰────────────┴──────────────┴──────────────────────────┴────────────────┴──────────────────────────╯

8 issue(s) found  ·  Score: 50  ·  1 critical
```

Every issue also maps to a failure mode in the taxonomy (`--format json` includes `taxonomy_refs`).

## Quickstart

```bash
# Single document
ragpreflight scan quarterly_report.pdf
ragpreflight scan contract.pdf --profile strict   # higher thresholds
ragpreflight scan report.pdf --format html --output report.html

# Whole knowledge base
ragpreflight audit ./knowledge_base/
ragpreflight audit ./knowledge_base/ --format html --output audit.html

# Chunk quality (needs the [full] extra)
ragpreflight chunks document.pdf --strategy sentence --size 512

# Retrieval simulation: dead chunks, query failures
ragpreflight simulate ./knowledge_base/ --queries 50

# Browse the research behind it
ragpreflight taxonomy list          # all 33 failure modes
ragpreflight taxonomy show F7       # full detail for one mode
ragpreflight coverage               # what ragpreflight can and cannot detect
```

## Gate your pipeline (CI/CD)

```bash
ragpreflight score my_doc.pdf                  # prints: 92
ragpreflight score my_doc.pdf --min-score 60   # exit 1 if below 60
ragpreflight score ./knowledge_base/           # corpus average score
```

`scan` and `audit` also exit 1 when a document scores below its profile threshold or has critical issues. SARIF output plugs straight into GitHub code scanning:

```bash
ragpreflight scan my_doc.pdf --format sarif --output results.sarif
```

```yaml
# .github/workflows/rag-quality.yml
name: RAG Document Quality Gate
on:
  pull_request:
    paths: ['knowledge_base/**', 'docs/**']
permissions:
  contents: read
  security-events: write   # required for SARIF upload
jobs:
  ragpreflight:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install ragpreflight
      - run: ragpreflight audit ./knowledge_base/ --format sarif --output results.sarif --no-progress
      - run: ragpreflight score ./knowledge_base/ --min-score 60
      - uses: github/codeql-action/upload-sarif@v3
        if: always()
        with: { sarif_file: results.sarif }
```

For pre-commit, see [`.pre-commit-hooks.yaml`](.pre-commit-hooks.yaml):

```yaml
- repo: https://github.com/anusky95/ragpreflight
  rev: v0.2.2
  hooks:
    - id: ragpreflight-scan   # fails the commit if a staged doc scores < 60
```

## How it compares

ragpreflight **audits** documents; the tools below **process** or **evaluate** them. Different jobs — use both.

| | **ragpreflight** | Ragas / DeepEval / TruLens | Unstructured / Docling / LlamaParse |
|---|---|---|---|
| **Runs** | before ingestion | after deployment | during ingestion |
| **Needs** | nothing — fully offline | live retriever + LLM judge | the documents |
| Readiness score (0–100) | ✅ | ❌ | ❌ |
| Issue diagnostics with fixes | ✅ | ❌ | ❌ |
| OCR error detection | ✅ | ❌ | ❌ |
| CI/CD quality gate + SARIF | ✅ | ❌ | ❌ |
| Peer-reviewed failure taxonomy | ✅ | ❌ | ❌ |
| Document extraction / conversion | ❌ | ❌ | ✅ |
| Answer faithfulness / relevance eval | ❌ | ✅ | ❌ |

*(Comparison as of October 2026; competitors evolve — verify against current docs.)*

## Install

```bash
pip install ragpreflight              # core: scan, audit, score, taxonomy
pip install "ragpreflight[full]"      # + chunk analysis, retrieval simulation, dedup
pip install "ragpreflight[llm]"       # + LLM-assisted query generation for simulate
```

Requires Python 3.10+. Core has zero API keys, zero network calls, zero model downloads.

## Python API

```python
from ragpreflight import scan_document, audit_corpus

report = scan_document("my_doc.pdf")
print(report.score)          # 83
for issue in report.issues:  # typed dataclasses, not dicts
    print(issue.severity.value, issue.category.value, issue.message)
    for ref in issue.taxonomy_refs:
        print(f"  → {ref.mode_id} ({ref.relationship})")

corpus = audit_corpus("./knowledge_base/")
print(corpus.average_score, corpus.duplicate_groups)
```

Framework gates:

```python
from ragpreflight.integrations.langchain import RAGPreflightLoader
loader = RAGPreflightLoader("doc.pdf", min_score=70)  # raises ValueError below 70
docs = loader.load()
```

## Quality profiles

| Profile | Min score | For |
|---|---|---|
| `permissive` | 40 | Chatbots, FAQ systems, internal tools |
| `standard` (default) | 60 | Enterprise search, customer support RAG |
| `strict` | 80 | Medical, legal, financial RAG (`medical`, `legal`, `financial` are aliases) |

Set defaults in `.ragpreflight.toml` in your project root — CLI flags always override.

```bash
ragpreflight scan doc.pdf --profile strict
```

## Links

- 📦 [PyPI](https://pypi.org/project/ragpreflight/) · 📊 [Download stats](https://pypistats.org/packages/ragpreflight)
- 📄 [Paper (ACL Anthology)](https://aclanthology.org/2026.trustnlp-main.27/) · [DOI](https://doi.org/10.18653/v1/2026.trustnlp-main.27)
- 🤝 [Contributing](./CONTRIBUTING.md) · [Good first issues](https://github.com/anusky95/ragpreflight/labels/good%20first%20issue)
- 📜 [MIT License](./LICENSE) · [Changelog](./CHANGELOG.md)
