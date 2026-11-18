# How v0.3.0 was calibrated

A linter that cries wolf gets uninstalled, so every check was run on real documents and tuned
until what it reports at `error` and `warn` level is worth a human's time. Numbers below are from
ragpreflight 0.3.0 on Windows, 16 cores. They are a calibration log, not a benchmark.

## Corpora

| Corpus | Docs | Size | What it stresses |
|---|---|---|---|
| Finance filings (FinanceBench documents + same-company adjacent years) | 288 PDFs | 22.5M words | Long born-digital PDFs, tables, yearly report families |
| arXiv papers (random sample) | 120 PDFs | — | Math, formulas, figures, tables, author emails |
| Bundled demo (fictional airline) | 7 files | — | Every check firing on purpose |

## Results after calibration

**Finance (288 PDFs, full scan 130 s):**

- `error`: 3. All true:
  - 1 `secret`: a published 10-K that prints a website login and password.
  - 2 `unreadable`: two 8-K PDFs that neither PDFium nor pypdf can open.
- `version-period-twins`: 38 company families. Example: "6 period versions of 3m-10k (2015–2021): retrieval can return the wrong year". This is the failure FinanceBench-style questions hit.
- `version-stale` / `version-conflict`: 0. Yearly reports are period twins, not stale revisions.
- `extraction-scanned`: 17 documents with image-only pages, mostly exhibits and signature pages.
- Other warnings:
  - 23 `pii`: contact-heavy filings.
  - 17 `chunk-split`: only in the most rule-dense filings.
  - 14 `extraction-table`: table-dominated filings.
  - 3 `extraction-garbled`: broken font encodings.

**arXiv (120 papers):**

- 0 errors and 28 warnings: table-dominated papers, image-only pages, 3 broken font encodings, 6 contact-heavy papers.
- No formula or LaTeX content was flagged as garbled.

## What calibration changed (first run → v0.3.0)

| Problem found on real data | Fix |
|---|---|
| 13 "payment card" errors on arXiv: digit runs from tables that pass Luhn | Card numbers must have a real issuer prefix and consistent grouping, and must not sit in a row of numbers. |
| Chart axes ("600 800 1000") read as phone numbers | Phone numbers need phone punctuation (`-`, `.`, `()`) or a leading `+`. |
| 271 PII warnings for a company's main phone line or author emails | A few emails or phones are a note. Five or more, or any SSN or card, is a warning or error. |
| 56 "stale" and 65 "conflict" errors between yearly 10-Ks | `2021_10k` was parsed as the date "2021-10" plus "k". Fixed. Table-of-contents page numbers ("Risk Factors 15 Item 1B") are no longer compared as values. |
| 99 of 120 papers warned about tables | A table warning is reserved for table-dominated documents; ordinary tables are a note. |
| 71 of 120 papers warned about chunk splits | A chunk split is a warning only in rule-dense text (policies, contracts) and only when the qualifier opens the next chunk; elsewhere it is a note. |
| 6 minutes to scan the finance set | PDFium for text, with per-document checks run in parallel worker processes. |

## Known limits

- Chunk checks simulate a fixed-size 380-word splitter. Your splitter may differ, so they stay advisory in most text.
- Version families are found by filename and text similarity. Renamed revisions with unrelated names, and heavy rewrites, can be missed.
- Conflicts compare numbers and negations in aligned sentences. Restructured sentences, and bare numbers without units, can be missed.
- PII detection is regex-based (no NER), so names and addresses are not detected.
- Scanned-page detection needs an image on the page. Vector-drawn text without a text layer is reported as empty.
