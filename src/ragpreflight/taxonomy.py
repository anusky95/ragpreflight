"""The 33 failure modes from Garani (2026), "A Systematic Taxonomy of Failure Modes in
Retrieval-Augmented Generation Systems", ACL TrustNLP 2026.
https://aclanthology.org/2026.trustnlp-main.27/
"""

from __future__ import annotations

PAPER_URL = "https://aclanthology.org/2026.trustnlp-main.27/"

# id: (stage, name, evidence grade)
MODES: dict[str, tuple[str, str, str]] = {
    "F1": ("Ingestion", "Stale / outdated data", "Strong"),
    "F2": ("Ingestion", "Missing / incomplete documents", "Moderate"),
    "F3": ("Ingestion", "Layout parsing errors", "Strong"),
    "F4": ("Ingestion", "Multimodality conversion loss", "Moderate"),
    "F5": ("Representation", "Tokenization fragmentation", "Limited"),
    "F6": ("Representation", "Embedding drift / model mismatch", "Limited"),
    "F7": ("Retrieval", "Chunking boundary errors", "Strong"),
    "F8": ("Retrieval", "Domain embedding mismatch", "Strong"),
    "F9": ("Retrieval", "Multi-hop reasoning gaps", "Strong"),
    "F10": ("Retrieval", "Graph RAG trade-offs", "Moderate"),
    "F11": ("Retrieval", "Low recall / ranking failures", "Moderate"),
    "F12": ("Retrieval", "Position-of-gold bias", "Strong"),
    "F13": ("Generation", "Hallucination despite context", "Strong"),
    "F14": ("Generation", "Conflicting information unresolved", "Strong"),
    "F15": ("Generation", "Incomplete / partial answers", "Moderate"),
    "F16": ("Generation", "Incorrect specificity", "Moderate"),
    "F17": ("Generation", "Wrong output format", "Limited"),
    "F18": ("Evaluation", "Metric inadequacy", "Strong"),
    "F19": ("Evaluation", "Lack of continuous monitoring", "Limited"),
    "F20": ("Deployment", "Latency / cost bottlenecks", "Moderate"),
    "F21": ("Deployment", "Auditability gaps", "Moderate"),
    "F22": ("Deployment", "Domain portability degradation", "Moderate"),
    "F23": ("Deployment", "PII / compliance leaks", "Moderate"),
    "F24": ("Deployment", "Prompt sensitivity", "Moderate"),
    "F25": ("Deployment", "Authorization / policy failures", "Moderate"),
    "F26": ("Agentic", "Planning failures", "Limited"),
    "F27": ("Agentic", "Tool selection / execution errors", "Limited"),
    "F28": ("Agentic", "Context memory degradation", "Limited"),
    "F29": ("Agentic", "Multi-agent coordination failures", "Limited"),
    "F30": ("Agentic", "Recursive hallucination cascades", "Limited"),
    "F31": ("Agentic", "Unbounded cost / latency spirals", "Limited"),
    "F32": ("Agentic", "Unsafe reasoning chains", "Limited"),
    "F33": ("Agentic", "Attribution & governance gaps", "Limited"),
}

# What ragpreflight checks *before* ingestion, and which modes those checks target.
# Modes not listed here happen at query time or inside agents; use eval / tracing tools for them.
COVERED: dict[str, str] = {
    "F1": "version-stale: older copies of the same document still in the corpus",
    "F2": "extraction-empty: files that yield no usable text",
    "F3": "extraction-scanned, extraction-garbled, extraction-table: text that won't survive parsing",
    "F4": "extraction-images: image-heavy pages whose content is lost as text",
    "F7": "chunk-split: qualifiers ('not', 'unless', 'before') that land at a chunk start",
    "F14": "version-conflict: numbers or negations that disagree across versions",
    "F21": "metadata-missing: no date / owner / source, so answers can't be traced or aged",
    "F23": "pii, secret: personal data and credentials that would be retrievable",
}


def label(code: str) -> str:
    stage, name, _ = MODES[code]
    return f"{code} {name}"
