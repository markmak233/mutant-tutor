"""Selected approval and deferred generation, using only scratch data and fake commands."""

import json
import sys
from html.parser import HTMLParser

import pytest

pytest.importorskip("flask")
pytest.importorskip("langgraph.checkpoint.sqlite")

from fake_checkpoint import write_checkpoint  # noqa: E402
from gui_generation_helpers import (  # noqa: E402
    FAKE_GRAPH,
    _browser,
    _draft,
    _record_starts,
    _task,
    _wait_idle,
    _write,
    approval_form,
    get,
    post,
)
from gui_generation_helpers import course as course  # noqa: E402
from gui_generation_helpers import root as root  # noqa: E402

from tutor.graph.plan_selection import checkpoint_values  # noqa: E402
from tutor.gui import generation, plan_items, plan_selection, tasks  # noqa: E402

PAGE = "/c/demo-course/plans/w9-test/"


class Inputs(HTMLParser):
    """Inspect native inputs and form structure without adding a parser dependency."""

    def __init__(self, page):
        super().__init__()
        self.checks = {}
        self.form = None
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            assert self.form is None, "nested forms would submit ambiguous authorization"
            self.form = attrs
        if tag == "input" and attrs.get("type") == "checkbox":
            self.checks[attrs["value"]] = attrs

    def handle_endtag(self, tag):
        if tag == "form":
            self.form = None


def approved(course, selected=("a",), complete=True):
    """A real checkpoint at approval or completion, with no output files required."""
    draft = _draft(course, ids=("a", "b", "c"))
    plan = json.loads((draft / "plan.json").read_text(encoding="utf-8"))
    plan[1]["notes"] = "Full approved notes. " * 100 + "FINAL NOTE"
    write_checkpoint(draft, {"plan": plan, "lectures": ["L1"], "approved": True,
                             "selected_ids": list(selected), "generation_complete": complete})
    return draft, plan


def test_native_checkboxes_default_all_and_keep_entire_notes(root, course, monkeypatch):
    draft = _draft(course)
    plan = json.loads((draft / "plan.json").read_text(encoding="utf-8"))
    plan[0]["notes"] = "Complete notes. " * 200 + "FINAL NOTE"
    _write(draft / "plan.json", json.dumps(plan))
    started = _record_starts(monkeypatch)
    page = get(_browser(root), PAGE).get_data(as_text=True)
    checks = Inputs(page).checks
    assert set(checks) == {"a", "b"} and all("checked" in row for row in checks.values())
    assert all(row["form"] == "approve-selection" for row in checks.values())
    assert "FINAL NOTE" in page and "Approve and generate selected" in page and not started


@pytest.mark.parametrize("chosen", [[], ["unknown"], ["a", "a"]])
def test_refused_selection_keeps_values_and_starts_nothing(root, course, monkeypatch, chosen):
    _draft(course)
    started = _record_starts(monkeypatch)
    response = post(_browser(root), PAGE + "approve", approval_form(course, selected=chosen))
    assert response.status_code == 400 and not started
    checks = Inputs(response.get_data(as_text=True)).checks
    assert {i for i, row in checks.items() if "checked" in row} == set(chosen) & {"a", "b"}


def test_missing_form_marker_never_means_all(root, course, monkeypatch):
    _draft(course)
    started = _record_starts(monkeypatch)
    response = post(_browser(root), PAGE + "approve")
    assert response.status_code == 400 and not started


def test_changed_plan_requires_fresh_full_review(root, course, monkeypatch):
    draft = _draft(course)
    form = approval_form(course, selected=["a"])
    plan = json.loads((draft / "plan.json").read_text(encoding="utf-8"))
    plan[1]["notes"] = "New material must be reviewed before spending."
    _write(draft / "plan.json", json.dumps(plan))
    started = _record_starts(monkeypatch)
    response = post(_browser(root), PAGE + "approve", form)
    page = response.get_data(as_text=True)
    assert response.status_code == 400 and not started
    assert "plan changed after review" in page and plan[1]["notes"] in page
    assert all("checked" in attrs for attrs in Inputs(page).checks.values())


