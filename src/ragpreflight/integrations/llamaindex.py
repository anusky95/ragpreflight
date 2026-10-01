"""LlamaIndex integration for ragpreflight.

Provides a ragpreflight node post-processor that filters out low-quality nodes
from a retrieval result, and a document validator that gates ingestion.

Usage:
    from ragpreflight.integrations.llamaindex import RAGPreflightNodePostprocessor

    postprocessor = RAGPreflightNodePostprocessor(min_score=60)
    # Use in a query engine:
    query_engine = index.as_query_engine(node_postprocessors=[postprocessor])

    # Or as a pre-ingestion validator:
    from ragpreflight.integrations.llamaindex import validate_documents
    valid_docs = validate_documents(["doc1.pdf", "doc2.pdf"], min_score=70)
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

logger = logging.getLogger(__name__)


class RAGPreflightNodePostprocessor:
    """A LlamaIndex node post-processor that drops low-quality retrieved nodes.

    Nodes whose source document has a ragpreflight score below ``min_score`` are
    removed from the retrieval result to prevent low-quality content from
    reaching the LLM.

    Requires llama-index-core to be installed:
        pip install llama-index-core

    Args:
        min_score: Minimum acceptable readiness score (0–100). Default 60.
        profile: ragpreflight quality profile name. Default "standard".
    """

    def __init__(self, min_score: int = 60, profile: str = "standard") -> None:
        self.min_score = min_score
        self.profile = profile
        self._score_cache: dict[str, int] = {}

    def postprocess_nodes(
        self,
        nodes: list,
        query_bundle: object | None = None,
    ) -> list:
        """Filter nodes whose source document fails the quality threshold.

        Args:
            nodes: List of NodeWithScore objects from LlamaIndex.
            query_bundle: Ignored; present for interface compatibility.

        Returns:
            Filtered list of NodeWithScore objects.
        """
        from ragpreflight import scan_document

        filtered = []
        for node_with_score in nodes:
            source_path = self._get_source_path(node_with_score)
            if source_path is None:
                filtered.append(node_with_score)
                continue

            if source_path not in self._score_cache:
                try:
                    report = scan_document(source_path)
                    self._score_cache[source_path] = report.score
                except Exception as exc:
                    logger.debug("ragpreflight scan failed for '%s': %s", source_path, exc)
                    self._score_cache[source_path] = 100  # Allow on error

            score = self._score_cache[source_path]
            if score >= self.min_score:
                filtered.append(node_with_score)
            else:
                logger.info(
                    "ragpreflight dropped node from '%s' (score %d < %d)",
                    source_path,
                    score,
                    self.min_score,
                )

        return filtered

    def _get_source_path(self, node_with_score: object) -> str | None:
        """Extract source file path from a LlamaIndex NodeWithScore."""
        try:
            node = node_with_score.node  # type: ignore[attr-defined]
            metadata = node.metadata or {}
            return metadata.get("file_path") or metadata.get("source")
        except AttributeError:
            return None


def validate_documents(
    file_paths: Sequence[str | Path],
    min_score: int = 60,
    profile: str = "standard",
) -> list[Path]:
    """Pre-ingestion validator: return only documents that pass the quality gate.

    Args:
        file_paths: Paths to documents to validate.
        min_score: Minimum acceptable readiness score (0–100). Default 60.
        profile: ragpreflight quality profile. Default "standard".

    Returns:
        List of Path objects for documents that passed the quality threshold.
    """
    from ragpreflight import scan_document

    passing: list[Path] = []
    for fp in file_paths:
        path = Path(fp)
        try:
            report = scan_document(path)
            if report.score >= min_score:
                passing.append(path)
            else:
                logger.warning(
                    "ragpreflight: '%s' failed quality gate (score %d < %d) — %d issue(s) detected.",
                    path.name,
                    report.score,
                    min_score,
                    len(report.issues),
                )
        except Exception as exc:
            logger.warning("ragpreflight: could not scan '%s': %s", path, exc)

    return passing
