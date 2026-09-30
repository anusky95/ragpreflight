"""Threshold profiles for RAGCheck quality gates.

Usage:
    from ragpreflight.profiles import get_profile, PROFILES

    profile = get_profile("strict")
    if report.score < profile["min_document_score"]:
        ...
"""

from __future__ import annotations

from typing import Any

PROFILES: dict[str, dict[str, Any]] = {
    "permissive": {
        "min_document_score": 40,
        "min_chunk_coherence": 0.5,
        "max_ocr_error_rate": 0.10,
        "max_duplicate_similarity": 0.95,
        "stale_months": 24,
        "description": "Chatbots, FAQ systems, internal tools",
    },
    "standard": {
        "min_document_score": 60,
        "min_chunk_coherence": 0.65,
        "max_ocr_error_rate": 0.05,
        "max_duplicate_similarity": 0.90,
        "stale_months": 12,
        "description": "Enterprise search, customer support RAG",
    },
    "strict": {
        "min_document_score": 80,
        "min_chunk_coherence": 0.8,
        "max_ocr_error_rate": 0.02,
        "max_duplicate_similarity": 0.85,
        "stale_months": 6,
        "description": "Medical, legal, financial RAG systems",
    },
}

# Alias for domain-specific naming
PROFILES["medical"] = PROFILES["strict"]
PROFILES["legal"] = PROFILES["strict"]
PROFILES["financial"] = PROFILES["strict"]


def get_profile(name: str) -> dict[str, Any]:
    """Return threshold profile by name.

    Args:
        name: One of 'permissive', 'standard', 'strict', 'medical', 'legal', 'financial'.

    Returns:
        Dictionary of threshold values.

    Raises:
        ValueError: If the profile name is not recognised.
    """
    name = name.lower()
    if name not in PROFILES:
        valid = ", ".join(sorted(set(PROFILES.keys())))
        raise ValueError(f"Unknown profile '{name}'. Valid profiles are: {valid}")
    return PROFILES[name]
