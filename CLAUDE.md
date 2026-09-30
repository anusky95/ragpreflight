# CLAUDE.md — RAGCheck Project Brief

## What Is This

RAGCheck is an open-source Python package that scores document readiness for RAG pipelines. It tells you what's broken in your documents BEFORE you embed and retrieve them — not after your chatbot starts hallucinating.

**One-liner:** `pip install ragcheck` → `ragcheck scan my_document.pdf` → readiness score 0-100 with actionable issues.

## Design Principles (Follow These Always)

1. **Zero API keys for core functionality.** Everything runs locally by default. LLM-powered features are optional extras behind `--use-llm` flags. Never make an external API call without the user explicitly opting in.
2. **Lightweight base install.** Core dependencies: `pymupdf`, `pdfplumber`, `chardet`, `click`, `rich`. Heavy deps like `sentence-transformers` and `datasketch` are optional extras (`pip install ragcheck[full]`).
3. **Never crash on bad input.** Malformed PDFs, 500MB files, zip bombs disguised as docs, empty files, binary garbage — handle all of it gracefully with clear error messages. Wrap all file I/O in try/except and return meaningful errors, not tracebacks.
4. **Typed dataclasses for all outputs.** Every public function returns typed dataclasses, not raw dicts. Users get autocomplete and type checking for free.
5. **Tests alongside code.** Every module gets a corresponding test file. Write tests as you implement, not as a separate phase. Include fixture files in `tests/fixtures/` for reproducibility.
6. **Src layout.** Use `src/ragcheck/` package structure with `pyproject.toml`. No `setup.py`.
7. **Fail informatively.** When something goes wrong, the error message should tell the user what happened, what file caused it, and what to do about it.

## Target Users

- ML engineers building RAG pipelines who want to debug document quality issues
- Data engineers responsible for ingestion pipelines
- Teams evaluating vendor document processing tools
- Anyone who has ever asked "why is my RAG system hallucinating?"

## Package Structure

```
ragcheck/
├── CLAUDE.md                  # This file
├── LICENSE                    # MIT
├── README.md                  # Badges, install, quickstart, examples
├── pyproject.toml             # Build config, dependencies, optional extras
├── .github/
│   └── workflows/
│       └── ci.yml             # pytest + ruff + mypy
├── src/
│   └── ragcheck/
│       ├── __init__.py        # Public API exports + __version__
│       ├── cli.py             # Click CLI entry point
│       ├── models.py          # All dataclasses (DocumentReport, ChunkReport, CorpusReport, Issue)
│       ├── scanner.py         # Document scanning module
│       ├── chunker.py         # Chunk analysis module
│       ├── corpus.py          # Corpus-level analysis module
│       ├── retrieval.py       # Retrieval simulation module
│       ├── profiles.py        # Threshold profiles (strict, standard, permissive)
│       ├── report.py          # Output formatting (terminal, JSON, HTML)
│       ├── utils.py           # File detection, encoding helpers, size guards
│       └── _constants.py      # OCR error patterns, default thresholds, magic numbers
├── tests/
│   ├── conftest.py            # Shared fixtures
│   ├── fixtures/              # Test PDFs, DOCX, CSVs, etc.
│   │   ├── clean.pdf
│   │   ├── ocr_errors.pdf
│   │   ├── scanned.pdf
│   │   ├── messy_table.pdf
│   │   ├── empty.pdf
│   │   ├── sample.docx
│   │   ├── sample.csv
│   │   └── sample.md
│   ├── test_scanner.py
│   ├── test_chunker.py
│   ├── test_corpus.py
│   ├── test_retrieval.py
│   ├── test_cli.py
│   └── test_utils.py
└── docs/
    └── profiles.md            # Explain strict/standard/permissive thresholds
```

## Data Models (Core Contract — Do Not Deviate)

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

class Severity(Enum):
    CRITICAL = "critical"   # Will definitely cause RAG failures
    WARNING = "warning"     # Likely to cause issues
    INFO = "info"           # Worth knowing, may not cause problems

