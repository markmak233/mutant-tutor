"""Repair evidence/stop rules with no installation, model or real course."""

import json

import pytest

from tutor.doctor_advice.models import Change, GateResult
from tutor.doctor_advice.verification import MARKER, Check, measured, probe_script, repair
from tutor.execution import Outcome, RunResult


def accepted(name="demo", spec="<2"):
    return GateResult("accepted", "permitted", (Change("version", package=name, spec=spec),))


def test_a_passing_baseline_spends_nothing():
    def forbidden(*args):
        pytest.fail("a passing baseline must not request advice/install")
    result = repair(Check(True, "passed"), propose=forbidden, verify=forbidden)
    assert result.status == "already-passes" and not result.changes


def test_verified_requires_before_failure_after_same_check_success():
    result = repair(Check(False, "import failed", "demo"),
                    propose=lambda *a: accepted(), verify=lambda c: Check(True, "passed"))
    assert result.status == "verified" and not result.baseline.passed
    assert result.checks[-1].passed and len(result.changes) == 1


def test_changes_are_cumulative_and_each_trial_receives_the_whole_batch():
    seen = []

    def propose(history, changes):
        return accepted("demo" if not history else "companion")

    def verify(changes):
        seen.append(changes)
        return Check(len(changes) == 2, "measured")

    result = repair(Check(False, "failed"), propose=propose, verify=verify)
    assert result.status == "verified" and [len(c) for c in seen] == [1, 2]


def test_same_module_twice_stops_including_the_baseline():
    result = repair(Check(False, "failed", "missing"), propose=lambda *a: accepted(),
                    verify=lambda c: Check(False, "failed", "missing"))
    assert result.status == "stopped" and len(result.checks) == 1
    assert "twice" in result.reason


def test_repeated_proposals_do_not_repeat_an_install():
    result = repair(Check(False, "failed"), propose=lambda *a: accepted(),
                    verify=lambda c: Check(False, "failed"))
    assert len(result.checks) == 1 and "repeated" in result.reason


def test_three_round_cap_and_no_false_repair_claim():
    def propose(history, changes):
        return accepted(spec=f"<{len(history) + 2}")
    result = repair(Check(False, "failed"), propose=propose,
                    verify=lambda c: Check(False, "failed"))
    assert result.status == "stopped" and len(result.checks) == 3


@pytest.mark.parametrize("status", ["stopped", "rejected", "new-package-review"])
def test_unaccepted_gate_results_never_reach_installation(status):
    def forbidden(changes):
        pytest.fail("gate released no changes")
    result = repair(Check(False, "failed"),
                    propose=lambda *a: GateResult(status, "stop"), verify=forbidden)
    assert result.status == status and not result.checks


@pytest.mark.parametrize("module", ["a;b", "x\ny", "-m", "__main__", "x.__dict__", "a/b"])
def test_probe_accepts_only_plain_module_data(module):
    with pytest.raises(ValueError):
        probe_script(module)


@pytest.mark.parametrize("returncode,valid", [(0, True), (1, False), (None, False)])
def test_measured_pass_needs_both_protocol_and_process_success(returncode, valid):
    output = MARKER + json.dumps({"passed": True, "detail": "passed", "missing": None})
    result = RunResult(Outcome.PASSED, returncode, 0.0, output)
    assert measured(result).passed is valid
    assert not measured(RunResult(Outcome.ERROR, 0, 0.0, "not evidence")).passed


def test_fixed_script_is_valid_python_and_has_a_dependency_check():
    script = probe_script("demo.module")
    compile(script, "fixed-probe", "exec")
    assert '"-m", "pip", "check"' in script and "importlib.import_module" in script
