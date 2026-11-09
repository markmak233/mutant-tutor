"""Bounded repair state machine; callbacks own processes, model requests and cleanup.

Evidence means the same fixed module/dependency check failed before and passed after.
It says nothing about untested exercises. No model-written program is ever executed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from tutor.doctor_advice.models import Change, GateResult

_MODULE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
MARKER = "tutor-doctor-check:"


@dataclass(frozen=True)
class Check:
    """Measured check, not an interpretation of pip's installation stdout."""

    passed: bool
    detail: str
    missing: str | None = None


@dataclass(frozen=True)
class Verification:
    """Cumulative proposals are evidence only; none have been applied to a course."""

    status: str
    baseline: Check
    checks: tuple[Check, ...] = ()
    changes: tuple[Change, ...] = ()
    reason: str = ""
    review: GateResult | None = None


def probe_script(module: str) -> str:
    """Only a dotted module identifier enters our fixed script, JSON-encoded as data."""
    if not _MODULE.fullmatch(module) or any(p.startswith("__") for p in module.split(".")):
        raise ValueError("--module must be a plain dotted Python import name")
    return f'''# Fixed tutor doctor import/dependency check, not model-generated code.
import importlib, json, subprocess, sys
def emit(passed, detail, missing=None):
    print({MARKER!r} + json.dumps(dict(passed=passed, detail=detail, missing=missing)))
try:
    importlib.import_module({json.dumps(module)})
except ModuleNotFoundError as exc:
    emit(False, "module import failed", exc.name)
    sys.exit(1)
except Exception as exc:
    emit(False, "module import failed: " + type(exc).__name__)
    sys.exit(1)
try:
    done = subprocess.run([sys.executable, "-m", "pip", "check"], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=20)
    detail = done.stdout[-8000:] if done.returncode else "import and pip check passed"
    emit(done.returncode == 0, detail)
    sys.exit(0 if done.returncode == 0 else 1)
except (OSError, subprocess.TimeoutExpired):
    emit(False, "pip dependency check did not complete")
    sys.exit(1)
'''


def measured(result) -> Check:
    """Malformed/absent evidence or a process failure can never become a verified pass."""
    lines = [s[len(MARKER):] for s in result.output.splitlines() if s.startswith(MARKER)]
    try:
        if len(lines) != 1:
            raise ValueError
        value = json.loads(lines[0])
        if (set(value) != {"passed", "detail", "missing"}
                or type(value["passed"]) is not bool or not isinstance(value["detail"], str)
                or (value["missing"] is not None and not _MODULE.fullmatch(value["missing"]))):
            raise ValueError
        passed = value["passed"] and result.returncode == 0 and not result.stop_reason
        return Check(bool(passed), value["detail"], value["missing"])
    except (ValueError, TypeError, AttributeError):
        return Check(False, "probe produced no valid evidence (failed, interrupted or timed out)")


def repair(baseline: Check, *, propose: Callable, verify: Callable) -> Verification:
    """Three cumulative fresh-environment trials, with repeat/missing-module stop rules.

    propose receives only measured history and prior allowed changes. The caller gates
    every reply against original permissions. verify builds a fresh environment from
    the original snapshot plus the cumulative batch; it must clean it in a finally block.
    """
    if baseline.passed:
        return Verification("already-passes", baseline, reason="baseline already passes; no repair")
    history, changes, signatures = [], {}, set()
    missing = {baseline.missing: 1} if baseline.missing else {}
    for _ in range(3):
        reply = propose(tuple(history), tuple(changes.values()))
        if reply.status != "accepted":
            return Verification(reply.status, baseline, tuple(history), tuple(changes.values()),
                                reply.reason, reply)
        updated = dict(changes)
        for change in reply.changes:
            updated[(change.kind == "python", change.package)] = change
        signature = tuple(sorted((c.kind, c.python or "", c.package or "", c.spec or "")
                                 for c in updated.values()))
        if signature in signatures or updated == changes:
            return Verification("stopped", baseline, tuple(history), tuple(changes.values()),
                                "repeated proposal; no further trial")
        signatures.add(signature)
        changes = updated
        check = verify(tuple(changes.values()))
        history.append(check)
        if check.passed:
            return Verification("verified", baseline, tuple(history), tuple(changes.values()),
                                "baseline failed; the same import/dependency check now passes")
        if check.missing:
            missing[check.missing] = missing.get(check.missing, 0) + 1
            if missing[check.missing] >= 2:
                return Verification("stopped", baseline, tuple(history), tuple(changes.values()),
                                    "the same missing module failed twice")
    return Verification("stopped", baseline, tuple(history), tuple(changes.values()),
                        "three repair rounds exhausted")
