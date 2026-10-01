"""English wordlist for dictionary-aware OCR false-positive filtering.

Loads a bundled gzipped wordlist (derived from nltk words corpus, ~234K words).
Falls back to nltk at runtime if the bundled file is missing, and to a minimal
hardcoded set if neither is available.
"""

from __future__ import annotations

import functools
import gzip
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_BUNDLED_PATH = Path(__file__).parent / "_wordlist.txt.gz"

_FALLBACK_RN_WORDS = frozenset(
    {
        "learn",
        "learned",
        "learning",
        "learner",
        "turn",
        "turned",
        "turning",
        "burn",
        "burned",
        "burning",
        "return",
        "returned",
        "returning",
        "returns",
        "internal",
        "internally",
        "international",
        "internationally",
        "journal",
        "journalism",
        "journalist",
        "government",
        "governmental",
        "concern",
        "concerned",
        "concerning",
        "concerns",
        "discern",
        "discerning",
        "pattern",
        "patterns",
        "modern",
        "modernize",
        "western",
        "eastern",
        "northern",
        "southern",
        "alternative",
        "alternatives",
        "alternatively",
        "external",
        "externally",
        "eternal",
        "eternally",
        "attorney",
        "attorneys",
        "journey",
        "journeys",
        "tournament",
        "tournaments",
        "furniture",
        "internet",
        "kernel",
        "kernels",
        "fern",
        "ferns",
        "stern",
        "sternly",
        "lantern",
        "lanterns",
        "intern",
        "interns",
        "internship",
        "nocturnal",
        "maternal",
        "paternal",
        "fraternal",
        "infernal",
        "ornament",
        "ornamental",
        "ornaments",
        "warning",
        "warnings",
        "morning",
        "mornings",
        "corner",
        "corners",
        "cornerstone",
        "barn",
        "barns",
        "yarn",
        "yarns",
        "earning",
        "earnings",
        "yearning",
        "discernment",
        "alternation",
        "hibernate",
        "hibernation",
        "govern",
        "governed",
        "governing",
        "governor",
        "governance",
    }
)


@functools.lru_cache(maxsize=1)
def get_english_words() -> frozenset[str]:
    """Load the English word dictionary.

    Priority: bundled gzipped file > nltk corpus > hardcoded fallback.
    """
    if _BUNDLED_PATH.exists():
        try:
            with gzip.open(_BUNDLED_PATH, "rt", encoding="utf-8") as f:
                words = frozenset(f.read().split("\n"))
            logger.debug("Loaded %d words from bundled wordlist", len(words))
            return words
        except Exception:
            logger.debug("Failed to load bundled wordlist, trying nltk")

    try:
        from nltk.corpus import words as nltk_words

        try:
            word_list = nltk_words.words()
        except LookupError:
            import nltk

            nltk.download("words", quiet=True)
            word_list = nltk_words.words()
        result = frozenset(w.lower() for w in word_list if len(w) >= 2)
        logger.debug("Loaded %d words from nltk", len(result))
        return result
    except Exception:
        pass

    logger.debug("Using fallback wordlist (%d words)", len(_FALLBACK_RN_WORDS))
    return _FALLBACK_RN_WORDS


def is_real_word(word: str) -> bool:
    """Check if a word exists in the English dictionary."""
    return word.lower() in get_english_words()
