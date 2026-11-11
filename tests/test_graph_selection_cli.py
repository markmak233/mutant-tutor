"""Real CLI/graph selection guards must run before pending work and settings writes."""

import json
from dataclasses import replace

import pytest

pytest.importorskip("langgraph.checkpoint.sqlite")

from test_graph_selection import setup as setup  # noqa: E402

from tutor.graph import __main__ as cli  # noqa: E402
from tutor.graph.plan_selection import checkpoint_values, digest  # noqa: E402


@pytest.fixture
def planned(setup, monkeypatch):
    """The production CLI plans a synthetic course; only its model/check dependencies differ."""
    deps, plan, calls = setup
    deps.drafts.rmdir()  # _new_batch owns creating the empty synthetic draft directory.
    monkeypatch.chdir(deps.workspace)
    monkeypatch.setenv("WORKSPACE_DIR", str(deps.workspace))
    monkeypatch.setattr(cli, "load_env", lambda: None)
    monkeypatch.setattr(cli, "_deps", lambda workspace, batch_dir, profiles, settings:
                        replace(deps, batch_dir=batch_dir))
    assert cli.main(["plan", "--lectures=L1", "--batch=demo"]) == 0
    draft = next((deps.workspace / "batches" / ".drafts").iterdir())
    return draft, calls


def resume(*flags):
    return cli.main(["resume", "--batch=demo", *flags])


def test_only_and_digest_freeze_subset_then_reject_changed_authorization(planned):
    draft, calls = planned
    data = (draft / "plan.json").read_bytes()
    assert resume("--approve", "--only=charlie,alpha", "--plan-digest=" + digest(data)) == 0
    assert sorted(calls) == [("generate", "alpha"), ("generate", "charlie")]
    assert checkpoint_values(draft)["selected_ids"] == ["alpha", "charlie"]
    before = (draft / "run.json").read_bytes()
    calls.clear()
    assert resume("--approve", "--only=bravo", "--set=max_rounds=1") == 1
    assert not calls and (draft / "run.json").read_bytes() == before
    assert resume() == 0 and not calls
    assert (draft / "plan.json").read_bytes() == data


@pytest.mark.parametrize("flags", [["--only="], ["--only=missing"],
                                   ["--only=alpha,alpha"], ["--plan-digest=stale"]])
def test_bad_selection_never_calls_model_or_mutates_saved_settings(planned, flags):
    draft, calls = planned
    before = (draft / "run.json").read_bytes()
    assert resume("--approve", "--set=max_rounds=1", *flags) == 1
    assert not calls and (draft / "run.json").read_bytes() == before
    assert not checkpoint_values(draft).get("approved")


@pytest.mark.parametrize("flags", [["--only=alpha"], ["--plan-digest=stale"],
                                   ["--approve", "--feedback=change"]])
def test_incompatible_flags_are_rejected_before_graph_import_or_work(flags):
    with pytest.raises(SystemExit) as stopped:
        resume(*flags)
    assert stopped.value.code == 2


def test_queued_approval_rejects_a_later_plan_edit(planned):
    draft, calls = planned
    path = draft / "plan.json"
    fingerprint = digest(path.read_bytes())
    plan = json.loads(path.read_text(encoding="utf-8"))
    plan[0]["notes"] = "Changed since review: require another review."
    path.write_text(json.dumps(plan), encoding="utf-8")
    assert resume("--approve", "--only=alpha", "--plan-digest=" + fingerprint) == 1
    assert not calls and not checkpoint_values(draft).get("approved")


def test_explicit_selection_cannot_dispatch_stopped_work_before_being_rejected(planned):
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.types import Command

    from tutor.graph import build

    draft, calls = planned
    deps = cli._deps(None, draft.parent.parent / draft.name, None, None)
    config = {"configurable": {"thread_id": draft.name}}
    with SqliteSaver.from_conn_string(str(draft / "checkpoint.sqlite")) as saver:
        app = build.build_batch(deps, saver)
        app.invoke(Command(resume={"action": "approve", "selected_ids": ["alpha"]}),
                   config, interrupt_after=["approve"])
    assert not calls
    before = (draft / "run.json").read_bytes()
    assert resume("--approve", "--only=bravo", "--set=max_rounds=1") == 1
    assert not calls and (draft / "run.json").read_bytes() == before
    assert resume() == 0 and calls == [("generate", "alpha")]
