"""python -m tutor.retrieval [--split dev|test|cross] [--route NAME ...] [--lecture N]
                           [--run N] [--agent-search bm25|hybrid] [--agent-thinking] [--save NAME]
python -m tutor.retrieval --build-concepts [LECTURE ...]
python -m tutor.retrieval --freeze-queries
python -m tutor.retrieval --compare NAME/ROUTE NAME/ROUTE [--scope lecture|course]

Runs the retrieval routes over labelled queries and prints their scores, searching one lecture
and then the whole course (--lecture: only that lecture's queries, searching it alone).

`dev` (from coverage.md) is the default and the split to tune on. `test` is for the final
comparison: looking at it while tuning would make its numbers look better than the routes
really are. `cross` holds the queries whose answer spans lectures, so it searches the whole
course only. Both need --freeze-queries first, which snapshots every query and label.

BM25 costs nothing. The vector and hybrid routes need the workspace's `embedding` role
([llm.roles] in workspace.toml) and are left out without one. The concept route needs the
`concept` role and an index of every lecture it searches: --build-concepts writes those, only
for lectures new or changed since. By default it runs once every lecture has one. The agent
route needs the `agent` role and langgraph. What models produce is kept in cache/retrieval/, one
folder per complete configuration, so only what has not been asked yet costs anything. --run N
makes a repeated measurement of the routes that use a model: the same command with the same N
reads the stored answers, so it does not measure anything again.

--save keeps each query's results in experiments/<today>_retrieval/, and --compare judges one
saved route against another query by query (see evaluate.compare).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from tutor import llm
from tutor.config import WorkspaceError, load_env, load_workspace
from tutor.retrieval.agent import AgentRetriever, tool_settings
from tutor.retrieval.asker import ModelAsker, first_run_cost, settings_folder
from tutor.retrieval.bm25 import BM25Retriever
from tutor.retrieval.concepts import (
    ConceptRetriever,
    ConceptStore,
    build_lecture,
    by_lecture,
)
from tutor.retrieval.corpus import Page, load_pages, load_settings
from tutor.retrieval.evaluate import (
    BUDGETS,
    PRIMARY_BUDGET,
    RANKS,
    Result,
    Retriever,
    baselines,
    compare,
    evaluate,
)
from tutor.retrieval.hybrid import HybridRetriever, NeighbourRetriever
from tutor.retrieval.labels import freeze, load_frozen, load_queries
from tutor.retrieval.vectors import DIMENSIONS, EmbeddingStore, VectorRetriever

# hybrid-near runs only when asked for: it is a control to compare the agent against.
ROUTES = ("bm25", "vector", "hybrid", "hybrid-near", "concept", "agent")


@dataclass(frozen=True)
class AgentOptions:
    search: str = "bm25"
    thinking: bool = False


def format_table(results: list[Result]) -> str:
    """One row per route and scope: recall as a share, then what the route actually returned
    within the largest budget, and this run's time (a stored answer takes none)."""
    header = (["scope", "route"] + [f"R@{k}" for k in RANKS] + ["MRR"]
              + [f"R<{b}" for b in BUDGETS] + ["pages", "tokens", "ms"])
    rows = [header]
    for r in results:
        rows.append([r.scope, r.route]
                    + [f"{r.recall_at[k]:.2f}" for k in RANKS]
                    + [f"{r.mrr:.2f}"]
                    + [f"{r.recall_within[b]:.2f}" for b in BUDGETS]
                    + [f"{r.pages_returned:.1f}", f"{r.tokens_returned:.0f}",
                       f"{r.ms_per_query:.1f}"])
    widths = [max(len(row[i]) for row in rows) for i in range(len(header))]
    return "\n".join("  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True))
                     for row in rows)


def _log_path(workspace: Path) -> Path:
    return workspace / "logs" / "llm_calls.jsonl"


def _embedding_store(workspace: Path) -> EmbeddingStore:
    """The store for the workspace's embedding model. Its file name holds the model and the
    size, as vectors from different models cannot be compared."""
    config = llm.config_for("embedding", workspace=workspace)

    def embed(texts: list[str]) -> list[list[float]]:
        return llm.embed(config, texts, purpose="embedding", log_path=_log_path(workspace),
                         dimensions=DIMENSIONS)

    name = f"{config.model.replace('/', '_')}-{DIMENSIONS}.jsonl"
    return EmbeddingStore(workspace / "cache" / "retrieval" / name, embed)


