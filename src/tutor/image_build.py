"""Per-build base snapshot and provenance labels, without permanently pinning a course.

Build a FROM-only owned tag, inspect its actual content ID, then use that local tag for
the real build. Inspecting a mutable base tag afterwards would not establish provenance.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

from tutor.doctor_advice.redaction import Redactor
from tutor.image_recipe import ContainerError
from tutor.runtime_env import environment

# The image label that carries a build's provenance (base identity, recipe digests, time).
LABEL = "org.mutant-tutor.build"
# An exact content ID as `docker image inspect` gives it; anything else is not an identity.
_ID = re.compile(r"sha256:[0-9a-f]{64}")
# How much of Docker's own error a failed base snapshot shows: its last lines, which name
# the cause (a TLS timeout, a refused pull), without the progress lines before them.
ERROR_LINES, ERROR_CHARS = 5, 1000


def command(run, argv, *, timeout=60, **kwargs):
    """Bound metadata/build-helper calls and strip model/pip credentials from the client."""
    run = subprocess.run if run is None else run
    # Output is captured (these calls answer the code, not the owner), decoded as UTF-8 on
    # every platform, and the client sees an environment without credentials.
    options = {**kwargs, "capture_output": True, "text": True, "encoding": "utf-8",
               "errors": "replace", "timeout": timeout, "env": environment(docker=True)}
    try:
        return run(argv, **options)
    except (OSError, subprocess.TimeoutExpired):
        raise ContainerError("Docker metadata operation failed or timed out") from None


def inspect(image: str, run=None) -> dict:
    """Read one exact image identity, not a readiness inference from a tag or file."""
    done = command(run, ["docker", "image", "inspect", image])
    try:
        # One image, with a well-formed content ID; any other answer means no identity.
        data = json.loads(done.stdout)
        if done.returncode or not isinstance(data, list) or len(data) != 1:
            raise ValueError
        result = data[0]
        if not _ID.fullmatch(result["Id"]):
            raise ValueError
        return result
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ContainerError("image metadata is unavailable; "
                             "build/check the course image") from None


def recipe_digest(recipe: str) -> str:
    """The recipe's identity: a changed recipe gives a changed digest."""
    return hashlib.sha256(recipe.encode("utf-8")).hexdigest()


def docker_error(stderr: str | None) -> str:
    """The last lines of Docker's error, made safe to log: found 2026-11-22, a base pull that
    timed out showed only "base snapshot build failed" in the GUI's task log, with no cause.
    The redactor drops URL userinfo and queries (a proxy's password, a registry token's
    scope), local paths, and the values of credential variables in this environment."""
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    text = "\n".join(lines[-ERROR_LINES:])[-ERROR_CHARS:]
    return Redactor.from_environment(os.environ).clean(text)


@contextmanager
def snapshot(recipe: str, run=None, *, canonical: str | None = None):
    """Capture the actual base; only the unique owned temporary tag is removed."""
    first, _, rest = recipe.partition("\n")
    if not first.startswith("FROM "):
        raise ContainerError("expected the canonical image recipe's FROM line")
    base, tag = first[5:], "tutor-base-" + uuid.uuid4().hex
    try:
        # A build of the FROM line alone: it pulls the base if needed and tags it as ours.
        done = command(run, ["docker", "build", "--tag", tag, "-"],
                       input=first + "\n", timeout=300)
        if done.returncode:
            # Docker's own words name the cause; the next step says where to look.
            said = "".join(f"\n  {line}" for line in docker_error(done.stderr).splitlines())
            raise ContainerError(
                f"Docker could not get the base image {base} ready"
                + (f"; Docker said:{said}\n" if said else "; ")
                + "check the network, a VPN or Docker's proxy settings, or try "
                + f"`docker pull {base}` in a terminal")
        captured = inspect(tag, run)
        # What the label records: the base actually used, and both recipes' digests (the
        # canonical one names the image, the build one is what ran, pinned versions and all).
        provenance = {"schema": 1, "base": {"name": base, "id": captured["Id"],
                       "repo_digests": captured.get("RepoDigests") or []},
                      "recipe": recipe_digest(canonical or recipe),
                      "build_recipe": recipe_digest(recipe),
                      "built_at": datetime.now(UTC).isoformat()}
        yield "FROM " + tag + "\n" + rest, provenance
    finally:
        # Only our own temporary tag goes, also when the build failed or was interrupted.
        removed = command(run, ["docker", "image", "rm", tag], timeout=30)
        if removed.returncode and "No such image" not in removed.stderr:
            raise ContainerError("owned base-snapshot tag cleanup failed; inspect tutor-base tags")


def labels(provenance: dict) -> list[str]:
    """A label preserves exact base/build facts for later metadata-only refreshes."""
    return ["--label", LABEL + "=" + json.dumps(provenance, separators=(",", ":"))]
