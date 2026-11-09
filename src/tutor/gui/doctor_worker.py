"""Explicit GUI doctor tasks, sharing CLI services and binding sends/applies to previews.

This process alone reads an explicitly selected credential file into a private map; the
GUI server never inherits it. Only selected failure text enters model calls. No real
model/installer is used by acceptance stand-ins. Task input paths are checked again here.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from tutor import doctor_checks
from tutor.doctor_advice import (
    application,
    facts,
    independent,
    records,
    service,
    transaction,
    verify_cli,
)
from tutor.doctor_advice.redaction import Redactor
from tutor.gui import courses, doctor_page


def _write(path: Path, data: dict) -> None:
    transaction._plain(path)
    transaction._atomic(path, json.dumps(data).encode("utf-8"))


def _matches(path: Path, expected: str) -> bool:
    transaction._plain(path)
    try:
        raw = path.read_bytes()
        return len(raw) <= 1024 and json.loads(raw.decode("utf-8")) == {"digest": expected}
    except (OSError, ValueError):
        return False


def _identity(course, folder, prepared, second=None) -> str:
    """A new destination/config/input/environment requires a new inspected preview."""
    data = {"request": prepared.preview(), "destination": prepared.destination,
            "workspace": records.digest((course.workspace.root / "workspace.toml").read_bytes()),
            "failure": records.digest((folder / "failure.log").read_bytes()),
            "second": (second.preview(), second.destination) if second else None}
    return records.digest(json.dumps(data, sort_keys=True).encode("utf-8"))


def execute(course, identifier, action, *, env_file=None, confirm=False):
    """Every action is fixed in code, and revalidates local input/profile state."""
    folder, data = doctor_page.selected(course, identifier)
    selected = doctor_checks.read_course(course.workspace.root)
    if selected.error:
        raise service.AdviceSetupError("workspace could not load; use Environment first")
    found = facts.collect(selected)
    if action in {"repair-preview", "apply"}:
        record = records.read(folder / "repair.json")
        plan = application.prepare(selected, found, record)
        diff = plan.diff()
        if len(diff.encode("utf-8")) > 256 * 1024:
            raise service.AdviceInputError("full repair diff is too large for GUI review; "
                                           "use the CLI preview instead")
        environment = records.environment_digest(found).encode("utf-8")
        identity = records.digest(plan.before + plan.after + environment)
        if action == "repair-preview":
            print(Redactor.from_environment(os.environ).clean(diff), flush=True)
            _write(folder / "apply.json", {"digest": identity})
            return 0
        if not confirm or not _matches(folder / "apply.json", identity):
            raise service.AdviceSetupError("review the current complete repair diff "
                                           "before applying")
        transaction.apply(plan, selected)
        print("Repair applied and checked; backups retained. "
              "Recheck Doctor or use Undo.", flush=True)
        return 0
    failure = service.read_failure(folder / "failure.log")
    prepared = service.prepare(found, failure, course=selected.root, profile=data["profile"],
                               env_file=env_file)
    second = (service.prepare(found, failure, course=selected.root, profile=data["second"],
                              env_file=env_file) if data["second"] else None)
    if second:
        prepared, second = service.shared_redaction(prepared, second)
    identity = _identity(course, folder, prepared, second)
    print("Advice destination: " + prepared.destination, flush=True)
    print("Data: this selected sanitized failure record and measured Python/distribution facts.",
          flush=True)
    if second:
        print("Independent review destination: " + second.destination, flush=True)
    if action == "preview":
        print(prepared.preview(), flush=True)
        _write(folder / "preview.json", {"digest": identity})
        print("Preview only. No model request or install.", flush=True)
        return 0
    if not _matches(folder / "preview.json", identity):
        raise service.AdviceSetupError("input, destination or facts changed; "
                                       "preview again before sending")
    if action == "advice":
        rows = [service.result_row(service.request(prepared), prepared.redactor)]
    elif action == "verify":
        if not data["module"]:
            raise service.AdviceInputError("choose the module to reproduce before verification")
        records.check_target(folder / "repair.json")
        rows = verify_cli.perform(prepared, selected, data["module"], save=folder / "repair.json")
    elif action == "independent":
        if second is None:
            raise service.AdviceInputError("select an independent second provider before review")
        candidate = independent.review(prepared, second)
        if candidate:
            print(f"Owner-review candidate: {candidate.name}; {candidate.releases} PyPI releases "
                  f"with files; first upload {candidate.first_upload}. "
                  "Not a security endorsement; nothing installed.", flush=True)
        else:
            print("No independent agreement on a new package; nothing installed.", flush=True)
        return 0
    else:
        raise service.AdviceInputError("unsupported doctor action")
    for row in rows:
        print(f"{row.status:<5} {row.text}", flush=True)
        if row.next_step:
            print("      next: " + row.next_step, flush=True)
    return int(any(r.status == "FAIL" for r in rows))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("course", type=Path)
    parser.add_argument("input")
    parser.add_argument("action", choices=sorted(doctor_page.ACTIONS))
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    try:
        # Resolve through the same private-course boundary as the GUI routes.
        course = courses.get_course(args.course.parent, args.course.name)
        if course is None:
            raise service.AdviceInputError("select an existing private course")
        return execute(course, args.input, args.action,
                       env_file=args.env_file, confirm=args.confirm)
    except KeyboardInterrupt:
        print("doctor task interrupted", flush=True)
        return 130
    except (service.AdviceInputError, service.AdviceSetupError, facts.FactsError) as exc:
        print("doctor task stopped: " + str(exc), flush=True)
        return 1
    except Exception:
        # Config/providers may quote credentials in exceptions; never print raw bodies.
        print("doctor task failed; recheck the selected input/profile/environment", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
