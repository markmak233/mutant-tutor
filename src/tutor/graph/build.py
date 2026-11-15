"""Wire nodes into LangGraph graphs; model-facing nodes stay independent of the scheduler.

One item (`oracle` passes the draft through when no `oracle` role is set, and goes
straight to repair or discard when the oracle model could not write the oracle):

    retrieve_context -> generate -> oracle -> validate --VALID--> save
                                      ^          |
                                      |          +--fail, rounds left--> repair --+
                                      +--------------------------------------------+
                                                 +--fail, no rounds left--> discard

A batch:

    plan -> approve (interrupt: the owner approves or sends it back) --back--> plan
                |
                +--approved--> item x N (the graph above, in parallel) --> answers
                                  ^                                          |
                                  +-- items waiting, once answered ----------+
                                                          nothing waiting --> collect

`answers` matters only in assistant mode (tutor/exchange.py): an item that stopped at an
unanswered request is "waiting"; the batch pauses once for all of them (an interrupt the
CLI reports), and after the answers it runs those items again, replaying their answered
calls.

How LangGraph runs these, in the terms the code below uses:
- A StateGraph is built on a state type (ItemState, BatchState in state.py: TypedDicts). A
  node is a function of the whole state that returns only the keys it changes; LangGraph
  merges that partial update into the state. A key with a reducer (BatchState's `results`,
  `Annotated[list, operator.add]`) is appended to instead of replaced, which is what lets
  items running in parallel each add their own result without overwriting the others'.
- add_edge(a, b): after a, always b. add_conditional_edges(a, router, targets): after a, the
  router (a plain function of the state) names the next node; `targets` lists every name it
  may return, so the graph can be checked when compiled.
- Send("item", payload): a router may return a list of these instead of a name; each one
  runs the node `item` once with its own payload, all in parallel (the fan-out), up to the
  run's `max_concurrency`. Their updates meet again in the next node (the fan-in).
- interrupt(value): stops the run inside a node and hands `value` to the caller; the state
  is saved by the checkpointer. `Command(resume=answer)` later (another process is fine)
  runs that node again from its start, and this time interrupt() returns `answer`.
- compile(checkpointer=...): a batch's graph keeps its state in SQLite (graph/__main__.py
  passes a SqliteSaver on the draft folder's checkpoint.sqlite, the thread_id being the
  batch's folder name); an item's graph keeps none.

The model-facing work is in the nodes (nodes.py, oracle.py, drafts.py, batch.py): this
module only says which runs after which, and no model ever chooses the next step; the
routers read the deterministic validator's result and the round and token counts.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from tutor.container import check_course
from tutor.exchange import AwaitingAnswer
from tutor.graph import batch, drafts, nodes, oracle
from tutor.graph.nodes import Deps
from tutor.graph.plan_selection import selected_rows
from tutor.graph.state import BatchState, ItemState


def build_item(deps: Deps):
    """The compiled graph for one plan item."""
    graph = StateGraph(ItemState)
    # The nodes. Each lambda binds the batch's Deps (models, settings, the course folder),
    # since LangGraph calls a node with the state alone.
    #   retrieve_context: the lecture pages the item's slides name (the plan named them, so
    #                     nothing is searched); stops the item if one is not fully read
    #   generate:         the model writes question, tests, reference and mutants
    #   oracle:           another model writes an independent specification (oracle.py)
    #   validate:         the deterministic validator runs everything (no model)
    #   repair:           the model fixes the draft, given the validator's report
    #   save / discard:   copy the valid round into the batch, or record why it failed
    graph.add_node("retrieve_context", lambda s: nodes.retrieve_context(s, deps))
    graph.add_node("generate", lambda s: nodes.generate(s, deps))
    graph.add_node("oracle", lambda s: oracle.write_oracle(s, deps))
    graph.add_node("validate", lambda s: drafts.validate_draft(s, deps))
    graph.add_node("repair", lambda s: nodes.repair(s, deps))
    graph.add_node("save", lambda s: nodes.save(s, deps))
    graph.add_node("discard", lambda s: nodes.discard(s, deps))
    # The fixed part: every item reads its pages, is generated, then gets its oracle.
    graph.add_edge(START, "retrieve_context")
    graph.add_edge("retrieve_context", "generate")
    graph.add_edge("generate", "oracle")
    # After the oracle: validate, unless the oracle could not be written; then that round has
    # failed already, and nodes.route decides between repair and discard as after validation.
    graph.add_conditional_edges("oracle", lambda s: oracle.after_oracle(s, deps),
                                ["validate", "repair", "discard"])
    # After validation (nodes.route): VALID -> save; failed with rounds and token budget
    # left -> repair; else discard. Code decides, from the validator's verdict and counts.
    graph.add_conditional_edges("validate", lambda s: nodes.route(s, deps),
                                ["save", "repair", "discard"])
    # The repair loop: a repaired draft gets a new oracle and is validated again, so every
    # round is checked the same way as the first.
    graph.add_edge("repair", "oracle")
    graph.add_edge("save", END)
    graph.add_edge("discard", END)
    # checkpointer=False: run inside a batch, it must not inherit the batch's checkpointer
    # (it would save every step with its models in the batch's SQLite); a stopped batch
    # redoes a whole unfinished item anyway.
    return graph.compile(checkpointer=False)


def build_batch(deps: Deps, checkpointer):
    """The compiled graph for a batch. The checkpointer keeps its state between runs, so
    the owner can approve the plan in a later process; run it with a thread_id and
    `max_concurrency` (items generated at once) in the config."""
    # Compiled once and run inside the `item` node below: a graph used as a function.
    item_graph = build_item(deps)

    # --- the batch's nodes and routers, defined here so they can reach deps and item_graph --

    def approve(state: BatchState) -> BatchState:
        # Pauses the graph; `Command(resume=decision)` continues it, also in a new process.
        # On resume this node runs again from the top and interrupt() returns the decision
        # (approve, possibly with an edited plan and a selection, or send back with feedback).
        decision = interrupt({"plan": state["plan"], "problems": state.get("plan_problems", [])})
        return batch.apply_decision(state, decision, batch.existing_ids(deps.workspace),
                                    deps.profile)

    def after_approve(state: BatchState):
        # Sent back: plan again, with the owner's feedback in the state (batch.apply_decision).
        if not state.get("approved"):
            return "plan"
        # One lecture, then one kind of exercise, after another: an item started after
        # another with the same course pages (and example) finds them in the provider's
        # prompt cache.
        ordered = sorted(selected_rows(state), key=lambda p: (p["lecture"], p["framework"],
                                                       p["interface"]))
        # The fan-out: one `item` run per selected plan row, in parallel (see the docstring).
        return [Send("item", {"item": p}) for p in ordered]

    def item(payload: dict) -> BatchState:
        # A Send's node gets the Send's payload, not the batch state: one plan row, kept as a
        # plain dict in the state (so it checkpoints as data), checked into the language
        # profile's PlanItem again.
        plan_item = deps.profile.plan_item.model_validate(payload["item"])
        final: ItemState = {"item": plan_item}
        try:
            # Docker may have stopped since the batch began. The item that finds out reports
            # an error like any other, and the ones after it spend no tokens to do the same.
            check_course(deps.workspace)
            # stream(..., "updates") yields {node: its update} after each node, so the page
            # (deps.on_update) shows the item's progress, and `final` gathers the whole state.
            for update in item_graph.stream({"item": plan_item}, stream_mode="updates"):
                for node, change in update.items():
                    final.update(change or {})
                    if deps.on_update:
                        deps.on_update(plan_item.id, node, change or {})
        except AwaitingAnswer as e:
            # Not an error: an assistant has a request to answer; the item runs again then.
            return {"results": [batch.item_summary(plan_item, final, waiting=e.request_id)]}
        except Exception as e:  # noqa: BLE001 - one item must never stop the others
            # The model unreachable, a name clash, a reply that breaks the client: this
            # item is reported as an error, the others run on and collect still runs.
            error = f"{type(e).__name__}: {e}"
            nodes.log_error(final, deps, error)
            return {"results": [batch.item_summary(plan_item, final, error)]}
        # One summary per run: `results` has the operator.add reducer, so parallel items'
        # lists are joined, never overwritten.
        return {"results": [batch.item_summary(plan_item, final)]}

    def waiting(state: BatchState) -> list[dict]:
        # The items whose latest result is "waiting" (an assistant's answer is due); an item
        # run again has an older result too, so only its last counts (batch.latest_results).
        return [r for r in batch.latest_results(state.get("results", []))
                if r["outcome"] == "waiting"]

    def answers(state: BatchState) -> BatchState:
        # Runs once all items of a round have stopped. With requests waiting it pauses the
        # batch; `resume` continues here when they are answered.
        if found := waiting(state):
            interrupt({"waiting": [r["request"] for r in found]})
        return {}

    def after_answers(state: BatchState):
        # Answered: run only the items that waited again (a second fan-out); their answered
        # calls are replayed from the files, so nothing is asked twice. Else collect.
        if found := waiting(state):
            ids = {r["id"] for r in found}
            return [Send("item", {"item": p}) for p in selected_rows(state) if p["id"] in ids]
        return "collect"

    # --- the wiring: plan -> approve -> item x N -> answers -> collect ----------------------
    graph = StateGraph(BatchState)
    graph.add_node("plan", lambda s: batch.plan_batch(s, deps))
    graph.add_node("approve", approve)
    graph.add_node("item", item)
    graph.add_node("answers", answers)
    graph.add_node("collect", lambda s: batch.collect(s, deps))
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "approve")
    graph.add_conditional_edges("approve", after_approve, ["plan", "item"])
    # Every item's run leads here; LangGraph runs `answers` once all of them have ended.
    graph.add_edge("item", "answers")
    graph.add_conditional_edges("answers", after_answers, ["item", "collect"])
    graph.add_edge("collect", END)
    # With the checkpointer, a batch survives its process: approve, answers and a stopped
    # run all continue from the saved state under the same thread_id.
    return graph.compile(checkpointer=checkpointer)