class IssueCategory(Enum):
    OCR = "ocr"
    ENCODING = "encoding"
    STRUCTURE = "structure"
    CONTENT = "content"
    METADATA = "metadata"
    CHUNKING = "chunking"
    DUPLICATION = "duplication"
    STALENESS = "staleness"

@dataclass
class Issue:
    category: IssueCategory
    severity: Severity
    message: str                        # Human-readable description
    location: Optional[str] = None      # e.g., "page 3", "rows 12-15", "chunk 7"
    suggestion: Optional[str] = None    # Actionable fix

@dataclass
class DocumentReport:
    filepath: str
    score: int                          # 0-100, overall readiness
    issues: list[Issue] = field(default_factory=list)
    page_count: int = 0
    text_extractable_ratio: float = 0.0 # 0.0-1.0, how much text is extractable
    file_format: str = ""
    file_size_bytes: int = 0

@dataclass
class ChunkReport:
    chunk_index: int
    text_preview: str                   # First 100 chars
    coherence_score: float              # 0.0-1.0
    issues: list[Issue] = field(default_factory=list)

@dataclass
class CorpusReport:
    directory: str
    total_documents: int
    average_score: float
    documents: list[DocumentReport] = field(default_factory=list)
    corpus_issues: list[Issue] = field(default_factory=list)  # Cross-document issues
    duplicate_groups: list[list[str]] = field(default_factory=list)  # Groups of near-dupes
```

## CLI Interface

```bash
# Single document scan
ragcheck scan document.pdf
ragcheck scan document.pdf --json
ragcheck scan document.pdf --profile strict

# Full corpus audit
ragcheck audit ./knowledge_base/
ragcheck audit ./knowledge_base/ --format html --output report.html
ragcheck audit ./knowledge_base/ --profile medical

# Chunk analysis (requires optional deps)
ragcheck chunks document.pdf --strategy recursive --size 512 --overlap 50

# Retrieval simulation (requires optional deps)
ragcheck simulate ./knowledge_base/ --queries 50
ragcheck simulate ./knowledge_base/ --use-llm --llm-endpoint http://localhost:11434

