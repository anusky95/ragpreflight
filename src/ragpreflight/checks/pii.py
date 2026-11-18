"""PII and secrets that would become retrievable (and quotable) once indexed. F23 / F25."""

from __future__ import annotations

import re
from pathlib import Path

from ..models import Document, Finding, Severity
from . import rel, where_in

NAME = "pii"

ROLE_MAILBOXES = {
    "info", "support", "help", "contact", "sales", "admin", "noreply", "no-reply", "hello",
    "team", "press", "media", "privacy", "security", "billing", "careers", "jobs", "office",
    "service", "customerservice", "feedback", "webmaster", "legal", "hr", "marketing",
}
EXAMPLE_DOMAINS = ("example.com", "example.org", "example.net", "domain.com", "email.com")

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# Phone numbers need real phone punctuation: "415-555-0187", "(415) 555-0187", "415.555.0187",
# or a leading "+". Space-separated digit runs are chart axes and table rows, not phones.
PHONE = re.compile(
    r"(?<![\w.-])(?:\+?1[ .-]?)?(?:\([2-9]\d{2}\)\s?|[2-9]\d{2}[-.])\d{3}[-.]\d{4}(?![\w-])"
    r"|(?<![\w.+])\+[1-9]\d{0,2}[ .-]?\(?\d{1,4}\)?(?:[ .-]\d{2,4}){2,4}(?![\w.-])")
SSN = re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")
# Real card prefixes, 15-16 digits, one consistent separator (or none). Tables of numbers that
# happen to pass Luhn were the main false positive on research papers.
CARD = re.compile(r"(?<![\d.,-])(?:4\d{3}|5[1-5]\d{2}|2[2-7]\d{2}|6011|3[47]\d{2})([ -]?)"
                  r"\d{4}\1\d{4}\1\d{3,4}(?![\d.,-])")
NUMBERS = re.compile(r"\d+(?:[.,]\d+)*")


def _line_at(text: str, m: re.Match) -> str:
    start = text.rfind("\n", 0, m.start()) + 1
    end = text.find("\n", m.end())
    return text[start: end if end != -1 else len(text)]


def _in_number_row(text: str, m: re.Match) -> bool:
    """True when the match sits in a row of other numbers (a table), not in prose."""
    return len(NUMBERS.findall(_line_at(text, m))) - len(NUMBERS.findall(m.group(0))) >= 4

SECRETS = [
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("OpenAI / Anthropic API key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("Password in text", re.compile(r"(?i)\b(?:password|passwd|pwd)\s*[:=]\s*(?!\*{3,})[^\s,;)\]}'\"]{6,}")),
]


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _mask(s: str) -> str:
    s = s.strip()
    if "@" in s:
        user, dom = s.split("@", 1)
        return f"{user[:1]}***@{dom}"
    keep = re.sub(r"\D", "", s)
    return f"***{keep[-4:]}" if len(keep) >= 4 else "***"


def find_all(text: str) -> list[tuple[str, re.Match]]:
    """Every PII / secret match in one text, as (kind, match), in text order."""
    found: list[tuple[str, re.Match]] = []
    for m in EMAIL.finditer(text):
        user, dom = m.group(0).lower().split("@", 1)
        if user not in ROLE_MAILBOXES and not dom.endswith(EXAMPLE_DOMAINS):
            found.append(("email address", m))
    for m in SSN.finditer(text):
        found.append(("US SSN", m))
    for m in CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if _luhn(digits) and len(set(digits)) > 1 and not _in_number_row(text, m):
            found.append(("payment card number", m))
    for m in PHONE.finditer(text):
        if not _in_number_row(text, m):
            found.append(("phone number", m))
    for label, rx in SECRETS:
        for m in rx.finditer(text):
            found.append((label, m))
    found.sort(key=lambda km: km[1].start())
    return found


def scan_text(text: str) -> dict[str, list[str]]:
    """Distinct values per kind (kept for API compatibility)."""
    hits: dict[str, list[str]] = {}
    for kind, m in find_all(text):
        hits.setdefault(kind, [])
        if m.group(0) not in hits[kind]:
            hits[kind].append(m.group(0))
    return hits


