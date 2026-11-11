"""Bound image/base facts with content-keyed Docker stand-ins, never owner course data."""

import hashlib
import json

import pytest
import test_container as support

from tutor import image_build, image_records
from tutor.container import build
from tutor.image_recipe import ContainerError, image_name


def test_build_record_binds_actual_base_and_image_without_changing_canonical_name(tmp_path):
    ws, docker = support.workspace(tmp_path), support.FakeDocker()
    expected = image_name(ws.course, ws.effective_requires)
    assert build(ws, docker, say=lambda text: None) == expected
    view = image_records.view(ws, docker)
    assert view.status == "current" and view.data["image"] == expected
    assert view.data["provenance"]["base"]["id"] != view.data["image_id"]
    assert str(tmp_path) not in json.dumps(view.data)
    assert view.data["provenance"]["base"]["name"] == "python:3.11-slim-bookworm"


def test_changed_image_id_makes_an_existing_record_stale(tmp_path):
    ws, docker = support.workspace(tmp_path), support.FakeDocker()
    build(ws, docker, say=lambda text: None)
    tag = image_name(ws.course, ws.effective_requires)
    docker.manifests[tag]["Id"] = "sha256:" + hashlib.sha256(b"changed").hexdigest()
    assert image_records.view(ws, docker).status == "stale"


def test_legacy_refresh_neither_builds_nor_invents_base_provenance(tmp_path, monkeypatch):
    ws, docker = support.workspace(tmp_path), support.FakeDocker()
    monkeypatch.setenv("DOCTOR_TOKEN", "sk-test-not-a-real-key")

    def run(command, **kwargs):
        assert "DOCTOR_TOKEN" not in kwargs["env"]
        return docker(command, **kwargs)

    data = image_records.record(ws, run)
    assert data["provenance"] is None
    assert "build" not in docker.verbs() and "pull" not in docker.verbs()
    probe = next(c for c in docker.commands if c[1] == "run")
    assert "--read-only" in probe and "none" in probe and "-v" not in probe
    # Distribution iteration must not replace the owned container name used for cleanup.
    killed = next(c for c in docker.commands if c[1] == "kill")
    assert killed[-1] == probe[probe.index("--name") + 1]
    assert killed[-1].startswith("tutor-image-record-")


def test_malformed_and_oversized_private_records_never_look_current(tmp_path):
    ws = support.workspace(tmp_path)
    assert image_records.view(ws).status == "missing"
    path = image_records.path_for(ws, create=True)
    for raw in (b"{}", b"x" * (image_records.MAX_BYTES + 1)):
        path.write_bytes(raw)
        assert image_records.view(ws).status == "invalid"


def test_comparison_uses_course_results_and_reports_unknown_separately(tmp_path):
    ws, docker = support.workspace(tmp_path, {"python": "3.11", "demo": "1"}), support.FakeDocker()
    data = image_records.record(ws, docker)
    data["inventory"]["packages"]["demo"] = "1.5"
    rows = image_records.comparison(ws, data, [{"name": "python", "found": "3.13.2"},
                                              {"name": "demo", "found": "1.5"}])
    assert [r["state"] for r in rows] == ["different", "same"]
    assert all(r["state"] == "unknown" for r in image_records.comparison(ws, data, []))


def test_doctor_build_keeps_old_images_for_rollback(tmp_path):
    docker = support.FakeDocker(images="tutor-run-demo:old\n")
    build(support.workspace(tmp_path), docker, keep_older=True, say=lambda text: None)
    assert "image ls" not in docker.verbs()
    removed = [c[-1] for c in docker.commands if c[1:3] == ["image", "rm"]]
    assert all(n.startswith("tutor-base-") for n in removed)


def test_snapshot_is_cleaned_when_the_caller_is_interrupted():
    docker = support.FakeDocker()
    with pytest.raises(KeyboardInterrupt), image_build.snapshot("FROM python:3.11\n", docker):
        raise KeyboardInterrupt
    assert docker.commands[-1][:3] == ["docker", "image", "rm"]


def test_tag_change_during_inventory_is_refused_without_persisting_a_false_snapshot(tmp_path):
    ws, docker = support.workspace(tmp_path), support.FakeDocker()
    tag = image_name(ws.course, ws.effective_requires)

    def run(command, **kwargs):
        result = docker(command, **kwargs)
        if command[1] == "run":
            docker.manifests[tag] = {"Id": "sha256:" + hashlib.sha256(b"new image").hexdigest()}
        return result

    with pytest.raises(ContainerError, match="changed"):
        image_records.record(ws, run)
    assert not image_records.path_for(ws).exists()
