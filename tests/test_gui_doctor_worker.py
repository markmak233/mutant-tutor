"""Full preview/send/verify/apply GUI worker flow, with fake models/installers/HTTP."""

import os

import pytest
import test_doctor_advice_facts as course_support
import test_doctor_advice_service as support
import test_doctor_independent as independent_support
import test_doctor_verify_cli as verify_support

from tutor import llm
from tutor.doctor_advice import independent, records, service, transaction, verify_cli
from tutor.gui import courses, doctor_page, doctor_worker


@pytest.fixture
def ready(tmp_path, monkeypatch):
    raw = course_support.course(tmp_path)
    marker = raw.root / "workspace.toml"
    marker.write_text(marker.read_text(encoding="utf-8") + '''
[llm.profiles.first]
provider = "deepseek"
model = "demo-model"
base_url = "https://first.invalid/v1"
api_key_env = "DOCTOR_TOKEN"
[llm.profiles.second]
provider = "glm"
model = "other-model"
base_url = "https://second.invalid/v1"
api_key_env = "DOCTOR_TOKEN"
[llm.roles]
doctor = "first"
''', encoding="utf-8")
    monkeypatch.setenv("DOCTOR_TOKEN", "sk-test-not-a-real-key")
    monkeypatch.setattr(doctor_worker.facts, "collect", lambda *a, **kw: support.snapshot())
    calls = []

    def chat(model, messages, **kwargs):
        calls.append((model.provider, kwargs["purpose"]))
        if kwargs["purpose"] == "doctor-independent":
            return independent_support.reply()
        return llm.Reply(support.answer(), 1, 1, 0)

    monkeypatch.setattr(llm, "chat", chat)
    course = courses.get_course(tmp_path, "demo")
    identifier = doctor_page.create_input(course, "synthetic failure sk-test-not-a-real-key",
                                           "first", "", "demo")
    return course, identifier, calls


def test_send_refuses_without_preview_then_uses_same_material(ready, capsys):
    course, identifier, calls = ready
    with pytest.raises(service.AdviceSetupError, match="preview"):
        doctor_worker.execute(course, identifier, "advice")
    assert not calls
    before = dict(os.environ)
    assert doctor_worker.execute(course, identifier, "preview") == 0 and not calls
    assert "sk-test-not-a-real-key" not in capsys.readouterr().out
    assert doctor_worker.execute(course, identifier, "advice") == 0
    assert calls == [("deepseek", "doctor-advice")] and dict(os.environ) == before


@pytest.mark.parametrize("changed", ["input", "profile"])
def test_changed_input_or_model_config_refuses_paid_request(ready, changed):
    course, identifier, calls = ready
    doctor_worker.execute(course, identifier, "preview")
    if changed == "input":
        path = doctor_page.folder(course, identifier) / "failure.log"
        path.write_text("changed", encoding="utf-8")
    else:
        marker = course.workspace.root / "workspace.toml"
        text = marker.read_text(encoding="utf-8").replace("first.invalid", "changed.invalid")
        marker.write_text(text, encoding="utf-8")
    with pytest.raises(service.AdviceSetupError, match="changed"):
        doctor_worker.execute(course, identifier, "advice")
    assert not calls


def test_verify_saves_artifact_and_apply_requires_current_diff_preview(ready, monkeypatch):
    course, identifier, calls = ready
    monkeypatch.setattr(verify_cli, "Disposable", verify_support.Trial)
    doctor_worker.execute(course, identifier, "preview")
    assert doctor_worker.execute(course, identifier, "verify") == 0
    assert records.read(doctor_page.folder(course, identifier) / "repair.json").module == "demo"
    applied = []
    monkeypatch.setattr(transaction, "apply", lambda plan, c: applied.append(plan))
    with pytest.raises(service.AdviceSetupError, match="diff"):
        doctor_worker.execute(course, identifier, "apply", confirm=True)
    assert doctor_worker.execute(course, identifier, "repair-preview") == 0
    assert doctor_worker.execute(course, identifier, "apply", confirm=True) == 0
    assert len(applied) == 1 and b"package-bounds" in applied[0].after


def test_independent_review_has_two_blind_calls_and_fake_registry_facts(ready, monkeypatch):
    course, _, calls = ready
    identifier = doctor_page.create_input(course, "synthetic", "first", "second", "demo")
    monkeypatch.setattr(independent, "pypi_facts", lambda n:
                        independent.PackageFacts(n, 2, "2020-01-01"))
    assert doctor_worker.execute(course, identifier, "preview") == 0 and not calls
    assert doctor_worker.execute(course, identifier, "independent") == 0
    assert calls == [("deepseek", "doctor-independent"), ("glm", "doctor-independent")]


def test_storage_identifier_and_profile_cannot_supply_a_shell_or_path(ready):
    course, _, calls = ready
    with pytest.raises(ValueError):
        doctor_page.command(course, "../../other", "advice")
    with pytest.raises(ValueError):
        doctor_page.create_input(course, "synthetic", "not a profile;command", "", "demo")
    assert not calls
