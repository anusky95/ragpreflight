"""Retrieval simulation module for RAGCheck.

Generates synthetic queries from document content and tests how well chunks
are retrieved, surfacing dead chunks and query failure rates.

Usage:
    from ragpreflight.retrieval import simulate_retrieval

    result = simulate_retrieval("./knowledge_base/")
    print(result["dead_chunk_rate"])
    print(result["query_failure_rate"])
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from ragpreflight._constants import (
    DEFAULT_QUERIES_PER_DOC,
    DEFAULT_QUERY_FAILURE_THRESHOLD,
    DEFAULT_RETRIEVAL_TOP_K,
    QUERY_TEMPLATES,
)
from ragpreflight.chunker import _extract_text, _split_recursive
from ragpreflight.utils import iter_supported_files

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def simulate_retrieval(
    directory: str | Path,
    queries_per_doc: int = DEFAULT_QUERIES_PER_DOC,
    top_k: int = DEFAULT_RETRIEVAL_TOP_K,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
    failure_threshold: float = DEFAULT_QUERY_FAILURE_THRESHOLD,
    use_llm: bool = False,
    llm_endpoint: str = "http://localhost:11434",
    llm_model: str = "llama3",
) -> dict[str, Any]:
    """Simulate retrieval over a corpus of documents.

    Generates synthetic queries (without any LLM by default) and measures:
    - synthetic_retrieval_hit_rate: fraction of top-k with similarity >= threshold.
      NOTE: this is NOT standard precision@k — it uses a similarity threshold in
      place of relevance labels. Without human-labeled relevance judgments it cannot
      be interpreted as precision in the information-retrieval sense.
    - dead chunk rate (chunks never retrieved by any query)
    - query failure rate (best match similarity < threshold)

    Args:
        directory: Directory containing documents to simulate against.
        queries_per_doc: Number of synthetic queries to generate per document.
        top_k: Number of top chunks to retrieve per query.
        chunk_size: Chunk size in characters for splitting.
        chunk_overlap: Overlap in characters between chunks.
        failure_threshold: Cosine similarity below which a query is considered failed.
        use_llm: If True, use an LLM endpoint to generate richer queries.
        llm_endpoint: OpenAI-compatible API base URL (for --use-llm).
        llm_model: LLM model name to use.

    Returns:
        Dictionary with simulation metrics and per-query details.
    """
    root = Path(directory)
    files = iter_supported_files(root)

    if not files:
        return {
            "error": "No supported documents found.",
            "directory": str(directory),
        }

    embedder = _get_embedder()
    if embedder is None:
        return {
            "error": (
                "sentence-transformers is required for retrieval simulation. "
                "Install it with: pip install ragpreflight[full]"
            ),
            "directory": str(directory),
        }

    # Build corpus: list of (filepath, chunk_text) pairs
    corpus_chunks: list[tuple[str, str]] = []
    for fpath in files:
        try:
            from ragpreflight.scanner import scan_document

            doc_report = scan_document(fpath)
            text = _extract_text(fpath, doc_report.file_format)
            if text.strip():
                chunks = _split_recursive(
                    text, chunk_size, chunk_overlap, ["\n\n", "\n", ". ", " ", ""]
                )
                for chunk in chunks:
                    if chunk.strip():
                        corpus_chunks.append((str(fpath), chunk))
        except Exception as exc:
            logger.warning("Could not process '%s' for retrieval sim: %s", fpath, exc)

    if not corpus_chunks:
        return {
            "error": "No extractable text found in any documents.",
            "directory": str(directory),
        }

    # Generate queries
    all_queries: list[str] = []
    for fpath in files:
        try:
            from ragpreflight.scanner import scan_document

            doc_report = scan_document(fpath)
            text = _extract_text(fpath, doc_report.file_format)
            if not text.strip():
                continue

            if use_llm:
                llm_queries = _generate_llm_queries(text, queries_per_doc, llm_endpoint, llm_model)
                if llm_queries:
                    all_queries.extend(llm_queries)
                    continue

            # Fallback: extractive query generation
            queries = _generate_extractive_queries(text, queries_per_doc)
            all_queries.extend(queries)
        except Exception as exc:
            logger.warning("Query generation failed for '%s': %s", fpath, exc)

    if not all_queries:
        return {
            "error": "Could not generate any queries from the corpus.",
            "directory": str(directory),
        }

    # Embed corpus chunks
    chunk_texts = [c for _, c in corpus_chunks]
    try:
        import numpy as np  # type: ignore[import]

        chunk_embeddings = embedder.encode(  # type: ignore[attr-defined]
            chunk_texts, show_progress_bar=False, batch_size=32
        )
        query_embeddings = embedder.encode(  # type: ignore[attr-defined]
            all_queries, show_progress_bar=False, batch_size=32
        )
    except Exception as exc:
        return {"error": f"Embedding failed: {exc}", "directory": str(directory)}

    # Normalise
    import numpy as np

    chunk_norms = np.linalg.norm(chunk_embeddings, axis=1, keepdims=True)
    chunk_norms = np.where(chunk_norms == 0, 1, chunk_norms)
    chunk_emb_norm = chunk_embeddings / chunk_norms

    query_norms = np.linalg.norm(query_embeddings, axis=1, keepdims=True)
    query_norms = np.where(query_norms == 0, 1, query_norms)
    query_emb_norm = query_embeddings / query_norms

    # Cosine similarity matrix: (num_queries, num_chunks)
    sim_matrix = query_emb_norm @ chunk_emb_norm.T

    # Metrics
    retrieved_chunk_indices: set[int] = set()
    failed_queries = 0
    hit_rate_total = 0.0
    query_results: list[dict] = []

    for q_idx, query in enumerate(all_queries):
        sims = sim_matrix[q_idx]
        top_indices = np.argsort(sims)[::-1][:top_k]
        top_sims = sims[top_indices]

        best_sim = float(top_sims[0]) if len(top_sims) > 0 else 0.0
        if best_sim < failure_threshold:
            failed_queries += 1

        retrieved_chunk_indices.update(int(i) for i in top_indices)

        # similarity_hit_rate_at_k: fraction of top-k with similarity >= threshold.
        # Not labeled precision@k — no relevance labels available.
        relevant = sum(1 for s in top_sims if s >= failure_threshold)
        hit_rate_total += relevant / top_k

        query_results.append(
            {
                "query": query,
                "best_similarity": round(best_sim, 4),
                "failed": best_sim < failure_threshold,
                "top_chunks": [
                    {
                        "filepath": corpus_chunks[int(i)][0],
                        "preview": corpus_chunks[int(i)][1][:80],
                        "similarity": round(float(sims[i]), 4),
                    }
                    for i in top_indices
                ],
            }
        )

    n_queries = len(all_queries)
    n_chunks = len(corpus_chunks)

    dead_chunk_rate = 1.0 - (len(retrieved_chunk_indices) / n_chunks)
    query_failure_rate = failed_queries / n_queries
    synthetic_retrieval_hit_rate = hit_rate_total / n_queries

    return {
        "directory": str(directory),
        "total_documents": len(files),
        "total_chunks": n_chunks,
        "total_queries": n_queries,
        "synthetic_retrieval_hit_rate": round(synthetic_retrieval_hit_rate, 4),
        "_note": "synthetic_retrieval_hit_rate uses similarity threshold, not labeled relevance — not standard precision@k",
        "dead_chunk_rate": round(dead_chunk_rate, 4),
        "query_failure_rate": round(query_failure_rate, 4),
        "dead_chunks": n_chunks - len(retrieved_chunk_indices),
        "failed_queries": failed_queries,
        "query_results": query_results,
    }


# ---------------------------------------------------------------------------
# Extractive query generation (no LLM required)
# ---------------------------------------------------------------------------


def _generate_extractive_queries(text: str, n: int = DEFAULT_QUERIES_PER_DOC) -> list[str]:
    """Generate n queries from document text using extractive techniques.

    Uses TF-IDF key phrase extraction + section headers + template patterns.

    Args:
        text: Full document text.
        n: Number of queries to generate.

    Returns:
        List of query strings.
    """
    queries: list[str] = []

    # 1. Extract section headers (Markdown / plain heading patterns)
    headers = re.findall(r"^#{1,4}\s+(.+)$", text, re.MULTILINE)
    headers += re.findall(r"^([A-Z][A-Za-z\s]{5,60})\n[-=]{3,}", text, re.MULTILINE)
    for header in headers[:3]:
        header = header.strip()
        if header:
            queries.append(f"Summarize {header}.")
            queries.append(f"What does the section '{header}' cover?")

    # 2. Named-entity-like extraction (capitalized noun phrases)
    entities = re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", text)
    seen_entities: set[str] = set()
    for entity in entities:
        if entity not in seen_entities and len(entity) > 4:
            seen_entities.add(entity)
            template = QUERY_TEMPLATES[len(queries) % len(QUERY_TEMPLATES)]
            queries.append(
                template.format(
                    entity=entity, concept=entity, key_phrase=entity, section_title=entity
                )
            )
            if len(queries) >= n * 2:
                break

    # 3. TF-IDF-style key phrase extraction (no sklearn needed)
    key_phrases = _simple_keyphrases(text, top_n=n)
    for phrase in key_phrases:
        template = QUERY_TEMPLATES[len(queries) % len(QUERY_TEMPLATES)]
        queries.append(
            template.format(entity=phrase, concept=phrase, key_phrase=phrase, section_title=phrase)
        )

    # Deduplicate and trim
    seen: set[str] = set()
    unique: list[str] = []
    for q in queries:
        q_norm = q.strip().lower()
        if q_norm not in seen and len(q_norm) > 10:
            seen.add(q_norm)
            unique.append(q.strip())

    return unique[:n]


def _simple_keyphrases(text: str, top_n: int = 10) -> list[str]:
    """Extract key phrases by term frequency heuristic.

    Counts significant multi-word noun phrases by capitalization and frequency.

    Args:
        text: Source text.
        top_n: Number of top phrases to return.

    Returns:
        List of key phrase strings.
    """
    # Find 2-3 word lowercase phrases that appear multiple times
    words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
    stopwords = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "are",
        "was",
        "were",
        "has",
        "have",
        "been",
        "not",
        "but",
        "can",
        "will",
        "its",
        "into",
        "they",
        "their",
        "our",
        "your",
        "also",
        "when",
        "then",
        "than",
        "more",
        "all",
        "any",
        "each",
        "only",
        "may",
        "should",
    }

    # Bigram frequency
    bigram_freq: dict[str, int] = {}
    for i in range(len(words) - 1):
        if words[i] not in stopwords and words[i + 1] not in stopwords:
            bigram = f"{words[i]} {words[i + 1]}"
            bigram_freq[bigram] = bigram_freq.get(bigram, 0) + 1

    sorted_bigrams = sorted(bigram_freq.items(), key=lambda x: -x[1])
    return [phrase for phrase, freq in sorted_bigrams if freq >= 2][:top_n]


# ---------------------------------------------------------------------------
# LLM-based query generation (optional)
# ---------------------------------------------------------------------------


def _generate_llm_queries(
    text: str,
    n: int,
    endpoint: str,
    model: str,
) -> list[str]:
    """Generate queries using an OpenAI-compatible LLM endpoint.

    Falls back to empty list if the endpoint is unreachable.

    Args:
        text: Document text to generate queries from.
        n: Number of queries to generate.
        endpoint: OpenAI-compatible base URL.
        model: Model name to use.

    Returns:
        List of query strings (empty on failure).
    """
    try:
        import openai  # type: ignore[import]
    except ImportError:
        logger.warning("openai package not installed. Use: pip install ragpreflight[llm]")
        return []

    try:
        client = openai.OpenAI(base_url=endpoint, api_key="ragpreflight-local")
        sample = text[:3000]  # Keep prompt small
        prompt = (
            f"Generate {n} diverse, natural questions that a user might ask when "
            f"searching a knowledge base containing the following text. "
            f"Output one question per line, no numbering.\n\n---\n{sample}\n---"
        )
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.7,
        )
        raw = response.choices[0].message.content or ""
        queries = [line.strip("- •\t") for line in raw.strip().splitlines() if line.strip()]
        return [q for q in queries if len(q) > 10][:n]
    except Exception as exc:
        logger.warning(
            "LLM endpoint '%s' unreachable or returned an error: %s. "
            "Falling back to extractive query generation.",
            endpoint,
            exc,
        )
        return []


# ---------------------------------------------------------------------------
# Embedder (shared cache)
# ---------------------------------------------------------------------------

_embedder_cache: object | None = None
_embedder_tried = False


def _get_embedder() -> object | None:
    """Return a sentence-transformers model, or None if not installed.

    Returns:
        SentenceTransformer model instance, or None.
    """
    global _embedder_cache, _embedder_tried
    if _embedder_tried:
        return _embedder_cache
    _embedder_tried = True
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore[import]

        _embedder_cache = SentenceTransformer("all-MiniLM-L6-v2")
    except ImportError:
        logger.warning(
            "sentence-transformers is required for retrieval simulation. "
            "Install with: pip install ragpreflight[full]"
        )
        _embedder_cache = None
    except Exception as exc:
        logger.warning("Could not load SentenceTransformer: %s", exc)
        _embedder_cache = None
    return _embedder_cache
