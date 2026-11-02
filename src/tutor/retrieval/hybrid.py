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


class NeighbourRetriever:
    """Another route's ranking with each page followed by the pages either side of it.

    A control, not a candidate of its own: it asks whether the agent's extra model calls beat
    this cheap rule, as a topic on slides usually runs over consecutive pages. Read its scores
    with care: labels are consecutive ranges too, so it may fit the labels rather than the need.
    The pooled labels of the final comparison are what can tell those apart."""

    def __init__(self, route, pages: Sequence[Page], name: str) -> None:
        self.name = name
        self._route = route
        self._pages = {page.key: page for page in pages}

    def search(self, query: str, lectures: Collection[str] | None = None) -> list[Page]:
        found: dict[PageKey, Page] = {}
        for page in self._route.search(query, lectures):
            found.setdefault(page.key, page)
            lecture, number = page.key
            for near in ((lecture, number - 1), (lecture, number + 1)):
                if near in self._pages:  # a neighbour is in the same lecture, so in scope
                    found.setdefault(near, self._pages[near])
        return list(found.values())
