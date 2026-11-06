"""python -m tutor.validation.batch <label>: validate every exercise of a generated batch.

The GUI runs this right after a batch is generated, in the same task (owner, 2026-11-06:
"Validate all" did not start by itself, though the batch page reads its verdicts from
`validation.json`). Generation validates each item already, but `validate()` writes no file,
so the graph leaves none behind; this writes them, for the files as saved. It only calls the
command line's own `main` once per exercise the batch's manifest lists, in its order: no rule
of its own.

The batch is the newest `batches/*_p3-<label>`, as `python -m tutor.graph` names it. One with
no manifest yet (a run that stopped midway, or waits for an assistant) has nothing to
validate, which is not an error: the generation's own exit code says how it ended.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tutor.config import WorkspaceError, load_env, load_workspace
from tutor.validation.__main__ import main as validate_one


def batch_dir(batches: Path, label: str) -> Path | None:
    found = sorted(batches.glob(f"*_p3-{label}"))
    return found[-1] if found else None


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("Usage: python -m tutor.validation.batch <label>")
        return 2
    load_env()
    try:
        workspace = load_workspace()
    except WorkspaceError as e:
        print(f"workspace: {e}", file=sys.stderr)
        return 1
    folder = batch_dir(workspace.batches_dir, args[0])
    manifest = folder / "manifest.json" if folder else None
    if manifest is None or not manifest.is_file():
        print(f"batch {args[0]}: no manifest yet, so nothing to validate")
        return 0
    ids = json.loads(manifest.read_text(encoding="utf-8")).get("exercises", [])
    codes = []
    for exercise in ids:
        print(f"\n== {exercise}", flush=True)
        codes.append(validate_one([str(folder / "exercises" / exercise),
                                   str(folder / "solutions" / exercise)]))
    valid = codes.count(0)
    print(f"\n{folder.name}: {valid} of {len(codes)} valid")
    return 0 if valid == len(codes) else max(codes)


if __name__ == "__main__":
    sys.exit(main())
