"""Saved-repair CLI contracts; no real model, installer, course or network endpoint."""

import pytest
import test_doctor_advice_cli as support
import test_doctor_verify_cli as verify_support

from tutor import doctor, doctor_checks, llm
from tutor.doctor_advice import facts, records, transaction, verify_cli
from tutor.doctor_advice.models import Change

rig = support.rig
ready = support.ready


def artifact(ready, tmp_path):
    root, failure, run = ready
    course = doctor_checks.read_course(root)
    found = facts.collect(course, run=run)
    record = records.capture(found, course, "demo", (Change("version", package="demo", spec="<2"),))
    path = tmp_path / "repair.json"
    records.save(path, record)
    return path


def test_preview_has_full_diff_and_does_not_call_chat_or_mutate(ready, tmp_path, monkeypatch,
                                                             capsys):
    path = artifact(ready, tmp_path)
    root, failure, run = ready
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    monkeypatch.setattr(llm, "chat", lambda *a, **kw: pytest.fail("repair preview called model"))
    assert doctor.main([str(root), "--preview-repair", str(path)], run=run) == 0
    text = capsys.readouterr().out
    assert "workspace.toml proposed" in text and "package-bounds" in text
    assert "repair preview only" in text
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_confirmed_apply_reaches_transaction_without_model(ready, tmp_path, monkeypatch):
    path = artifact(ready, tmp_path)
    root, failure, run = ready
    seen = []
    monkeypatch.setattr(llm, "chat", lambda *a, **kw: pytest.fail("application called model"))
    monkeypatch.setattr(transaction, "apply", lambda plan, course: seen.append(plan))
    assert doctor.main([str(root), "--apply-repair", str(path), "--confirm"], run=run) == 0
    assert len(seen) == 1 and b"package-bounds" in seen[0].after


def test_verified_cli_can_save_a_private_artifact(ready, tmp_path, monkeypatch):
    monkeypatch.setattr(verify_cli, "Disposable", verify_support.Trial)
    path = tmp_path / "verified.json"
    arguments = ("--verify-advice", "--module", "demo", "--save-repair", str(path))
    assert support.invoke(ready, *arguments) == 0
    assert records.read(path).module == "demo"


@pytest.mark.parametrize("args", [
    ["--apply-repair", "repair.json"], ["--finish-repair"], ["--confirm"],
    ["--save-repair", "repair.json"], ["--preview-repair", "repair.json", "--failure", "log"],
])
def test_repair_actions_require_their_explicit_inputs_before_any_probe(rig, args):
    with pytest.raises(SystemExit) as exc:
        doctor.main(args, run=rig)
    assert exc.value.code == 2 and not rig.calls