def _mask_value(kind: str, val: str) -> str:
    if kind == "Password in text":  # keep "Password:" so the reader can find it, hide the value
        k, _, v = re.split(r"([:=])", val, maxsplit=1) if re.search(r"[:=]", val) else (val, "", "")
        return f"{k}{_}{' ' if v.startswith(' ') else ''}{'*' * 6}"
    if kind in [s[0] for s in SECRETS]:
        return val[:4] + "*" * 8
    return _mask(val)


def _excerpt(text: str, kind: str, m: re.Match, width: int = 60) -> str:
    """The line around a match, with this match (and any other PII on the line) masked."""
    start = max(text.rfind("\n", 0, m.start()) + 1, m.start() - width)
    end_nl = text.find("\n", m.end())
    end = min(end_nl if end_nl != -1 else len(text), m.end() + width)
    piece = text[start:end]
    for k2, m2 in sorted(find_all(piece), key=lambda km: -km[1].start()):
        piece = piece[:m2.start()] + _mask_value(k2, m2.group(0)) + piece[m2.end():]
    piece = re.sub(r"\s+", " ", piece).strip()
    return ("…" if start > 0 else "") + piece + ("…" if end < len(text) else "")


MAX_LISTED = 10


SEVERE = {"US SSN", "payment card number"}
CONTACTS_WARN = 5  # distinct emails + phones in one document before it becomes a warning


def _plural(n: int, noun: str) -> str:
    if n == 1:
        return f"1 {noun}"
    return f"{n} {noun}es" if noun.endswith("ss") else f"{n} {noun}s"


def run(docs: list[Document], root: Path) -> list[Finding]:
    out: list[Finding] = []
    secret_kinds = {k for k, _ in SECRETS}
    for doc in docs:
        hits: list[tuple[str, str, str, str]] = []  # (kind, value, location, masked excerpt)
        for page in doc.pages:
            text = page.text or ""
            for kind, m in find_all(text):
                hits.append((kind, m.group(0), where_in(doc, m.start(), text, page.number),
                             _excerpt(text, kind, m)))
        if not hits:
            continue
        for is_secret in (True, False):
            group = [h for h in hits if (h[0] in secret_kinds) == is_secret]
            if not group:
                continue
            distinct: dict[str, set[str]] = {}
            for kind, val, _, _ in group:
                distinct.setdefault(kind, set()).add(val)
            kinds = ", ".join(_plural(len(v), k) for k, v in distinct.items())
            locs = []
            for _, _, loc, _ in group:
                if loc not in locs:
                    locs.append(loc)
            location = "; ".join(locs[:3]) + (f" (+{len(locs) - 3} more)" if len(locs) > 3 else "")
            lines = [f"{loc}: {kind}: {ex}" for kind, _, loc, ex in group[:MAX_LISTED]]
            if len(group) > MAX_LISTED:
                lines.append(f"… and {len(group) - MAX_LISTED} more occurrence(s).")
            detail = "Where (values masked):\n" + "\n".join(lines)
            if is_secret:
                out.append(Finding(
                    "secret", Severity.ERROR, ["F23", "F25"], f"Credentials in a document: {kinds}",
                    rel(doc.path, root), location=location, detail=detail,
                    fix="Remove these credentials at the locations listed, rotate them, then re-scan. "
                        "Anything indexed can be retrieved and quoted to any user."))
            else:
                contacts = sum(len(v) for k, v in distinct.items() if k in ("email address", "phone number"))
                sev = (Severity.ERROR if SEVERE & distinct.keys()
                       else Severity.WARN if (set(distinct) - {"email address", "phone number"}
                                              or contacts >= CONTACTS_WARN)
                       else Severity.INFO)
                out.append(Finding(
                    "pii", sev, ["F23"], f"Personal data: {kinds}", rel(doc.path, root),
                    location=location, detail=detail,
                    fix="Redact at the locations listed before indexing, or restrict this document to "
                        "users allowed to see it (enforce at retrieval time, not in the prompt)."))
    return out
