"""BM25 keyword ranking, written out by hand (docs/design.md, "RAG").

BM25 scores a document for a query by adding one number per query term:

    idf(term) * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / average_length))

- `tf` is how often the term occurs in the document. The fraction grows with it but levels
  off (k1 sets how fast), so ten mentions do not count ten times as much as one.
- `idf` is high for rare terms: a term on few pages says more than one on every page.
- `b` corrects for length: a long page contains more terms by chance alone.

No library: the formula is short and has to be explained in interviews.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable

from tutor.retrieval.corpus import Page

# The usual defaults from the literature. Other values moved the dev scores by no more than
# one or two queries (2026-11-02), which is noise, so they stay.
K1 = 1.5
B = 0.75

_WORD = re.compile(r"\w+")


def tokenize(text: str) -> list[str]:
    """Lowercase words, so an API name stays one rare term that matches exactly, which is
    what vector search handles poorly. Splitting camelCase, removing stop words and
    stripping plurals made no measurable difference on the dev queries (2026-11-02)."""
    return _WORD.findall(text.lower())


class BM25Index:
    """An inverted index: for each term, the documents holding it and how often. Scoring
    then only visits documents that share a term with the query."""

    def __init__(self, documents: Iterable[list[str]], k1: float = K1, b: float = B) -> None:
        self._k1, self._b = k1, b
        self._lengths: list[int] = []
        self._postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for doc_id, tokens in enumerate(documents):
            self._lengths.append(len(tokens))
            for term, count in Counter(tokens).items():
                self._postings[term].append((doc_id, count))
        total = len(self._lengths)
        self._average = sum(self._lengths) / total if total else 0.0
        # The "+ 1" inside the log keeps idf positive even for a term on most pages, so a
        # common word can never lower a page's score.
        self._idf = {term: math.log(1 + (total - len(docs) + 0.5) / (len(docs) + 0.5))
                     for term, docs in self._postings.items()}

    def scores(self, query_tokens: Iterable[str]) -> dict[int, float]:
        """Document id -> score, for documents with at least one query term. Each distinct
        term counts once: an identifier's parts can repeat a word the query also says."""
        totals: dict[int, float] = defaultdict(float)
        for term in set(query_tokens):
            for doc_id, tf in self._postings.get(term, ()):
                length = 1 - self._b + self._b * self._lengths[doc_id] / self._average
                totals[doc_id] += self._idf[term] * tf * (self._k1 + 1) / (tf + self._k1 * length)
        return dict(totals)


class BM25Retriever:
    """The keyword route. The index covers the whole course, so a term's idf does not change
    with the scope; the scope only filters the results."""

    name = "bm25"

    def __init__(self, pages: list[Page]) -> None:
        self._pages = pages
        self._index = BM25Index(tokenize(page.text) for page in pages)

    def search(self, query: str, lectures: Collection[str] | None = None) -> list[Page]:
        """Pages sharing a term with `query`, best first, within `lectures` (None: all).
        Ties go to the earlier page, so a run can be repeated exactly."""
        scores = self._index.scores(tokenize(query))
        ranked = sorted(scores, key=lambda doc_id: (-scores[doc_id], doc_id))
        return [self._pages[doc_id] for doc_id in ranked
                if lectures is None or self._pages[doc_id].lecture in lectures]
