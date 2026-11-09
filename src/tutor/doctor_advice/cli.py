"""Lazy CLI adapter: ordinary doctor imports only stdlib argument helpers here.

Advice modules and credentials are touched only after an explicit advice/preview flag
and the tool's Python bootstrap. Provider failures never echo raw exception bodies.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from tutor.doctor_rows import Row


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """One explicit action, and advice-only inputs; existing smoke remains available."""
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--smoke", action="store_true", help="validate one shipped example (slow)")
    modes.add_argument("--advise", action="store_true",
                       help="send selected failure/facts for model advice")
    modes.add_argument("--preview-advice", action="store_true",
                       help="preview sanitized advice data only")
    modes.add_argument("--verify-advice", action="store_true",
                       help="verify gated model repairs in fresh temporary environments")
    modes.add_argument("--review-new-package", action="store_true",
                       help="independent two-provider/PyPI new-name review; never install")
    modes.add_argument("--preview-repair", type=Path, help="preview saved repair's workspace diff")
    modes.add_argument("--apply-repair", type=Path, help="reverify and apply a saved repair")
    modes.add_argument("--undo-repair", action="store_true", help="restore retained repair backups")
    modes.add_argument("--finish-repair", action="store_true",
                       help="discard retained repair backups")
    parser.add_argument("--confirm", action="store_true", help="confirm apply or backup discard")
    parser.add_argument("--save-repair", type=Path, help="save verified changes as private JSON")
    parser.add_argument("--second-profile", help="independent provider profile for new-name review")
    parser.add_argument("--module", help="dotted import to reproduce for --verify-advice")
    parser.add_argument("--failure", type=Path, help="reviewed UTF-8 failure record for advice")
    parser.add_argument("--profile", help="explicit doctor model profile")
    parser.add_argument("--env-file", type=Path,
                        help="explicit credentials file, read into a private map")


def argument_problem(args: argparse.Namespace) -> str | None:
    """Validate flag grammar without importing environment/probe/model modules."""
    enabled = args.advise or args.preview_advice or args.verify_advice or args.review_new_package
    if enabled and args.failure is None:
        return "advice modes require --failure FILE"
    if not enabled and any(x is not None for x in (args.failure, args.profile, args.env_file)):
        return "--failure/--profile/--env-file requires --advise or --preview-advice"
    if args.profile is not None and not args.profile.strip():
        return "--profile must name a model profile"
    if args.verify_advice:
        if (not args.module or not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", args.module)
                or any(p.startswith("__") for p in args.module.split("."))):
            return "--verify-advice requires --module with a plain dotted Python import name"
    elif args.module is not None:
        return "--module requires --verify-advice"
    if args.review_new_package and not args.second_profile:
        return "--review-new-package requires --second-profile NAME"
    if args.second_profile is not None and not args.review_new_package:
        return "--second-profile requires --review-new-package"
    if args.save_repair is not None and not args.verify_advice:
        return "--save-repair requires --verify-advice"
    if (args.apply_repair or args.finish_repair) and not args.confirm:
        return "--apply-repair/--finish-repair requires explicit --confirm"
    if args.confirm and not (args.apply_repair or args.finish_repair):
        return "--confirm is only for --apply-repair or --finish-repair"
    return None


def perform(args, course, failure: str, *, run, docker_ready: bool,
            image_ready: bool | None) -> list[Row]:
    """Probe first, then configure, preview or request. No failure can clear earlier FAILs."""
    from tutor import llm
    from tutor.doctor_advice import facts, service

    try:
        if args.save_repair is not None:
            from tutor.doctor_advice import records

            records.check_target(args.save_repair)
            if course is None:
                raise service.AdviceInputError("saving an applicable repair requires a course")
        found = facts.collect(course, run=run, docker_ready=docker_ready, image_ready=image_ready)
        prepared = service.prepare(found, failure, course=course.root if course else None,
                                   profile=args.profile, env_file=args.env_file)
        second = None
        if args.review_new_package:
            second = service.prepare(found, failure, course=course.root if course else None,
                                     profile=args.second_profile, env_file=args.env_file)
            prepared, second = service.shared_redaction(prepared, second)
        # Prepared output has its own selected-secret redactor. The ordinary doctor's
        # blanket environment-value mask would corrupt JSON and hide a model identifier.
        print(f"Advice destination: {prepared.destination}", flush=True)
        print(f"Data: selected sanitized failure record and {found.source} distribution/Python "
              "facts. Review the preview for private content before requesting a send.", flush=True)
        if args.preview_advice:
            print(prepared.preview(), flush=True)
            return [Row("OK", "advice preview only; no model request, install or verification")]
        if args.verify_advice:
            from tutor.doctor_advice import verify_cli

            return verify_cli.perform(prepared, course, args.module, save=args.save_repair)
        if args.review_new_package:
            from tutor.doctor_advice import independent

            print(f"Independent review destination: {second.destination}", flush=True)
            reviewed = independent.review(prepared, second)
            if reviewed is None:
                return [Row("WARN", "independent models did not agree on a new package",
                            "review the failure; no new package is offered or installed")]
            return [Row("WARN", f"candidate for owner review: {reviewed.name}; "
                        f"PyPI releases with files: {reviewed.releases}; "
                        f"first upload: {reviewed.first_upload} (not a security endorsement)",
                        "review the project independently; no candidate has been installed")]
        return [service.result_row(service.request(prepared), prepared.redactor)]
    except service.AdviceInputError:
        raise  # input errors keep exit 2, not a diagnostic failure status
    except (facts.FactsError, service.AdviceSetupError) as exc:
        # Both types contain controlled messages without user values or provider bodies.
        return [Row("FAIL", "advice unavailable: " + str(exc),
                    "restore the selected environment or doctor profile, then preview again")]
    except llm.LLMError as exc:
        status = re.search(r"\bHTTP [0-9]{3}\b", str(exc))
        suffix = f" ({status.group()})" if status else ""
        return [Row("FAIL", "doctor model request failed" + suffix,
                    "check the selected profile's endpoint/credentials and retry --advise")]
    except Exception:
        # Unexpected provider/configuration objects might put a secret in their repr.
        return [Row("FAIL", "doctor advice check itself failed",
                    "report this output to the owner; no repair was applied")]
