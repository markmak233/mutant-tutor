"""Synchronous command-keyed fake tasks for wizard acceptance; no real model/install."""

import hashlib
import io
from contextlib import redirect_stdout

import pytest
import test_doctor_verify_cli as verify_support
import test_gui_doctor_worker as worker_support

from tutor.doctor_advice import service, transaction, verify_cli
from tutor.gui import doctor_page, doctor_worker, tasks
from tutor.gui.app import create_app

ready = worker_support.ready
BASE = "http://127.0.0.1:5000"


@pytest.fixture
def wizard(ready, monkeypatch):
    course, identifier, model_calls = ready
    history, requests, applied, finished = [], [], [], []
    monkeypatch.setattr(verify_cli, "Disposable", verify_support.Trial)

    def apply(plan, selected):
        applied.append(plan)
        (selected.root / transaction.RECEIPT).write_text("{}", encoding="utf-8")

    monkeypatch.setattr(transaction, "apply", apply)
    monkeypatch.setattr(tasks, "list_tasks", lambda root: list(history))
    monkeypatch.setattr(tasks, "running", lambda root: None)
    monkeypatch.setattr(tasks, "get", lambda root, ident:
                        next((t for t in history if t.id == ident), None))

    def start(root, title, commands, env, meta):
        requests.append((commands, meta))
        command = commands[0]
        output = io.StringIO()
        with redirect_stdout(output):
            if command[2] == "tutor.gui.doctor_worker":
                try:
                    code = doctor_worker.execute(course, command[4], command[5],
                                                 confirm="--confirm" in command)
                except (service.AdviceInputError, service.AdviceSetupError) as exc:
                    print("Stopped: " + str(exc))
                    code = 1
            else:
                assert command[2:4] == ["tutor", "doctor"]
                assert "--undo-repair" in command or "--finish-repair" in command
                finished.append(command)
                (root / transaction.RECEIPT).unlink(missing_ok=True)
                code = 0
        # Identity derives from content, not call order (production callers can be threaded).
        identity = hashlib.sha256((repr(command) + output.getvalue()).encode()).hexdigest()[:6]
        task_id = "20260102-100000-" + identity
        folder = root / tasks.TASKS_DIR / task_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / tasks.LOG_FILE).write_text(output.getvalue(), encoding="utf-8")
        task = tasks.Task(task_id, folder, title, tasks.DONE if code == 0 else tasks.FAILED,
                          "2026-01-02 10:00:00", "2026-01-02 10:00:01", commands, [code],
                          None, meta)
        history.insert(0, task)
        return task

    monkeypatch.setattr(tasks, "start", start)
    client = create_app(course.workspace.root.parent, "synthetic", port=5000).test_client()
    client.get("/?token=synthetic", base_url=BASE)
    return {"course": course, "id": identifier, "client": client, "models": model_calls,
            "history": history, "requests": requests, "applied": applied, "finished": finished}


def action(wizard, name, **data):
    return wizard["client"].post(f"/c/demo/doctor/input/{wizard['id']}/{name}",
                                data=data, base_url=BASE, headers={"Origin": BASE})


def page(wizard, step=None):
    suffix = "" if step is None else f"?step={step}"
    return wizard["client"].get(f"/c/demo/environment/fix/{wizard['id']}" + suffix,
                                base_url=BASE).get_data(as_text=True)


def through_advice(wizard):
    assert action(wizard, "preview").status_code == 303
    assert action(wizard, "advice").status_code == 303


def through_diff(wizard):
    through_advice(wizard)
    assert action(wizard, "verify", confirmed="on").status_code == 303
    assert action(wizard, "repair-preview").status_code == 303


def folder(wizard):
    return doctor_page.folder(wizard["course"], wizard["id"])
