"""The agentic search route: a model looks for the pages itself (docs/design.md, "RAG").

The other routes rank pages for one fixed query. Here a model works like a person with a search
box: it searches, looks at what came back, reads what seems promising, tries other words, and
says when it is done. It is a loop of two nodes, wired with LangGraph:

    decide --search or read--> act --> decide
       |
       +--finish, or out of steps--> end

The model chooses each action as a JSON object (`Action`) through the client's structured
output, which every profile supports, so the client needs no tool-calling support. The tools
themselves are plain code: searching is another route (BM25 or hybrid), and reading returns a
page's text. The model never sees a page it did not ask for, and every id it names is checked.

A whole run is kept on disk per query, so asking again costs nothing, with its trace: what the
model saw, read and chose, which tells "never found" from "found but not chosen". `run` separates
repeated measurements, since a model's path may differ between runs, which is worth knowing.
"""

from __future__ import annotations

import time
from collections.abc import Collection
from pathlib import Path
from typing import Literal, TypedDict

from pydantic import BaseModel

from tutor.retrieval.asker import Ask, digest, usage_of
from tutor.retrieval.bm25 import BM25Retriever
from tutor.retrieval.corpus import Page
from tutor.retrieval.jsonl import JsonLines

# Part of what a stored run depends on: a new version runs again. Version 2 (2026-11-02): the
# first one chose accurately but too few pages; on the dev queries, 46 of the 51 pages it missed
# within a lecture were within two pages of one it returned.
PROMPT_VERSION = 2
MAX_STEPS = 6  # the model's turns; bounds the cost and time of one query
SEARCH_RESULTS = 10  # pages one search shows
MAX_READ = 5  # pages one read returns, so one action cannot pull in a whole lecture
# A limit, not a target: an action is short, but a model's thinking may count towards this too.
STEP_MAX_TOKENS = 4_000
_TITLE_CHARS = 80

SYSTEM_PROMPT = f"""\
You find the pages of a course's lecture slides that teach what a reader asks for. You cannot
see the slides; you act, one action per reply, and see what each action returns.

- search: `words` are keywords to rank pages by, so use the terms slides would use, API names
  included. `lecture` optionally narrows it to one lecture. Returns up to {SEARCH_RESULTS} pages,
  each as: page id | slide | its first line.
- read: `pages` are up to {MAX_READ} page ids. Returns their text.
- finish: `pages` are the ids of every page that teaches the request, the most useful first.
  A topic usually runs over several consecutive pages, so when a page fits, look at the page
  ids just before and after it: those that continue the topic or show its example or code count
  too. Missing a page is worse than including one too many.

If a search finds nothing useful, try other words. Read a page before choosing it when its
first line does not settle it. You have {MAX_STEPS} actions."""


class Action(BaseModel):
    action: Literal["search", "read", "finish"]
    words: str = ""
    lecture: str | None = None
    pages: list[str] = []


class AgentState(TypedDict):
    query: str
    scope: list[str]  # the lectures this query may search
    log: list[str]  # what has happened so far, as the model will read it
    steps: int  # the model's turns so far
    action: dict | None  # the action just chosen; None if the reply could not be read
    read: list[str]  # page ids read so far, in order
    seen: list[str]  # page ids any search has shown, in order, each once
    trace: list[dict]  # every action chosen, for finding out afterwards why a page was missed
    failed: int  # turns lost to a reply that could not be read as an action


def _page_id(page: Page) -> str:
    return f"{page.lecture}:{page.number}"


def _title(page: Page) -> str:
    """The page's first line that says something: slides often start with their own footer."""
    for line in page.text.splitlines():
        text = line.strip().strip("#*_ ")
        if text and text != page.slide:
            return text[:_TITLE_CHARS]
    return ""


def tool_settings(search_name: str) -> dict:
    """What a stored run depends on besides the model; see asker.settings_folder."""
    return {"route": "agent", "prompt_version": PROMPT_VERSION, "max_steps": MAX_STEPS,
            "search_results": SEARCH_RESULTS, "max_read": MAX_READ,
            "step_max_tokens": STEP_MAX_TOKENS, "search": search_name}


