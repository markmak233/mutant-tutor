"""Versioned private image inventories, bound to immutable image ID and canonical recipe.

Refresh reads the current image without build/pull/install. Legacy images have unknown
base/build provenance. Version differences are information, not automatic pinning/failure.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tutor import image_build, image_inventory, image_recipe, package_bounds
from tutor.config import WorkspaceError, load_workspace
from tutor.image_recipe import ContainerError

MAX_BYTES = 2 * 1024 * 1024


def path_for(ws, *, create: bool = False) -> Path:
    """Reject links/junctions and paths outside the course before any write/read."""
    from tutor.execution import _is_link

    root = ws.root.resolve()
    for folder in (root / "logs", root / "logs" / "image-builds"):
        if _is_link(folder) or not folder.resolve().is_relative_to(root):
            raise ContainerError("image records must stay inside the private course")
        if create:
            folder.mkdir(exist_ok=True, mode=0o700)
    tag = image_recipe.image_name(ws.course, ws.effective_requires).rsplit(":", 1)[-1]
    path = root / "logs" / "image-builds" / (tag + ".json")
    if _is_link(path):
        raise ContainerError("image record cannot be a symbolic link or junction")
    return path


def record(ws, run=None) -> dict:
    """Probe an immutable image, then prove the course tag still points to that image."""
    image = image_recipe.image_name(ws.course, ws.effective_requires)
    before = image_build.inspect(image, run)
    inventory = image_inventory.collect(before["Id"], run)
    after = image_build.inspect(image, run)
    if before["Id"] != after["Id"]:
        raise ContainerError("image changed during recording; refresh after the build finishes")
    canonical = image_build.recipe_digest(image_recipe.dockerfile(ws.effective_requires))
    labels = (before.get("Config") or {}).get("Labels") or {}
    try:
        provenance = json.loads(labels[image_build.LABEL]) if image_build.LABEL in labels else None
        if provenance is not None and provenance["recipe"] != canonical:
            raise ValueError
        data = {"schema": 1, "image": image, "image_id": before["Id"], "recipe": canonical,
                "recorded_at": datetime.now(UTC).isoformat(),
                "provenance": provenance, "inventory": inventory}
        validate(data)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ContainerError("image provenance is invalid or belongs to another recipe; "
                             "rebuild") from None
    encoded = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    if len(encoded) > MAX_BYTES:
        raise ContainerError("image record exceeds its size limit")
    target = path_for(ws, create=True)
    historical = target.with_name(target.stem + "-" + before["Id"][7:] + ".json")
    from tutor.execution import _is_link

    if _is_link(historical):
        raise ContainerError("historical image record cannot be a link")
    if not historical.exists():
        descriptor = os.open(historical, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
        except BaseException:
            historical.unlink(missing_ok=True)
            raise
    temporary = target.with_name(".image-record-" + uuid.uuid4().hex + ".tmp")
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return data


def validate(data) -> None:
    """Strict persisted shape/versions/digests; a malformed record is never current."""
    fields = {"schema", "image", "image_id", "recipe", "recorded_at", "provenance", "inventory"}
    if (set(data) != fields
            or type(data["schema"]) is not int or data["schema"] != 1
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", data["image_id"])
            or not re.fullmatch(r"[0-9a-f]{64}", data["recipe"])):
        raise ValueError
    if datetime.fromisoformat(data["recorded_at"]).tzinfo is None:
        raise ValueError
    inventory = data["inventory"]
    if set(inventory) != {"python", "packages", "tools"}:
        raise ValueError
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}", inventory["python"]):
        raise ValueError
    for name, version in inventory["packages"].items():
        if package_bounds.identity(name) != name or not image_inventory._VERSION.fullmatch(version):
            raise ValueError
    if set(inventory["tools"]) != {"java", "gcc", "hadoop"} or any(
            v is not None and not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", v)
            for v in inventory["tools"].values()):
        raise ValueError
    provenance = data["provenance"]
    if provenance is not None:
        base = provenance["base"]
        if (set(provenance) != {"schema", "base", "recipe", "build_recipe", "built_at"}
                or type(provenance["schema"]) is not int or provenance["schema"] != 1
                or provenance["recipe"] != data["recipe"]
                or not re.fullmatch(r"[0-9a-f]{64}", provenance["build_recipe"])
                or datetime.fromisoformat(provenance["built_at"]).tzinfo is None
                or set(base) != {"name", "id", "repo_digests"}
                or not re.fullmatch(r"[A-Za-z0-9_./:@-]+", base["name"])
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", base["id"])
                or not isinstance(base["repo_digests"], list)
                or any(not isinstance(v, str) or len(v) > 512
                       or not re.fullmatch(r"[A-Za-z0-9_./:@-]+@sha256:[0-9a-f]{64}", v)
                       for v in base["repo_digests"])):
            raise ValueError


@dataclass(frozen=True)
class View:
    status: str
    data: dict | None = None
    detail: str = ""


def view(ws, run=None) -> View:
    """Re-read disk and actual image identity; never infer currency from a file alone."""
    try:
        with path_for(ws).open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError
        data = json.loads(raw.decode("utf-8"))
        validate(data)
        image = image_recipe.image_name(ws.course, ws.effective_requires)
        canonical = image_build.recipe_digest(image_recipe.dockerfile(ws.effective_requires))
        if (data["image"] != image or data["recipe"] != canonical
                or image_build.inspect(image, run)["Id"] != data["image_id"]):
            return View("stale", data, "Image identity/recipe changed; refresh the record.")
        return View("current", data, "Measured versions of this exact image.")
    except FileNotFoundError:
        return View("missing", detail="No record yet. Refresh the existing image or build it.")
    except ContainerError:
        return View("unavailable", detail="Docker/image or private record path is unavailable.")
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return View("invalid", detail="Record is unreadable/invalid. "
                                      "Refresh it from the current image.")


def comparison(ws, data, host_results: list[dict]) -> list[dict]:
    """Declared versions only, against actual course venv results, with unknown explicit."""
    host = {package_bounds.identity(r["name"]): r.get("found") for r in host_results
            if isinstance(r.get("name"), str) and r["name"] in ws.effective_requires}
    found = data["inventory"]
    rows = []
    for name, required in ws.effective_requires.items():
        package = package_bounds.identity(name)
        image = (found["python"] if name == "python" else found["tools"].get(name)
                 if name in package_bounds.NOT_PIP else found["packages"].get(package))
        local = host.get(package)
        state = ("unknown" if local is None or image is None
                 else "same" if local == image else "different")
        rows.append({"name": name, "required": required, "host": local,
                     "image": image, "state": state})
    return rows


def history(ws) -> list[dict]:
    """Recent immutable-content snapshots across recipes; refresh does not erase builds."""
    from tutor.execution import _is_link

    folder = path_for(ws).parent
    if not folder.is_dir():
        return []
    candidates = [p for p in folder.iterdir()
                  if re.fullmatch(r"[0-9a-f]{12}-[0-9a-f]{64}\.json", p.name) and not _is_link(p)]
    result = []
    for path in sorted(candidates, key=lambda p: p.stat().st_mtime_ns, reverse=True)[:20]:
        try:
            with path.open("rb") as handle:
                raw = handle.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError
            data = json.loads(raw.decode("utf-8"))
            validate(data)
            result.append(data)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("course", type=Path)
    args = parser.parse_args(argv)
    try:
        ws = load_workspace({"WORKSPACE_DIR": str(args.course)})
        data = record(ws)
        print("Recorded current image " + data["image"] + " (no build or install)", flush=True)
        return 0
    except (WorkspaceError, ContainerError, OSError):
        print("Image record unavailable; start Docker/build the course image, "
              "then refresh.", flush=True)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
