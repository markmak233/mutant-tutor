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
    parser.add_argument("--failure", type=Path, help="reviewed UTF-8 failure record for advice")
    parser.add_argument("--profile", help="explicit doctor model profile")
    parser.add_argument("--env-file", type=Path,
                        help="explicit credentials file, read into a private map")


def argument_problem(args: argparse.Namespace) -> str | None:
    """Validate flag grammar without importing environment/probe/model modules."""
    enabled = args.advise or args.preview_advice
    if enabled and args.failure is None:
        return "--advise/--preview-advice requires --failure FILE"
    if not enabled and any(x is not None for x in (args.failure, args.profile, args.env_file)):
        return "--failure/--profile/--env-file requires --advise or --preview-advice"
    if args.profile is not None and not args.profile.strip():
        return "--profile must name a model profile"
    return None


def perform(args, course, failure: str, *, run, docker_ready: bool,
            image_ready: bool | None) -> list[Row]:
    """Probe first, then configure, preview or request. No failure can clear earlier FAILs."""
    from tutor import llm
    from tutor.doctor_advice import facts, service

    try:
        found = facts.collect(course, run=run, docker_ready=docker_ready, image_ready=image_ready)
        prepared = service.prepare(found, failure, course=course.root if course else None,
                                   profile=args.profile, env_file=args.env_file)
        # Prepared output has its own selected-secret redactor. The ordinary doctor's
        # blanket environment-value mask would corrupt JSON and hide a model identifier.
        print(f"Advice destination: {prepared.destination}", flush=True)
        print(f"Data: selected sanitized failure record and {found.source} distribution/Python "
              "facts. Review the preview for private content before requesting a send.", flush=True)
        if args.preview_advice:
            print(prepared.preview(), flush=True)
            return [Row("OK", "advice preview only; no model request, install or verification")]
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
