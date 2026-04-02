"""Reciprocal Rank Fusion (RRF) for combining multiple ranked search results."""

from typing import Any, Dict, List, Optional


def rrf_fuse(
    ranked_lists: Dict[str, List[Dict[str, Any]]],
    id_key: str = "id",
    k: int = 60,
    weights: Optional[Dict[str, float]] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Fuse multiple ranked result lists using Reciprocal Rank Fusion.

    Each document's fused score is:  score(d) = Σ weight_i / (k + rank_i(d))
    where rank_i is the 1-based position in ranked list i.

    Args:
        ranked_lists: Mapping of source name → ordered list of result dicts.
                      Each result dict must contain the key specified by ``id_key``.
        id_key: The key used to identify unique documents across lists.
        k: RRF constant (default 60, per Cormack et al.).
        weights: Optional per-source weight multipliers.  Missing sources
                 default to 1.0.
        limit: Maximum number of fused results to return.

    Returns:
        Merged list of result dicts sorted by descending RRF score,
        with an added ``rrf_score`` field.
    """
    if weights is None:
        weights = {}

    scores: Dict[Any, float] = {}
    docs: Dict[Any, Dict[str, Any]] = {}

    for source_name, results in ranked_lists.items():
        w = weights.get(source_name, 1.0)
        for rank_0, result in enumerate(results):
            doc_id = result[id_key]
            rrf_contribution = w / (k + rank_0 + 1)  # rank is 1-based
            scores[doc_id] = scores.get(doc_id, 0.0) + rrf_contribution
            # Keep the richest version of each document (first seen wins)
            if doc_id not in docs:
                docs[doc_id] = dict(result)

    # Sort by descending RRF score
    sorted_ids = sorted(scores, key=lambda d: scores[d], reverse=True)

    fused = []
    for doc_id in sorted_ids[:limit]:
        entry = docs[doc_id]
        entry["rrf_score"] = round(scores[doc_id], 6)
        fused.append(entry)

    return fused
