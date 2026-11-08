"""`python -m tutor.graph plan / resume`: the loop that drives a batch (graph/__main__.py).

The real command line, the real SQLite checkpoint and the owner's plan.json; only the graph's
nodes are stand-ins (FakeBatch), with the real graph's shape: plan -> approve (interrupt) ->
items -> answers (interrupt while assistant requests wait) -> collect. No model is called.
"""

import json
import operator
from types import SimpleNamespace
from typing import Annotated, TypedDict

import pytest

from tutor.exchange import AwaitingAnswer
from tutor.graph import __main__ as cli
from tutor.graph.languages import PYTHON

pytest.importorskip("langgraph.checkpoint.sqlite")
from langgraph.graph import END, START, StateGraph  # noqa: E402
from langgraph.types import Send, interrupt  # noqa: E402


def plan_item(item_id: str, lecture: str = "L1", slides: str = "1.3-1.4") -> dict:
    return {"id": item_id, "knowledge_point": f"point of {item_id}", "lecture": lecture,
            "slides": slides, "type": "implementation", "interface": "function",
            "framework": "python", "notes": "", "reason": ""}


class State(TypedDict, total=False):
    lectures: list[str]
    plan: list[dict]
    plan_problems: list[str]
    feedback: str
    approved: bool
    results: Annotated[list[dict], operator.add]


class FakeBatch:
    """What the stand-in nodes do, set by each test; `calls` records them in order. It lives
    across the test's commands, as the real models' answers would."""

    def __init__(self, plans: list[list[dict]]):
        self.plans = plans  # one per plan call, in order
        self.calls: list[tuple] = []
        self.plan_waits: list[str] = []  # request ids the plan call waits for, once each
        self.item_waits: dict[str, int] = {}  # item id -> times it still waits
        self.crash_once: set[str] = set()  # items whose first run stops the process

    def build_batch(self, deps, checkpointer):
        def plan(state: State) -> State:
            self.calls.append(("plan", state.get("feedback")))
            if self.plan_waits:
                raise AwaitingAnswer(deps.drafts / f"{self.plan_waits.pop(0)}.json")
            return {"plan": self.plans.pop(0), "plan_problems": ["a note from the check"],
                    "approved": False}

        def approve(state: State) -> State:
            decision = interrupt({"plan": state["plan"]})
            self.calls.append(("decision", decision))
            if decision.get("action") == "approve":
                return {"plan": decision["plan"], "approved": True}
            return {"feedback": decision["feedback"], "approved": False}

        def after_approve(state: State):
            if not state.get("approved"):
                return "plan"
            return [Send("item", {"item": p}) for p in state["plan"]]

        def item(payload: dict) -> State:
            item_id = payload["item"]["id"]
            self.calls.append(("item", item_id))
            if item_id in self.crash_once:
                self.crash_once.discard(item_id)
                raise RuntimeError("the process was stopped")
            if self.item_waits.get(item_id, 0) > 0:
                self.item_waits[item_id] -= 1
                return {"results": [{"id": item_id, "outcome": "waiting",
                                     "request": f"req-{item_id}"}]}
            return {"results": [{"id": item_id, "outcome": "saved", "rounds": 0,
                                 "tokens": 100, "last_failure": ""}]}

        def waiting(state: State) -> list[dict]:
            return [r for r in cli.latest_results(state.get("results", []))
                    if r["outcome"] == "waiting"]

        def answers(state: State) -> State:
            if found := waiting(state):
                interrupt({"waiting": [r["request"] for r in found]})
            return {}

        def after_answers(state: State):
            if found := waiting(state):
                ids = {r["id"] for r in found}
                return [Send("item", {"item": p}) for p in state["plan"] if p["id"] in ids]
            return "collect"

        graph = StateGraph(State)
        graph.add_node("plan", plan)
        graph.add_node("approve", approve)
        graph.add_node("item", item)
        graph.add_node("answers", answers)
        graph.add_node("collect", lambda state: {})
        graph.add_edge(START, "plan")
        graph.add_edge("plan", "approve")
        graph.add_conditional_edges("approve", after_approve, ["plan", "item"])
        graph.add_edge("item", "answers")
        graph.add_conditional_edges("answers", after_answers, ["item", "collect"])
        graph.add_edge("collect", END)
        return graph.compile(checkpointer=checkpointer)