def test_partial_selection_schedules_exactly_that_subset(root, course, monkeypatch):
    draft = _draft(course, ids=("a", "b", "c"))
    before = (draft / "plan.json").read_bytes()
    monkeypatch.setattr(generation, "GRAPH", [sys.executable, str(FAKE_GRAPH)])
    monkeypatch.setattr(generation, "validate_command", lambda *a: [
        sys.executable, "-c", "print('synthetic validation')"])
    response = post(_browser(root), PAGE + "approve", approval_form(course, selected=["c", "a"]))
    assert response.status_code == 303
    task = _wait_idle(course.workspace.root, 1)
    assert task.status == tasks.DONE, tasks.tail(task)[0]
    assert "--only=a,c" in task.commands[0]
    assert not (draft / "b").exists()
    assert (draft / "plan.json").read_bytes() == before
    assert checkpoint_values(draft)["selected_ids"] == ["a", "c"]
    page = get(_browser(root), PAGE).get_data(as_text=True)
    assert "Left out by you" in page and not Inputs(page).checks


def test_approved_snapshot_and_completion_win_over_mutable_plan_files(root, course):
    draft, plan = approved(course, complete=False)
    _write(draft / "plan.json", json.dumps([{"id": "externally-changed"}]))
    view = generation.plan_view(course, "w9-test", [])
    assert view.state == generation.STOPPED and view.can_continue and not view.can_approve
    assert view.approved_plan == plan
    page = get(_browser(root), PAGE).get_data(as_text=True)
    assert "FINAL NOTE" in page and "externally-changed" not in page
    assert "Edit plan.json" not in page and "Continue" in page
    state = checkpoint_values(draft)
    write_checkpoint(draft, {**state, "generation_complete": True})
    page = get(_browser(root), PAGE).get_data(as_text=True)
    assert "Open the batch" not in page and ">Generate</button>" in page


def test_left_out_generate_uses_frozen_notes_and_saved_settings(root, course, monkeypatch):
    draft, plan = approved(course)
    _write(draft / "plan.json", "[]")
    started = _record_starts(monkeypatch)
    response = post(_browser(root), PAGE + "generate-item", {"item": "b"})
    assert response.status_code == 303 and len(started) == 1
    _, commands, meta = started[0]
    assert meta["item"] == "b" and len(commands) == 2
    assert "--set=mode=review" in commands[0]
    assert json.loads((draft / "retry-b.json").read_text(encoding="utf-8")) == plan[1]


@pytest.mark.parametrize("complete,busy,saved", [(False, False, False), (True, True, False),
                                               (True, False, True)])
def test_deferred_generate_rejects_early_busy_or_successful_row(
        root, course, monkeypatch, complete, busy, saved):
    draft, _ = approved(course, complete=complete)
    if saved:
        _write(course.workspace.root / "batches" / draft.name / "manifest.json",
               json.dumps({"exercises": ["b"]}))
    started = _record_starts(monkeypatch)
    monkeypatch.setattr(tasks, "running", lambda root: busy)
    response = post(_browser(root), PAGE + "generate-item", {"item": "b"})
    assert response.status_code == 303 and not started and not (draft / "retry-b.json").exists()


def test_try_all_excludes_left_out_and_later_outcomes_override_it(root, course, monkeypatch):
    draft, _ = approved(course, selected=("a", "c"))
    batch = course.workspace.root / "batches" / draft.name
    _write(batch / "generation.jsonl", json.dumps({"exercise": "a", "outcome": "error",
                                                  "error": "synthetic failure"}) + "\n")
    view = generation.plan_view(course, "w9-test", [])
    statuses = plan_items.item_statuses(course, view)
    assert statuses["b"].can_generate and not statuses["b"].can_retry
    started = _record_starts(monkeypatch)
    post(_browser(root), PAGE + "retry-all")
    assert started[0][2]["items"] == ["a"]
    _write(batch / "generation.jsonl", json.dumps({"exercise": "b", "outcome": "error",
                                                  "error": "later synthetic failure"}) + "\n")
    assert plan_items.item_statuses(course, view)["b"].can_retry


