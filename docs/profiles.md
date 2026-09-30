# RAGCheck Quality Profiles

RAGCheck ships with three built-in threshold profiles — **permissive**, **standard**, and **strict** — plus three domain aliases for regulated industries.

---

## When to Choose Which Profile

| Profile | Use Case | Min Score | OCR Tolerance | Dedup Threshold |
|---------|----------|-----------|---------------|-----------------|
| `permissive` | Chatbots, FAQ bots, internal wikis | 40 | 10% | 95% |
| `standard` | Enterprise search, customer support | 60 | 5% | 90% |
| `strict` | Medical, legal, financial, compliance | 80 | 2% | 85% |
| `medical` | Clinical decision support, pharma | 80 | 2% | 85% |
| `legal` | Contract analysis, case law | 80 | 2% | 85% |
| `financial` | Financial reporting, audit trails | 80 | 2% | 85% |

> `medical`, `legal`, and `financial` are aliases for `strict` — they exist so your CI pipeline flags the intent clearly.

---

## Profile Details

### `permissive`

```toml
# .ragcheckrc
profile = "permissive"
```

**Thresholds:**

| Dimension | Threshold |
|-----------|-----------|
| Minimum document score | 40 |
| Minimum chunk coherence | 0.50 |
| Maximum OCR error rate | 10% |
| Maximum duplicate similarity | 95% |
| Staleness cutoff | 24 months |

**When to use:** Internal tools where the audience is forgiving of occasional errors. FAQ bots, support ticket deflection, internal wikis. Documents that would score 40–60 on `standard` are acceptable here.

**What it lets through:** Moderate OCR noise, missing metadata, near-duplicate documents (up to 95% similar). Still blocks completely unextractable or empty documents.

---

### `standard` (default)

```toml
# .ragcheckrc
profile = "standard"
```

**Thresholds:**

| Dimension | Threshold |
|-----------|-----------|
| Minimum document score | 60 |
| Minimum chunk coherence | 0.65 |
| Maximum OCR error rate | 5% |
| Maximum duplicate similarity | 90% |
| Staleness cutoff | 12 months |

**When to use:** Most production RAG systems. Enterprise search, customer-facing support bots, knowledge management platforms. This is the right starting point for 80% of use cases.

**What it blocks:** Documents with significant OCR damage, documents missing all metadata, near-duplicates within 90%, documents older than one year.

---

### `strict`

```toml
# .ragcheckrc
profile = "strict"
```

**Thresholds:**

| Dimension | Threshold |
|-----------|-----------|
| Minimum document score | 80 |
| Minimum chunk coherence | 0.80 |
| Maximum OCR error rate | 2% |
| Maximum duplicate similarity | 85% |
| Staleness cutoff | 6 months |

**When to use:** High-stakes domains where hallucinations have consequences. Medical diagnosis support, legal contract analysis, financial compliance, regulatory filings. Even mild OCR noise or missing metadata triggers failures.

**What it blocks:** Any document scoring below 80. This means PDFs with even minor scanning artifacts, documents with no title/author metadata, and anything more than 85% similar to another document in the corpus.

---

## Score Dimensions

Every document score is a weighted sum of five dimensions:

| Dimension | Weight | What It Measures |
|-----------|--------|------------------|
| Text Extractability | 30% | % of pages/content yielding actual text vs. images |
| OCR Cleanliness | 25% | Absence of OCR substitution errors and artifacts |
| Structural Integrity | 20% | Heading hierarchy, table consistency, well-formed structure |
| Content Density | 15% | Ratio of meaningful tokens to boilerplate/whitespace |
| Metadata Completeness | 10% | Presence of title, author, date, subject |

**Example score breakdown:**

```
Document: quarterly_report.pdf
  Text Extractability:    0.95 × 30 = 28.5
  OCR Cleanliness:        0.88 × 25 = 22.0
  Structural Integrity:   0.80 × 20 = 16.0
  Content Density:        0.70 × 15 = 10.5
  Metadata Completeness:  0.50 × 10 =  5.0
                                      ─────
  Final Score:                         82  ✓ PASS (strict)
```

---

## Custom Thresholds via Config File

Create a `.ragcheckrc` file in your project root or home directory:

```toml
# .ragcheckrc — project-level defaults
[ragcheck]
profile = "strict"
max_file_size_mb = 50
stale_months = 6
output_format = "terminal"
chunk_size = 512
chunk_overlap = 50
chunk_strategy = "recursive"
```

CLI flags always override config file values:

```bash
# Config says strict, but this run uses permissive:
ragcheck scan legacy_doc.pdf --profile permissive
```

---

## Using Profiles in Python

```python
from ragcheck import scan_document, audit_corpus, get_profile

# Get a profile dict
profile = get_profile("strict")

# Pass to scanner (used for threshold comparisons in reports)
report = scan_document("document.pdf")
if report.score < profile["min_document_score"]:
    print(f"FAIL: score {report.score} < threshold {profile['min_document_score']}")

# Pass to corpus auditor
corpus = audit_corpus("./docs/", profile=profile)
```

---

## CI/CD Integration

```yaml
# .github/workflows/docs-quality.yml
- name: Check document quality
  run: |
    pip install ragcheck
    ragcheck audit ./knowledge_base/ --profile strict --quiet
    # Exits 1 if any document has critical issues
```

For a score-threshold gate:

```bash
score=$(ragcheck score important.pdf)
if [ "$score" -lt 80 ]; then
  echo "Document quality too low: $score/100"
  exit 1
fi
```
