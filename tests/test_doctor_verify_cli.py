"""Explicit verification CLI with fake model, installer and original-course probes."""

import pytest
import test_doctor_advice_cli as support

from tutor import doctor, llm
from tutor.doctor_advice import verification, verify_cli

rig = support.rig
ready = support.ready


class Trial:
    def __init__(self, found, course, module):
        assert module in {"demo", "json"}
        self.module = module

    def baseline(self):
        return verification.Check(self.module == "json", "synthetic baseline")

    def verify(self, changes):
        assert changes[0].package == "demo" and changes[0].spec == "<2"
        return verification.Check(True, "synthetic fixed check passed")


def test_explicit_verification_has_real_before_after_evidence_and_no_original_writes(
        ready, monkeypatch, capsys):
    monkeypatch.setattr(verify_cli, "Disposable", Trial)
    root = ready[0]
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert support.invoke(ready, "--verify-advice", "--module", "demo") == 0
    text = capsys.readouterr().out
    assert "verified in temporary environment" in text and "no course fix applied" in text
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_baseline_pass_does_not_call_a_model(ready, monkeypatch, capsys):
    monkeypatch.setattr(verify_cli, "Disposable", Trial)
    monkeypatch.setattr(llm, "chat", lambda *a, **kw: pytest.fail("baseline passed"))
    assert support.invoke(ready, "--verify-advice", "--module", "json") == 0
    assert "already passes" in capsys.readouterr().out


@pytest.mark.parametrize("arguments", [
    ["--verify-advice", "--failure", "demo.log"], ["--module", "demo"],
    ["--review-new-package", "--failure", "demo.log"], ["--second-profile", "demo"],
    ["--verify-advice", "--failure", "demo.log", "--module", "a;b"],
])
def test_new_flags_are_explicit_and_checked_before_probes(rig, arguments):
    with pytest.raises(SystemExit) as exc:
        doctor.main(arguments, run=rig)
    assert exc.value.code == 2 and not rig.calls
