"""Measure a retrieval route against the labelled queries (docs/design.md, "RAG").

Two kinds of number, because the routes return two kinds of result:

- recall@k and MRR assume a ranked list, which the keyword and vector routes give.
- recall within a token budget also works for the routes that return a set of pages of no
  fixed size. A route could raise plain recall just by returning more pages, so every route
  gets the same room: its results are taken in order until the budget is full. A route may
  stop early, which is its own behaviour, so what it actually returned is reported as well.

The budget measures the pages delivered, by the rough estimate in corpus.py, not a run's cost:
the agent's turns and the concept index are paid for apart, and reported apart.

Means hide what 18 queries can and cannot show: one query moves a mean recall by at most 1/18.
So a change is judged by `compare`, query against query.
"""

from __future__ import annotations

import time
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean
from typing import Protocol

from tutor.retrieval.corpus import Page, PageKey
from tutor.retrieval.labels import Query

RANKS = (3, 5, 10)
BUDGETS = (500, 1000, 2000, 4000)  # tokens of pages a route may return
# The number a route is chosen by, fixed before the final comparison: about twice the labelled
# pages of an average query, and under a third of an average lecture.
PRIMARY_BUDGET = 2000
# How many more queries must improve than worsen before a change replaces a simpler setting.
ADOPT_MARGIN = 3


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
    ms_per_query: float  # this run's own time; a stored answer takes none, see asker.first_run_cost
    pages_returned: float = 0.0
    tokens_returned: float = 0.0
    per_query: tuple[dict, ...] = ()  # one row per query, for comparing two runs query by query


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
    """`scope` "lecture": search only the lecture(s) holding the query's pages. That uses the
    labels, which is fair for generation, where the batch has already named its lecture, but
    is prior knowledge for a question of unknown source. "course": search everything, which is
    harder, as other lectures can match the same words."""
    per_rank: dict[int, list[float]] = {k: [] for k in RANKS}
    per_budget: dict[int, list[float]] = {b: [] for b in BUDGETS}
    ranks: list[float] = []
    rows: list[dict] = []
    start = time.perf_counter()
    for query in queries:
        ranked = route.search(query.text, query.lectures if scope == "lecture" else None)
        keys = [page.key for page in ranked]
        for k in RANKS:
            per_rank[k].append(recall(keys[:k], query.relevant))
        within = {}
        for budget in BUDGETS:
            kept = within_budget(ranked, budget)
            within[budget] = recall([page.key for page in kept], query.relevant)
            per_budget[budget].append(within[budget])
        ranks.append(reciprocal_rank(keys, query.relevant))
        kept = within_budget(ranked, max(BUDGETS))
        rows.append({"id": query.id, "recall_within": within, "rr": ranks[-1],
                     "pages": [list(page.key) for page in kept],
                     "tokens": sum(page.tokens for page in kept)})
    elapsed = time.perf_counter() - start
    return Result(
        route.name, scope, len(queries),
        {k: fmean(values) for k, values in per_rank.items()},
        fmean(ranks),
        {b: fmean(values) for b, values in per_budget.items()},
        elapsed / len(queries) * 1000,
        fmean(len(row["pages"]) for row in rows),
        fmean(row["tokens"] for row in rows),
        tuple(rows),
    )


def compare(base: Sequence[Mapping], other: Sequence[Mapping],
            budget: int = PRIMARY_BUDGET) -> dict:
    """`other` against `base`, query by query, on recall within `budget`: how many got better
    and worse, and whether that clears the margin for adopting the change. Means alone would
    let one query's large swing decide. Rows come from Result.per_query, and may have been
    through JSON, where a dictionary's number keys become strings."""
    def score(row: Mapping) -> float:
        within = row["recall_within"]
        return within[budget] if budget in within else within[str(budget)]

    before = {row["id"]: score(row) for row in base}
    after = {row["id"]: score(row) for row in other}
    if before.keys() != after.keys():
        raise ValueError("these two runs did not measure the same queries")
    better = sum(after[i] > before[i] for i in before)
    worse = sum(after[i] < before[i] for i in before)
    return {"better": better, "worse": worse, "same": len(before) - better - worse,
            "mean_change": fmean(after[i] - before[i] for i in before),
            "adopt": better - worse >= ADOPT_MARGIN}


def baselines(pages: Sequence[Page], queries: Sequence[Query]) -> dict[str, float]:
    """Mean tokens per query of the two arms that need no search, for scale. Both find every
    labelled page by construction, so their tokens are what a route competes with: the whole
    lecture is what generation sends today, the labelled pages are perfect retrieval. Perfect
    for retrieval only: what they would generate is for the generation comparison to show."""
    tokens = {page.key: page.tokens for page in pages}
    by_lecture: dict[str, int] = {}
    for page in pages:
        by_lecture[page.lecture] = by_lecture.get(page.lecture, 0) + page.tokens
    return {
        "whole lecture": fmean(sum(by_lecture[lecture] for lecture in q.lectures)
                               for q in queries),
        "labelled pages": fmean(sum(tokens[key] for key in q.relevant) for q in queries),
    }