def test_deferred_collision_and_bad_source_offer_fix_path(root, course, monkeypatch):
    draft, _ = approved(course)
    _write(course.workspace.root / "batches" / "other" / "exercises" / "b" / "student.py",
           "# An existing synthetic answer must stay intact.\n")
    page = get(_browser(root), PAGE).get_data(as_text=True)
    assert "already exists in another batch" in page and "New batch with a valid source" in page
    started = _record_starts(monkeypatch)
    post(_browser(root), PAGE + "generate-item", {"item": "b"})
    assert not started and not (draft / "retry-b.json").exists()


def test_corrupt_checkpoint_blocks_paid_controls_and_offers_refresh(root, course, monkeypatch):
    draft = _draft(course)
    (draft / "checkpoint.sqlite").write_bytes(b"invalid sqlite")
    started = _record_starts(monkeypatch)
    page = get(_browser(root), PAGE).get_data(as_text=True)
    assert "Cannot read the plan checkpoint" in page and "Refresh plan" in page
    assert "Approve and generate" not in page
    post(_browser(root), PAGE + "approve", {"item": "a"})
    assert not started


def test_real_deferred_task_blocks_repeated_requests_and_then_becomes_saved(
        root, course, monkeypatch):
    draft, _ = approved(course)
    monkeypatch.setattr(generation, "GRAPH", [sys.executable, str(FAKE_GRAPH)])
    monkeypatch.setattr(plan_items, "validate_one_command", lambda *args: [
        sys.executable, "-c", "print('synthetic validation of one saved item')"])
    monkeypatch.setenv("FAKE_GRAPH_SLOW_S", "0.5")
    client = _browser(root)
    assert post(client, PAGE + "generate-item", {"item": "b"}).status_code == 303
    assert post(client, PAGE + "generate-item", {"item": "b"}).status_code == 303
    task = _wait_idle(course.workspace.root, 1)
    assert task.status == tasks.DONE, tasks.tail(task)[0]
    assert "synthetic validation of one saved item" in tasks.tail(task)[0]
    view = generation.plan_view(course, "w9-test")
    assert plan_items.item_statuses(course, view)["b"].state == plan_items.ITEM_SAVED
    post(client, PAGE + "generate-item", {"item": "b"})
    assert len(tasks.list_tasks(course.workspace.root)) == 1
    manifest = json.loads((course.workspace.root / "batches" / draft.name / "manifest.json")
                          .read_text(encoding="utf-8"))
    assert manifest["exercises"] == ["b"]


def test_effective_models_use_saved_overrides_with_current_course_definitions(course):
    draft, _ = approved(course)
    _write(draft / "run.json", json.dumps({"profiles": {"profile": "other"}, "settings": {}}))
    view = generation.plan_view(course, "w9-test", [])
    assert dict(plan_selection.effective_models(course, view))["generate"].startswith("other (")
    path = course.workspace.root / "workspace.toml"
    _write(path, path.read_text(encoding="utf-8").replace("gpt-6.1-terra", "fake-current-model"))
    assert "fake-current-model" in dict(plan_selection.effective_models(course, view))["generate"]


def test_page_before_the_draft_exists_still_renders_models(root, course, monkeypatch):
    monkeypatch.setattr(tasks, "list_tasks", lambda root: [_task("plan", status=tasks.RUNNING)])
    page = get(_browser(root), PAGE)
    assert page.status_code == 200 and "making the plan" in page.get_data(as_text=True)
