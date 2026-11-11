"""Numeric bounds propagate through checks/install/recipes without changing old prefixes."""

import pytest
import test_doctor_advice_facts as support

from tutor import compat, config, envcheck, image_recipe, package_bounds, workspace


@pytest.mark.parametrize("found,spec,expected", [
    ("1.5", "~=1.4", True), ("2.0", "~=1.4", False), ("1.4.9", "~=1.4.3", True),
    ("1.5", "~=1.4.3", False), ("2", ">=2,<2", False), ("3.5.0", "!=3.5", False),
    ("2.0rc1", ">=1", False), ("2.4", ">=2,<3,!=2.3", True),
])
def test_numeric_comparisons_and_unsupported_installed_releases(found, spec, expected):
    assert package_bounds.matches(found, spec) is expected


@pytest.mark.parametrize("spec,overlap", [("<3.5", False), (">=4", False), ("~=4.0", False),
                                       (">=3,<4", True), ("==3.5.0", True), (">=4,<3", False)])
def test_known_conflict_family_cannot_be_bypassed_by_a_bound(spec, overlap):
    assert bool(compat.conflicts({"pyspark": spec}, "3.14")) is overlap


@pytest.mark.parametrize("requires,bounds", [
    ({"Demo_pkg": "1"}, {"demo-pkg": ">=2"}), ({}, {"python": ">=3"}),
    ({}, {"javac": ">=1"}), ({}, {"demo": "https://example.invalid"}),
])
def test_duplicate_or_nonpackage_bounds_are_refused(requires, bounds):
    with pytest.raises(ValueError):
        package_bounds.merge(requires, bounds)


def test_all_environment_readers_use_the_explicit_separate_table(tmp_path, monkeypatch):
    course = support.course(tmp_path)
    marker = course.root / "workspace.toml"
    marker.write_text('course = "demo"\n[requires]\npython = "3.11"\n'
                      '[package-bounds]\ndemo = ">=2,<3"\n[isolation]\nrunner = "host"\n',
                      encoding="utf-8")
    ws = config.load_workspace({"WORKSPACE_DIR": str(course.root)})
    assert "demo" not in ws.requires and ws.effective_requires["demo"] == ">=2,<3"
    assert workspace._requires(course.root)["demo"] == ">=2,<3"
    assert "demo>=2,<3" in envcheck.pip_requirements(ws.effective_requires)
    assert "demo>=2,<3" in image_recipe.dockerfile(ws.effective_requires)
    monkeypatch.setattr(envcheck.metadata, "version", lambda name: "2.4")
    monkeypatch.setattr(envcheck.metadata, "files", lambda name: [])
    assert next(c for c in envcheck.check_workspace(ws) if c.name == "demo").ok
    assert envcheck.requires_problems({"demo": ">=2"})  # raw legacy table keeps prefix grammar


def test_empty_bounds_preserve_legacy_recipe_identity():
    requires = {"python": "3.11", "demo": "1"}
    assert image_recipe.dockerfile(requires) == image_recipe.dockerfile(
        package_bounds.merge(requires, {}))
    assert image_recipe.image_name("demo", requires) == image_recipe.image_name(
        "demo", package_bounds.merge(requires, {}))
