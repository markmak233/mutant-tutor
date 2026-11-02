"""python -m tutor.retrieval [--split dev|test|cross] [--route NAME ...] [--lecture N]
python -m tutor.retrieval --build-concepts [LECTURE ...]

Runs the retrieval routes over labelled queries and prints their scores, searching one lecture
and then the whole course (--lecture: only that lecture's queries, searching it alone).

`dev` (from coverage.md) is the default and the split to tune on. `test` is for the final
comparison: looking at it while tuning would make its numbers look better than the routes
really are. `cross` holds the queries whose answer spans lectures, so it searches the whole
course only.

BM25 costs nothing. The vector and hybrid routes need the workspace's `embedding` role
([llm.roles] in workspace.toml) and are left out without one. The concept route needs the
`concept` role and an index of every lecture it searches: --build-concepts writes those, only
for lectures new or changed since. By default it runs once every lecture has one. What models
produce is kept in cache/retrieval/, so only what has not been asked yet costs anything; the
run says how much that was.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

from tutor import llm
from tutor.config import WorkspaceError, load_env, load_workspace
from tutor.retrieval.bm25 import BM25Retriever
from tutor.retrieval.concepts import (
    ConceptRetriever,
    ConceptStore,
    ModelAsker,
    build_lecture,
    by_lecture,
)
from tutor.retrieval.corpus import Page, load_pages, load_settings
from tutor.retrieval.evaluate import BUDGETS, RANKS, Result, Retriever, baselines, evaluate
from tutor.retrieval.hybrid import HybridRetriever
from tutor.retrieval.labels import load_cross_lecture, load_queries
from tutor.retrieval.vectors import DIMENSIONS, EmbeddingStore, VectorRetriever

ROUTES = ("bm25", "vector", "hybrid", "concept")


def format_table(results: list[Result]) -> str:
    """One row per route and scope, recall as a share and the time in milliseconds."""
    header = (["scope", "route"] + [f"R@{k}" for k in RANKS] + ["MRR"]
              + [f"R<{b}" for b in BUDGETS] + ["ms"])
    rows = [header]
    for r in results:
        rows.append([r.scope, r.route]
                    + [f"{r.recall_at[k]:.2f}" for k in RANKS]
                    + [f"{r.mrr:.2f}"]
                    + [f"{r.recall_within[b]:.2f}" for b in BUDGETS]
                    + [f"{r.ms_per_query:.1f}"])
    widths = [max(len(row[i]) for row in rows) for i in range(len(header))]
    return "\n".join("  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True))
                     for row in rows)


def _log_path(workspace: Path) -> Path:
    return workspace / "logs" / "llm_calls.jsonl"


def _model_file(model: str) -> str:
    """A model's name as part of a path: "org/model" would be a folder."""
    return model.replace("/", "_")


def _embedding_store(workspace: Path) -> EmbeddingStore:
    """The store for the workspace's embedding model. Its file name holds the model and the
    size, as vectors from different models cannot be compared."""
    config = llm.config_for("embedding", workspace=workspace)

    def embed(texts: list[str]) -> list[list[float]]:
        return llm.embed(config, texts, purpose="embedding", log_path=_log_path(workspace),
                         dimensions=DIMENSIONS)

    name = f"{_model_file(config.model)}-{DIMENSIONS}.jsonl"
    return EmbeddingStore(workspace / "cache" / "retrieval" / name, embed)


def _concept_tools(workspace: Path) -> tuple[ConceptStore, ModelAsker]:
    """The index folder for the workspace's concept model, and a way to ask that model."""
    config = llm.config_for("concept", workspace=workspace)
    folder = workspace / "cache" / "retrieval" / "concepts" / _model_file(config.model)
    return ConceptStore(folder), ModelAsker(config, _log_path(workspace))


@dataclass
class Routes:
    """The routes of a run, and what can say what they spent."""

    built: dict[str, Retriever] = field(default_factory=dict)
    embeddings: EmbeddingStore | None = None
    asker: ModelAsker | None = None

    def spending(self) -> list[str]:
        lines = []
        if self.embeddings is not None:
            lines.append(f"texts sent to the embedding model this run: "
                         f"{self.embeddings.embedded}")
        if self.asker is not None:
            lines.append(f"concept model calls this run: {self.asker.calls}, tokens in "
                         f"{self.asker.input_tokens}, out {self.asker.output_tokens}")
        return lines


