"""Per-build base snapshot and provenance labels, without permanently pinning a course.

Build a FROM-only owned tag, inspect its actual content ID, then use that local tag for
the real build. Inspecting a mutable base tag afterwards would not establish provenance.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

from tutor.image_recipe import ContainerError
from tutor.runtime_env import environment

LABEL = "org.mutant-tutor.build"
_ID = re.compile(r"sha256:[0-9a-f]{64}")


def command(run, argv, *, timeout=60, **kwargs):
    """Bound metadata/build-helper calls and strip model/pip credentials from the client."""
    run = subprocess.run if run is None else run
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
    return hashlib.sha256(recipe.encode("utf-8")).hexdigest()


@contextmanager
def snapshot(recipe: str, run=None, *, canonical: str | None = None):
    """Capture the actual base; only the unique owned temporary tag is removed."""
    first, _, rest = recipe.partition("\n")
    if not first.startswith("FROM "):
        raise ContainerError("expected the canonical image recipe's FROM line")
    base, tag = first[5:], "tutor-base-" + uuid.uuid4().hex
    try:
        done = command(run, ["docker", "build", "--tag", tag, "-"],
                       input=first + "\n", timeout=300)
        if done.returncode:
            raise ContainerError("base snapshot build failed; "
                                 "see Docker configuration/connectivity")
        captured = inspect(tag, run)
        provenance = {"schema": 1, "base": {"name": base, "id": captured["Id"],
                       "repo_digests": captured.get("RepoDigests") or []},
                      "recipe": recipe_digest(canonical or recipe),
                      "build_recipe": recipe_digest(recipe),
                      "built_at": datetime.now(UTC).isoformat()}
        yield "FROM " + tag + "\n" + rest, provenance
    finally:
        removed = command(run, ["docker", "image", "rm", tag], timeout=30)
        if removed.returncode and "No such image" not in removed.stderr:
            raise ContainerError("owned base-snapshot tag cleanup failed; inspect tutor-base tags")


def labels(provenance: dict) -> list[str]:
    """A label preserves exact base/build facts for later metadata-only refreshes."""
    return ["--label", LABEL + "=" + json.dumps(provenance, separators=(",", ":"))]
