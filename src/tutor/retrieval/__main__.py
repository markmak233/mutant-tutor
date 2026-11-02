"""python -m tutor.retrieval [--split dev|test|cross]

Runs the retrieval routes over labelled queries and prints their scores, searching one lecture
and then the whole course. Costs nothing and calls no model.

`dev` (from coverage.md) is the default and the split to tune on. `test` is for the final
comparison: looking at it while tuning would make its numbers look better than the routes
really are. `cross` holds the queries whose answer spans lectures, so it searches the whole
course only.
"""

from __future__ import annotations

import argparse
import sys

from tutor.config import WorkspaceError, load_env, load_workspace
from tutor.retrieval.bm25 import BM25Retriever
from tutor.retrieval.corpus import load_pages, load_settings
from tutor.retrieval.evaluate import BUDGETS, RANKS, Result, baselines, evaluate
from tutor.retrieval.labels import load_cross_lecture, load_queries


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tutor.retrieval")
    parser.add_argument("--split", choices=("dev", "test", "cross"), default="dev")
    args = parser.parse_args(argv)
    load_env()
    try:
        workspace = load_workspace().root
        pages = load_pages(workspace, load_settings(workspace).exclude_lectures)
        loaded, problems = (load_cross_lecture(workspace, pages) if args.split == "cross"
                            else load_queries(workspace, pages))
    except (WorkspaceError, FileNotFoundError, ValueError) as e:
        print(f"retrieval: {e}", file=sys.stderr)
        return 1
    chosen = [q for q in loaded if q.split == args.split]
    if not chosen:
        print(f"retrieval: no {args.split} queries found", file=sys.stderr)
        return 1
    for problem in problems:
        print(f"not measured: {problem}", file=sys.stderr)
    print(f"{args.split}: {len(chosen)} of {len(loaded)} queries, {len(pages)} pages")
    route = BM25Retriever(pages)
    scopes = ("course",) if args.split == "cross" else ("lecture", "course")
    print(format_table([evaluate(route, chosen, scope) for scope in scopes]))
    # Both arms find every labelled page by construction, so only their size is worth showing.
    sizes = baselines(pages, chosen)
    print("tokens per query: " + ", ".join(f"{name} {n:.0f}" for name, n in sizes.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