# Quick check — just the score, nothing else
ragcheck score document.pdf
```

## Profiles (Threshold Presets)

```python
PROFILES = {
    "permissive": {
        "min_document_score": 40,
        "min_chunk_coherence": 0.5,
        "max_ocr_error_rate": 0.10,
        "max_duplicate_similarity": 0.95,
        "description": "For chatbots, FAQ systems, internal tools"
    },
    "standard": {
        "min_document_score": 60,
        "min_chunk_coherence": 0.65,
        "max_ocr_error_rate": 0.05,
        "max_duplicate_similarity": 0.90,
        "description": "For enterprise search, customer support RAG"
    },
    "strict": {
        "min_document_score": 80,
        "min_chunk_coherence": 0.8,
        "max_ocr_error_rate": 0.02,
        "max_duplicate_similarity": 0.85,
        "description": "For medical, legal, financial RAG systems"
    }
}
```

## Supported File Formats

| Format | Base Install | Detection |
|--------|-------------|-----------|
| PDF (text-based) | ✅ pymupdf + pdfplumber | OCR errors, structure, tables |
| PDF (scanned) | ✅ pymupdf | Flags as low-extractability, suggests OCR |
| DOCX | ✅ python-docx | Structure, metadata, encoding |
| TXT | ✅ built-in | Encoding, content density |
| CSV/TSV | ✅ built-in | Encoding, structure, consistency |
| HTML | ✅ beautifulsoup4 | Boilerplate ratio, structure |
| Markdown | ✅ built-in | Structure, header hierarchy |

## OCR Error Detection Patterns

These are the common substitution patterns to check. Store in `_constants.py`:

```python
OCR_SUBSTITUTION_PATTERNS = {
    # Character confusions
    "l/I/1": r"(?<=[a-z])[I1](?=[a-z])",    # "cIinical" → "clinical"
    "O/0": r"(?<=[a-z])0(?=[a-z])",           # "pr0tocol" → "protocol"
    "rn/m": r"(?<=[a-z])rn(?=[a-z])",         # "inforrnation" → "information"
    "fi/fl_ligature": r"[ﬁﬂ]",               # Ligature artifacts
    "broken_ligatures": r"(?<=\w)[ffi](?=\w)", # Split ligatures
    # Spacing artifacts
    "mid_word_spaces": r"(?<=[a-z]) (?=[a-z]{2})",  # "pati ent" → "patient"
    "merged_words": None,  # Detected via dictionary lookup, not regex
    # Encoding artifacts
    "mojibake": r"[Ã¢â€™Â©Ã©]",             # UTF-8 decoded as Latin-1
}
```

## What NOT to Build

- **No web UI.** This is a CLI/library tool. Web dashboards can come from the community.
- **No database.** Reports are generated on-the-fly. Users can pipe JSON to wherever they want.
- **No user accounts or telemetry.** Zero tracking, zero phoning home.
- **No custom ML models.** Use existing sentence-transformers and standard NLP tools. Don't train anything.
- **No vendor lock-in.** The `--use-llm` flag works with any OpenAI-compatible endpoint (OpenAI, Ollama, vLLM, Azure, etc.), not just one provider.

## Implementation Phases

### Phase 1: Scaffold
Set up the package structure exactly as described above. `pyproject.toml` with:
- Build system: hatchling
- Python: >=3.9
- Core deps: pymupdf, pdfplumber, chardet, click, rich, python-docx, beautifulsoup4
- Optional extras: `[full]` = sentence-transformers, datasketch; `[llm]` = openai
- CLI entry point: `ragcheck = ragcheck.cli:main`
- Linting: ruff
- Type checking: mypy
- Testing: pytest

Create a working CLI that responds to `ragcheck --version` and `ragcheck scan --help`.
Write README.md with badges (PyPI, Python version, License, CI), one-liner install, quickstart example, and feature comparison table vs RAGAS/Unstructured/LlamaIndex.

### Phase 2: Document Scanner (`scanner.py`)
Build the core scanning engine. For each supported file format, detect:
- **Encoding issues** via chardet (flag confidence < 0.8)
- **Text extractable ratio** — what percentage of pages/content yield actual text vs images/scans
- **OCR error patterns** using the regex patterns in `_constants.py`
- **Character-level anomalies** — unusual Unicode, control characters, excessive whitespace
- **Empty or near-empty pages** (< 10 words)
- **Embedded tables or structured data** that need special chunking (detect via cell/row patterns)
- **Metadata presence** — does the doc have title, author, date, or is it bare?
- **File size sanity** — flag files > 100MB with a warning, reject > 500MB by default (configurable)

The scanner returns a `DocumentReport` with a score 0-100 computed as weighted sum:
- Text extractability: 30%
- OCR cleanliness: 25%
- Structural integrity: 20%
- Metadata completeness: 10%
- Content density: 15%

Write tests for each file format with both clean and problematic fixtures.

### Phase 3: Chunk Diagnostics (`chunker.py`)
Build the chunk analysis module. Given a document and a chunking strategy:
- Default: recursive character split at 512 tokens, 50 token overlap
- Accept a pluggable splitter function: `Callable[[str], list[str]]`

For each chunk, score:
- **Semantic coherence** using sentence-transformers `all-MiniLM-L6-v2` (optional dep — if not installed, skip this metric and warn)
- **Structural breaks** — does the chunk split a table, list, or code block mid-element?
- **Metadata preservation** — does the chunk retain source page number, section header context?
- **Information density** — ratio of meaningful tokens vs boilerplate/stopwords
- **Boundary quality** — does the chunk start/end mid-sentence?

Return a list of `ChunkReport` objects. Flag chunks below profile thresholds.
Make chunking strategy pluggable so users can bring their own splitter.

### Phase 4: Corpus Analysis (`corpus.py`)
Build the corpus-level analysis module. Given a directory of documents:
- Run `scanner.py` on each file
- **Near-duplicate detection** using MinHash/LSH via datasketch (optional dep)
- **Contradiction candidates** — find doc pairs covering the same topic (embedding similarity > 0.85) with different factual claims. Surface pairs for human review, don't auto-judge.
- **Temporal coverage** — if docs have detectable dates (file metadata, content regex), flag docs older than a configurable threshold (default: 12 months)
- **Format distribution** — breakdown by file type
- **Worst offenders** — bottom 10% by score
- **Corpus statistics** — total docs, average score, score distribution

Return a `CorpusReport`. Show a progress bar via `rich` for directories with 5+ files.

### Phase 5: Retrieval Simulation (`retrieval.py`)
Build the retrieval simulation module. Generate synthetic test queries WITHOUT requiring any LLM API:
- **Extractive methods**: Pull key phrases (TF-IDF top terms), named entities (simple regex-based NER for common types), and section headers → form natural question templates
- Template patterns: "What is {entity}?", "Explain {concept}", "Summarize {section_title}", "What are the details of {key_phrase}?"
- Generate 5-10 queries per document by default

Test retrieval:
- Embed queries and chunks using sentence-transformers
- Compute cosine similarity, retrieve top-k (default k=5)
- Score: precision@k, percentage of dead chunks (never retrieved by any query), query failure rate (best match similarity < 0.5)

Optional `--use-llm` flag:
- Accepts any OpenAI-compatible endpoint via `--llm-endpoint` (default: http://localhost:11434 for Ollama)
- Uses the LLM to generate more diverse, natural queries
- Falls back to extractive if LLM endpoint is unreachable

### Phase 6: CLI & Reporting (`cli.py`, `report.py`)
Polish the CLI commands:
- `ragcheck scan <file>` — single document, terminal table output by default
- `ragcheck audit <directory>` — full corpus audit with progress bar
- `ragcheck chunks <file>` — chunk analysis (warns if optional deps missing)
- `ragcheck simulate <directory>` — retrieval simulation (warns if optional deps missing)
- `ragcheck score <file>` — just the number, for scripting (`echo $(ragcheck score doc.pdf)`)

Output formats:
- Default: `rich` terminal table with color-coded severity
- `--json` flag: structured JSON to stdout
- `--format html --output report.html`: self-contained HTML report with expandable sections
- `--quiet` flag: suppress all output except the score (for CI/CD pipelines)

Config file support:
- `.ragcheckrc` (TOML format) in project root or home directory
- Override profile, thresholds, file size limits, output preferences
- CLI flags override config file values

### Phase 7: Harden for Release
Review entire codebase for:
- **Type hints** on every function signature and return type
- **Docstrings** on every public method (Google style)
- **Input validation** — clear ValueError/TypeError with actionable messages
- **Edge cases** — empty directories, permission errors, symlinks, binary files with wrong extensions
- **Security** — no path traversal, no arbitrary code execution, no eval/exec, no pickling untrusted data, size limits on all file reads
- **CI pipeline** — GitHub Actions running pytest, ruff, mypy on Python 3.9/3.10/3.11/3.12
- **`--verbose` flag** for debug logging (use stdlib `logging`, not print)
- **CONTRIBUTING.md** with dev setup instructions, PR guidelines, and code style expectations
- **CHANGELOG.md** with initial release notes

### Phase 8: Integration & Dogfooding
Wire up the full pipeline so `ragcheck audit mydir/` runs scanning → chunk analysis → corpus analysis in sequence with a unified report. Each module stays independently importable.

Then run ragcheck against:
1. The test fixtures in `tests/fixtures/`
2. The ragcheck source code itself (`.py` files as TXT)
3. The README.md
4. A deliberately corrupted PDF (create one in tests)

Fix any crashes, confusing output, or scores that seem wrong. Verify that:
- A clean, well-structured PDF scores > 85
- A scanned PDF with OCR errors scores < 50
- An empty file scores 0 with a clear CRITICAL issue
- The CLI never shows a traceback to the user (all errors are caught and formatted)

## Code Style Rules

- Use ruff for formatting and linting (line length 99)
- Use Google-style docstrings
- Prefer early returns over deep nesting
- No global mutable state
- No `print()` — use `click.echo()` in CLI, `logging` everywhere else
- Constants in SCREAMING_SNAKE_CASE in `_constants.py`
- Private functions prefixed with underscore
- No `# type: ignore` without a comment explaining why
