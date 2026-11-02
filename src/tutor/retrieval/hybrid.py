"""Hybrid retrieval: several routes' rankings merged into one (docs/design.md, "RAG").

BM25 finds exact terms and vectors find similar meaning, so together they should miss less.
Their scores cannot be added: a BM25 score is unbounded and a cosine is below 1. Reciprocal
rank fusion ignores the scores and uses only positions: a page earns 1 / (k + position) from
each ranking it appears in. A page high in both beats a page first in just one, and nothing
needs tuning to make the two routes' numbers comparable.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Sequence

from tutor.retrieval.corpus import Page, PageKey

# From the original paper (Cormack et al. 2009). A larger k flattens the difference between
# ranks, so one route's top result cannot decide the merged order on its own.
RRF_K = 60


def fuse(rankings: Sequence[Sequence[Page]], k: int = RRF_K) -> list[Page]:
    """One ranking from several. Ties keep the order pages were first seen in, so a run can
    be repeated exactly."""
    scores: dict[PageKey, float] = defaultdict(float)
    pages: dict[PageKey, Page] = {}  # dicts keep insertion order: the first-seen order
    for ranking in rankings:
        for position, page in enumerate(ranking, 1):
            scores[page.key] += 1 / (k + position)
            pages.setdefault(page.key, page)
    # sorted() is stable, so equal scores stay in that first-seen order.
    return sorted(pages.values(), key=lambda page: -scores[page.key])


class HybridRetriever:
    """Merges the routes it is given; each still searches within the same scope."""

    name = "hybrid"

    def __init__(self, routes: Sequence) -> None:
        self._routes = routes

    def search(self, query: str, lectures: Collection[str] | None = None) -> list[Page]:
        return fuse([route.search(query, lectures) for route in self._routes])
