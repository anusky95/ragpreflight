"""Document scanning engine for ragpreflight.

For each supported file format, detects encoding issues, OCR errors,
text extractability, structural problems, and metadata completeness.

Usage:
    from ragpreflight.scanner import scan_document

    report = scan_document("document.pdf")
    print(report.score)   # 0-100
    print(report.issues)  # list[Issue]
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ragpreflight._constants import (
    CONTROL_CHAR_PATTERN,
    DEFAULT_MAX_FILE_SIZE_MB,
    DOCX_METADATA_FIELDS,
    EMPTY_PAGE_WORD_THRESHOLD,
    FORMULA_MIN_HITS,
    FORMULA_PATTERNS,
    HARD_MAX_FILE_SIZE_MB,
    ISSUE_CATEGORY_TO_FCODE,
    LANGUAGE_DETECTION_MIN_CHARS,
    LANGUAGE_DETECTION_SAMPLE_CHARS,
    OCR_PATTERN_DESCRIPTIONS,
    OCR_SUBSTITUTION_PATTERNS,
    PDF_METADATA_FIELDS,
    PII_MIN_HITS,
    PII_PATTERNS,
    SCANNED_PAGE_IMAGE_COVERAGE,
    SCORE_WEIGHTS,
)
from ragpreflight.models import DocumentReport, Issue, IssueCategory, Severity, TaxonomyReference
from ragpreflight.utils import (
    check_file_size,
    clamp,
    detect_encoding,
    detect_file_format,
    encoding_is_suspicious,
    is_supported,
    safe_read_text,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Taxonomy enrichment
# ---------------------------------------------------------------------------


def _enrich_taxonomy_refs(issues: list[Issue]) -> list[Issue]:
    """Populate taxonomy_refs on every issue from the Garani 2026 mapping.

    Looks up mode name and definition from the taxonomy YAML so callers get
    human-readable descriptions without a separate taxonomy query.
    """
    try:
        from ragpreflight.taxonomy import get_failure_mode
    except Exception:
        return issues  # taxonomy module unavailable — skip silently

    enriched: list[Issue] = []
    for issue in issues:
        if issue.taxonomy_refs:
            enriched.append(issue)
            continue
        category_key = issue.category.value

        # Message-level overrides for issues whose category is too broad
        if issue.message.startswith("Possible PII"):
            mappings: list[tuple[str, str, str]] = [
                (
                    "F23",
                    "direct",
                    "PII in source documents directly causes PII/Compliance Leaks (F23) — "
                    "retrieved chunks containing personal data may be surfaced to unauthorised users.",
                ),
            ]
        elif issue.message.startswith("File size"):
            mappings = [
                (
                    "F3",
                    "risk_signal",
                    "Oversized files are a risk signal for Layout Parsing Errors (F3) — "
                    "large files are more likely to contain complex layouts that parsers mis-handle.",
                ),
            ]
        else:
            mappings = ISSUE_CATEGORY_TO_FCODE.get(category_key, [])
        refs: list[TaxonomyReference] = []
        for mode_id, relationship, explanation in mappings:
            try:
                mode = get_failure_mode(mode_id)
                refs.append(
                    TaxonomyReference(
                        mode_id=mode_id,
                        relationship=relationship,
                        mode_name=mode.name,
                        definition=mode.definition,
                        explanation=explanation,
                    )
                )
            except Exception:
                refs.append(
                    TaxonomyReference(
                        mode_id=mode_id,
                        relationship=relationship,
                        explanation=explanation,
                    )
                )
        enriched.append(
            Issue(
                category=issue.category,
                severity=issue.severity,
                message=issue.message,
                location=issue.location,
                suggestion=issue.suggestion,
                context=issue.context,
                taxonomy_refs=refs,
            )
        )
    return enriched


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan_document(
    filepath: str | Path,
    max_file_size_mb: float = DEFAULT_MAX_FILE_SIZE_MB,
    hard_max_file_size_mb: float = HARD_MAX_FILE_SIZE_MB,
) -> DocumentReport:
    """Scan a single document and return a quality report.

    Args:
        filepath: Path to the document to scan.
        max_file_size_mb: Warn (but continue) above this size in MB.
        hard_max_file_size_mb: Refuse to process above this size in MB.

    Returns:
        DocumentReport with score 0-100 and a list of detected issues.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file is unsupported or exceeds the hard size limit.
    """
    path = Path(filepath).resolve()

    if not path.exists():
        raise FileNotFoundError(f"File not found: {filepath}\nCheck the path and try again.")

    if path.is_dir():
        raise ValueError(f"'{filepath}' is a directory. Use audit_corpus() to scan directories.")

    fmt = detect_file_format(path)
    if fmt == "unknown" or not is_supported(path):
        raise ValueError(
            f"Unsupported file format: '{path.suffix}'\n"
            f"Supported formats: pdf, docx, txt, csv, tsv, html, markdown.\n"
            f"To request support for a new format, open an issue on GitHub."
        )

    try:
        size_bytes, size_warning = check_file_size(path, max_file_size_mb, hard_max_file_size_mb)
    except (FileNotFoundError, ValueError) as exc:
        raise ValueError(str(exc)) from exc

    issues: list[Issue] = []

    if size_warning:
        issues.append(
            Issue(
                category=IssueCategory.CONTENT,
                severity=Severity.WARNING,
                message=size_warning,
                suggestion="Split large files into smaller logical sections before ingestion.",
            )
        )

    _scanner: Callable[..., _ScanResult] = _FORMAT_SCANNERS.get(fmt, _scan_txt)

    try:
        result = _scanner(path)
    except Exception as exc:
        logger.debug("Scanner error for %s: %s", path, exc, exc_info=True)
        return DocumentReport(
            filepath=str(filepath),
            score=0,
            issues=[
                Issue(
                    category=IssueCategory.CONTENT,
                    severity=Severity.CRITICAL,
                    message=f"Could not parse file: {exc}",
                    location=str(path.name),
                    suggestion="Verify the file is not corrupted and try again.",
                )
            ],
            file_format=fmt,
            file_size_bytes=size_bytes,
        )

    issues.extend(result.issues)
    score = _compute_score(result)

    return DocumentReport(
        filepath=str(filepath),
        score=score,
        issues=_enrich_taxonomy_refs(_sort_issues(issues)),
        page_count=result.page_count,
        text_extractable_ratio=result.text_extractable_ratio,
        file_format=fmt,
        file_size_bytes=size_bytes,
    )


# ---------------------------------------------------------------------------
# Internal result container
# ---------------------------------------------------------------------------


class _ScanResult:
    """Intermediate result passed from format-specific scanners to score computation."""

    __slots__ = (
        "issues",
        "page_count",
        "text_extractable_ratio",
        "ocr_error_rate",
        "structural_score",
        "metadata_score",
        "content_density",
        "score_cap",
    )

    def __init__(
        self,
        *,
        issues: list[Issue],
        page_count: int = 0,
        text_extractable_ratio: float = 1.0,
        ocr_error_rate: float = 0.0,
        structural_score: float = 1.0,
        metadata_score: float = 0.0,
        content_density: float = 1.0,
        score_cap: int = 100,
    ) -> None:
        self.score_cap = score_cap
        self.issues = issues
        self.page_count = page_count
        self.text_extractable_ratio = clamp(text_extractable_ratio)
        self.ocr_error_rate = clamp(ocr_error_rate)
        self.structural_score = clamp(structural_score)
        self.metadata_score = clamp(metadata_score)
        self.content_density = clamp(content_density)


# ---------------------------------------------------------------------------
# Score computation
# ---------------------------------------------------------------------------


def _compute_score(result: _ScanResult) -> int:
    """Compute 0-100 readiness score from scan dimensions.

    Weights (see SCORE_WEIGHTS in _constants.py):
        text_extractability: 30%
        ocr_cleanliness: 25%
        structural_integrity: 20%
        metadata_completeness: 10%
        content_density: 15%

    Floor/ceiling logic:
        - 0% extractable text → score capped at 20 (scanned PDF with no OCR)
        - Scanner-specific cap via result.score_cap (PDF: ≤50% pages with text → 50)
        - OCR error rate > 10% → score capped at 40 (severely corrupted text)
        - Empty document (0 content density) → score 0
    """
    if result.content_density == 0.0 and result.text_extractable_ratio == 0.0:
        return 0

    ocr_cleanliness = 1.0 - result.ocr_error_rate
    raw = (
        result.text_extractable_ratio * SCORE_WEIGHTS["text_extractability"]
        + ocr_cleanliness * SCORE_WEIGHTS["ocr_cleanliness"]
        + result.structural_score * SCORE_WEIGHTS["structural_integrity"]
        + result.metadata_score * SCORE_WEIGHTS["metadata_completeness"]
        + result.content_density * SCORE_WEIGHTS["content_density"]
    )
    score = max(0, min(100, round(raw * 100)))

    score = min(score, result.score_cap)
    if result.text_extractable_ratio == 0.0:
        score = min(score, 20)
    if result.ocr_error_rate > 0.10:
        score = min(score, 40)

    return score


# ---------------------------------------------------------------------------
# OCR error helpers (shared across scanners)
# ---------------------------------------------------------------------------


def _count_ocr_errors(text: str) -> int:
    """Count OCR substitution pattern matches in text.

    Combines regex-based detection (for patterns with low false-positive rates)
    with dictionary-aware detection (for rn→m and mid-word space patterns).

    Args:
        text: Plain text to scan.

    Returns:
        Total number of pattern matches.
    """
    total = 0
    for key, pattern in OCR_SUBSTITUTION_PATTERNS.items():
        if pattern is None:
            continue
        try:
            matches = len(re.findall(pattern, text))
            total += matches
        except re.error:
            logger.debug("Regex error for OCR pattern '%s'", key)
    total += len(_find_rn_m_artifacts(text))
    total += len(_find_mid_word_space_artifacts(text))
    return total


def _find_rn_m_artifacts(text: str) -> list[tuple[str, str]]:
    """Detect rn→m OCR artifacts using dictionary lookup.

    For each word containing 'rn', checks if replacing 'rn' with 'm' produces
    a valid dictionary word while the original is NOT a valid word. Tries both
    single and all-occurrence replacement to handle words like 'cornrnittee'.

    Returns:
        List of (original_word, corrected_word) tuples.
    """
    from ragpreflight._wordlist import is_real_word

    seen: set[str] = set()
    findings: list[tuple[str, str]] = []
    for match in re.finditer(r"\b([a-zA-Z]*rn[a-zA-Z]*)\b", text):
        word = match.group(1)
        lower = word.lower()
        if len(word) < 4 or lower in seen or is_real_word(lower):
            continue
        seen.add(lower)
        replaced_first = lower.replace("rn", "m", 1)
        replaced_all = lower.replace("rn", "m")
        if is_real_word(replaced_all):
            findings.append((word, replaced_all))
        elif is_real_word(replaced_first):
            findings.append((word, replaced_first))
    return findings


def _find_mid_word_space_artifacts(text: str) -> list[tuple[str, str]]:
    """Detect spaces inserted mid-word during OCR using dictionary lookup.

    Iterates over all consecutive pairs of lowercase tokens separated by
    exactly one space. Flags when joining produces a valid word and at least
    one part is NOT a valid word on its own.

    Returns:
        List of (original_spaced, corrected_joined) tuples.
    """
    from ragpreflight._wordlist import is_real_word

    tokens = [(m.start(), m.end(), m.group()) for m in re.finditer(r"[a-z]{2,}", text)]
    findings: list[tuple[str, str]] = []
    for i in range(len(tokens) - 1):
        _start1, end1, left = tokens[i]
        start2, _end2, right = tokens[i + 1]
        if text[end1:start2] != " ":
            continue
        joined = left + right
        if len(joined) < 4:
            continue
        if is_real_word(joined) and (not is_real_word(left) or not is_real_word(right)):
            findings.append((f"{left} {right}", joined))
    return findings


def _ocr_error_rate(text: str) -> float:
    """Return OCR error rate as a fraction of total characters.

    Args:
        text: Plain text extracted from document.

    Returns:
        Float in [0.0, 1.0].
    """
    if not text:
        return 0.0
    errors = _count_ocr_errors(text)
    return clamp(errors / max(len(text), 1))


def _build_ocr_issues(text: str, location_prefix: str = "") -> list[Issue]:
    """Detect OCR errors and return Issue objects.

    Combines regex-based detection (low false-positive patterns like l/I/1,
    O/0, ligatures, mojibake) with dictionary-aware detection (rn→m,
    mid-word spaces) to minimise false positives.

    Args:
        text: Plain text to analyse.
        location_prefix: Prefix for location strings (e.g. "page 3").

    Returns:
        List of Issue objects (may be empty).
    """
    issues: list[Issue] = []
    found_patterns: list[str] = []
    snippets: list[str] = []

    # --- Regex-based patterns (only the ones with low false-positive rates) ---
    for key, pattern in OCR_SUBSTITUTION_PATTERNS.items():
        if pattern is None:
            continue
        try:
            matches = list(re.finditer(pattern, text))
            if matches:
                found_patterns.append(key)
                if len(snippets) < 3:
                    m = matches[0]
                    start = max(0, m.start() - 30)
                    end = min(len(text), m.end() + 30)
                    raw = text[start:end].replace("\n", " ").strip()
                    snippets.append(f'"{raw}"')
        except re.error:
            pass

    # --- Dictionary-aware patterns (rn→m, mid-word spaces) ---
    rn_findings = _find_rn_m_artifacts(text)
    if rn_findings:
        found_patterns.append("rn_m")
        if len(snippets) < 3:
            orig, corrected = rn_findings[0]
            snippets.append(f'"{orig}" → "{corrected}"')

    space_findings = _find_mid_word_space_artifacts(text)
    if space_findings:
        found_patterns.append("mid_word_spaces")
        if len(snippets) < 3:
            orig, corrected = space_findings[0]
            snippets.append(f'"{orig}" → "{corrected}"')

    if found_patterns:
        pattern_lines: list[str] = []
        rag_impacts: list[str] = []
        for key in found_patterns:
            desc = OCR_PATTERN_DESCRIPTIONS.get(key)
            if desc:
                label, what_it_is, rag_impact = desc
                pattern_lines.append(f"  • {label}: {what_it_is}")
                rag_impacts.append(rag_impact)
            else:
                pattern_lines.append(f"  • {key}")

        detail = "\n".join(pattern_lines)
        rag_note = rag_impacts[0] if rag_impacts else ""
        message = (
            f"OCR substitution artifacts detected ({len(found_patterns)} pattern"
            f"{'s' if len(found_patterns) != 1 else ''}):\n{detail}"
        )

        issues.append(
            Issue(
                category=IssueCategory.OCR,
                severity=Severity.WARNING,
                message=message,
                location=location_prefix or None,
                context=" · ".join(snippets) if snippets else None,
                suggestion=(
                    f"{rag_note + ' ' if rag_note else ''}"
                    "Re-scan the source document with higher DPI (≥300 DPI recommended) "
                    "or post-process with an OCR correction tool (e.g. pytesseract, docTR)."
                ),
            )
        )

    if re.search(CONTROL_CHAR_PATTERN, text):
        issues.append(
            Issue(
                category=IssueCategory.ENCODING,
                severity=Severity.WARNING,
                message="Unexpected control characters found — possible binary corruption.",
                location=location_prefix or None,
                suggestion="Strip or replace control characters before ingestion.",
            )
        )

    return issues


def _measure_content_density(text: str) -> float:
    """Estimate meaningful content density (0.0-1.0).

    Computes ratio of alpha-numeric tokens to total tokens.

    Args:
        text: Plain text.

    Returns:
        Float in [0.0, 1.0].
    """
    tokens = text.split()
    if not tokens:
        return 0.0
    meaningful = sum(1 for t in tokens if re.search(r"[a-zA-Z0-9]", t))
    return clamp(meaningful / len(tokens))


# ---------------------------------------------------------------------------
# PDF scanner
# ---------------------------------------------------------------------------


def _scan_pdf(path: Path) -> _ScanResult:
    """Scan a PDF document.

    Args:
        path: Path to the PDF file.

    Returns:
        _ScanResult with all quality dimensions populated.
    """
    try:
        import pymupdf as fitz  # type: ignore[import]
    except ImportError:
        raise ImportError(
            "PyMuPDF is required for PDF scanning. Install it with: pip install pymupdf"
        ) from None

    issues: list[Issue] = []
    page_texts: list[str] = []
    pages_with_any_text = 0
    blank_pages = 0

    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        raise ValueError(f"Cannot open PDF '{path.name}': {exc}") from exc

    page_count = doc.page_count
    if page_count == 0:
        doc.close()
        return _ScanResult(
            issues=[
                Issue(
                    category=IssueCategory.CONTENT,
                    severity=Severity.CRITICAL,
                    message="PDF has zero pages — document is empty or corrupt.",
                    location=path.name,
                    suggestion="Verify the source file. Re-export from the original application.",
                )
            ],
            page_count=0,
            text_extractable_ratio=0.0,
        )

    for page_num in range(page_count):
        try:
            page = doc[page_num]
            text = page.get_text("text")  # type: ignore[attr-defined]
            page_texts.append(text)
            word_count = len(text.split())
            sparse = word_count < EMPTY_PAGE_WORD_THRESHOLD
            coverage = _image_coverage(page) if sparse else 0.0
            if word_count == 0 and coverage == 0.0:
                blank_pages += 1
                issues.append(
                    Issue(
                        category=IssueCategory.CONTENT,
                        severity=Severity.INFO,
                        message="Blank page (no text, no images).",
                        location=f"page {page_num + 1}",
                        suggestion="Remove blank pages or ignore them during ingestion.",
                    )
                )
                continue
            if word_count == 0 or coverage >= SCANNED_PAGE_IMAGE_COVERAGE:
                detail = (
                    "Page yields no extractable text"
                    if word_count == 0
                    else f"Page is mostly an image with only {word_count} word(s) of text"
                )
                issues.append(
                    Issue(
                        category=IssueCategory.CONTENT,
                        severity=Severity.WARNING,
                        message=f"{detail} — may be a scanned image.",
                        location=f"page {page_num + 1}",
                        suggestion=(
                            "Run OCR (e.g. Tesseract) on image-only pages before ingestion."
                        ),
                    )
                )
                continue
            pages_with_any_text += 1
            if sparse:
                issues.append(
                    Issue(
                        category=IssueCategory.CONTENT,
                        severity=Severity.INFO,
                        message=f"Near-empty page ({word_count} words) — sparse content.",
                        location=f"page {page_num + 1}",
                        suggestion="Review whether this page adds value to the knowledge base.",
                    )
                )
        except Exception as exc:
            logger.debug("Error reading page %d of %s: %s", page_num + 1, path, exc)
            page_texts.append("")

    content_pages = page_count - blank_pages
    text_extractable_ratio = pages_with_any_text / content_pages if content_pages > 0 else 0.0

    if text_extractable_ratio <= 0.5:
        issues.append(
            Issue(
                category=IssueCategory.CONTENT,
                severity=Severity.CRITICAL,
                message=(
                    f"Only {text_extractable_ratio:.0%} of pages have extractable text. "
                    f"This document is likely a scanned PDF."
                ),
                suggestion=(
                    "Apply OCR (Tesseract, AWS Textract, Google Document AI) to the full "
                    "document before ingestion."
                ),
            )
        )
    elif text_extractable_ratio < 0.8:
        issues.append(
            Issue(
                category=IssueCategory.CONTENT,
                severity=Severity.WARNING,
                message=f"{text_extractable_ratio:.0%} of pages have extractable text — some pages may be images.",
                suggestion="Review image-only pages and apply OCR where needed.",
            )
        )

    full_text = "\n".join(page_texts)

    # OCR errors
    for i, ptext in enumerate(page_texts):
        if ptext:
            issues.extend(_build_ocr_issues(ptext, location_prefix=f"page {i + 1}"))

    # Global OCR rate
    ocr_rate = _ocr_error_rate(full_text)

    # Header/footer noise and page number artifacts
    if len(page_texts) >= 3:
        issues.extend(_detect_header_footer_noise(page_texts))
    issues.extend(_detect_page_number_artifacts(page_texts))

    # Metadata
    metadata = doc.metadata or {}
    doc.close()
    metadata_score = _score_pdf_metadata(metadata, issues)

    # Table detection (simple heuristic via pdfplumber)
    structural_score = _check_pdf_structure(path, issues)

    content_density = _measure_content_density(full_text)
    if content_density < 0.3:
        issues.append(
            Issue(
                category=IssueCategory.CONTENT,
                severity=Severity.WARNING,
                message=f"Low content density ({content_density:.0%}) — document may be mostly whitespace or boilerplate.",
                suggestion="Verify the document contains meaningful content before ingestion.",
            )
        )

    # Formula and PII: paged versions give per-page locations and sample snippets
    _page_tuples = [(i + 1, t) for i, t in enumerate(page_texts)]
    issues.extend(_detect_formula_issues_paged(_page_tuples))
    issues.extend(_detect_pii_issues_paged(_page_tuples))

    # Language detection
    lang = _detect_language(full_text)
    lang_issue = _build_language_issue(lang)
    if lang_issue:
        issues.append(lang_issue)

    return _ScanResult(
        issues=issues,
        page_count=page_count,
        text_extractable_ratio=text_extractable_ratio,
        ocr_error_rate=ocr_rate,
        structural_score=structural_score,
        metadata_score=metadata_score,
        content_density=content_density,
        score_cap=50 if text_extractable_ratio <= 0.5 else 100,
    )


def _image_coverage(page: Any) -> float:
    """Return the fraction (0.0–1.0) of the page area covered by images."""
    try:
        rect = page.rect
        page_area = rect.width * rect.height
        if page_area <= 0:
            return 0.0
        covered = 0.0
        for info in page.get_image_info():
            x0, y0, x1, y1 = info["bbox"]
            w = max(0.0, min(x1, rect.x1) - max(x0, rect.x0))
            h = max(0.0, min(y1, rect.y1) - max(y0, rect.y0))
            covered += w * h
        return clamp(covered / page_area)
    except Exception:
        return 0.0


def _detect_header_footer_noise(page_texts: list[str]) -> list[Issue]:
    """Detect repeated header/footer text across pages.

    Compares the first and last lines of each page. Lines that repeat
    across ≥60% of pages are likely headers/footers that will pollute chunks.

    Args:
        page_texts: List of per-page text strings (at least 3 pages).

    Returns:
        List of issues (may be empty).
    """
    n_pages = len(page_texts)
    if n_pages < 3:
        return []

    first_lines: dict[str, int] = {}
    last_lines: dict[str, int] = {}
    for text in page_texts:
        lines = [ln.strip() for ln in text.strip().split("\n") if ln.strip()]
        if not lines:
            continue
        fl = lines[0][:80]
        ll = lines[-1][:80]
        if len(fl) >= 5:
            first_lines[fl] = first_lines.get(fl, 0) + 1
        if len(ll) >= 5 and ll != fl:
            last_lines[ll] = last_lines.get(ll, 0) + 1

    threshold = max(3, int(n_pages * 0.6))
    repeated = []
    for line, count in {**first_lines, **last_lines}.items():
        if count >= threshold:
            repeated.append(line)

    if repeated:
        samples = "; ".join(f'"{r[:40]}"' for r in repeated[:3])
        return [
            Issue(
                category=IssueCategory.STRUCTURE,
                severity=Severity.INFO,
                message=(
                    f"Repeated header/footer text detected across {len(repeated)} "
                    f"pattern(s) on ≥{threshold} pages."
                ),
                context=samples,
                suggestion=(
                    "Strip repeated headers/footers before chunking to avoid "
                    "polluting embeddings with boilerplate text."
                ),
            )
        ]
    return []


def _detect_page_number_artifacts(page_texts: list[str]) -> list[Issue]:
    """Detect page number strings that will become noise tokens in chunks.

    Looks for patterns like 'Page 3 of 10', '- 5 -', standalone numbers
    at line boundaries that match the page position.

    Args:
        page_texts: List of per-page text strings.

    Returns:
        List of issues (may be empty).
    """
    page_num_count = 0
    for i, text in enumerate(page_texts):
        page_1 = i + 1
        lines = [ln.strip() for ln in text.strip().split("\n") if ln.strip()]
        if not lines:
            continue
        check_lines = []
        if lines:
            check_lines.append(lines[0])
        if len(lines) > 1:
            check_lines.append(lines[-1])

        for ln in check_lines:
            if re.match(
                rf"^(?:page\s+{page_1}\s*(?:of\s+\d+)?|-\s*{page_1}\s*-|{page_1}\s*$)",
                ln,
                re.IGNORECASE,
            ):
                page_num_count += 1
                break

    if page_num_count >= 3:
        return [
            Issue(
                category=IssueCategory.STRUCTURE,
                severity=Severity.INFO,
                message=(
                    f"Page number artifacts detected on {page_num_count} page(s). "
                    f"These become noise tokens after chunking."
                ),
                suggestion="Strip page numbers during text extraction before chunking.",
            )
        ]
    return []


def _score_pdf_metadata(metadata: dict, issues: list[Issue]) -> float:
    """Score PDF metadata completeness and append issues.

    Args:
        metadata: PyMuPDF metadata dict.
        issues: Issues list to append to (mutated in place).

    Returns:
        Metadata completeness score 0.0–1.0.
    """
    present = sum(1 for field in PDF_METADATA_FIELDS if metadata.get(field, "").strip())
    score = present / len(PDF_METADATA_FIELDS)

    if score < 0.3:
        issues.append(
            Issue(
                category=IssueCategory.METADATA,
                severity=Severity.INFO,
                message="PDF metadata is sparse or missing (title, author, date).",
                suggestion=(
                    "Add metadata to improve retrieval context. Use a PDF editor or "
                    "exiftool to update document properties."
                ),
            )
        )
    return score


def _preview_looks_reversed(preview: str) -> bool:
    """Return True if table cells look like right-to-left glyph order (e.g. "ehT | waL").

    Some PDFs store glyphs in reverse order; pdfplumber then yields reversed words.
    A reversed Title-case word starts lowercase and ends uppercase.
    """
    cells = [c.strip() for c in preview.split("|") if c.strip()]
    if not cells:
        return False
    reversed_cells = sum(
        1 for c in cells for w in c.split() if len(w) > 1 and w[0].islower() and w[-1].isupper()
    )
    return reversed_cells >= max(1, len(cells) // 2)


def _check_pdf_structure(path: Path, issues: list[Issue]) -> float:
    """Use pdfplumber to detect tables and structural issues.

    Reports per-table location (page, dimensions, content preview) so users
    can see exactly where tables are and how they'll affect chunking.

    Args:
        path: Path to the PDF file.
        issues: Issues list to append to (mutated in place).

    Returns:
        Structural integrity score 0.0–1.0.
    """
    try:
        import pdfplumber  # type: ignore[import]
    except ImportError:
        return 1.0

    table_details: list[str] = []
    table_previews: list[str] = []
    table_count = 0
    try:
        with pdfplumber.open(str(path)) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                try:
                    tables = page.find_tables()
                    if not tables:
                        continue
                    for tbl in tables:
                        table_count += 1
                        rows = tbl.extract()
                        n_rows = len(rows) if rows else 0
                        n_cols = len(rows[0]) if rows and rows[0] else 0
                        table_details.append(f"pg {page_num}: {n_rows}×{n_cols}")
                        if rows and rows[0]:
                            first_cells = [
                                str(c or "").strip()[:30].replace("\n", " ") for c in rows[0][:3]
                            ]
                            preview = " | ".join(c for c in first_cells if c)
                            if preview and not _preview_looks_reversed(preview):
                                table_previews.append(f'pg {page_num}: "{preview}"')
                except Exception:
                    pass
    except Exception:
        return 1.0

    if table_count > 0:
        detail_lines = "; ".join(table_details[:10])
        if len(table_details) > 10:
            detail_lines += f" (+{len(table_details) - 10} more)"

        issues.append(
            Issue(
                category=IssueCategory.STRUCTURE,
                severity=Severity.WARNING,
                message=f"{table_count} table(s) detected. Tables often chunk poorly as plain text.",
                location=detail_lines,
                context=" · ".join(table_previews[:3]) or None,
                suggestion=(
                    "Use a table-aware extractor (e.g. pdfplumber, Camelot, AWS Textract) "
                    "and convert tables to Markdown or CSV before chunking."
                ),
            )
        )
        return max(0.6, 1.0 - (table_count * 0.05))

    return 1.0


# ---------------------------------------------------------------------------
# DOCX scanner
# ---------------------------------------------------------------------------


def _scan_docx(path: Path) -> _ScanResult:
    """Scan a DOCX document.

    Args:
        path: Path to the DOCX file.

    Returns:
        _ScanResult.
    """
    try:
        from docx import Document  # type: ignore[import]
        # type: ignore[import]
    except ImportError:
        raise ImportError(
            "python-docx is required for DOCX scanning. Install with: pip install python-docx"
        ) from None

    issues: list[Issue] = []

    try:
        doc = Document(str(path))
    except Exception as exc:
        raise ValueError(f"Cannot open DOCX '{path.name}': {exc}") from exc

    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    full_text = "\n".join(paragraphs)

    if not full_text.strip():
        return _ScanResult(
            issues=[
                Issue(
                    category=IssueCategory.CONTENT,
                    severity=Severity.CRITICAL,
                    message="DOCX contains no extractable text.",
                    location=path.name,
                    suggestion="Verify the file is not empty or corrupted.",
                )
            ],
            text_extractable_ratio=0.0,
        )

    # OCR / encoding issues
    issues.extend(_build_ocr_issues(full_text, location_prefix="document body"))
    ocr_rate = _ocr_error_rate(full_text)

    # Metadata
    core_props = doc.core_properties
    present = 0
    for field in DOCX_METADATA_FIELDS:
        val = getattr(core_props, field, None)
        if val and str(val).strip():
            present += 1
    metadata_score = present / len(DOCX_METADATA_FIELDS)

    if metadata_score < 0.3:
        issues.append(
            Issue(
                category=IssueCategory.METADATA,
                severity=Severity.INFO,
                message="DOCX metadata (title, author, date) is sparse or missing.",
                suggestion="Fill in document properties via File → Properties in Microsoft Word.",
            )
        )

    # Table detection
    table_count = len(doc.tables)
    structural_score = 1.0
    if table_count > 0:
        issues.append(
            Issue(
                category=IssueCategory.STRUCTURE,
                severity=Severity.WARNING,
                message=f"{table_count} table(s) found. Tables may not chunk well as plain text.",
                suggestion=(
                    "Convert tables to structured formats (Markdown, CSV) "
                    "or use python-docx to extract them separately."
                ),
            )
        )
        structural_score = max(0.6, 1.0 - table_count * 0.05)

    # Heading structure
    headings = [p for p in doc.paragraphs if p.style.name.startswith("Heading")]
    if not headings:
        issues.append(
            Issue(
                category=IssueCategory.STRUCTURE,
                severity=Severity.INFO,
                message="No heading styles detected — document lacks structural hierarchy.",
                suggestion="Add headings to improve chunk boundary detection and retrieval.",
            )
        )

    content_density = _measure_content_density(full_text)

    return _ScanResult(
        issues=issues,
        page_count=0,
        text_extractable_ratio=1.0,
        ocr_error_rate=ocr_rate,
        structural_score=structural_score,
        metadata_score=metadata_score,
        content_density=content_density,
    )


# ---------------------------------------------------------------------------
# TXT scanner
# ---------------------------------------------------------------------------


def _scan_txt(path: Path) -> _ScanResult:
    """Scan a plain-text file.

    Args:
        path: Path to the text file.

    Returns:
        _ScanResult.
    """
    issues: list[Issue] = []

    with open(path, "rb") as fh:
        raw = fh.read(5 * 1024 * 1024)

    encoding, confidence = detect_encoding(raw)

    if encoding_is_suspicious(confidence):
        issues.append(
            Issue(
                category=IssueCategory.ENCODING,
                severity=Severity.WARNING,
                message=(
                    f"Encoding detection confidence is low ({confidence:.0%}). "
                    f"Detected as '{encoding}'."
                ),
                suggestion=(
                    "Explicitly specify the encoding when reading this file. "
                    "Re-save as UTF-8 to avoid issues."
                ),
            )
        )

    try:
        text = raw.decode(encoding or "utf-8", errors="replace")
    except (UnicodeDecodeError, LookupError):
        text = raw.decode("utf-8", errors="replace")

    if not text.strip():
        return _ScanResult(
            issues=[
                Issue(
                    category=IssueCategory.CONTENT,
                    severity=Severity.CRITICAL,
                    message="File is empty.",
                    location=path.name,
                    suggestion="Verify the file has content before including it in your knowledge base.",
                )
            ],
            text_extractable_ratio=0.0,
            structural_score=0.0,
            content_density=0.0,
            ocr_error_rate=1.0,  # Forces score to 0 (empty = 100% error)
            metadata_score=0.0,
        )

    issues.extend(_build_ocr_issues(text))
    ocr_rate = _ocr_error_rate(text)
    content_density = _measure_content_density(text)

    if content_density < 0.2:
        issues.append(
            Issue(
                category=IssueCategory.CONTENT,
                severity=Severity.WARNING,
                message=f"Very low content density ({content_density:.0%}) — file may be mostly whitespace.",
                suggestion="Filter or clean the file before ingestion.",
            )
        )

    return _ScanResult(
        issues=issues,
        text_extractable_ratio=1.0,
        ocr_error_rate=ocr_rate,
        structural_score=1.0,
        metadata_score=0.0,  # No metadata in plain text
        content_density=content_density,
    )


# ---------------------------------------------------------------------------
# CSV/TSV scanner
# ---------------------------------------------------------------------------


def _scan_csv(path: Path) -> _ScanResult:
    """Scan a CSV or TSV file.

    Args:
        path: Path to the CSV/TSV file.

    Returns:
        _ScanResult.
    """
    import csv

    issues: list[Issue] = []

    with open(path, "rb") as fh:
        raw = fh.read(1 * 1024 * 1024)

    encoding, confidence = detect_encoding(raw)

    if encoding_is_suspicious(confidence):
        issues.append(
            Issue(
                category=IssueCategory.ENCODING,
                severity=Severity.WARNING,
                message=f"Encoding detection confidence is low ({confidence:.0%}) for CSV file.",
                suggestion="Re-save the CSV as UTF-8 with BOM or declare encoding explicitly.",
            )
        )

    text = raw.decode(encoding or "utf-8", errors="replace")
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","

    rows: list[list[str]] = []
    try:
        reader = csv.reader(text.splitlines(), delimiter=delimiter)
        rows = list(reader)
    except csv.Error as exc:
        issues.append(
            Issue(
                category=IssueCategory.STRUCTURE,
                severity=Severity.CRITICAL,
                message=f"CSV parsing error: {exc}",
                suggestion="Validate the CSV structure — check for unescaped delimiters or quotes.",
            )
        )

    if not rows:
        return _ScanResult(
            issues=[
                Issue(
                    category=IssueCategory.CONTENT,
                    severity=Severity.CRITICAL,
                    message="CSV file is empty.",
                    suggestion="Verify the file has data rows before ingestion.",
                )
            ],
            text_extractable_ratio=0.0,
        )

    # Check column consistency
    header_len = len(rows[0]) if rows else 0
    inconsistent_rows = [i + 2 for i, row in enumerate(rows[1:]) if len(row) != header_len]

    if inconsistent_rows:
        sample = inconsistent_rows[:5]
        issues.append(
            Issue(
                category=IssueCategory.STRUCTURE,
                severity=Severity.WARNING,
                message=f"Inconsistent column count in {len(inconsistent_rows)} row(s).",
                location=f"rows {sample}",
                suggestion="Fix or remove malformed rows before ingestion.",
            )
        )

    # Missing values in key columns
    if header_len > 0 and len(rows) > 1:
        empty_cells = sum(1 for row in rows[1:] for cell in row if not cell.strip())
        total_cells = sum(len(row) for row in rows[1:])
        empty_rate = empty_cells / max(total_cells, 1)
        if empty_rate > 0.2:
            issues.append(
                Issue(
                    category=IssueCategory.CONTENT,
                    severity=Severity.WARNING,
                    message=f"{empty_rate:.0%} of cells are empty — high sparsity.",
                    suggestion="Fill or remove sparse rows/columns before ingestion.",
                )
            )

    structural_score = 1.0 - (len(inconsistent_rows) / max(len(rows), 1))
    content_density = _measure_content_density(" ".join(cell for row in rows for cell in row))

    return _ScanResult(
        issues=issues,
        text_extractable_ratio=1.0,
        ocr_error_rate=0.0,
        structural_score=clamp(structural_score),
        metadata_score=0.0,
        content_density=content_density,
    )


# ---------------------------------------------------------------------------
# HTML scanner
# ---------------------------------------------------------------------------


def _scan_html(path: Path) -> _ScanResult:
    """Scan an HTML document.

    Args:
        path: Path to the HTML file.

    Returns:
        _ScanResult.
    """
    try:
        from bs4 import BeautifulSoup  # type: ignore[import]
    except ImportError:
        raise ImportError(
            "beautifulsoup4 is required for HTML scanning. Install with: pip install beautifulsoup4"
        ) from None

    issues: list[Issue] = []

    text, encoding = safe_read_text(path)

    try:
        soup = BeautifulSoup(text, "html.parser")
    except Exception as exc:
        raise ValueError(f"Cannot parse HTML '{path.name}': {exc}") from exc

    # Remove boilerplate elements
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()

    body_text = soup.get_text(separator=" ", strip=True)

    if not body_text.strip():
        return _ScanResult(
            issues=[
                Issue(
                    category=IssueCategory.CONTENT,
                    severity=Severity.CRITICAL,
                    message="HTML yields no meaningful text after removing boilerplate.",
                    suggestion="Verify the page has actual content in the body.",
                )
            ],
            text_extractable_ratio=0.0,
        )

    # Boilerplate ratio
    raw_text_len = len(soup.get_text())
    clean_text_len = len(body_text)
    boilerplate_ratio = 1.0 - (clean_text_len / max(raw_text_len, 1))

    if boilerplate_ratio > 0.5:
        issues.append(
            Issue(
                category=IssueCategory.CONTENT,
                severity=Severity.WARNING,
                message=f"High boilerplate ratio ({boilerplate_ratio:.0%}) — page is mostly nav/scripts.",
                suggestion=(
                    "Extract the main content area (e.g. <article>, <main>) before ingestion. "
                    "Consider trafilatura or newspaper3k for web content extraction."
                ),
            )
        )

    # Metadata
    title = soup.find("title")
    meta_desc = soup.find("meta", attrs={"name": "description"})
    metadata_score = (0.5 if title and title.get_text().strip() else 0.0) + (
        0.5 if meta_desc else 0.0
    )

    if metadata_score < 0.5:
        issues.append(
            Issue(
                category=IssueCategory.METADATA,
                severity=Severity.INFO,
                message="HTML is missing <title> or meta description.",
                suggestion="Add <title> and <meta name='description'> for better retrieval context.",
            )
        )

    # Heading structure
    headings = soup.find_all(re.compile(r"^h[1-6]$"))
    if not headings:
        issues.append(
            Issue(
                category=IssueCategory.STRUCTURE,
                severity=Severity.INFO,
                message="No heading elements found — document lacks structural hierarchy.",
                suggestion="Add <h1>–<h6> elements to improve chunk boundary detection.",
            )
        )

    issues.extend(_build_ocr_issues(body_text))
    ocr_rate = _ocr_error_rate(body_text)
    content_density = _measure_content_density(body_text)

    return _ScanResult(
        issues=issues,
        text_extractable_ratio=1.0 - boilerplate_ratio,
        ocr_error_rate=ocr_rate,
        structural_score=0.8 if headings else 0.5,
        metadata_score=metadata_score,
        content_density=content_density,
    )


# ---------------------------------------------------------------------------
# Markdown scanner
# ---------------------------------------------------------------------------


def _scan_markdown(path: Path) -> _ScanResult:
    """Scan a Markdown document.

    Args:
        path: Path to the Markdown file.

    Returns:
        _ScanResult.
    """
    issues: list[Issue] = []
    text, encoding = safe_read_text(path)

    if not text.strip():
        return _ScanResult(
            issues=[
                Issue(
                    category=IssueCategory.CONTENT,
                    severity=Severity.CRITICAL,
                    message="Markdown file is empty.",
                    suggestion="Verify the file has content.",
                )
            ],
            text_extractable_ratio=0.0,
        )

    lines = text.splitlines()

    # Heading hierarchy analysis
    headings = [ln for ln in lines if re.match(r"^#{1,6}\s", ln)]
    if not headings:
        issues.append(
            Issue(
                category=IssueCategory.STRUCTURE,
                severity=Severity.INFO,
                message="No Markdown headings found — document lacks structural hierarchy.",
                suggestion=(
                    "Add ATX headings (# H1, ## H2) to create clear section boundaries "
                    "for better chunking."
                ),
            )
        )
    else:
        # Check for skipped heading levels (h1 → h3 with no h2)
        levels = [len(re.match(r"^(#+)", h).group(1)) for h in headings]  # type: ignore[union-attr]
        for i in range(1, len(levels)):
            if levels[i] - levels[i - 1] > 1:
                issues.append(
                    Issue(
                        category=IssueCategory.STRUCTURE,
                        severity=Severity.INFO,
                        message=f"Heading level jumps from h{levels[i - 1]} to h{levels[i]} — skipped levels.",
                        suggestion="Use sequential heading levels to improve navigation and chunking.",
                    )
                )
                break  # Report once

    # Broken links / images (basic check)
    broken_imgs = re.findall(r"!\[([^\]]*)\]\(\s*\)", text)
    if broken_imgs:
        issues.append(
            Issue(
                category=IssueCategory.STRUCTURE,
                severity=Severity.WARNING,
                message=f"{len(broken_imgs)} image reference(s) with empty paths.",
                suggestion="Fix or remove broken image references.",
            )
        )

    issues.extend(_build_ocr_issues(text))
    ocr_rate = _ocr_error_rate(text)
    content_density = _measure_content_density(text)

    structural_score = 0.9 if headings else 0.6

    return _ScanResult(
        issues=issues,
        text_extractable_ratio=1.0,
        ocr_error_rate=ocr_rate,
        structural_score=structural_score,
        metadata_score=0.0,
        content_density=content_density,
    )


# ---------------------------------------------------------------------------
# Shared cross-format checks: PII, formula, language
# ---------------------------------------------------------------------------


def _mask_pii(value: str, pii_type: str) -> str:
    """Partially mask a PII value so it can be shown safely in reports."""
    if pii_type == "email":
        parts = value.split("@", 1)
        if len(parts) == 2:
            local = parts[0][:3] + "***" if len(parts[0]) > 3 else parts[0]
            return f"{local}@{parts[1]}"
    elif pii_type == "us_ssn":
        return "***-**-" + value[-4:]
    elif pii_type == "credit_card":
        digits = re.sub(r"\D", "", value)
        return "**** **** **** " + digits[-4:] if len(digits) >= 4 else "****"
    elif pii_type == "us_phone":
        digits = re.sub(r"\D", "", value)
        if len(digits) >= 10:
            return f"({digits[:3]}) {digits[3:6]}-****"
    elif pii_type == "ip_address":
        parts = value.split(".")
        if len(parts) == 4:
            return f"{parts[0]}.{parts[1]}.***.***"
    return value[:4] + "***"


def _detect_pii_issues(text: str) -> list[Issue]:
    """Scan text for PII patterns; include masked sample values in context.

    Args:
        text: Extracted document text.

    Returns:
        List of Issues (empty if no PII detected above threshold).
    """
    issues: list[Issue] = []
    for pii_type, pattern in PII_PATTERNS.items():
        try:
            hits = re.findall(pattern, text)
            if len(hits) >= PII_MIN_HITS.get(pii_type, 1):
                samples = [_mask_pii(h, pii_type) for h in hits[:3]]
                issues.append(
                    Issue(
                        category=IssueCategory.CONTENT,
                        severity=Severity.WARNING,
                        message=(
                            f"Possible PII: {len(hits)} {pii_type.replace('_', ' ')} "
                            f"value(s) found."
                        ),
                        context=f"e.g. {' · '.join(samples)}",
                        suggestion=(
                            f"Review whether {pii_type.replace('_', ' ')} values should be "
                            f"redacted before ingestion into your RAG corpus."
                        ),
                    )
                )
        except re.error:
            pass
    return issues


def _detect_pii_issues_paged(page_texts: list[tuple[int, str]]) -> list[Issue]:
    """PII detection with page-level granularity, used by the PDF scanner.

    Args:
        page_texts: List of (page_number, text) tuples.

    Returns:
        List of Issues grouped by PII type with page locations and masked samples.
    """
    issues: list[Issue] = []
    for pii_type, pattern in PII_PATTERNS.items():
        all_hits: list[str] = []
        pages_with_hits: list[int] = []
        try:
            for page_num, text in page_texts:
                hits = re.findall(pattern, text)
                if hits:
                    all_hits.extend(hits)
                    pages_with_hits.append(page_num)
        except re.error:
            continue

        if not all_hits or len(all_hits) < PII_MIN_HITS.get(pii_type, 1):
            continue

        samples = [_mask_pii(h, pii_type) for h in all_hits[:3]]

        if len(pages_with_hits) <= 5:
            loc = f"pages {', '.join(str(p) for p in pages_with_hits)}"
        else:
            shown = ", ".join(str(p) for p in pages_with_hits[:5])
            loc = f"pages {shown} (+{len(pages_with_hits) - 5} more)"

        issues.append(
            Issue(
                category=IssueCategory.CONTENT,
                severity=Severity.WARNING,
                message=(
                    f"Possible PII: {len(all_hits)} {pii_type.replace('_', ' ')} "
                    f"value(s) across {len(pages_with_hits)} page(s)."
                ),
                location=loc,
                context=f"e.g. {' · '.join(samples)}",
                suggestion=(
                    f"Review whether {pii_type.replace('_', ' ')} values should be "
                    f"redacted before ingestion into your RAG corpus."
                ),
            )
        )
    return issues


def _detect_formula_issues(text: str) -> list[Issue]:
    """Check whether a document is math/formula-heavy; include example snippets.

    Args:
        text: Extracted document text.

    Returns:
        List of Issues (empty if no formula density detected).
    """
    total_hits = 0
    snippets: list[str] = []
    for pattern in FORMULA_PATTERNS:
        try:
            matches = list(re.finditer(pattern, text))
            if matches:
                total_hits += len(matches)
                if len(snippets) < 3:
                    m = matches[0]
                    start = max(0, m.start() - 20)
                    end = min(len(text), m.end() + 20)
                    raw = text[start:end].replace("\n", " ").strip()
                    snippets.append(f'"{raw}"')
        except re.error:
            pass
    if total_hits >= FORMULA_MIN_HITS:
        return [
            Issue(
                category=IssueCategory.CONTENT,
                severity=Severity.WARNING,
                message=(
                    f"Math-heavy document detected ({total_hits} formula pattern hits). "
                    f"Equations often extract as garbled text from PDFs."
                ),
                context=" · ".join(snippets) if snippets else None,
                suggestion=(
                    "Consider converting equations to plain-language descriptions, or use "
                    "a math-aware parser (e.g. MathPix, GROBID) before ingestion."
                ),
            )
        ]
    return []


def _detect_formula_issues_paged(page_texts: list[tuple[int, str]]) -> list[Issue]:
    """Formula detection with page-level granularity, used by the PDF scanner.

    Args:
        page_texts: List of (page_number, text) tuples.

    Returns:
        List of Issues with page locations and example formula snippets.
    """
    total_hits = 0
    pages_with_formulas: list[int] = []
    snippets: list[str] = []

    for page_num, text in page_texts:
        page_hits = 0
        for pattern in FORMULA_PATTERNS:
            try:
                matches = list(re.finditer(pattern, text))
                if matches:
                    page_hits += len(matches)
                    if len(snippets) < 3:
                        m = matches[0]
                        start = max(0, m.start() - 20)
                        end = min(len(text), m.end() + 20)
                        raw = text[start:end].replace("\n", " ").strip()
                        snippets.append(f'"{raw}"')
            except re.error:
                pass
        if page_hits > 0:
            total_hits += page_hits
            pages_with_formulas.append(page_num)

    if total_hits < FORMULA_MIN_HITS:
        return []

    if len(pages_with_formulas) <= 5:
        loc = f"pages {', '.join(str(p) for p in pages_with_formulas)}"
    else:
        shown = ", ".join(str(p) for p in pages_with_formulas[:5])
        loc = f"pages {shown} (+{len(pages_with_formulas) - 5} more)"

    return [
        Issue(
            category=IssueCategory.CONTENT,
            severity=Severity.WARNING,
            message=(
                f"Math-heavy document: {total_hits} formula hits across "
                f"{len(pages_with_formulas)} page(s). Equations often extract as garbled text."
            ),
            location=loc,
            context=" · ".join(snippets) if snippets else None,
            suggestion=(
                "Consider converting equations to plain-language descriptions, or use "
                "a math-aware parser (e.g. MathPix, GROBID) before ingestion."
            ),
        )
    ]


def _detect_language(text: str) -> str | None:
    """Detect the primary language of the text.

    Args:
        text: Text sample.

    Returns:
        ISO 639-1 language code (e.g. 'en', 'de') or None on failure.
    """
    if len(text) < LANGUAGE_DETECTION_MIN_CHARS:
        return None
    try:
        from langdetect import detect  # type: ignore[import]

        return detect(text[:LANGUAGE_DETECTION_SAMPLE_CHARS])
    except Exception:
        return None


def _build_language_issue(lang: str | None, expected: str = "en") -> Issue | None:
    """Return an issue if the detected language differs from the expected language.

    Args:
        lang: Detected ISO 639-1 code, or None.
        expected: Expected language code.

    Returns:
        Issue if language mismatch, else None.
    """
    if lang is None or lang == expected:
        return None
    return Issue(
        category=IssueCategory.CONTENT,
        severity=Severity.INFO,
        message=f"Document language detected as '{lang}' (expected '{expected}').",
        suggestion=(
            "Ensure your embedding model supports this language. "
            "Mixed-language corpora may need separate embedding spaces."
        ),
    )


# ---------------------------------------------------------------------------
# PowerPoint scanner
# ---------------------------------------------------------------------------


def _scan_pptx(path: Path) -> _ScanResult:
    """Scan a PowerPoint (.pptx) presentation.

    Args:
        path: Path to the PPTX file.

    Returns:
        _ScanResult.
    """
    try:
        from pptx import Presentation  # type: ignore[import]
    except ImportError:
        raise ImportError(
            "python-pptx is required for PowerPoint scanning. "
            "Install it with: pip install python-pptx"
        ) from None

    issues: list[Issue] = []

    try:
        prs = Presentation(str(path))
    except Exception as exc:
        raise ValueError(f"Cannot open PPTX '{path.name}': {exc}") from exc

    slides = list(prs.slides)
    slide_count = len(slides)

    if slide_count == 0:
        return _ScanResult(
            issues=[
                Issue(
                    category=IssueCategory.CONTENT,
                    severity=Severity.CRITICAL,
                    message="Presentation has no slides.",
                    suggestion="Verify the source file.",
                )
            ],
            page_count=0,
            text_extractable_ratio=0.0,
        )

    all_text_parts: list[str] = []
    slides_with_text = 0
    empty_slides: list[int] = []

    for idx, slide in enumerate(slides, start=1):
        slide_text_parts: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    t = " ".join(run.text for run in para.runs if run.text)
                    if t.strip():
                        slide_text_parts.append(t)
            # Speaker notes
            if slide.has_notes_slide:
                notes_tf = slide.notes_slide.notes_text_frame
                for para in notes_tf.paragraphs:
                    t = para.text.strip()
                    if t:
                        slide_text_parts.append(t)

        slide_text = "\n".join(slide_text_parts)
        all_text_parts.append(slide_text)
        word_count = len(slide_text.split())
        if word_count >= EMPTY_PAGE_WORD_THRESHOLD:
            slides_with_text += 1
        elif word_count == 0:
            empty_slides.append(idx)

    if empty_slides:
        issues.append(
            Issue(
                category=IssueCategory.CONTENT,
                severity=Severity.INFO,
                message=(
                    f"{len(empty_slides)} slide(s) have no extractable text "
                    f"(may be image-only or decorative)."
                ),
                location=f"slides {empty_slides[:5]}",
                suggestion=(
                    "Add alt-text to image-only slides or ensure key content is in text boxes."
                ),
            )
        )

    full_text = "\n".join(all_text_parts)
    extractable_ratio = slides_with_text / slide_count if slide_count else 0.0

    if extractable_ratio < 0.5:
        issues.append(
            Issue(
                category=IssueCategory.CONTENT,
                severity=Severity.WARNING,
                message=(
                    f"Only {extractable_ratio:.0%} of slides have extractable text. "
                    f"This presentation may be image-heavy."
                ),
                suggestion=("Use a vision model to caption image-only slides before ingestion."),
            )
        )

    # Check core properties metadata
    props = prs.core_properties
    meta_fields_present = sum(
        [
            bool(props.title),
            bool(props.author),
            bool(props.subject),
            bool(getattr(props, "created", None)),
        ]
    )
    metadata_score = meta_fields_present / 4.0
    if metadata_score < 0.5:
        issues.append(
            Issue(
                category=IssueCategory.METADATA,
                severity=Severity.INFO,
                message="Presentation is missing core metadata (title, author, subject, date).",
                suggestion="Add document properties in PowerPoint → File → Info → Properties.",
            )
        )

    issues.extend(_build_ocr_issues(full_text))
    issues.extend(_detect_pii_issues(full_text))
    lang = _detect_language(full_text)
    lang_issue = _build_language_issue(lang)
    if lang_issue:
        issues.append(lang_issue)

    ocr_rate = _ocr_error_rate(full_text)
    content_density = _measure_content_density(full_text)

    return _ScanResult(
        issues=issues,
        page_count=slide_count,
        text_extractable_ratio=extractable_ratio,
        ocr_error_rate=ocr_rate,
        structural_score=0.85,
        metadata_score=metadata_score,
        content_density=content_density,
    )


# ---------------------------------------------------------------------------
# Excel scanner
# ---------------------------------------------------------------------------


def _scan_xlsx(path: Path) -> _ScanResult:
    """Scan an Excel (.xlsx) workbook.

    Note: legacy binary .xls files are NOT supported — openpyxl only handles .xlsx.
    Files with a .xls extension will be rejected at the format-detection step.

    Args:
        path: Path to the Excel file.

    Returns:
        _ScanResult.
    """
    try:
        import openpyxl  # type: ignore[import]
    except ImportError:
        raise ImportError(
            "openpyxl is required for Excel scanning. Install it with: pip install openpyxl"
        ) from None

    issues: list[Issue] = []

    try:
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    except Exception as exc:
        raise ValueError(f"Cannot open Excel file '{path.name}': {exc}") from exc

    sheet_names = wb.sheetnames
    if not sheet_names:
        wb.close()
        return _ScanResult(
            issues=[
                Issue(
                    category=IssueCategory.CONTENT,
                    severity=Severity.CRITICAL,
                    message="Workbook has no sheets.",
                    suggestion="Verify the source file.",
                )
            ],
            text_extractable_ratio=0.0,
        )

    all_text_parts: list[str] = []
    sheets_with_data = 0
    total_rows = 0
    column_counts: list[int] = []

    for sheet_name in sheet_names:
        ws = wb[sheet_name]
        sheet_rows: list[list[str]] = []
        row_count = 0
        col_widths: list[int] = []

        for row in ws.iter_rows(values_only=True):
            cells = [str(c) if c is not None else "" for c in row]
            non_empty = [c for c in cells if c and c.strip() not in ("None", "")]
            if non_empty:
                sheet_rows.append(cells)
                col_widths.append(len(cells))
                row_count += 1

        total_rows += row_count
        if row_count > 0:
            sheets_with_data += 1
            all_text_parts.extend(" ".join(r) for r in sheet_rows[:500])  # cap per sheet
            column_counts.extend(col_widths)

            # Detect inconsistent column counts (structural issue)
            if col_widths:
                max_cols = max(col_widths)
                min_cols = min(col_widths)
                if max_cols - min_cols > max(2, max_cols * 0.2):
                    issues.append(
                        Issue(
                            category=IssueCategory.STRUCTURE,
                            severity=Severity.WARNING,
                            message=(
                                f"Sheet '{sheet_name}' has inconsistent column counts "
                                f"(min {min_cols}, max {max_cols} columns per row)."
                            ),
                            suggestion=(
                                "Ensure all rows have the same number of columns before "
                                "ingestion. Ragged tables confuse chunk boundaries."
                            ),
                        )
                    )
        else:
            issues.append(
                Issue(
                    category=IssueCategory.CONTENT,
                    severity=Severity.INFO,
                    message=f"Sheet '{sheet_name}' is empty.",
                    suggestion="Remove empty sheets to reduce noise in your RAG corpus.",
                )
            )

    wb.close()

    extractable_ratio = sheets_with_data / len(sheet_names) if sheet_names else 0.0
    full_text = "\n".join(all_text_parts)

    if total_rows == 0:
        return _ScanResult(
            issues=[
                Issue(
                    category=IssueCategory.CONTENT,
                    severity=Severity.CRITICAL,
                    message="Workbook contains no data rows across all sheets.",
                    suggestion="Verify the file has content before ingestion.",
                )
            ],
            text_extractable_ratio=0.0,
        )

    issues.extend(_detect_pii_issues(full_text))
    lang = _detect_language(full_text)
    lang_issue = _build_language_issue(lang)
    if lang_issue:
        issues.append(lang_issue)

    content_density = _measure_content_density(full_text)

    return _ScanResult(
        issues=issues,
        page_count=len(sheet_names),
        text_extractable_ratio=extractable_ratio,
        ocr_error_rate=0.0,  # Excel text is always machine-generated
        structural_score=0.9 if not issues else 0.7,
        metadata_score=0.3,  # Excel rarely has rich metadata
        content_density=content_density,
    )


# ---------------------------------------------------------------------------
# Jupyter Notebook scanner
# ---------------------------------------------------------------------------


def _scan_ipynb(path: Path) -> _ScanResult:
    """Scan a Jupyter Notebook (.ipynb) file.

    Args:
        path: Path to the notebook file.

    Returns:
        _ScanResult.
    """
    import json as _json

    issues: list[Issue] = []

    try:
        raw = path.read_bytes()
        nb = _json.loads(raw)
    except Exception as exc:
        raise ValueError(f"Cannot parse notebook '{path.name}': {exc}") from exc

    cells = nb.get("cells", [])
    if not cells:
        return _ScanResult(
            issues=[
                Issue(
                    category=IssueCategory.CONTENT,
                    severity=Severity.CRITICAL,
                    message="Notebook has no cells.",
                    suggestion="Verify the file is a valid Jupyter notebook.",
                )
            ],
            text_extractable_ratio=0.0,
        )

    markdown_texts: list[str] = []
    code_texts: list[str] = []
    cells_with_output = 0

    for cell in cells:
        cell_type = cell.get("cell_type", "")
        source = "".join(cell.get("source", []))
        outputs = cell.get("outputs", [])

        if cell_type == "markdown":
            markdown_texts.append(source)
        elif cell_type == "code":
            code_texts.append(source)
            if outputs:
                cells_with_output += 1

    if not markdown_texts and not code_texts:
        return _ScanResult(
            issues=[
                Issue(
                    category=IssueCategory.CONTENT,
                    severity=Severity.CRITICAL,
                    message="Notebook cells contain no text content.",
                    suggestion="Ensure cells have source content before ingestion.",
                )
            ],
            text_extractable_ratio=0.0,
        )

    code_cells = [c for c in cells if c.get("cell_type") == "code"]
    if code_cells and cells_with_output == 0:
        issues.append(
            Issue(
                category=IssueCategory.CONTENT,
                severity=Severity.WARNING,
                message=f"Notebook has {len(code_cells)} code cells but no outputs.",
                suggestion=(
                    "Run the notebook (Kernel → Restart & Run All) and save with outputs "
                    "so retrievers can match queries against computed results."
                ),
            )
        )

    # Notebooks with mostly code and little markdown are hard to retrieve
    total_text_cells = len(markdown_texts)
    total_code_cells = len(code_texts)
    if total_code_cells > 0 and total_text_cells == 0:
        issues.append(
            Issue(
                category=IssueCategory.STRUCTURE,
                severity=Severity.WARNING,
                message="Notebook has no markdown cells — code-only notebooks are hard to retrieve.",
                suggestion=(
                    "Add markdown cells explaining the purpose of each code section to "
                    "improve retrieval quality."
                ),
            )
        )

    full_text = "\n".join(markdown_texts + code_texts)
    issues.extend(_build_ocr_issues("\n".join(markdown_texts)))  # OCR checks on prose only
    issues.extend(_detect_pii_issues(full_text))
    lang = _detect_language("\n".join(markdown_texts))
    lang_issue = _build_language_issue(lang)
    if lang_issue:
        issues.append(lang_issue)

    content_density = _measure_content_density(full_text)
    structural_score = min(1.0, 0.5 + 0.5 * (total_text_cells / max(total_code_cells, 1)))

    return _ScanResult(
        issues=issues,
        page_count=len(cells),
        text_extractable_ratio=1.0,
        ocr_error_rate=0.0,
        structural_score=structural_score,
        metadata_score=0.3,
        content_density=content_density,
    )


# ---------------------------------------------------------------------------
# Transcript scanner (SRT / VTT)
# ---------------------------------------------------------------------------


def _scan_transcript(path: Path) -> _ScanResult:
    """Scan a subtitle/transcript file (.srt or .vtt).

    Args:
        path: Path to the transcript file.

    Returns:
        _ScanResult.
    """
    issues: list[Issue] = []
    text, encoding = safe_read_text(path)

    if not text.strip():
        return _ScanResult(
            issues=[
                Issue(
                    category=IssueCategory.CONTENT,
                    severity=Severity.CRITICAL,
                    message="Transcript file is empty.",
                    suggestion="Verify the transcript was generated correctly.",
                )
            ],
            text_extractable_ratio=0.0,
        )

    is_vtt = path.suffix.lower() == ".vtt"

    # Extract just the spoken text, stripping timestamps and sequence numbers
    if is_vtt:
        # VTT: lines after "WEBVTT" header, skip timestamp lines (contain "-->")
        lines = text.splitlines()
        spoken_lines = [
            ln
            for ln in lines
            if ln.strip()
            and "-->" not in ln
            and ln.strip() != "WEBVTT"
            and not re.match(r"^\d+$", ln.strip())
            and not re.match(r"^NOTE", ln.strip())
        ]
    else:
        # SRT: skip sequence numbers and timestamp lines
        lines = text.splitlines()
        spoken_lines = [
            ln
            for ln in lines
            if ln.strip() and "-->" not in ln and not re.match(r"^\d+$", ln.strip())
        ]

    spoken_text = "\n".join(spoken_lines)
    word_count = len(spoken_text.split())

    if word_count < 50:
        issues.append(
            Issue(
                category=IssueCategory.CONTENT,
                severity=Severity.WARNING,
                message=f"Very short transcript ({word_count} words) — may be truncated.",
                suggestion="Verify the full transcript was captured.",
            )
        )

    # Count malformed timestamps
    fmt = "vtt" if is_vtt else "srt"
    if fmt == "srt":
        ts_pattern = r"\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}"
    else:
        ts_pattern = r"\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}"

    valid_timestamps = len(re.findall(ts_pattern, text))
    malformed_ts = len(re.findall(r"-->", text)) - valid_timestamps
    if malformed_ts > 0:
        issues.append(
            Issue(
                category=IssueCategory.STRUCTURE,
                severity=Severity.WARNING,
                message=f"{malformed_ts} malformed timestamp line(s) detected.",
                suggestion=(
                    "Re-export the transcript from your transcription tool "
                    "to ensure valid timestamp formatting."
                ),
            )
        )

    issues.extend(_build_ocr_issues(spoken_text))
    issues.extend(_detect_pii_issues(spoken_text))
    lang = _detect_language(spoken_text)
    lang_issue = _build_language_issue(lang)
    if lang_issue:
        issues.append(lang_issue)

    ocr_rate = _ocr_error_rate(spoken_text)
    content_density = _measure_content_density(spoken_text)

    return _ScanResult(
        issues=issues,
        page_count=0,
        text_extractable_ratio=1.0,
        ocr_error_rate=ocr_rate,
        structural_score=1.0 if malformed_ts == 0 else 0.7,
        metadata_score=0.0,
        content_density=content_density,
    )


# ---------------------------------------------------------------------------
# Format scanner dispatch table
# ---------------------------------------------------------------------------

_FORMAT_SCANNERS: dict[str, Callable[..., _ScanResult]] = {
    "pdf": _scan_pdf,
    "docx": _scan_docx,
    "txt": _scan_txt,
    "csv": _scan_csv,
    "tsv": _scan_csv,
    "html": _scan_html,
    "markdown": _scan_markdown,
    "pptx": _scan_pptx,
    "xlsx": _scan_xlsx,
    "ipynb": _scan_ipynb,
    "srt": _scan_transcript,
    "vtt": _scan_transcript,
}


# ---------------------------------------------------------------------------
# Issue sorting helper
# ---------------------------------------------------------------------------

_SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.INFO: 2}


def _sort_issues(issues: list[Issue]) -> list[Issue]:
    """Sort issues by severity (critical first).

    Args:
        issues: Unsorted list of Issue objects.

    Returns:
        New list sorted by severity then category.
    """
    return sorted(issues, key=lambda i: (_SEVERITY_ORDER[i.severity], i.category.value))
