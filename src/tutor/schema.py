"""The batch format: metadata.json and manifest.json as Pydantic models.

One definition for everyone who writes a batch, Claude Code by hand now and the phase 3
pipeline later (docs/design.md, "Batch format contract"). Validation rule 0 checks an
exercise's metadata.json with it.

    python -m tutor.schema <batch_dir> ...      check manifest, every metadata.json, folders
    python -m tutor.schema --export [<dir>]     write the models as JSON Schema files
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

SCHEMA_VERSION = 1
SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"  # the repo's schemas/

Name = Annotated[str, Field(min_length=1)]
# A function or class name, or Class.run for file-level Spark (which names have a dot is
# checked in Metadata). Generated metadata once put a call signature here.
EntryPoint = Annotated[str, Field(pattern=r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)?$")]


class _Strict(BaseModel):
    # An unknown field is an error: a misspelt or outdated field name is caught here
    # instead of being silently ignored by everything that reads the file. Strict types:
    # "yes" is not a bool and "4" is not an int.
    model_config = ConfigDict(extra="forbid", strict=True)


class Constraints(_Strict):
    """Static API constraints, checked with `ast` by validation/api.py."""

    # The descriptions reach the generating model through the JSON Schema: without them a
    # model once put API calls it wanted used into required_any and could not tell why
    # that failed.
    forbidden_calls: list[Name] = Field(default=[], description=(
        'Names that must not be used as a method or function, e.g. "groupByKey".'))
    forbidden_imports: list[Name] = Field(default=[], description=(
        'Modules that must not be imported, submodules included, e.g. "pyspark.sql".'))
    required_definitions: list[Name] = Field(default=[], description=(
        "Functions or methods the code must define (def, or an MRStep keyword), e.g. "
        '"mapper_final", or "AuctionStats.run" for a method of a class. Not API calls the '
        "code should make."))
    required_any: list[Annotated[list[Name], Field(min_length=1)]] = Field(
        default=[], description=(
            "Groups of names the code defines (as in required_definitions); at least one "
            'group must be fully defined, e.g. [["combiner"], ["mapper_init", '
            '"mapper_final"]]. Not API calls.'))
    count_uses: list[Name] = Field(default=[], description=(
        'Names whose uses are counted for the report, never limited, e.g. "MRStep".'))


class Source(_Strict):
    """Where a workspace exercise comes from: slide footers and PDF pages, both, since
    they differ (docs/design.md, batch-1 review)."""

    lecture: Name
    slides: Name
    pdf_pages: Name
    lab: str | None = None
    assumptions: list[str] = []


class Metadata(_Strict):
    """solutions/<id>/metadata.json (examples: solution/metadata.json)."""

    schema_version: int = SCHEMA_VERSION
    id: Annotated[str, Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")]
    title: Name
    topic: Name
    type: Literal["implementation", "completion", "debugging"]
    interface: Literal["function", "file"]
    difficulty: Literal["easy", "medium", "hard"]
    framework: Literal["python", "mrjob", "pyspark-rdd", "pyspark-df"]
    runner: Literal["inline", "hadoop"] | None = None
    # What the tests call: function names, an MRJob class, or "Class.run" for Spark.
    entry_point: Annotated[list[EntryPoint], Field(min_length=1)]
    constraints: Constraints = Constraints()
    partition_check: bool = False
    combiner_check: bool = False
    timeout_s: Annotated[float, Field(gt=0)] | None = None
    mutant_partitions: Annotated[list[Annotated[int, Field(ge=1)]], Field(min_length=1)] = [2]
    # `python student.py` runs on its own (validation rule "main"): a function exercise runs
    # the question's example and prints the result; a file exercise is a program taking its
    # paths from the command line, as the slides write them. False: code a framework
    # launches with no main of its own (a Hadoop submission, a project's entry point).
    # None: written before the rule (2026-10-29), not checked; the pipeline sets True.
    standalone: Annotated[bool | None, Field(description=(
        "student.py and reference.py run with `python <file>.py`: a function exercise has "
        "an `if __name__ == \"__main__\":` block that runs the question's example and prints "
        "the result; a file exercise one that reads its paths from sys.argv. False only for "
        "code a framework launches that has no main of its own (a Hadoop submission, a "
        "project's entry point). Left out: an exercise from before this rule."))] = None
    source: Literal["synthetic"] | Source

    @model_validator(mode="after")
    def _consistent(self) -> Metadata:
        mrjob = self.framework == "mrjob"
        if mrjob and self.runner is None:
            raise ValueError('an mrjob exercise names its runner: "inline" or "hadoop"')
        if not mrjob and self.runner is not None:
            raise ValueError("runner is only for mrjob exercises")
        if self.combiner_check and not (mrjob and self.runner == "inline"):
            # On Hadoop the task processes run the student's own script, so a combiner
            # removed in the test process would still run there.
            raise ValueError("combiner_check needs an mrjob exercise on the inline runner")
        dotted = [name for name in self.entry_point if "." in name]
        if self.interface == "function" or mrjob:
            if dotted:
                raise ValueError(f"entry_point names a function or class, no dots: {dotted}")
        elif self.framework.startswith("pyspark"):
            bad = [n for n in self.entry_point if n.count(".") != 1 or not n.endswith(".run")]
            if bad:
                raise ValueError(f'a file-level Spark entry_point is "Class.run": {bad}')
        return self


class SourceRef(_Strict):
    """One material a batch was generated from, relative to the workspace."""

    path: Name
    slides: str | None = None
    pdf_pages: str | None = None


class Priority(_Strict):
    """Which exercises the owner should solve; unlisted ones count as must."""

    must: list[Name] = []
    optional: list[Name] = []
    skip: list[Name] = []
    decided: str | None = None


class Manifest(_Strict):
    """batches/<batch>/manifest.json."""

    schema_version: int = SCHEMA_VERSION
    phase: Annotated[int, Field(ge=1)]
    date: Annotated[dt.date, Field(strict=False)]  # JSON has no date type: "2026-10-28"
    label: Name
    mode: Literal["learn", "review"]
    sources: Annotated[list[SourceRef], Field(min_length=1)]
    method: Name  # who drafted it, e.g. Claude Code in chat or the phase 3 pipeline
    model: Name
    git_commit: Name
    exercises: Annotated[list[Name], Field(min_length=1)]
    not_covered: str | None = None
    lab_references: str | None = None
    notes: str | None = None
    priority: Priority | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Manifest:
        if len(set(self.exercises)) != len(self.exercises):
            raise ValueError("exercises lists an id twice")
        if self.priority is not None:
            marked = self.priority.must + self.priority.optional + self.priority.skip
            unknown = sorted(set(marked) - set(self.exercises))
            if unknown:
                raise ValueError(f"priority names exercises not in the batch: {unknown}")
            if len(set(marked)) != len(marked):
                raise ValueError("priority gives an exercise more than one mark")
        return self


def problems(model: type[BaseModel], data: object) -> list[str]:
    """Every way `data` breaks `model`, one readable line each; empty if it fits."""
    try:
        model.model_validate(data)
    except ValidationError as e:
        return [_describe(err) for err in e.errors()]
    return []


def _describe(err: dict) -> str:
    where = ".".join(str(part) for part in err["loc"])
    message = err["msg"].removeprefix("Value error, ")
    return f"{where}: {message}" if where else message


def check_batch(batch_dir: Path) -> list[str]:
    """Problems with one batch: its manifest, every exercise's metadata, and whether the
    manifest, exercises/ and solutions/ agree on the exercise ids."""
    found: list[str] = []
    manifest = _load_json(batch_dir / "manifest.json", "manifest.json", found)
    if manifest is not _UNREADABLE:  # a file holding null is checked, and fails
        found += [f"manifest.json: {p}" for p in problems(Manifest, manifest)]

    exercise_ids = _subdirs(batch_dir / "exercises")
    solution_ids = _subdirs(batch_dir / "solutions")
    if exercise_ids != solution_ids:
        found.append(f"exercises/ and solutions/ differ: {sorted(exercise_ids ^ solution_ids)}")
    listed_ids = manifest.get("exercises") if isinstance(manifest, dict) else None
    # Compared only when it is a list of ids; any other shape is reported above.
    if isinstance(listed_ids, list) and all(isinstance(i, str) for i in listed_ids):
        listed = set(listed_ids)
        if listed != exercise_ids:
            found.append(f"manifest exercises and exercises/ differ: "
                         f"{sorted(listed ^ exercise_ids)}")

    for exercise_id in sorted(solution_ids):
        rel = f"solutions/{exercise_id}/metadata.json"
        metadata = _load_json(batch_dir / rel, rel, found)
        if metadata is _UNREADABLE:
            continue
        found += [f"{rel}: {p}" for p in problems(Metadata, metadata)]
        if isinstance(metadata, dict) and metadata.get("id") != exercise_id:
            found.append(f"{rel}: id {metadata.get('id')!r} differs from its folder name")
    return found


_UNREADABLE = object()  # not None: a file may hold JSON null, which is a problem to report


def _load_json(path: Path, label: str, found: list[str]) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        found.append(f"missing {label}")
    except json.JSONDecodeError as e:
        found.append(f"{label}: not valid JSON: {e}")
    return _UNREADABLE


def _subdirs(path: Path) -> set[str]:
    if not path.is_dir():
        return set()
    return {p.name for p in path.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))}


def json_schemas() -> dict[str, dict]:
    """The models as JSON Schema, by file name: for assistants that do not run Python and
    for phase 3's structured output, so all of them read the same definition; and the
    generation settings, for a settings form (imported here: graph.settings uses this
    module, so a top-level import would be circular)."""
    from tutor.graph.settings import settings_schema

    return {
        "metadata.schema.json": Metadata.model_json_schema(),
        "manifest.schema.json": Manifest.model_json_schema(),
        "generation-settings.schema.json": settings_schema(),
    }


def export(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, schema in json_schemas().items():
        path = out_dir / name
        path.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["--export"] and len(args) <= 2:
        for path in export(Path(args[1]) if len(args) == 2 else SCHEMA_DIR):
            print(f"wrote {path}")
        return 0
    if not args or args[0].startswith("-"):
        print("Usage: python -m tutor.schema <batch_dir> ...\n"
              "       python -m tutor.schema --export [<dir>]")
        return 2
    all_ok = True
    for arg in args:
        found = check_batch(Path(arg))
        print(Path(arg).name)
        for line in found:
            print(f"  FAIL {line}")
        print("ok" if not found else f"INVALID ({len(found)} problems)")
        all_ok = all_ok and not found
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
