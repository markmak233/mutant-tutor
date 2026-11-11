"""Shared synthetic course fixtures for GUI generation, selection and retry tests."""

import json
import time
from pathlib import Path

import pytest

pytest.importorskip("flask")  # the `gui` extra

from tutor.gui import courses, generation, launch, tasks  # noqa: E402
from tutor.gui.app import create_app  # noqa: E402

TOKEN = "test-token"
BASE = "http://127.0.0.1:5000"
FAKE_GRAPH = Path(__file__).parent / "fake_graph.py"
WORKSPACE_TOML = """course = "demo"

[llm.roles]
plan = "cheap"
generate = "cheap"
oracle = "other"

[llm.profiles.cheap]
provider = "deepseek"
model = "deepseek-v4-flash"
api_key_env = "DEEPSEEK_API_KEY"

[llm.profiles.other]
provider = "openai"
model = "gpt-6.1-terra"

[generation]
mode = "review"
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def root(tmp_path):
    """A course with two lectures read into the cache and one PDF not read yet."""
    course = tmp_path / "courses" / "demo-course"
    _write(course / "workspace.toml", WORKSPACE_TOML)
    for name in ("L1", "L2"):
        _write(course / "cache" / "lectures" / f"{name}.pdf.md", "## p.1 · slide 1.1\ntext\n")
    _write(course / "lectures" / "L3.pdf", "%PDF")
    return tmp_path / "courses"


@pytest.fixture
def course(root):
    return courses.get_course(root, "demo-course")


class FakeLauncher:
    LaunchError = launch.LaunchError

    def __init__(self):
        self.calls = []

    def open_folder(self, path):
        self.calls.append(("folder", path))

    def open_in_vscode(self, folder, file):
        self.calls.append(("vscode", folder, file))


def _browser(root, launcher=None):
    client = create_app(root, TOKEN, port=5000, launcher=launcher or FakeLauncher()).test_client()
    client.get(f"/?token={TOKEN}", base_url=BASE)
    return client


def get(client, path):
    return client.get(path, base_url=BASE)


def post(client, path, data=None):
    return client.post(path, base_url=BASE, data=data or {}, headers={"Origin": BASE})


def _form(**changes):
    form = {"label": "w9-test", "lecture": ["L1"], "mode": "learn", "how": "auto",
            "max_count": "6", "count": "", "prefs": ""}
    return {**form, **changes}


def _check(course, **changes):
    form = {"label": "w9-test", "chosen": ["L1"], "mode": "learn", "how": "auto", "count": "",
            "max_count": "6", "prefs": "", "hide_answers": False, **changes}
    return generation.check_form(course, **form)


def _task(kind, label="w9-test", status=tasks.DONE, codes=(0,), **how_many):
    return tasks.Task("20260102-100000-abcdef", Path("/nowhere"), f"{kind} {label}", status,
                      "2026-01-02 10:00:00", None, [], list(codes), 1,
                      {"kind": kind, "label": label, **how_many})


def _draft(course, label="w9-test", plan=True, ids=("a", "b")):
    """A draft folder as `plan` leaves it at the pause."""
    draft = course.workspace.root / "batches" / ".drafts" / f"2026-01-02_p3-{label}"
    draft.mkdir(parents=True, exist_ok=True)
    _write(draft / "run.json", json.dumps({"settings": {"mode": "review", "hide_answers": True}}))
    pytest.importorskip("langgraph.checkpoint.sqlite")
    if plan:
        from fake_graph import _item

        # Legacy retry tests intentionally start with no optional notes.
        items = [{k: v for k, v in _item(i, "L1").items() if k not in ("notes", "reason")}
                 for i in ids]
        _write(draft / "plan.json", json.dumps(items))
        _write(draft / "plan.model.json", json.dumps(items))
    from fake_checkpoint import write_checkpoint

    write_checkpoint(draft, {"plan": items if plan else [], "lectures": ["L1"],
                             "approved": False})
    return draft


def approval_form(course, label="w9-test", selected=None):
    """The native approval form, including its explicit empty-selection marker."""
    view = generation.plan_view(course, label, [])
    return {"selection_form": "1", "plan_digest": view.plan_digest,
            "item": selected if selected is not None else [i["id"] for i in view.items]}




def _wait_idle(course_root: Path, count: int, within_s: float = 30) -> tasks.Task:
    """The course's `count`-th task, once it has ended."""
    deadline = time.monotonic() + within_s
    while time.monotonic() < deadline:
        found = tasks.list_tasks(course_root)
        if len(found) == count and not found[0].active:
            return found[0]
        time.sleep(0.1)
    raise AssertionError("the task did not end")


def _generated(course, label="w9-test", hide_answers=True):
    """A batch generated from a 4-item plan: a valid, b saved with no verdict yet, c discarded,
    d failed with a provider error."""
    draft = _draft(course, label, ids=("a", "b", "c", "d"))
    _write(draft / "run.json", json.dumps({
        "profiles": {"repair_profile": "deepseek-pro", "plan_profile": "glm-flash"},
        "settings": {"mode": "review", "hide_answers": hide_answers, "max_rounds": 3,
                     "parallel": None}}))
    batch = course.workspace.root / "batches" / draft.name
    _write(batch / "manifest.json", json.dumps({"exercises": ["a", "b"]}))
    _write(batch / "solutions" / "a" / "validation.json", json.dumps({"valid": True}))
    lines = [{"exercise": "a", "outcome": "saved", "rounds": 0, "attempts": []},
             {"exercise": "b", "outcome": "saved", "rounds": 1, "attempts": []},
             {"exercise": "c", "outcome": "discarded", "rounds": 3, "attempts": [
                 {"failure": "rule 1 (reference passes) FAIL: expected 42, got 41"}]},
             {"exercise": "d", "outcome": "error", "rounds": 0, "attempts": [],
              "error": "LLMError: the stream ended after 2 characters"}]
    _write(batch / "generation.jsonl", "\n".join(json.dumps(line) for line in lines) + "\n")
    return draft, batch


def _record_starts(monkeypatch):
    started = []
    monkeypatch.setattr(tasks, "start", lambda course_root, title, commands, env, meta=None:
                        started.append((title, commands, meta)) or tasks.Task(
                            "20260102-100000-abcdef", course_root, title, "running", "", None,
                            commands, [], 1, meta or {}))
    monkeypatch.setattr(courses, "container_problem", lambda course: None)
    return started


