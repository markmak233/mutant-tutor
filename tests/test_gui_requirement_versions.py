"""One version cell, two existing TOML tables; fake courses and no model/install calls."""

import html
import tomllib

import pytest
import test_gui_environment as support

from tutor import config, llm
from tutor.gui import courses, environment, tasks

root = support.root
BASE = support.BASE
EXTRA = '''
# Numeric bounds and comments stay in the course's existing format.
[package-bounds] # compatible releases
requests = ">=2,<3"

[safety]
allow_imports = ["subprocess"] # unrelated permission
'''


@pytest.fixture
def versions(root, monkeypatch):
    """Every route uses a synthetic file; fail immediately on credentials/paid work."""
    path = root / "demo-course" / "workspace.toml"
    path.write_text(support.TOML + EXTRA, encoding="utf-8")
    monkeypatch.setattr(config, "load_env", lambda *a, **kw: pytest.fail("GUI loaded .env"))
    monkeypatch.setattr(llm, "chat", lambda *a, **kw: pytest.fail("model called"))
    monkeypatch.setattr(tasks, "start", lambda *a, **kw: pytest.fail("install/task started"))
    return root, path, support._browser(root)


def save(versions, rows, **extra):
    """Keep tool rows present, as the browser form does; Python lives in its own list."""
    data = {"name": [n for n, _ in rows], "version": [v for _, v in rows],
            "key": [f"r{i}" for i in range(len(rows))], **extra}
    return support.post(versions[2], "/c/demo-course/environment/requires", data)


def rows(requests=">=2,<3"):
    return [("java", "17"), ("pyspark", "3.5"), ("requests", requests)]


def read(versions):
    return tomllib.loads(versions[1].read_text(encoding="utf-8"))


def test_existing_bounds_appear_once_in_the_one_table_and_get_writes_nothing(versions):
    _, path, client = versions
    before = path.read_bytes()
    response = client.get("/c/demo-course/environment/", base_url=BASE)
    text = html.unescape(response.get_data(as_text=True))
    table = text.split('id="requires-rows"', 1)[1].split("</tbody>", 1)[0]
    assert table.count('name="name" value="requests"') == 1
    assert 'name="version" value=">=2,<3"' in table
    assert "Package version bounds" not in text and "/doctor/bounds" not in text
    assert "3.5</code> for any 3.5.x" in text and ">=1,<2" in text
    assert path.read_bytes() == before and not path.with_suffix(".toml.bak").exists()


@pytest.mark.parametrize("version, section, expected", [
    ("2.31", "requires", "2.31"),
    (">= 2, < 3", "package-bounds", ">=2,<3"),
])
def test_cell_saves_to_the_matching_table_and_keeps_other_data(
        versions, version, section, expected):
    before = read(versions)
    assert save(versions, rows(version)).status_code == 303
    after = read(versions)
    other = "requires" if section == "package-bounds" else "package-bounds"
    assert after[section]["requests"] == expected and "requests" not in after.get(other, {})
    assert {k: v for k, v in after.items() if k not in {"requires", "package-bounds"}} == {
        k: v for k, v in before.items() if k not in {"requires", "package-bounds"}}
    assert after["requires"]["python"] == "3.11" and after["requires"]["pyspark"] == "3.5"


def test_bound_to_prefix_and_back_is_atomic_with_comments_and_each_previous_backup(versions):
    path = versions[1]
    for version, section in [("2.31", "requires"), (">=2,<3", "package-bounds")]:
        before = path.read_text(encoding="utf-8")
        assert save(versions, rows(version)).status_code == 303
        after = read(versions)
        assert after[section]["requests"] == version
        assert sum("requests" in after.get(t, {}) for t in ("requires", "package-bounds")) == 1
        assert path.with_suffix(".toml.bak").read_text(encoding="utf-8") == before
        text = path.read_text(encoding="utf-8")
        assert '# the course id' in text and '# the venv\'s Python' in text
        assert '# compatible releases' in text and '# unrelated permission' in text


def test_deleting_a_bound_row_removes_the_hidden_declaration_too(versions):
    assert save(versions, rows()[:-1]).status_code == 303
    after = read(versions)
    assert all("requests" not in after.get(t, {}) for t in ("requires", "package-bounds"))
    assert after["requires"]["java"] == "17"


@pytest.mark.parametrize("version", [">=2,<", ">=2rc1", "https://example.invalid/file.whl"])
def test_invalid_comparison_keeps_typed_cells_and_never_writes(versions, version):
    path = versions[1]
    before = path.read_bytes()
    response = save(versions, rows(version))
    text = html.unescape(response.get_data(as_text=True))
    assert response.status_code == 400
    assert f'name="version" value="{version}"' in text
    assert 'name="name" value="requests"' in text
    assert path.read_bytes() == before and not path.with_suffix(".toml.bak").exists()


@pytest.mark.parametrize("name, version", [("python", ">=3"), ("java", ">=17"),
                                         ("hadoop", ">=3"), ("gcc", ">=12")])
def test_toolchain_comparisons_are_refused_by_the_shared_form_rules(name, version):
    table, problems = environment.check_requires([name], [version], "host")
    assert table is None and any(name in p and "version prefix" in p for p in problems)


def test_java_comparison_is_refused_without_losing_the_form(versions):
    before = versions[1].read_bytes()
    response = save(versions, [("java", ">=17"), ("requests", ">=2,<3")])
    text = html.unescape(response.get_data(as_text=True))
    assert response.status_code == 400 and 'name="version" value=">=17"' in text
    assert "version prefix" in text and versions[1].read_bytes() == before


@pytest.mark.parametrize("specs", [("1", ">=2"), (">=1", "<2")])
def test_aliases_cannot_erase_each_other_when_entered_as_separate_rows(versions, specs):
    before = versions[1].read_bytes()
    response = save(versions, [("Demo_Pkg", specs[0]), ("demo-pkg", specs[1])])
    assert response.status_code == 400 and "listed twice" in response.get_data(as_text=True)
    assert versions[1].read_bytes() == before


def test_bound_row_import_consent_is_saved_with_the_versions(versions):
    assert save(versions, rows(), **{"import": "r2", "module:r2": "requests"}).status_code == 303
    assert read(versions)["safety"]["allow_imports"] == ["subprocess", "requests"]
    assert save(versions, rows("2.31"), **{"module:r2": "requests"}).status_code == 303
    assert read(versions)["safety"]["allow_imports"] == ["subprocess"]
    assert read(versions)["requires"]["requests"] == "2.31"


def test_saving_a_language_version_keeps_existing_package_bounds(versions):
    response = support.post(versions[2], "/c/demo-course/environment/languages",
                            {"version:python": "3.12"})
    assert response.status_code == 303
    after = read(versions)
    assert after["requires"]["python"] == "3.12"
    assert after["package-bounds"] == {"requests": ">=2,<3"}


def test_accepting_a_draft_keeps_bounds_and_can_move_one_selected_entry(versions):
    root, _, _ = versions
    course = courses.get_course(root, "demo-course")
    assert environment.accept(course, {"httpx": "0.28"}) == []
    assert read(versions)["package-bounds"] == {"requests": ">=2,<3"}
    course = courses.get_course(root, "demo-course")
    assert environment.accept(course, {"requests": "2.31"}) == []
    assert read(versions)["requires"]["requests"] == "2.31"
    assert "requests" not in read(versions)["package-bounds"]