def _concept_tools(workspace: Path) -> tuple[ConceptStore, ModelAsker]:
    """The index folder for the workspace's concept model, and a way to ask that model."""
    asker = ModelAsker(llm.config_for("concept", workspace=workspace), _log_path(workspace))
    folder = settings_folder(workspace / "cache" / "retrieval" / "concepts", asker.settings)
    return ConceptStore(folder), asker


@dataclass
class Routes:
    """The routes of a run, and what can say what they spent."""

    built: dict[str, Retriever] = field(default_factory=dict)
    embeddings: EmbeddingStore | None = None
    asker: ModelAsker | None = None
    concept: ConceptRetriever | None = None
    agent: AgentRetriever | None = None
    agent_asker: ModelAsker | None = None

    def spending(self) -> list[str]:
        """What this run paid for, and what the answers it reads cost when they were made."""
        lines = []
        if self.embeddings is not None:
            lines.append(f"texts sent to the embedding model this run: "
                         f"{self.embeddings.embedded}")
        if self.concept is not None:
            lines.append(f"concept model calls this run: {self.asker.calls}, tokens in "
                         f"{self.asker.input_tokens}, out {self.asker.output_tokens}; "
                         f"{first_run_cost(self.concept.used)}")
        if self.agent is not None and self.agent.used:
            used = self.agent.used
            done = sum(row["finished"] for row in used)
            steps = sum(row["steps"] for row in used) / len(used)
            lines.append(f"agent model calls this run: {self.agent_asker.calls}, tokens in "
                         f"{self.agent_asker.input_tokens}, out {self.agent_asker.output_tokens}; "
                         f"{done} of {len(used)} searches finished, {steps:.1f} steps each; "
                         f"{first_run_cost(used)}")
        return lines


def build_routes(pages: list[Page], workspace: Path, names: list[str] | None, run: int = 0,
                 agent: AgentOptions | None = None) -> Routes:
    """The routes asked for (default: all that can run here). A route explicitly asked for
    that cannot run is an error; left to the default, it is simply not run."""
    options = agent or AgentOptions()
    roles = llm.roles(workspace)
    has_embedding, has_concept = "embedding" in roles, "concept" in roles
    concept_tools = _concept_tools(workspace) if has_concept else None
    if names is None:
        names = ["bm25"] + (["vector", "hybrid"] if has_embedding else [])
        if concept_tools and all(concept_tools[0].load(lecture_pages) is not None
                                 for lecture_pages in by_lecture(pages).values()):
            names.append("concept")
        if "agent" in roles:
            names.append("agent")
    needs_vectors = bool({"vector", "hybrid", "hybrid-near"} & set(names)) or (
        "agent" in names and options.search == "hybrid")
    if needs_vectors and not has_embedding:
        raise ValueError("the vector and hybrid routes need an `embedding` role in [llm.roles]")
    if "concept" in names and not has_concept:
        raise ValueError("the concept route needs a `concept` role in [llm.roles]")
    if "agent" in names and "agent" not in roles:
        raise ValueError("the agent route needs an `agent` role in [llm.roles]")
    routes = Routes()
    bm25 = BM25Retriever(pages)
    built: dict[str, Retriever] = {"bm25": bm25}
    if needs_vectors:
        # Hybrid is made of the other two, so vector is built whichever of them was asked for.
        routes.embeddings = _embedding_store(workspace)
        vector = VectorRetriever(pages, routes.embeddings)
        hybrid = HybridRetriever([bm25, vector])
        built |= {"vector": vector, "hybrid": hybrid,
                  "hybrid-near": NeighbourRetriever(hybrid, pages, "hybrid-near")}
    if "concept" in names:
        store, routes.asker = concept_tools
        routes.concept = ConceptRetriever(pages, store, routes.asker, run=run)
        built["concept"] = routes.concept
    if "agent" in names:
        routes.agent_asker = ModelAsker(llm.config_for("agent", workspace=workspace),
                                        _log_path(workspace), thinking=options.thinking)
        folder = settings_folder(workspace / "cache" / "retrieval" / "agent",
                                 {**routes.agent_asker.settings, **tool_settings(options.search)})
        # The variant is in the name, so a saved comparison can tell them apart.
        label = "agent" + ("+hybrid" if options.search == "hybrid" else "") + (
            "+think" if options.thinking else "")
        routes.agent = AgentRetriever(pages, routes.agent_asker, folder, run=run, name=label,
                                      search_route=built.get(options.search))
        built["agent"] = routes.agent
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


def _queries(workspace: Path, pages: list[Page], split: str) -> tuple[list, list[str]]:
    """The queries of a split: from the snapshot once frozen, which is required for anything but
    tuning, so that the final numbers rest on labels nobody can have adjusted since."""
    frozen = load_frozen(workspace)
    if frozen is not None:
        return frozen, []
    if split != "dev":
        raise ValueError(f"--split {split} needs frozen queries: run --freeze-queries first")
    return load_queries(workspace, pages)