@pytest.fixture
def course(tmp_path, monkeypatch):
    """A Python course run on the host, its working directory with no .env."""
    root = tmp_path / "course"
    (root / "batches").mkdir(parents=True)
    (root / "workspace.toml").write_text('course = "demo"\n[isolation]\nrunner = "host"\n',
                                         encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_DIR", str(root))
    monkeypatch.chdir(tmp_path)
    return root


@pytest.fixture
def fake(course, monkeypatch):
    batch = FakeBatch([[plan_item("count-words"), plan_item("sum-values")]])
    monkeypatch.setattr("tutor.graph.build.build_batch", batch.build_batch)
    monkeypatch.setattr(cli, "_deps", lambda workspace, batch_dir, profiles, settings:
                        SimpleNamespace(drafts=workspace / "batches" / ".drafts" / batch_dir.name,
                                        profile=PYTHON))
    return batch


def drafts(course):
    [found] = (course / "batches" / ".drafts").iterdir()
    return found


def plan(*extra):
    return cli.main(["plan", "--batch", "w1", "--lectures", "L1", *extra])


def resume(*extra):
    return cli.main(["resume", "--batch", "w1", *extra])


def test_plan_shows_the_plan_and_pauses_for_the_owner(course, fake, capsys):
    assert plan("--prefs", "short ones") == 0
    out = capsys.readouterr().out
    assert "plan: 2 items" in out
    assert "1. count-words  [implementation, function, python]  L1 slides 1.3-1.4" in out
    assert "   ! a note from the check" in out
    assert "Paused. Continue with: python -m tutor.graph resume --batch w1 --approve" in out
    folder = drafts(course)
    assert folder.name.endswith("_p3-w1")
    assert [p["id"] for p in json.loads((folder / "plan.json").read_text(encoding="utf-8"))] == [
        "count-words", "sum-values"]
    assert (folder / "run.json").is_file() and (folder / "checkpoint.sqlite").is_file()
    assert fake.calls == [("plan", None)]


def test_a_label_in_use_is_refused(course, fake, capsys):
    assert plan() == 0
    assert plan() == 1
    assert "a batch labelled w1 exists" in capsys.readouterr().err


def test_resume_needs_a_planned_batch(course, fake, capsys):
    assert resume("--approve") == 1
    assert "no planned batch labelled w1" in capsys.readouterr().err


def test_feedback_makes_a_new_plan_which_waits_again(course, fake, capsys):
    fake.plans.append([plan_item("count-words")])
    plan()
    assert resume("--feedback", "only one") == 0
    out = capsys.readouterr().out
    assert out.count("Paused.") == 2
    assert fake.calls == [("plan", None), ("decision", {"action": "revise",
                                                         "feedback": "only one"}),
                          ("plan", "only one")]
    assert len(json.loads((drafts(course) / "plan.json").read_text(encoding="utf-8"))) == 1


def test_approve_generates_the_plan_as_the_owner_left_it(course, fake, capsys):
    plan()
    plan_file = drafts(course) / "plan.json"
    edited = [plan_item("count-words", slides="1.5")]
    plan_file.write_text(json.dumps(edited), encoding="utf-8")
    assert resume("--approve") == 0
    out = capsys.readouterr().out
    assert "plan.json differs from the model's latest plan; approving uses plan.json" in out
    assert "generating 1 items" in out
    assert "saved     count-words: 1 round(s), 100 tokens" in out
    assert ("decision", {"action": "approve", "plan": edited}) in fake.calls
    assert ("item", "sum-values") not in fake.calls


def test_an_edited_plan_with_a_blocking_problem_is_not_approved(course, fake, capsys):
    plan()
    plan_file = drafts(course) / "plan.json"
    plan_file.write_text(json.dumps([plan_item("count-words", lecture="L9")]), encoding="utf-8")
    assert resume("--approve") == 1
    err = capsys.readouterr().err
    assert "plan.json cannot be approved: count-words: lecture 'L9' is not one of ['L1']" in err
    assert not any(call[0] == "decision" for call in fake.calls)


def test_the_check_of_an_edited_plan_is_shown_before_approving(course, fake, capsys):
    plan()
    capsys.readouterr()
    plan_file = drafts(course) / "plan.json"
    plan_file.write_text(json.dumps([plan_item("count-words", lecture="L9")]), encoding="utf-8")
    assert resume() == 0  # no flag, no terminal: shows the plan and pauses
    out = capsys.readouterr().out
    assert "   ! count-words: lecture 'L9' is not one of ['L1']" in out
    assert "a note from the check" not in out  # the model's check was of another plan
    assert "Paused." in out


def test_resume_set_changes_the_settings_for_the_rest_of_the_batch(course, fake, capsys):
    plan()
    resume("--set", "max_rounds=1")
    saved = json.loads((drafts(course) / "run.json").read_text(encoding="utf-8"))
    assert saved["settings"]["max_rounds"] == 1
    capsys.readouterr()
    resume()  # a later resume keeps it
    assert "settings: max_rounds=1" in capsys.readouterr().out


def test_resume_profile_changes_the_model_for_the_rest_of_the_batch(course, fake):
    plan()
    resume("--profile", "other-model")
    saved = json.loads((drafts(course) / "run.json").read_text(encoding="utf-8"))
    assert saved["profiles"]["profile"] == "other-model"
    resume()  # a later resume without the flag keeps it
    saved = json.loads((drafts(course) / "run.json").read_text(encoding="utf-8"))
    assert saved["profiles"]["profile"] == "other-model"


def test_a_run_stopped_midway_continues_where_it_stopped(course, fake, capsys):
    fake.crash_once = {"sum-values"}
    plan()
    with pytest.raises(RuntimeError):
        resume("--approve")
    capsys.readouterr()
    assert resume() == 0
    out = capsys.readouterr().out
    assert "continuing the stopped run at: item" in out
    assert "saved     sum-values" in out and "saved     count-words" in out
    assert fake.calls.count(("item", "sum-values")) == 2


def test_assistant_requests_are_resumed_once_then_reported(course, fake, capsys):
    plan()
    fake.item_waits = {"sum-values": 2}
    assert resume("--approve") == cli.WAITING
    out = capsys.readouterr().out
    assert "1 requests wait for the assistant: req-sum-values" in out
    # Waiting after the approval, then once more after the one resume of this run.
    assert fake.calls.count(("item", "sum-values")) == 2
    assert resume() == 0  # answered now: the item runs once more and the batch ends
    out = capsys.readouterr().out
    assert "saved     sum-values" in out
    assert fake.calls.count(("item", "sum-values")) == 3


def test_a_plan_call_waiting_for_an_assistant_stops_the_batch(course, fake, capsys):
    fake.plan_waits = ["req-plan"]
    assert plan() == cli.WAITING
    assert "1 requests wait for the assistant: req-plan" in capsys.readouterr().out
    assert resume() == 0  # the plan call is made again, and the plan waits for the owner
    out = capsys.readouterr().out
    assert "continuing the stopped run at: plan" in out and "Paused." in out
