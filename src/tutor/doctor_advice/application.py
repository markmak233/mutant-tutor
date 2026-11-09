"""Repair preview/preconditions, independent of the filesystem transaction backend.

Only version/Python declarations are edited. Saved evidence is rechecked against current
facts; no artifact's status field grants authority. TOML comments/other tables stay intact.
"""

from __future__ import annotations

import difflib
import tomllib
from dataclasses import dataclass, field

from tutor import package_bounds
from tutor.doctor_advice import records
from tutor.doctor_advice.gate import review_cumulative
from tutor.doctor_advice.service import AdviceInputError, AdviceSetupError


@dataclass(frozen=True)
class Plan:
    record: records.Repair
    before: bytes = field(repr=False)
    after: bytes = field(repr=False)
    found: object = field(repr=False)

    def diff(self) -> str:
        return "".join(difflib.unified_diff(self.before.decode("utf-8").splitlines(True),
                                           self.after.decode("utf-8").splitlines(True),
                                           fromfile="workspace.toml before",
                                           tofile="workspace.toml proposed"))


def prepare(course, found, record: records.Repair) -> Plan:
    """Reject stale facts/text and re-gate every cumulative change before any install."""
    if course is None or course.error:
        raise AdviceInputError("applying/previewing a repair requires a valid course")
    before = (course.root / "workspace.toml").read_bytes()
    if (records.digest(before) != record.workspace
            or records.environment_digest(found) != record.environment):
        raise AdviceInputError("repair is stale; verify again against the current "
                               "course/environment")
    gated = review_cumulative(record.changes, found.context)
    if gated.status != "accepted":
        raise AdviceInputError("saved repair is outside current cumulative permissions")
    try:
        import tomlkit
    except ImportError:
        raise AdviceSetupError("repair editing needs the doctor extra; "
                               "reinstall with [doctor]") from None
    text = before.decode("utf-8")
    doc = tomlkit.parse(text)
    original = tomllib.loads(text)
    for change in gated.changes:
        if change.kind == "python":
            if "requires" not in doc:
                doc.add("requires", tomlkit.table())
            doc["requires"]["python"] = change.python
            continue
        for table in ("requires", "package-bounds"):
            section = doc.get(table, {})
            for name in list(section):
                if package_bounds.identity(name) == change.package:
                    del section[name]
        if "package-bounds" not in doc:
            doc.add("package-bounds", tomlkit.table())
        doc["package-bounds"][change.package] = change.spec or ">=0"
    after = tomlkit.dumps(doc).encode("utf-8")
    parsed = tomllib.loads(after.decode("utf-8"))
    # Verify only the two intended tables changed, and that the effective declaration is
    # still valid before saving anything. No lecture/safety/model/runner edit is permitted.
    ignored = {"requires", "package-bounds"}
    if ({k: v for k, v in original.items() if k not in ignored}
            != {k: v for k, v in parsed.items() if k not in ignored}):
        raise AdviceSetupError("repair could not preserve unrelated workspace settings")
    package_bounds.merge(parsed.get("requires", {}), parsed.get("package-bounds", {}))
    return Plan(record, before, after, found)