def _save_path(workspace: Path, name: str) -> Path:
    return workspace / "experiments" / f"{date.today().isoformat()}_retrieval" / f"{name}.json"


def save_results(path: Path, results: list[Result], args: argparse.Namespace) -> None:
    """Each query's results, in the private workspace: queries and pages are course content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"split": args.split, "run": args.run, "routes": {}}
    for r in results:
        data["routes"].setdefault(r.route, {})[r.scope] = {
            "recall_within": r.recall_within, "mrr": r.mrr, "pages": r.pages_returned,
            "tokens": r.tokens_returned, "per_query": list(r.per_query)}
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")


def _saved_rows(workspace: Path, spec: str, scope: str) -> list[dict]:
    """The rows of "NAME/ROUTE": NAME as given to --save, on any day, the latest if several."""
    name, _, route = spec.partition("/")
    found = sorted((workspace / "experiments").glob(f"*_retrieval/{name}.json"))
    if not found:
        raise ValueError(f"no saved run named {name!r}")
    routes = json.loads(found[-1].read_text(encoding="utf-8"))["routes"]
    if route not in routes or scope not in routes[route]:
        raise ValueError(f"{name!r} has no {route!r} in scope {scope!r}; it has: {sorted(routes)}")
    return routes[route][scope]["per_query"]


def run_compare(workspace: Path, base: str, other: str, scope: str) -> None:
    verdict = compare(_saved_rows(workspace, base, scope), _saved_rows(workspace, other, scope))
    print(f"{other} against {base}, {scope}, recall within {PRIMARY_BUDGET} tokens: "
          f"{verdict['better']} better, {verdict['worse']} worse, {verdict['same']} same, "
          f"mean change {verdict['mean_change']:+.3f}; "
          + ("adopt" if verdict["adopt"] else "keep the simpler setting"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tutor.retrieval")
    parser.add_argument("--split", choices=("dev", "test", "cross"), default="dev")
    parser.add_argument("--route", choices=ROUTES, action="append", dest="routes")
    parser.add_argument("--lecture", help="only this lecture's queries, searching it alone")
    parser.add_argument("--run", type=int, default=0, help="which repeated measurement this is")
    parser.add_argument("--agent-search", choices=("bm25", "hybrid"), default="bm25")
    parser.add_argument("--agent-thinking", action="store_true")
    parser.add_argument("--save", metavar="NAME", help="keep each query's results under this name")
    parser.add_argument("--compare", nargs=2, metavar="NAME/ROUTE",
                        help="judge the second saved route against the first, then stop")
    parser.add_argument("--scope", choices=("lecture", "course"), default="lecture",
                        help="which scope --compare judges")
    parser.add_argument("--build-concepts", nargs="*", metavar="LECTURE",
                        help="write the concept index of these lectures (none: all), then stop")
    parser.add_argument("--freeze-queries", action="store_true",
                        help="snapshot every query and label, then stop")
    args = parser.parse_args(argv)
    load_env()
    try:
        workspace = load_workspace().root
        if args.compare:
            run_compare(workspace, *args.compare, args.scope)
            return 0
        pages = load_pages(workspace, load_settings(workspace).exclude_lectures)
        if args.freeze_queries:
            frozen = freeze(workspace, pages)
            print(f"frozen: {len(frozen)} queries, labels included")
            return 0
        if args.build_concepts is not None:
            build_concepts(pages, workspace, args.build_concepts)
            return 0
        loaded, problems = _queries(workspace, pages, args.split)
        chosen = [q for q in loaded if q.split == args.split
                  and (args.lecture is None or q.lectures == {args.lecture})]
        if not chosen:
            print(f"retrieval: no {args.split} queries found", file=sys.stderr)
            return 1
        routes = build_routes(pages, workspace, args.routes, args.run,
                              AgentOptions(args.agent_search, args.agent_thinking))
        for problem in problems:
            print(f"not measured: {problem}", file=sys.stderr)
        print(f"{args.split}: {len(chosen)} queries, {len(pages)} pages, run {args.run}")
        scopes = (("lecture",) if args.lecture else
                  ("course",) if args.split == "cross" else ("lecture", "course"))
        results = [evaluate(route, chosen, scope)
                   for scope in scopes for route in routes.built.values()]
        print(format_table(results))
        if args.save:
            path = _save_path(workspace, args.save)
            save_results(path, results, args)
            print(f"saved to {path}")
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
