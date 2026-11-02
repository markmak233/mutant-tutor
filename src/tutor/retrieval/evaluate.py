"""Measure a retrieval route against the labelled queries (docs/design.md, "RAG").

Two kinds of number, because the routes return two kinds of result:

- recall@k and MRR assume a ranked list, which the keyword and vector routes give.
- recall within a token budget also works for the routes that return a set of pages of no
  fixed size. A route could raise plain recall just by returning more pages, so every route
  gets the same room: its results are taken in order until the budget is full.
"""

from __future__ import annotations

import time
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from statistics import fmean
from typing import Protocol

from tutor.retrieval.corpus import Page, PageKey
from tutor.retrieval.labels import Query

RANKS = (3, 5, 10)
BUDGETS = (500, 1000, 2000, 4000)  # tokens of pages a route may return


class Retriever(Protocol):
    """A route: the pages for a query, best first, within `lectures` (None: the course)."""

    name: str

    def search(self, query: str, lectures: Collection[str] | None = None) -> list[Page]: ...


@dataclass(frozen=True)
class Result:
    route: str
    scope: str
    queries: int
    recall_at: dict[int, float]
    mrr: float
    recall_within: dict[int, float]
    ms_per_query: float


def recall(found: Sequence[PageKey], relevant: Collection[PageKey]) -> float:
    """The share of the relevant pages that were found."""
    return len(set(found) & set(relevant)) / len(relevant)


def reciprocal_rank(found: Sequence[PageKey], relevant: Collection[PageKey]) -> float:
    """1 if the first result is relevant, 1/2 if the second is first, ... 0 if none is:
    how far down a reader has to go. It only looks at the first relevant page."""
    for position, key in enumerate(found, 1):
        if key in relevant:
            return 1 / position
    return 0.0


def within_budget(ranked: Sequence[Page], budget: int) -> list[Page]:
    """The results in order, up to the first that no longer fits. Stopping there, rather
    than skipping it and going on, keeps the route's own order the only thing that counts."""
    taken: list[Page] = []
    used = 0
    for page in ranked:
        used += page.tokens
        if used > budget:
            break
        taken.append(page)
    return taken


def evaluate(route: Retriever, queries: Sequence[Query], scope: str) -> Result:
    """`scope` "lecture": search only the lecture(s) holding the query's pages, as generation
    does once a batch's sources are known. "course": search everything, which is harder, as
    other lectures can match the same words."""
    per_rank: dict[int, list[float]] = {k: [] for k in RANKS}
    per_budget: dict[int, list[float]] = {b: [] for b in BUDGETS}
    ranks: list[float] = []
    start = time.perf_counter()
    for query in queries:
        ranked = route.search(query.text, query.lectures if scope == "lecture" else None)
        keys = [page.key for page in ranked]
        for k in RANKS:
            per_rank[k].append(recall(keys[:k], query.relevant))
        for budget in BUDGETS:
            kept = within_budget(ranked, budget)
            per_budget[budget].append(recall([page.key for page in kept], query.relevant))
        ranks.append(reciprocal_rank(keys, query.relevant))
    elapsed = time.perf_counter() - start
    return Result(
        route.name, scope, len(queries),
        {k: fmean(values) for k, values in per_rank.items()},
        fmean(ranks),
        {b: fmean(values) for b, values in per_budget.items()},
        elapsed / len(queries) * 1000,
    )


def baselines(pages: Sequence[Page], queries: Sequence[Query]) -> dict[str, float]:
    """Mean tokens per query of the two arms that need no search, for scale. Both find every
    relevant page by construction, so their tokens are what a route competes with: the whole
    lecture is what generation sends today, the labelled pages are perfect retrieval."""
    tokens = {page.key: page.tokens for page in pages}
    by_lecture: dict[str, int] = {}
    for page in pages:
        by_lecture[page.lecture] = by_lecture.get(page.lecture, 0) + page.tokens
    return {
        "whole lecture": fmean(sum(by_lecture[lecture] for lecture in q.lectures)
                               for q in queries),
        "labelled pages": fmean(sum(tokens[key] for key in q.relevant) for q in queries),
    }
