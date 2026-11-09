"""One screen per guarded repair step, with fake workers/models/packages only."""

import json
import os
from html.parser import HTMLParser

import gui_fix_support as support
import pytest
import test_doctor_advice_service as advice_support
import test_doctor_independent as independent_support

from tutor import config, llm, pythons
from tutor.doctor_advice import independent, verification, verify_cli
from tutor.doctor_rows import Row
from tutor.gui import doctor_page, fix_state, tasks

ready = support.ready
wizard = support.wizard


class Fields(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.fields, self.active = {}, None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "input":
            self.fields[values.get("name")] = values.get("value", "")
        if tag == "textarea":
            self.active = values.get("name")
            self.fields[self.active] = ""

    def handle_data(self, data):
        if self.active:
            self.fields[self.active] += data

    def handle_endtag(self, tag):
        if tag == "textarea":
            self.active = None


def test_prefill_uses_current_problem_and_last_failed_setup_tail(wizard, monkeypatch):
    course = wizard["course"]
    row = Row("FAIL", "host demo imports: required unknown_runtime, found not found",
              "review the import", "ask-model")
    monkeypatch.setattr(doctor_page, "report", lambda course: [row])
    path = course.workspace.root / "logs" / "gui-tasks" / "synthetic-failed"
    path.mkdir(parents=True)
    (path / tasks.LOG_FILE).write_text("older prefix\n" + "例子 log tail\n" * 10000,
                                      encoding="utf-8")
    failed = tasks.Task("20260102-100000-abcdef", path, "Install", tasks.FAILED,
                        "", "", [], [1], None, {"kind": "install"})
    wizard["history"].append(failed)
    before = {p.relative_to(course.workspace.root): p.read_bytes()
              for p in course.workspace.root.rglob("*") if p.is_file()}
    env = dict(os.environ)
    monkeypatch.setattr(config, "load_env", lambda *a, **k: pytest.fail("server loaded .env"))
    response = wizard["client"].get("/c/demo/environment/fix/" + fix_state.row_id(row),
                                    base_url=support.BASE)
    fields = Fields(response.get_data(as_text=True)).fields
    assert response.status_code == 200 and fields["module"] == "demo"
    assert row.text in fields["failure"] and fields["failure"].endswith("例子 log tail\n")
    assert len(fields["failure"].encode("utf-8")) <= fix_state.FAILURE_LIMIT
    assert "older prefix" not in fields["failure"]
    assert before == {p.relative_to(course.workspace.root): p.read_bytes()
                      for p in course.workspace.root.rglob("*") if p.is_file()}
    assert not wizard["models"] and not wizard["requests"] and dict(os.environ) == env


def test_ask_model_link_only_on_uncovered_problems(wizard, monkeypatch):
    rows = [Row("FAIL", "unknown", "", "ask-model"), Row("WARN", "warning", "", "ask-model"),
            Row("FAIL", "known", "", "install-venv"), Row("FAIL", "manual", "")]
    monkeypatch.setattr(doctor_page, "report", lambda course: rows)
    monkeypatch.setattr(pythons, "find", lambda: [])
    html = wizard["client"].get("/c/demo/environment/check", base_url=support.BASE).get_data(
        as_text=True)
    assert html.count("Ask a model for a fix") == 1
    response = wizard["client"].get("/c/demo/environment/fix/" + fix_state.row_id(rows[2]),
                                    base_url=support.BASE)
    assert response.status_code == 404 and not wizard["models"]


def test_step_guards_reject_send_test_and_apply_before_previews(wizard):
    assert "Step 1 of 4" in support.page(wizard, 4)
    for name in ("advice", "verify", "repair-preview", "apply", "independent"):
        assert support.action(wizard, name, confirmed="on").status_code == 400
    assert not wizard["requests"] and not wizard["models"]
    assert "Independent second profile" not in support.page(wizard)


@pytest.mark.parametrize("ending", ["undo", "finish"])
def test_four_steps_apply_and_explicit_undo_or_keep(wizard, ending):
    assert support.action(wizard, "preview").status_code == 303 and not wizard["models"]
    first = support.page(wizard)
    assert "Step 1 of 4" in first and "Destination:" in first
    # Original selected text stays editable; the separate transmission preview is redacted.
    preview = (support.folder(wizard) / "preview.txt").read_text(encoding="utf-8")
    assert "sk-test-not-a-real-key" not in preview
    assert support.action(wizard, "advice").status_code == 303
    second = support.page(wizard)
    assert "Step 2 of 4" in second and "Independent second profile" not in second
    assert support.action(wizard, "verify").status_code == 400
    assert support.action(wizard, "verify", confirmed="on").status_code == 303
    assert "Step 3 of 4" in support.page(wizard)
    assert support.action(wizard, "apply", confirmed="on").status_code == 400
    assert support.action(wizard, "repair-preview").status_code == 303
    fourth = support.page(wizard)
    assert "Step 4 of 4" in fourth and "[package-bounds]" in fourth
    assert support.action(wizard, "apply").status_code == 400
    assert support.action(wizard, "apply", confirmed="on").status_code == 303
    assert len(wizard["applied"]) == 1 and "Your original setup is kept" in support.page(wizard)
    assert support.action(wizard, "apply", confirmed="on").status_code == 400
    path = f"/c/demo/doctor/retained/{ending}"
    client = wizard["client"]
    assert client.post(path, data={"input": wizard["id"]}, base_url=support.BASE,
                       headers={"Origin": support.BASE}).status_code == 400
    response = client.post(path, data={"input": wizard["id"], "confirmed": "on"},
                           base_url=support.BASE, headers={"Origin": support.BASE})
    assert response.status_code == 303 and len(wizard["finished"]) == 1
    assert "no longer has an original backup" in support.page(wizard)


def test_failed_temporary_verification_cannot_reach_apply(wizard, monkeypatch):
    class Failed(support.verify_support.Trial):
        def verify(self, changes):
            return verification.Check(False, "synthetic unchanged failure", "demo")

    monkeypatch.setattr(verify_cli, "Disposable", Failed)
    support.through_advice(wizard)
    assert support.action(wizard, "verify", confirmed="on").status_code == 303
    html = support.page(wizard, 4)
    assert "Step 3 of 4" in html and "The fix was not verified" in html and "Back to step 1" in html
    assert support.action(wizard, "repair-preview").status_code == 400
    assert not wizard["applied"]


def test_new_package_review_requires_a_new_two_destination_preview(wizard, monkeypatch):
    def chat(model, messages, **kwargs):
        wizard["models"].append((model.provider, kwargs["purpose"]))
        if kwargs["purpose"] == "doctor-independent":
            return independent_support.reply()
        reply = json.loads(advice_support.answer())
        reply.update(changes=[], needs_new_package="unreviewed-candidate")
        return llm.Reply(json.dumps(reply), 1, 1, 0)

    monkeypatch.setattr(llm, "chat", chat)
    monkeypatch.setattr(independent, "pypi_facts", lambda name:
                        independent.PackageFacts(name, 2, "2020-01-01"))
    support.through_advice(wizard)
    assert "Independent second profile" in support.page(wizard)
    assert support.action(wizard, "independent").status_code == 400
    old = (support.folder(wizard) / "preview.json").read_bytes()
    calls = list(wizard["models"])
    assert support.action(wizard, "prepare-independent", second="second").status_code == 303
    assert wizard["models"] == calls and (support.folder(wizard)/"preview.json").read_bytes() != old
    assert "Independent destination:" in support.page(wizard, 2)
    assert support.action(wizard, "independent").status_code == 303
    assert "Candidate for your review" in support.page(wizard, 2)
    assert support.action(wizard, "verify", confirmed="on").status_code == 400


def test_worker_fingerprint_still_refuses_changed_input_before_any_model_call(wizard):
    support.action(wizard, "preview")
    (support.folder(wizard) / "failure.log").write_text("changed failure", encoding="utf-8")
    assert support.action(wizard, "advice").status_code == 303
    assert wizard["history"][0].status == tasks.FAILED and not wizard["models"]


def test_oversized_preview_is_not_cut_or_sendable(wizard):
    support.action(wizard, "preview")
    (support.folder(wizard) / "preview.txt").write_text("x" * (fix_state.TEXT_LIMIT + 1),
                                                      encoding="utf-8")
    assert "x" * 100 not in support.page(wizard)
    assert support.action(wizard, "advice").status_code == 400 and not wizard["models"]


def test_complete_non_ascii_advice_is_not_lost_at_json_escape_expansion(wizard, monkeypatch):
    cause = "例" * 50000

    def chat(model, messages, **kwargs):
        reply = json.loads(advice_support.answer())
        reply["cause"] = cause
        return llm.Reply(json.dumps(reply, ensure_ascii=False), 1, 1, 0)

    monkeypatch.setattr(llm, "chat", chat)
    support.through_advice(wizard)
    assert cause in support.page(wizard)


def test_task_completion_returns_to_the_corresponding_screen(wizard):
    support.through_advice(wizard)
    task = wizard["history"][0]
    response = wizard["client"].get(f"/c/demo/tasks/{task.id}/log", base_url=support.BASE,
                                    headers={"HX-Request": "true"})
    assert response.headers["HX-Redirect"] == (
        f"/c/demo/environment/fix/{wizard['id']}?step=2")


def test_editing_step_one_creates_a_new_input_without_reusing_results(wizard):
    support.through_advice(wizard)
    original = wizard["id"]
    calls = list(wizard["models"])
    response = wizard["client"].post("/c/demo/doctor/input", base_url=support.BASE,
        headers={"Origin": support.BASE}, data={"failure": "edited failure", "module": "demo",
                                                "profile": "first"})
    assert response.status_code == 303 and wizard["models"] == calls
    identifier = wizard["history"][0].meta["doctor_input"]
    assert identifier != original
    view = fix_state.view(wizard["course"], identifier)
    assert view.preview_ready and not view.advice_ready and view.highest == 1


def test_busy_course_cannot_create_inputs_or_send_requests(wizard, monkeypatch):
    root = wizard["course"].workspace.root
    task = tasks.Task("20260102-100000-abcdef", root, "Existing task", tasks.RUNNING,
                      "", None, [], [], 1)
    monkeypatch.setattr(tasks, "running", lambda root: task)
    before = list((root / "logs" / "doctor").iterdir())
    response = wizard["client"].post("/c/demo/doctor/input", base_url=support.BASE,
        headers={"Origin": support.BASE}, data={"failure": "synthetic", "profile": "first"})
    assert response.status_code == 303 and "notice=busy" in response.location
    assert support.action(wizard, "advice").status_code == 303
    assert list((root / "logs" / "doctor").iterdir()) == before
    assert not wizard["models"] and not wizard["requests"]
