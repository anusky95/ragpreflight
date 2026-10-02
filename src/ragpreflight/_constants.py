"""Constants, patterns, and default thresholds for ragpreflight."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# OCR error detection patterns
# ---------------------------------------------------------------------------

OCR_SUBSTITUTION_PATTERNS: dict[str, str | None] = {
    # Character confusions (regex-safe patterns with low false-positive rate)
    "l_I_1": r"(?<=[a-z])[I1](?=[a-z])",  # "cIinical" → "clinical"
    "O_0": r"(?<=[a-z])0(?=[a-z])",  # "pr0tocol" → "protocol"
    "fi_fl_ligature": r"[ﬁﬂ]",  # Ligature artifacts (Unicode chars)
    # Encoding artifacts
    "mojibake": r"[ÃÂÃÂ¢ÃÂ©ÃÂ©]",  # UTF-8 decoded as Latin-1
    # Common OCR numeral/letter confusions in context
    "zero_as_o": r"\b0[a-z]+\b",  # "0ne" instead of "one"
    # Dictionary-aware patterns (detected via _wordlist.py, not regex)
    "rn_m": None,
    "mid_word_spaces": None,
    "broken_ligatures": None,  # Removed: [ffi] char-class matched every f/i in English
    "merged_words": None,
}

# Human-readable descriptions for each OCR pattern, shown in issue output.
# Each entry: (label, what_it_is, rag_impact)
OCR_PATTERN_DESCRIPTIONS: dict[str, tuple[str, str, str]] = {
    "l_I_1": (
        "l / I / 1 confusion",
        "The scanner misread lowercase l, capital I, and digit 1 as each other "
        "(e.g. 'cIinical' instead of 'clinical', '1iver' instead of 'liver').",
        "Keyword and semantic search both fail — a user querying 'clinical' won't "
        "match 'cIinical' in the vector index.",
    ),
    "O_0": (
        "O / 0 confusion",
        "The letter O was substituted with the digit 0 (e.g. 'pr0tocol' instead of 'protocol').",
        "Breaks exact-match retrieval and degrades embedding quality for technical terms.",
    ),
    "rn_m": (
        "rn / m split",
        "The letter m was split into rn by the scanner "
        "(e.g. 'inforrnation' instead of 'information').",
        "Common in low-DPI scans. Misspelled tokens create retrieval gaps — "
        "the LLM may still understand the text but retrieval ranking degrades.",
    ),
    "fi_fl_ligature": (
        "fi / fl ligature characters",
        "Unicode ligature characters ﬁ (fi) and ﬂ (fl) survived extraction. "
        "These are single characters, not two letters, so 'ﬂight' ≠ 'flight'.",
        "Users searching 'flight', 'financial', or 'flexible' won't match "
        "chunks containing the ligature forms. Affects millions of typeset PDFs.",
    ),
    "broken_ligatures": (
        "broken ligatures",
        "Ligature characters were split mid-word during extraction, "
        "inserting phantom letter combinations into words.",
        "Creates misspelled tokens that fragment embedding representation "
        "and fail keyword search.",
    ),
    "mid_word_spaces": (
        "mid-word spaces",
        "Spaces were inserted inside words during scanning "
        "(e.g. 'pati ent' instead of 'patient', 'treat ment' instead of 'treatment').",
        "Each broken word becomes two meaningless tokens. Semantic embeddings "
        "for entire sentences degrade, and exact-match search fails completely.",
    ),
    "mojibake": (
        "mojibake (encoding corruption)",
        "UTF-8 text was decoded as Latin-1 or Windows-1252, producing garbled "
        "characters like Ã©, Â£, â€™ instead of é, £, '.",
        "Garbled characters corrupt every sentence they appear in. "
        "The LLM may misinterpret or hallucinate around them.",
    ),
    "zero_as_o": (
        "digit-as-letter substitution",
        "The digit 0 was used where the letter o was intended at word boundaries "
        "(e.g. '0ne' instead of 'one', '0ver' instead of 'over').",
        "Common in scanned documents. Breaks numeric-aware retrieval and "
        "creates unknown tokens in the embedding model's vocabulary.",
    ),
}

# F-code mapping: IssueCategory value → list of (mode_id, relationship, explanation)
# Applied post-scan to populate taxonomy_refs on all issues automatically.
ISSUE_CATEGORY_TO_FCODE: dict[str, list[tuple[str, str, str]]] = {
    "ocr": [
        (
            "F3",
            "direct",
            "OCR artifacts directly cause Document Quality failures (F3): garbled tokens "
            "degrade embedding quality and break keyword retrieval (Garani 2026, §Ingestion).",
        ),
    ],
    "encoding": [
        (
            "F3",
            "direct",
            "Encoding corruption produces garbled text that directly causes Document Quality "
            "failures (F3) — embeddings for corrupted sentences are unreliable.",
        ),
    ],
    "content": [
        (
            "F3",
            "proxy",
            "Low content density or empty pages are a proxy for Layout Parsing Errors (F3). "
            "Pages that yield no extractable text indicate failed parsing — embeddings for "
            "blank or garbled chunks degrade corpus-wide retrieval recall.",
        ),
    ],
    "structure": [
        (
            "F3",
            "proxy",
            "Detected tables are a proxy for Layout Parsing Errors (F3) — heterogeneous "
            "layouts resist uniform text extraction.",
        ),
        (
            "F7",
            "risk_signal",
            "Tables and structured elements are a risk signal for Chunking Boundary Errors (F7). "
            "A chunker that does not understand table structure will split rows mid-cell, "
            "producing incoherent chunks that hurt retrieval precision.",
        ),
    ],
    "metadata": [
        (
            "F11",
            "risk_signal",
            "Sparse or missing metadata is a risk signal for Low Recall / Ranking Failures (F11). "
            "Without title, author, or date, retrieval systems cannot filter or re-rank by source "
            "quality — relevant documents are harder to surface above the top-k cutoff.",
        ),
    ],
    "chunking": [
        (
            "F7",
            "direct",
            "Chunking boundary issues directly cause Structure-Unaware Chunking failures (F7): "
            "mid-sentence cuts and table splits degrade chunk coherence and retrieval precision.",
        ),
    ],
    "duplication": [
        (
            "F11",
            "proxy",
            "Near-duplicate documents are a proxy for Redundant/Duplicate Context (F11). "
            "Duplicate chunks inflate context windows and dilute the relevant signal.",
        ),
    ],
    "staleness": [
        (
            "F1",
            "proxy",
            "File-age detection is a proxy for Outdated/Stale Data (F1). "
            "This does NOT confirm content is outdated — only that the file is old. "
            "Human review required.",
        ),
    ],
}

# Minimum word length to be considered a "real" word for density calculations
MIN_WORD_LENGTH = 2

# Text considered "empty" if fewer than this many words
EMPTY_PAGE_WORD_THRESHOLD = 10

# File size limits
DEFAULT_MAX_FILE_SIZE_MB = 100
HARD_MAX_FILE_SIZE_MB = 500

# Bytes per MB
BYTES_PER_MB = 1024 * 1024

# ---------------------------------------------------------------------------
# Score weights for DocumentReport computation
# ---------------------------------------------------------------------------

SCORE_WEIGHTS = {
    "text_extractability": 0.30,
    "ocr_cleanliness": 0.25,
    "structural_integrity": 0.20,
    "metadata_completeness": 0.10,
    "content_density": 0.15,
}

# ---------------------------------------------------------------------------
# Encoding detection
# ---------------------------------------------------------------------------

MIN_ENCODING_CONFIDENCE = 0.8  # charset-normalizer confidence below this → flag

# ---------------------------------------------------------------------------
# Common control characters (exclude normal ones like \n, \r, \t)
# ---------------------------------------------------------------------------

CONTROL_CHAR_PATTERN = r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]"

# ---------------------------------------------------------------------------
# Boilerplate detection for HTML
# ---------------------------------------------------------------------------

HTML_BOILERPLATE_TAGS = {
    "nav",
    "footer",
    "header",
    "aside",
    "script",
    "style",
    "noscript",
    "advertisement",
    "cookie-banner",
}

# ---------------------------------------------------------------------------
# Metadata fields we look for
# ---------------------------------------------------------------------------

PDF_METADATA_FIELDS = ["title", "author", "subject", "keywords", "creationdate", "creator"]
DOCX_METADATA_FIELDS = ["title", "author", "subject", "keywords", "created", "modified"]

# ---------------------------------------------------------------------------
# Supported file extensions
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".txt",
    ".csv",
    ".tsv",
    ".html",
    ".htm",
    ".md",
    ".markdown",
    ".pptx",
    ".xlsx",
    ".ipynb",
    ".srt",
    ".vtt",
}

# ---------------------------------------------------------------------------
# PII detection patterns
# ---------------------------------------------------------------------------

PII_PATTERNS: dict[str, str] = {
    "email": r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
    "us_ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "us_phone": r"\b(?:\+1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b",
    "credit_card": r"\b(?:\d{4}[\s\-]?){3}\d{4}\b",
    "ip_address": r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
}

# Minimum PII hits before raising a WARNING (avoids false-positive noise)
PII_MIN_HITS = 3

# ---------------------------------------------------------------------------
# Formula / equation detection patterns (PDF text artifacts)
# ---------------------------------------------------------------------------

FORMULA_PATTERNS = [
    r"[αβγδεζηθικλμνξπρστυφχψωΑΒΓΔΕΖΗΘΙΚΛΜΝΞΠΡΣΤΥΦΧΨΩ]",  # Greek letters
    r"\b(?:sin|cos|tan|cot|log|ln|exp|sqrt|lim|sum|prod|int)\s*[\(\[]",  # Math functions
    r"[∑∏∫∂∇∞≤≥≠≈±×÷√∝∈∉⊂⊃∪∩]",  # Unicode math symbols
    r"\$[^$\n]{2,80}\$",  # LaTeX inline math $...$
    r"\\\[.{2,200}\\\]",  # LaTeX display math \[...\]
    r"\b[a-zA-Z]\s*=\s*[-+]?\d*\.?\d+\s*[+\-*/^]",  # Simple assignments: x = 3 +
]

# Minimum formula pattern hits to flag a document as math-heavy
FORMULA_MIN_HITS = 5

# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

LANGUAGE_DETECTION_MIN_CHARS = 200  # Skip language detection on very short texts
LANGUAGE_DETECTION_SAMPLE_CHARS = 5000  # Use first N chars for language detection

# ---------------------------------------------------------------------------
# Chunking defaults
# ---------------------------------------------------------------------------

DEFAULT_CHUNK_SIZE = 512
DEFAULT_CHUNK_OVERLAP = 50

# Structural break markers inside chunks
STRUCTURAL_BREAK_PATTERNS = [
    r"\|\s*[-:]+\s*\|",  # Table row boundary
    r"^\s*[-*+]\s",  # List item start
    r"^#{1,6}\s",  # Markdown header
    r"```",  # Code block fence
]

# ---------------------------------------------------------------------------
# Temporal staleness default (months)
# ---------------------------------------------------------------------------

DEFAULT_STALE_MONTHS = 12

# ---------------------------------------------------------------------------
# Retrieval simulation
# ---------------------------------------------------------------------------

DEFAULT_RETRIEVAL_TOP_K = 5
DEFAULT_QUERY_FAILURE_THRESHOLD = 0.5  # cosine similarity below this → failed query
DEFAULT_QUERIES_PER_DOC = 7

QUERY_TEMPLATES = [
    "What is {entity}?",
    "Explain {concept}.",
    "Summarize {section_title}.",
    "What are the details of {key_phrase}?",
    "How does {entity} work?",
    "What is the purpose of {key_phrase}?",
    "Describe {concept} in detail.",
]

# ---------------------------------------------------------------------------
# Report colours (rich markup)
# ---------------------------------------------------------------------------

SEVERITY_COLOURS = {
    "critical": "bold red",
    "warning": "yellow",
    "info": "cyan",
}

# ---------------------------------------------------------------------------
# Tool / library suggestions per issue category
# (name, description, install command)
# ---------------------------------------------------------------------------

TOOL_SUGGESTIONS: dict[str, list[tuple[str, str, str]]] = {
    "ocr": [
        ("pytesseract", "OCR engine for scanned documents", "pip install pytesseract"),
        ("doctr", "Deep-learning document text recognition", "pip install python-doctr[torch]"),
    ],
    "encoding": [
        ("ftfy", "Fix Unicode encoding errors automatically", "pip install ftfy"),
    ],
    "structure": [
        ("camelot-py", "Table extraction from PDFs", "pip install camelot-py[cv]"),
        ("unstructured", "Layout-aware document parsing", "pip install unstructured"),
    ],
    "content": [
        (
            "presidio",
            "PII detection and redaction",
            "pip install presidio-analyzer presidio-anonymizer",
        ),
        ("nougat-ocr", "Math-aware PDF extraction (Meta AI)", "pip install nougat-ocr"),
    ],
    "metadata": [
        ("pikepdf", "Edit PDF metadata programmatically", "pip install pikepdf"),
    ],
    "chunking": [
        ("langchain", "Structure-aware text splitters", "pip install langchain-text-splitters"),
    ],
    "duplication": [
        ("datasketch", "MinHash LSH near-duplicate detection", "pip install datasketch"),
    ],
}
