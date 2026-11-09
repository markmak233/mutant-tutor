"""Private synthetic repair artifacts and full transaction rollback; installers are fakes."""

import json
from dataclasses import replace

import pytest
import test_doctor_advice_facts as course_support
import test_doctor_advice_service as support

from tutor import doctor_checks
from tutor.doctor_advice import application, records, transaction, verification
from tutor.doctor_advice.models import Change
from tutor.doctor_advice.service import AdviceInputError, AdviceSetupError


@pytest.fixture
def rig(tmp_path):
    course = course_support.course(tmp_path)
    (course.root / ".venv" / "original.txt").write_text("original", encoding="utf-8")
    found = support.snapshot()
    changes = (Change("version", package="demo", spec="<2"),)
    record = records.capture(found, course, "demo", changes)
    return course, found, record


class Backend:
    """Actions are selected by operation/explicit failure mode, never chat call order."""

    def __init__(self, course, mode="ready"):
        self.course, self.mode, self.calls = course, mode, []

    def baseline(self):
        return verification.Check(self.mode == "already-passes", "baseline")

    def verify(self, changes):
        self.calls.append("verify")
        return verification.Check(self.mode != "unverified", "trial")

    def prepare_python(self, changes):
        return "synthetic-python"

    def install(self, live, python, changes):
        self.calls.append("install")
        live.mkdir()
        (live / "replacement.txt").write_text("replacement", encoding="utf-8")
        if self.mode == "interrupt":
            raise KeyboardInterrupt
        if self.mode == "install-failed":
            raise AdviceSetupError("synthetic installation failed")
        if self.mode == "owner-edited":
            (self.course.root / "workspace.toml").write_text("# owner's concurrent edit\n",
                                                            encoding="utf-8")

    def build(self, proposed):
        self.calls.append("build")
        assert b"package-bounds" in proposed
        return "synthetic-image"

    def _probe(self, **kwargs):
        return verification.Check(self.mode != "probe-failed", "replacement check")


def test_preview_preserves_other_tables_and_moves_prefix_to_explicit_bound(rig):
    course, found, record = rig
    before = (course.root / "workspace.toml").read_bytes()
    plan = application.prepare(course, found, record)
    assert b"[package-bounds]" in plan.after and b'demo = "<2"' in plan.after
    assert '-demo = "1"' in plan.diff() and b'[isolation]\nrunner = "host"' in plan.after
    assert (course.root / "workspace.toml").read_bytes() == before


def test_stale_artifact_cannot_start_application(rig):
    course, found, record = rig
    for invalid in (replace(record, workspace=records.digest(b"different")),
                    replace(record, environment=records.digest(b"different"))):
        with pytest.raises(AdviceInputError, match="stale"):
            application.prepare(course, found, invalid)


def test_records_roundtrip_without_private_text_paths_or_overwrites(rig, tmp_path):
    course, found, record = rig
    path = tmp_path / "repair.json"
    records.save(path, record)
    assert records.read(path) == record
    text = path.read_text(encoding="utf-8")
    assert str(course.root) not in text and "failure" not in text
    with pytest.raises(AdviceInputError):
        records.save(path, record)
    with pytest.raises(AdviceInputError):
        records.save(tmp_path / "student.py", record)
    data = json.loads(text)
    data["changes"][0]["command"] = "not permitted"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(AdviceInputError):
        records.read(path)


@pytest.mark.parametrize("mode", ["install-failed", "interrupt", "probe-failed"])
def test_any_failed_replacement_restores_original_venv_and_text(rig, mode):
    course, found, record = rig
    plan = application.prepare(course, found, record)
    exception = KeyboardInterrupt if mode == "interrupt" else AdviceSetupError
    with pytest.raises(exception):
        transaction.apply(plan, course, backend=Backend(course, mode))
    assert (course.root / "workspace.toml").read_bytes() == plan.before
    assert (course.root / ".venv" / "original.txt").read_text(encoding="utf-8") == "original"
    assert not any((course.root / n).exists() for n in (
        transaction.BEFORE, transaction.OLD_VENV, transaction.RECEIPT, transaction.LOCK))