class AgentRetriever:
    def __init__(self, pages: list[Page], ask: Ask, folder: Path, run: int = 0,
                 search_route=None, name: str = "agent") -> None:
        self.name = name
        self._pages = {_page_id(page): page for page in pages}
        self._lectures = list(dict.fromkeys(page.lecture for page in pages))
        self._search_route = search_route or BM25Retriever(pages)
        self._ask = ask
        self._run = run
        self._corpus = digest([(page.key, page.text) for page in pages])  # for the stored runs
        self._file = JsonLines(folder / "runs.jsonl")
        self._runs = {row["key"]: row for row in self._file.read()}
        self._graph = None  # built on first use: only a run needs langgraph
        self.used: list[dict] = []  # this process's runs, stored or new, for the summary

    # --- the nodes: plain functions of the state, which LangGraph only connects -------------

    def decide(self, state: AgentState) -> dict:
        """One model turn: choose the next action, given everything so far."""
        step = state["steps"] + 1
        last = " This is the last one: finish now." if step == MAX_STEPS else ""
        # What changes comes last, so the prompt's start stays the same from turn to turn,
        # which is what a provider's prompt cache can reuse.
        user = (f"Request: {state['query']}\n"
                f"Lectures you may search: {', '.join(state['scope'])}\n\n"
                + "\n\n".join(state["log"])
                + f"\n\nAction {step} of {MAX_STEPS}.{last}")
        try:
            reply = self._ask("agent-step", SYSTEM_PROMPT, user, Action.model_json_schema(),
                              STEP_MAX_TOKENS)
            action = Action.model_validate_json(reply).model_dump()
        except ValueError:  # not an action, or cut off: costs the turn, not the whole query
            return {"steps": step, "action": None, "failed": state["failed"] + 1,
                    "log": [*state["log"], "Your last reply could not be read as an action."]}
        return {"steps": step, "action": action, "trace": [*state["trace"], action]}

    def act(self, state: AgentState) -> dict:
        """Carry out the action chosen. Plain code: no model is involved here."""
        action = state["action"]
        if action is None or action["action"] == "finish":
            return {}
        if action["action"] == "search":
            return self._search(action, state)
        return self._read(action, state)

    def next_node(self, state: AgentState) -> str:
        return "end" if self.is_done(state) else "act"

    @staticmethod
    def is_done(state: AgentState) -> bool:
        """Finished, or out of turns. A last action that is not `finish` is not carried out:
        the model would never see its result."""
        action = state["action"]
        return state["steps"] >= MAX_STEPS or bool(action and action["action"] == "finish")

    def _search(self, action: dict, state: AgentState) -> dict:
        words, lecture, scope = action["words"], action["lecture"], state["scope"]
        # An embedding model refuses empty text, and it would rank nothing anyway. The model has
        # sent this (2026-11-02): it costs that turn, not the whole query.
        if not words.strip():
            return {"log": [*state["log"], "search: it needs `words`; nothing was searched."]}
        if lecture is not None and lecture not in scope:
            text = f"search {words!r} in lecture {lecture}: you may only search {', '.join(scope)}."
            return {"log": [*state["log"], text]}
        wanted = {lecture} if lecture else set(scope)
        found = self._search_route.search(words, wanted)[:SEARCH_RESULTS]
        lines = [f"{_page_id(p)} | {p.slide or '-'} | {_title(p)}" for p in found]
        text = f"search {words!r}:\n" + ("\n".join(lines) if lines else "(no page has these words)")
        seen = list(dict.fromkeys([*state["seen"], *map(_page_id, found)]))
        return {"log": [*state["log"], text], "seen": seen}

    def _read(self, action: dict, state: AgentState) -> dict:
        wanted = action["pages"][:MAX_READ]
        known = [i for i in wanted if self._allowed(i, state["scope"])]
        parts = [f"## {i}\n{self._pages[i].text}" for i in known]
        if unknown := [i for i in wanted if i not in known]:
            parts.append(f"No such pages here: {', '.join(unknown)}")
        return {"log": [*state["log"], "read:\n" + "\n\n".join(parts or ["(nothing)"])],
                "read": [*state["read"], *known]}

    def _allowed(self, page_id: str, scope: list[str]) -> bool:
        page = self._pages.get(page_id)
        return page is not None and page.lecture in scope

    def finish_pages(self, state: AgentState) -> tuple[list[str], bool]:
        """The page ids to return, and whether the model finished. If it ran out of turns, the
        pages it read are the best evidence of what it found, though it may have read some and
        rejected them, so a run that did not finish is counted and reported as such."""
        action = state["action"]
        finished = bool(action and action["action"] == "finish")
        chosen = action["pages"] if finished else state["read"]
        # A page named twice counts once, and a made-up or out-of-scope id not at all.
        return ([i for i in dict.fromkeys(chosen) if self._allowed(i, state["scope"])], finished)

    # --- the route ------------------------------------------------------------------------

    def search(self, query: str, lectures: Collection[str] | None = None) -> list[Page]:
        scope = [lecture for lecture in self._lectures
                 if lectures is None or lecture in lectures]
        key = digest(self._run, query, scope, self._corpus)
        if key not in self._runs:
            in_before, out_before = usage_of(self._ask)
            start = time.monotonic()
            state = self._invoke({"query": query, "scope": scope, "log": [], "steps": 0,
                                  "action": None, "read": [], "seen": [], "trace": [],
                                  "failed": 0})
            in_after, out_after = usage_of(self._ask)
            pages, finished = self.finish_pages(state)
            row = {"key": key, "query": query, "scope": scope, "run": self._run, "pages": pages,
                   "finished": finished, "steps": state["steps"], "failed": state["failed"],
                   "seen": state["seen"], "read": state["read"], "trace": state["trace"],
                   "tokens_in": in_after - in_before, "tokens_out": out_after - out_before,
                   "seconds": round(time.monotonic() - start, 2)}
            self._file.append([row])
            self._runs[key] = row
        self.used.append(self._runs[key])
        return [self._pages[page_id] for page_id in self._runs[key]["pages"]]

    def _invoke(self, state: AgentState) -> AgentState:
        if self._graph is None:
            self._graph = self._build_graph()
        # Each turn passes through two nodes, so this limit is never the one that stops a run.
        return self._graph.invoke(state, {"recursion_limit": 2 * MAX_STEPS + 2})

    def _build_graph(self):
        try:
            from langgraph.graph import END, START, StateGraph
        except ImportError:
            raise ValueError('the agent route needs langgraph: pip install -e ".[gen]"') from None
        graph = StateGraph(AgentState)
        graph.add_node("decide", self.decide)
        graph.add_node("act", self.act)
        graph.add_edge(START, "decide")
        graph.add_conditional_edges("decide", self.next_node, {"act": "act", "end": END})
        graph.add_edge("act", "decide")
        return graph.compile()
