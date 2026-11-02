"""Vector search over the pages, and the embeddings it needs (docs/design.md, "RAG").

An embedding model turns a text into a list of numbers such that texts with similar meaning
get lists pointing in similar directions. Searching is then: embed the query, and rank pages
by the cosine of the angle between its vector and theirs. So this route can find a page that
says the same thing in other words, which keyword search cannot.

Standard library only: a course's few hundred pages take about 8 ms per query in plain Python
(6000 pages: 86 ms), and numpy in the environment that runs Spark would change what some
PySpark methods return, which the learner's environment would not show.
"""

from __future__ import annotations

import hashlib
import math
import operator
from collections.abc import Callable, Collection, Iterator
from pathlib import Path

from tutor.retrieval.corpus import CHARS_PER_TOKEN, Page
from tutor.retrieval.jsonl import JsonLines

DIMENSIONS = 1024  # asked of the model; enough for a course, and smaller than most defaults
# A request's limits: the strictest documented so far (Zhipu's 64 texts, 3072 tokens), so any
# provider accepts them. The tokens are this project's rough estimate, not the provider's
# count, so the limit keeps a margin.
MAX_BATCH_TEXTS = 64
MAX_BATCH_TOKENS = 2500

Embed = Callable[[list[str]], list[list[float]]]  # texts -> one vector each, in their order


def _batches(texts: list[str]) -> Iterator[list[str]]:
    """Consecutive runs of `texts` within a request's limits. A text over the token limit
    still goes, alone, so the provider's own error says what is wrong with it."""
    batch: list[str] = []
    tokens = 0
    for text in texts:
        size = len(text) // CHARS_PER_TOKEN
        if batch and (len(batch) == MAX_BATCH_TEXTS or tokens + size > MAX_BATCH_TOKENS):
            yield batch
            batch, tokens = [], 0
        batch.append(text)
        tokens += size
    if batch:
        yield batch


class EmbeddingStore:
    """Embeddings kept on disk, one JSON line per text, so a text is paid for only once.

    Keyed by a hash of the text, not by page: a page whose text changed gets a new vector,
    and a query asked again costs nothing. Each batch is appended as soon as it returns, so
    an interrupted run keeps what it already paid for. The file holds one model's vectors at
    one size, and the caller names it accordingly."""

    def __init__(self, path: Path, embed: Embed) -> None:
        self._file = JsonLines(path)
        self._embed = embed
        self.embedded = 0  # texts sent to the model by this store, to report what was spent
        self._vectors = {row["hash"]: row["vector"] for row in self._file.read()}

    def __call__(self, texts: list[str]) -> list[list[float]]:
        """A vector per text, asking the model only for those not stored yet."""
        keys = [_key(text) for text in texts]
        missing = list({key: text for key, text in zip(keys, texts, strict=True)
                        if key not in self._vectors}.values())
        for batch in _batches(missing):
            rows = [{"hash": _key(text), "vector": vector}
                    for text, vector in zip(batch, self._embed(batch), strict=True)]
            self._file.append(rows)
            self._vectors.update((row["hash"], row["vector"]) for row in rows)
            self.embedded += len(batch)
        return [self._vectors[key] for key in keys]


def _key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _unit(vector: list[float]) -> list[float]:
    """Scaled to length 1, so that a dot product between two vectors is their cosine."""
    length = math.sqrt(sum(x * x for x in vector))
    return [x / length for x in vector] if length else vector


class VectorRetriever:
    """The vector route: every page in the scope, closest in meaning first."""

    name = "vector"

    def __init__(self, pages: list[Page], embed: Embed) -> None:
        self._pages = pages
        self._embed = embed
        self._vectors = [_unit(vector) for vector in embed([page.text for page in pages])]

    def search(self, query: str, lectures: Collection[str] | None = None) -> list[Page]:
        """Ties go to the earlier page, so a run can be repeated exactly."""
        target = _unit(self._embed([query])[0])
        scored = [(-sum(map(operator.mul, target, vector)), position)
                  for position, vector in enumerate(self._vectors)
                  if lectures is None or self._pages[position].lecture in lectures]
        return [self._pages[position] for _, position in sorted(scored)]