def test_success_keeps_backups_and_explicit_undo_restores_them(rig):
    course, found, record = rig
    plan = application.prepare(course, found, record)
    transaction.apply(plan, course, backend=Backend(course))
    assert (course.root / "workspace.toml").read_bytes() == plan.after
    assert (course.root / transaction.OLD_VENV / "original.txt").is_file()
    transaction.finish(course.root, undo=True)
    assert (course.root / "workspace.toml").read_bytes() == plan.before
    assert (course.root / ".venv" / "original.txt").is_file()


def test_concurrent_owner_edit_is_preserved_even_during_failed_apply(rig):
    course, found, record = rig
    plan = application.prepare(course, found, record)
    with pytest.raises(AdviceSetupError, match="edited"):
        transaction.apply(plan, course, backend=Backend(course, "owner-edited"))
    text = (course.root / "workspace.toml").read_text(encoding="utf-8")
    assert text == "# owner's concurrent edit\n"
    assert (course.root / ".venv" / "original.txt").is_file()


def test_discard_backups_keeps_new_environment(rig):
    course, found, record = rig
    transaction.apply(application.prepare(course, found, record), course, backend=Backend(course))
    transaction.finish(course.root, undo=False)
    assert (course.root / ".venv" / "replacement.txt").is_file()
    assert not (course.root / transaction.OLD_VENV).exists()


def test_changed_configuration_refuses_undo(rig):
    course, found, record = rig
    transaction.apply(application.prepare(course, found, record), course, backend=Backend(course))
    marker = course.root / "workspace.toml"
    marker.write_text(marker.read_text(encoding="utf-8") + "\n# owner edited\n", encoding="utf-8")
    with pytest.raises(AdviceSetupError, match="edited"):
        transaction.finish(course.root, undo=True)
    assert (course.root / transaction.OLD_VENV).is_dir()


def test_finish_preserves_later_owner_edits_while_discarding_only_original_backups(rig):
    course, found, record = rig
    transaction.apply(application.prepare(course, found, record), course, backend=Backend(course))
    marker = course.root / "workspace.toml"
    text = marker.read_text(encoding="utf-8") + "\n# owner edited\n"
    marker.write_text(text, encoding="utf-8")
    transaction.finish(course.root, undo=False)
    assert marker.read_text(encoding="utf-8") == text
    assert (course.root / ".venv" / "replacement.txt").is_file()


def test_container_application_changes_no_host_venv(rig):
    course, found, record = rig
    marker = course.root / "workspace.toml"
    marker.write_text(marker.read_text(encoding="utf-8").replace('runner = "host"',
                                                                 'runner = "container"'),
                      encoding="utf-8")
    course = doctor_checks.read_course(course.root)
    found = replace(found, source="course image",
                    context=replace(found.context, python_locked=True))
    record = records.capture(found, course, record.module, record.changes)
    plan = application.prepare(course, found, record)
    backend = Backend(course)
    transaction.apply(plan, course, backend=backend)
    assert backend.calls == ["verify", "build"]
    assert (course.root / ".venv" / "original.txt").is_file()
    transaction.finish(course.root, undo=True)


def test_failed_undo_promotion_restores_the_applied_environment(rig, monkeypatch):
    course, found, record = rig
    plan = application.prepare(course, found, record)
    transaction.apply(plan, course, backend=Backend(course))
    original = transaction._atomic

    def fail_old_configuration(path, raw):
        if raw == plan.before:
            raise OSError("synthetic atomic promotion failure")
        original(path, raw)

    monkeypatch.setattr(transaction, "_atomic", fail_old_configuration)
    with pytest.raises(OSError):
        transaction.finish(course.root, undo=True)
    assert (course.root / "workspace.toml").read_bytes() == plan.after
    assert (course.root / ".venv" / "replacement.txt").is_file()
    assert (course.root / transaction.OLD_VENV / "original.txt").is_file()
