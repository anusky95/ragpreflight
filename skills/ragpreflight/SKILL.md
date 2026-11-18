---
name: ragpreflight
description: Lint a folder of documents before they are indexed for RAG, an LLM wiki, an OKF bundle, or any agent's knowledge base. Use when the user is building or debugging retrieval, ingesting documents, setting up a vector store, or asks why a RAG/agent answer was wrong ("it hallucinated", "it quoted an old policy"). Finds stale versions, conflicting statements across versions, scanned pages with no text, tables that will extract badly, risky chunk splits, PII and secrets — offline, no API keys.
---

# ragpreflight

Run a deterministic, offline lint over the documents an AI system will read, **before** they are embedded or compiled into a wiki. Most "hallucinations" in retrieval systems start upstream: a stale policy still in the index, two versions that disagree, a scanned PDF with no text layer, a table that becomes run-on text, a qualifier ("must be requested before travel") split into another chunk.

## When to use

- The user is about to ingest/index/embed a folder of documents, or writes ingestion code.
- The user reports a wrong or outdated answer from a RAG system or agent — check the source documents before touching the prompt.
- The user sets up CI for a knowledge base, docs repo, LLM wiki or OKF bundle.

## How to run

Prefer zero-install:

```bash
uvx ragpreflight ./path/to/docs            # or: pipx run ragpreflight ./path/to/docs
```

Fallback: `pip install ragpreflight` then `ragpreflight ./path/to/docs`.

For machine-readable output (use this to reason over results):

```bash
uvx ragpreflight ./docs --format json --fail-on never
```

JSON fields: `verdict` (READY | REVIEW | NOT READY), `score` (0–100), `findings[]` with `check`, `severity` (error|warn|info), `f_codes` (failure modes F1–F33 from Garani 2026), `title`, `path`, `location`, `detail`, `fix`, `related`.

Other commands: `ragpreflight demo` (shows a planted example), `ragpreflight explain F14`, `ragpreflight coverage`.

## How to act on results

1. Report the verdict and the errors first, grouped by type. Quote `title` and `path`; don't dump raw JSON.
2. `version-conflict` and `version-stale` are the most damaging: recommend removing or archiving the older file, or marking it superseded in metadata, and say which statement conflicts.
3. `secret` → tell the user to remove and rotate the credential. `pii` → redact or restrict access at retrieval time.
4. `extraction-scanned` → OCR before ingesting (e.g. `ocrmypdf`). `extraction-table` → use a table-aware parser (Docling, pdfplumber) and keep headers with rows.
5. `chunk-split` → split on headings/sentences or add overlap; keep qualifiers with their clause.
6. Do not delete or edit the user's documents yourself unless asked. Offer the fix; let them decide.
7. If the user is debugging a wrong answer, run ragpreflight on the source folder, then check whether the right chunk was actually retrieved, before suggesting prompt changes.
