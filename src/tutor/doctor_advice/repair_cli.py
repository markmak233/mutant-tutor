"""No-model explicit repair preview/application/undo CLI, after deterministic diagnostics."""

import os

from tutor.doctor_advice import application, facts, records, transaction
from tutor.doctor_advice.redaction import Redactor
from tutor.doctor_advice.service import AdviceInputError, AdviceSetupError
from tutor.doctor_rows import Row


def perform(args, course, *, run, docker_ready, image_ready):
    """Application is a distinct command with confirmation, never automatic advice fallout."""
    if course is None or course.error:
        raise AdviceInputError("repair actions require a valid course folder")
    try:
        if args.undo_repair or args.finish_repair:
            transaction.finish(course.root, undo=args.undo_repair)
            text = "original configuration/environment restored" if args.undo_repair else (
                "retained repair backups discarded")
            return [Row("WARN", text, "run tutor doctor again to refresh diagnostics")]
        found = facts.collect(course, run=run, docker_ready=docker_ready, image_ready=image_ready)
        record = records.read(args.preview_repair or args.apply_repair)
        plan = application.prepare(course, found, record)
        print(Redactor.from_environment(os.environ).clean(plan.diff()), flush=True)
        if args.preview_repair:
            return [Row("OK", "repair preview only; no install, model call or file change")]
        print("Applying: reverify in disposable environments, then replace only the course "
              "environment/configuration with retained undo backups.", flush=True)
        transaction.apply(plan, course)
        return [Row("WARN", "repair applied and checked; original backups retained",
                    "run doctor again; use --undo-repair or --finish-repair --confirm")]
    except AdviceInputError:
        raise
    except (AdviceSetupError, facts.FactsError) as exc:
        return [Row("FAIL", "repair stopped: " + str(exc),
                    "refresh the preview/verification; retained originals are available for undo")]
    except Exception:
        return [Row("FAIL", "repair operation failed; no raw error/configuration is printed",
                    "inspect retained backups and run doctor before another repair")]
