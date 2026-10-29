"""Wire the nodes into LangGraph graphs. The only module that imports langgraph.

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
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from tutor.exchange import AwaitingAnswer
from tutor.graph import nodes
from tutor.graph.nodes import Deps
from tutor.graph.state import BatchState, ItemState, PlanItem


def build_item(deps: Deps):
    """The compiled graph for one plan item."""
    graph = StateGraph(ItemState)
    graph.add_node("retrieve_context", lambda s: nodes.retrieve_context(s, deps))
    graph.add_node("generate", lambda s: nodes.generate(s, deps))
    graph.add_node("oracle", lambda s: nodes.write_oracle(s, deps))
    graph.add_node("validate", lambda s: nodes.validate_draft(s, deps))
    graph.add_node("repair", lambda s: nodes.repair(s, deps))
    graph.add_node("save", lambda s: nodes.save(s, deps))
    graph.add_node("discard", lambda s: nodes.discard(s, deps))
    graph.add_edge(START, "retrieve_context")
    graph.add_edge("retrieve_context", "generate")
    graph.add_edge("generate", "oracle")
    graph.add_conditional_edges("oracle", lambda s: nodes.after_oracle(s, deps),
                                ["validate", "repair", "discard"])
    graph.add_conditional_edges("validate", lambda s: nodes.route(s, deps),
                                ["save", "repair", "discard"])
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
    item_graph = build_item(deps)

    def approve(state: BatchState) -> BatchState:
        # Pauses the graph; `Command(resume=decision)` continues it, also in a new process.
        decision = interrupt({"plan": state["plan"], "problems": state.get("plan_problems", [])})
        return nodes.apply_decision(state, decision, nodes.existing_ids(deps.workspace))

    def after_approve(state: BatchState):
        if not state.get("approved"):
            return "plan"
        # One lecture, then one kind of exercise, after another: an item started after
        # another with the same course pages (and example) finds them in the provider's
        # prompt cache.
        ordered = sorted(state["plan"], key=lambda p: (p["lecture"], p["framework"],
                                                       p["interface"]))
        return [Send("item", {"item": p}) for p in ordered]

    def item(payload: dict) -> BatchState:
        plan_item = PlanItem.model_validate(payload["item"])
        final: ItemState = {"item": plan_item}
        try:
            for update in item_graph.stream({"item": plan_item}, stream_mode="updates"):
                for node, change in update.items():
                    final.update(change or {})
                    if deps.on_update:
                        deps.on_update(plan_item.id, node, change or {})
        except AwaitingAnswer as e:
            # Not an error: an assistant has a request to answer; the item runs again then.
            return {"results": [nodes.item_summary(plan_item, final, waiting=e.request_id)]}
        except Exception as e:  # noqa: BLE001 - one item must never stop the others
            # The model unreachable, a name clash, a reply that breaks the client: this
            # item is reported as an error, the others run on and collect still runs.
            error = f"{type(e).__name__}: {e}"
            nodes.log_error(final, deps, error)
            return {"results": [nodes.item_summary(plan_item, final, error)]}
        return {"results": [nodes.item_summary(plan_item, final)]}

    def waiting(state: BatchState) -> list[dict]:
        return [r for r in nodes.latest_results(state.get("results", []))
                if r["outcome"] == "waiting"]

    def answers(state: BatchState) -> BatchState:
        # Runs once all items of a round have stopped. With requests waiting it pauses the
        # batch; `resume` continues here when they are answered.
        if found := waiting(state):
            interrupt({"waiting": [r["request"] for r in found]})
        return {}

    def after_answers(state: BatchState):
        if found := waiting(state):
            ids = {r["id"] for r in found}
            return [Send("item", {"item": p}) for p in state["plan"] if p["id"] in ids]
        return "collect"

    graph = StateGraph(BatchState)
    graph.add_node("plan", lambda s: nodes.plan_batch(s, deps))
    graph.add_node("approve", approve)
    graph.add_node("item", item)
    graph.add_node("answers", answers)
    graph.add_node("collect", lambda s: nodes.collect(s, deps))
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "approve")
    graph.add_conditional_edges("approve", after_approve, ["plan", "item"])
    graph.add_edge("item", "answers")
    graph.add_conditional_edges("answers", after_answers, ["item", "collect"])
    graph.add_edge("collect", END)
    return graph.compile(checkpointer=checkpointer)
