"""Reciprocal Rank Fusion (Cormack et al., 2009). See ADR 0005."""

from collections.abc import Sequence


def rrf(rankings: Sequence[Sequence[int]], k: int = 60) -> list[int]:
    """Merge several ranked lists of row ids into one, best first.

    Each id earns 1 / (k + rank) from every list it appears in (rank starts at 1),
    and the points are added up.
    """
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1 / (k + rank)
    return sorted(scores, key=scores.__getitem__, reverse=True)