def build_routes(pages: list[Page], workspace: Path, names: list[str] | None) -> Routes:
    """The routes asked for (default: all that can run here). A route explicitly asked for
    that cannot run is an error; left to the default, it is simply not run."""
    roles = llm.roles(workspace)
    has_embedding, has_concept = "embedding" in roles, "concept" in roles
    concept_tools = _concept_tools(workspace) if has_concept else None
    if names is None:
        names = ["bm25"] + (["vector", "hybrid"] if has_embedding else [])
        if concept_tools and all(concept_tools[0].load(lecture_pages) is not None
                                 for lecture_pages in by_lecture(pages).values()):
            names.append("concept")
    if {"vector", "hybrid"} & set(names) and not has_embedding:
        raise ValueError("the vector and hybrid routes need an `embedding` role in [llm.roles]")
    if "concept" in names and not has_concept:
        raise ValueError("the concept route needs a `concept` role in [llm.roles]")
    routes = Routes()
    bm25 = BM25Retriever(pages)
    built: dict[str, Retriever] = {"bm25": bm25}
    if {"vector", "hybrid"} & set(names):
        # Hybrid is made of the other two, so vector is built whichever of them was asked for.
        routes.embeddings = _embedding_store(workspace)
        vector = VectorRetriever(pages, routes.embeddings)
        built |= {"vector": vector, "hybrid": HybridRetriever([bm25, vector])}
    if "concept" in names:
        store, routes.asker = concept_tools
        built["concept"] = ConceptRetriever(pages, store, routes.asker)
    routes.built = {name: built[name] for name in names}
    return routes


def build_concepts(pages: list[Page], workspace: Path, lectures: list[str]) -> None:
    """Index the lectures named (none: all), skipping those already indexed as they are."""
    store, asker = _concept_tools(workspace)
    grouped = by_lecture(pages)
    if unknown := set(lectures) - set(grouped):
        raise ValueError(f"no such lectures in the corpus: {sorted(unknown)}")
    for lecture in lectures or grouped:
        lecture_pages = grouped[lecture]
        if store.load(lecture_pages) is not None:
            print(f"L{lecture}: already indexed")
            continue
        concepts, dropped = build_lecture(lecture_pages, asker)
        store.save(lecture_pages, concepts)
        covered = {number for concept in concepts for number in concept.pages}
        print(f"L{lecture}: {len(concepts)} concepts, {len(covered)} of {len(lecture_pages)} "
              f"pages covered")
        for line in dropped:
            print(f"  dropped {line}")
    print(f"model calls {asker.calls}, tokens in {asker.input_tokens}, "
          f"out {asker.output_tokens}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tutor.retrieval")
    parser.add_argument("--split", choices=("dev", "test", "cross"), default="dev")
    parser.add_argument("--route", choices=ROUTES, action="append", dest="routes")
    parser.add_argument("--lecture", help="only this lecture's queries, searching it alone")
    parser.add_argument("--build-concepts", nargs="*", metavar="LECTURE",
                        help="write the concept index of these lectures (none: all), then stop")
    args = parser.parse_args(argv)
    load_env()
    try:
        workspace = load_workspace().root
        pages = load_pages(workspace, load_settings(workspace).exclude_lectures)
        if args.build_concepts is not None:
            build_concepts(pages, workspace, args.build_concepts)
            return 0
        loaded, problems = (load_cross_lecture(workspace, pages) if args.split == "cross"
                            else load_queries(workspace, pages))
        chosen = [q for q in loaded if q.split == args.split
                  and (args.lecture is None or q.lectures == {args.lecture})]
        if not chosen:
            print(f"retrieval: no {args.split} queries found", file=sys.stderr)
            return 1
        routes = build_routes(pages, workspace, args.routes)
        for problem in problems:
            print(f"not measured: {problem}", file=sys.stderr)
        print(f"{args.split}: {len(chosen)} of {len(loaded)} queries, {len(pages)} pages")
        scopes = (("lecture",) if args.lecture else
                  ("course",) if args.split == "cross" else ("lecture", "course"))
        print(format_table([evaluate(route, chosen, scope)
                            for scope in scopes for route in routes.built.values()]))
    except (WorkspaceError, FileNotFoundError, ValueError, llm.LLMError) as e:
        print(f"retrieval: {e}", file=sys.stderr)
        return 1
    # Both arms find every labelled page by construction, so only their size is worth showing.
    sizes = baselines(pages, chosen)
    print("tokens per query: " + ", ".join(f"{name} {n:.0f}" for name, n in sizes.items()))
    for line in routes.spending():
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
