"""python -m tutor.retrieval [--split dev|test|cross] [--route NAME ...]

Runs the retrieval routes over labelled queries and prints their scores, searching one lecture
and then the whole course.

`dev` (from coverage.md) is the default and the split to tune on. `test` is for the final
comparison: looking at it while tuning would make its numbers look better than the routes
really are. `cross` holds the queries whose answer spans lectures, so it searches the whole
course only.

BM25 costs nothing. The vector and hybrid routes need the workspace's `embedding` role
([llm.roles] in workspace.toml) and are left out without one. Their embeddings are kept in
cache/retrieval/, so only a text not seen before costs anything (the run says how many).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tutor import llm
from tutor.config import WorkspaceError, load_env, load_workspace
from tutor.retrieval.bm25 import BM25Retriever
from tutor.retrieval.corpus import Page, load_pages, load_settings
from tutor.retrieval.evaluate import BUDGETS, RANKS, Result, Retriever, baselines, evaluate
from tutor.retrieval.hybrid import HybridRetriever
from tutor.retrieval.labels import load_cross_lecture, load_queries
from tutor.retrieval.vectors import DIMENSIONS, EmbeddingStore, VectorRetriever

ROUTES = ("bm25", "vector", "hybrid")


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


def _embedding_store(workspace: Path) -> EmbeddingStore:
    """The store for the workspace's embedding model. Its file name holds the model and the
    size, as vectors from different models cannot be compared."""
    config = llm.config_for("embedding", workspace=workspace)
    log_path = workspace / "logs" / "llm_calls.jsonl"

    def embed(texts: list[str]) -> list[list[float]]:
        return llm.embed(config, texts, purpose="embedding", log_path=log_path,
                         dimensions=DIMENSIONS)

    name = f"{config.model.replace('/', '_')}-{DIMENSIONS}.jsonl"
    return EmbeddingStore(workspace / "cache" / "retrieval" / name, embed)


def build_routes(pages: list[Page], workspace: Path,
                 names: list[str] | None) -> tuple[list[Retriever], EmbeddingStore | None]:
    """The routes asked for (default: all that can run here), and the embedding store if any
    of them uses one. A route explicitly asked for that cannot run is an error."""
    has_embedding = "embedding" in llm.roles(workspace)
    wanted = names or [name for name in ROUTES if has_embedding or name == "bm25"]
    if not has_embedding and set(wanted) - {"bm25"}:
        raise ValueError("the vector and hybrid routes need an `embedding` role in [llm.roles]")
    # Hybrid is made of the other two, so those are built whichever of them was asked for.
    bm25 = BM25Retriever(pages)
    if wanted == ["bm25"]:
        return [bm25], None
    store = _embedding_store(workspace)
    vector = VectorRetriever(pages, store)
    built = {"bm25": bm25, "vector": vector, "hybrid": HybridRetriever([bm25, vector])}
    return [built[name] for name in wanted], store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tutor.retrieval")
    parser.add_argument("--split", choices=("dev", "test", "cross"), default="dev")
    parser.add_argument("--route", choices=ROUTES, action="append", dest="routes")
    args = parser.parse_args(argv)
    load_env()
    try:
        workspace = load_workspace().root
        pages = load_pages(workspace, load_settings(workspace).exclude_lectures)
        loaded, problems = (load_cross_lecture(workspace, pages) if args.split == "cross"
                            else load_queries(workspace, pages))
        chosen = [q for q in loaded if q.split == args.split]
        if not chosen:
            print(f"retrieval: no {args.split} queries found", file=sys.stderr)
            return 1
        routes, store = build_routes(pages, workspace, args.routes)
        for problem in problems:
            print(f"not measured: {problem}", file=sys.stderr)
        print(f"{args.split}: {len(chosen)} of {len(loaded)} queries, {len(pages)} pages")
        scopes = ("course",) if args.split == "cross" else ("lecture", "course")
        print(format_table([evaluate(route, chosen, scope)
                            for scope in scopes for route in routes]))
    except (WorkspaceError, FileNotFoundError, ValueError, llm.LLMError) as e:
        print(f"retrieval: {e}", file=sys.stderr)
        return 1
    # Both arms find every labelled page by construction, so only their size is worth showing.
    sizes = baselines(pages, chosen)
    print("tokens per query: " + ", ".join(f"{name} {n:.0f}" for name, n in sizes.items()))
    if store is not None:
        print(f"texts sent to the embedding model this run: {store.embedded}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
