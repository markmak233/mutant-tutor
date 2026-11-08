"""Selection through real batch fan-out/checkpoint reopen, with content-indexed stand-ins.

No model or owner course is used. Schema checks cover both language profiles; the
production item graph also runs synthetic Python material through generate/check/save.
"""

import builtins
import json
import sqlite3
from dataclasses import replace

import pytest

pytest.importorskip("langgraph.checkpoint.sqlite")

from fake_checkpoint import write_checkpoint  # noqa: E402
from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: E402
from langgraph.types import Command  # noqa: E402
from test_c_real import course as c_course  # noqa: E402, F401
from test_graph import draft, report  # noqa: E402
from test_graph_c import c_draft  # noqa: E402
from test_graph_cli import plan_item  # noqa: E402

from tutor.exchange import AwaitingAnswer  # noqa: E402
from tutor.graph import batch, build  # noqa: E402
from tutor.graph.languages import PYTHON, C  # noqa: E402
from tutor.graph.nodes import Deps  # noqa: E402
from tutor.graph.plan_selection import (  # noqa: E402
    checkpoint_values,
    choose,
    digest,
    validate_selection,
)
from tutor.llm import LLMConfig, Reply  # noqa: E402
from tutor.materials import cache  # noqa: E402


@pytest.fixture
def setup(tmp_path):
    """A complete synthetic lecture cache; no files from a real course."""
    (tmp_path / "workspace.toml").write_text('course = "demo"\n', encoding="utf-8")
    cache.save(cache.cache_path(tmp_path, tmp_path / "lectures" / "L1.pdf"), cache.render(
        "L1.pdf", [cache.Entry(n, f"1.{n}", "text", f"h{n}", f"synthetic page {n}")
                   for n in range(1, 7)]))
    plan = [plan_item(i) for i in ("alpha", "bravo", "charlie")]
    calls = []

    def chat(config, messages, *, purpose, **kw):
        if purpose == "plan":
            return Reply(json.dumps({"items": plan}), 100, 50, 0.1, None)
        # Select the reply by ID in the actual request, independent of thread order.
        ident = next(i["id"] for i in plan if i["id"] in messages[-1]["content"])
        calls.append((purpose, ident))
        data = draft()
        data["metadata"]["id"] = ident
        return Reply(json.dumps(data), 100, 50, 0.1, None)

    deps = Deps(tmp_path, tmp_path / "batches" / "2026-11-08_p3-demo",
                LLMConfig("fake", "deepseek", "http://example.invalid", "fake"),
                chat=chat, check=lambda *a: report(), stream=False)
    deps.drafts.mkdir(parents=True)
    return deps, plan, calls


@pytest.mark.parametrize("profile", [PYTHON, C], ids=["python", "c"])
def test_only_chosen_schemas_sources_and_collisions_block(profile):
    good = {**plan_item("alpha"), "framework": "c" if profile is C else "python"}
    bad = {"id": "bravo", "lecture": "unknown", "slides": "bad"}
    assert validate_selection([good, bad], ["alpha"], ["L1"], ["bravo"], profile) == ["alpha"]
    with pytest.raises(ValueError):
        validate_selection([good, bad], ["bravo"], ["L1"], [], profile)
    with pytest.raises(ValueError, match="used twice"):
        validate_selection([good, {**bad, "id": "alpha"}], ["alpha"], ["L1"], [], profile)


@pytest.mark.parametrize("ids", [[], ["missing"], ["alpha", "alpha"]])
def test_invalid_selection_cannot_become_approval(ids):
    with pytest.raises(ValueError):
        batch.apply_decision({"plan": [plan_item("alpha")], "lectures": ["L1"]},
                             {"action": "approve", "selected_ids": ids}, [])


