"""Wire the nodes into LangGraph graphs. The only module that imports langgraph.

One item:

    retrieve_context -> generate -> validate --VALID--> save
                                      ^   |
                                      |   +--fail, rounds left--> repair --+
                                      +-------------------------------------+
                                          +--fail, no rounds left--> discard

A batch:

    plan -> approve (interrupt: the owner approves or sends it back) --back--> plan
                |
                +--approved--> item x N (the graph above, in parallel) --> collect
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from tutor.graph import nodes
from tutor.graph.nodes import Deps
from tutor.graph.state import BatchState, ItemState, PlanItem


def build_item(deps: Deps):
    """The compiled graph for one plan item."""
    graph = StateGraph(ItemState)
    graph.add_node("retrieve_context", lambda s: nodes.retrieve_context(s, deps))
    graph.add_node("generate", lambda s: nodes.generate(s, deps))
    graph.add_node("validate", lambda s: nodes.validate_draft(s, deps))
    graph.add_node("repair", lambda s: nodes.repair(s, deps))
    graph.add_node("save", lambda s: nodes.save(s, deps))
    graph.add_node("discard", lambda s: nodes.discard(s, deps))
    graph.add_edge(START, "retrieve_context")
    graph.add_edge("retrieve_context", "generate")
    graph.add_edge("generate", "validate")
    graph.add_conditional_edges("validate", lambda s: nodes.route(s, deps),
                                ["save", "repair", "discard"])
    graph.add_edge("repair", "validate")
    graph.add_edge("save", END)
    graph.add_edge("discard", END)
    return graph.compile()


def build_batch(deps: Deps, checkpointer):
    """The compiled graph for a batch. The checkpointer keeps its state between runs, so
    the owner can approve the plan in a later process; run it with a thread_id and
    `max_concurrency` (items generated at once) in the config."""
    item_graph = build_item(deps)

    def approve(state: BatchState) -> BatchState:
        # Pauses the graph; `Command(resume=decision)` continues it, also in a new process.
        decision = interrupt({"plan": state["plan"], "problems": state.get("plan_problems", [])})
        return nodes.apply_decision(state, decision)

    def after_approve(state: BatchState):
        if not state.get("approved"):
            return "plan"
        return [Send("item", {"item": p}) for p in state["plan"]]

    def item(payload: dict) -> BatchState:
        plan_item = PlanItem.model_validate(payload["item"])
        final: ItemState = {"item": plan_item}
        try:
            for update in item_graph.stream({"item": plan_item}, stream_mode="updates"):
                for node, change in update.items():
                    final.update(change or {})
                    if deps.on_update:
                        deps.on_update(plan_item.id, node, change or {})
        except Exception as e:  # noqa: BLE001 - one item must never stop the others
            # The model unreachable, a name clash, a reply that breaks the client: this
            # item is reported as an error, the others run on and collect still runs.
            error = f"{type(e).__name__}: {e}"
            nodes.log_error(final, deps, error)
            return {"results": [nodes.item_summary(plan_item, final, error)]}
        return {"results": [nodes.item_summary(plan_item, final)]}

    graph = StateGraph(BatchState)
    graph.add_node("plan", lambda s: nodes.plan_batch(s, deps))
    graph.add_node("approve", approve)
    graph.add_node("item", item)
    graph.add_node("collect", lambda s: nodes.collect(s, deps))
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "approve")
    graph.add_conditional_edges("approve", after_approve, ["plan", "item"])
    graph.add_edge("item", "collect")
    graph.add_edge("collect", END)
    return graph.compile(checkpointer=checkpointer)