def test_real_graph_reopens_and_saves_only_selected_work(setup):
    deps, plan, calls = setup
    config = {"configurable": {"thread_id": deps.batch_dir.name}, "max_concurrency": 3}
    path = deps.drafts / "checkpoint.sqlite"
    with SqliteSaver.from_conn_string(str(path)) as saver:
        app = build.build_batch(deps, saver)
        app.invoke({"lectures": ["L1"]}, config)
    # The CLI file is deliberately formatted; neither approval nor collect may filter it.
    data = (json.dumps(plan, indent=3) + "\n").encode("utf-8")
    (deps.drafts / "plan.json").write_bytes(data)
    with SqliteSaver.from_conn_string(str(path)) as saver:
        app = build.build_batch(deps, saver)
        app.invoke(Command(resume={"action": "approve", "plan": plan,
                                   "selected_ids": ["charlie", "alpha"],
                                   "plan_digest": digest(data)}), config)
    state = checkpoint_values(deps.drafts)
    assert state["plan"] == plan and state["selected_ids"] == ["alpha", "charlie"]
    assert state["generation_complete"] is True
    assert sorted(calls) == [("generate", "alpha"), ("generate", "charlie")]
    assert {r["id"] for r in state["results"]} == {"alpha", "charlie"}
    assert not (deps.drafts / "bravo").exists()
    assert not (deps.batch_dir / "exercises" / "bravo").exists()
    manifest = json.loads((deps.batch_dir / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["exercises"]) == {"alpha", "charlie"}
    assert (deps.drafts / "plan.json").read_bytes() == data


def test_assistant_resume_and_stop_before_dispatch_keep_selection(setup, monkeypatch):
    deps, plan, calls = setup
    waiting = {"alpha"}

    class Item:
        def stream(self, state, **kw):
            ident = state["item"].id
            calls.append(("item", ident))
            if ident in waiting:
                raise AwaitingAnswer(deps.drafts / f"req-{ident}.json")
            yield {"discard": {"outcome": "discarded", "failure": "synthetic failure"}}

    monkeypatch.setattr(build, "build_item", lambda deps: Item())
    config = {"configurable": {"thread_id": deps.batch_dir.name}}
    path = deps.drafts / "checkpoint.sqlite"
    with SqliteSaver.from_conn_string(str(path)) as saver:
        app = build.build_batch(deps, saver)
        app.invoke({"lectures": ["L1"]}, config)
        # Persist approval, stopping before the first item/log can exist.
        app.invoke(Command(resume={"action": "approve", "selected_ids": ["alpha"]}),
                   config, interrupt_after=["approve"])
        assert not calls
    assert checkpoint_values(deps.drafts)["approved"] is True
    with SqliteSaver.from_conn_string(str(path)) as saver:
        app = build.build_batch(deps, saver)
        app.invoke(None, config)
        assert app.get_state(config).next == ("answers",)
    waiting.clear()
    with SqliteSaver.from_conn_string(str(path)) as saver:
        app = build.build_batch(deps, saver)
        app.invoke(Command(resume={"answered": True}), config, interrupt_before=["collect"])
        assert checkpoint_values(deps.drafts)["generation_complete"] is False
    with SqliteSaver.from_conn_string(str(path)) as saver:
        build.build_batch(deps, saver).invoke(None, config)
    assert calls == [("item", "alpha"), ("item", "alpha")]
    assert checkpoint_values(deps.drafts)["generation_complete"] is True
    assert not (deps.batch_dir / "manifest.json").exists()


def test_legacy_checkpoints_choose_all_and_new_plan_clears_old_selection():
    plan = [plan_item("alpha"), plan_item("bravo")]
    assert choose(plan) == ["alpha", "bravo"]
    revised = batch.apply_decision({"plan": plan, "selected_ids": ["alpha"]},
                                   {"action": "revise", "feedback": "harder"}, [])
    assert revised["selected_ids"] is None and not revised["generation_complete"]


def test_checkpoint_read_is_read_only_and_reports_corrupt_or_busy(tmp_path):
    draft_dir = tmp_path / "draft"
    write_checkpoint(draft_dir, {"approved": True, "plan": [plan_item("alpha")]})
    path = draft_dir / "checkpoint.sqlite"
    before = path.read_bytes()
    assert checkpoint_values(draft_dir)["approved"] is True
    assert path.read_bytes() == before
    # A writer's exclusive transaction must produce a bounded, explicit error.
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("BEGIN EXCLUSIVE")
        with pytest.raises(ValueError, match="Cannot read"):
            checkpoint_values(draft_dir)
        connection.rollback()
    path.write_bytes(b"not sqlite")
    with pytest.raises(ValueError, match="Cannot read"):
        checkpoint_values(draft_dir)


def test_checkpoint_without_optional_dependency_offers_install_instruction(tmp_path, monkeypatch):
    original = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "langgraph.checkpoint.sqlite":
            raise ImportError("optional dependency absent")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(ValueError, match="gen extra"):
        checkpoint_values(tmp_path)


@pytest.mark.docker
def test_selected_c_item_runs_real_generate_validate_save(setup, request):
    """Container-only C acceptance uses the synthetic example and fake model replies."""
    deps, plan, calls = setup
    container_root = request.getfixturevalue("c_course")
    (deps.workspace / "workspace.toml").write_text(
        (container_root / "workspace.toml").read_text(encoding="utf-8"), encoding="utf-8")
    cplan = [{**row, "framework": "c"} for row in plan]

    def chat(config, messages, *, purpose, **kw):
        if purpose == "plan":
            return Reply(json.dumps({"items": cplan}), 100, 50, 0.1, None)
        ident = next(row["id"] for row in cplan if row["id"] in messages[-1]["content"])
        calls.append((purpose, ident))
        data = c_draft()
        data["metadata"]["id"] = ident
        return Reply(json.dumps(data), 100, 50, 0.1, None)

    from tutor.graph.nodes import check_round

    deps = replace(deps, language="c", chat=chat, check=check_round)
    with SqliteSaver.from_conn_string(str(deps.drafts / "checkpoint.sqlite")) as saver:
        app = build.build_batch(deps, saver)
        config = {"configurable": {"thread_id": deps.batch_dir.name}}
        app.invoke({"lectures": ["L1"]}, config)
        app.invoke(Command(resume={"action": "approve", "selected_ids": ["alpha"]}), config)
    state = checkpoint_values(deps.drafts)
    assert [(row["id"], row["outcome"]) for row in state["results"]] == [("alpha", "saved")]
    assert calls == [("generate", "alpha")]
