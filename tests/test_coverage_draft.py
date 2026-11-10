"""Drafting coverage.md from the lectures (tutor.materials.coverage): the model is a stand-in,
so these test what the program makes of its answers. Lecture text here is made up."""

import json
from pathlib import Path

import pytest

from tutor.llm import Reply
from tutor.materials import cache
from tutor.materials import coverage as cov

PAGES = {1: ("1.1", "Course admin"), 2: ("1.2", "Word count | map and reduce"),
         3: ("1.3", "Word count with a combiner"), 4: (None, "Back matter"),
         5: ("1.5", "History of the field")}


def entries(pages=PAGES) -> list[cache.Entry]:
    return [cache.Entry(n, label, "text", f"h{n}", body) for n, (label, body) in pages.items()]


def make_lecture(workspace: Path, name: str, pages=PAGES) -> Path:
    path = workspace / "cache" / "lectures" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cache.render(name, entries(pages)), encoding="utf-8")
    return path


def by_number(pages=PAGES) -> dict[int, cache.Entry]:
    return {e.number: e for e in entries(pages)}


def point(**kw) -> cov.Point:
    return cov.Point(**{"point": "Word count", "first_page": 2, "last_page": 3,
                        "status": "gap-code", "check": "the counts with a combiner", **kw})


def test_pages_become_slide_footers_and_gap_code_says_what_to_check():
    s = cov.section("L1.pdf", [point(), point(point="Admin", first_page=1, last_page=1,
                                              status="n/a", check="ignored")], by_number())
    assert s.rows[0] == cov.Row("1.2-1.3", "Word count", "gap-code [the counts with a combiner]",
                                2, 3)  # the pages stay on the row, for coverage.json
    assert s.rows[1] == cov.Row("1.1", "Admin", "n/a", 1, 1)  # check only for gap-code
    assert s.uncovered == [4, 5] and s.gap_code == 1


def test_a_page_with_no_footer_is_named_by_its_number():
    s = cov.section("L1.pdf", [point(first_page=3, last_page=4)], by_number())
    assert s.rows[0].slides == "1.3-p.4"


@pytest.mark.parametrize("bad, reason", [
    (point(last_page=9), "no page 9"),
    (point(first_page=3, last_page=2), "run backwards"),
])
def test_a_point_whose_pages_are_not_in_the_lecture_is_dropped(bad, reason):
    s = cov.section("L1.pdf", [bad], by_number())
    assert s.rows == [] and reason in s.dropped[0]


def test_a_cell_cannot_break_the_table():
    s = cov.section("L1.pdf", [point(point="map | reduce\nand more")], by_number())
    assert s.rows[0].point == "map \\| reduce and more"


class FakeModel:
    def __init__(self, answer):
        self.answer = answer
        self.calls: list[dict] = []

    def __call__(self, config, messages, **kw):
        self.calls.append({"messages": messages, **kw})
        return Reply(json.dumps(self.answer), 900, 120, 0.1, "stop")


ANSWER = {"points": [
    {"point": "Admin", "first_page": 1, "last_page": 1, "status": "n/a", "check": ""},
    {"point": "Word count", "first_page": 2, "last_page": 3, "status": "gap-code",
     "check": "the counts with a combiner"},
    {"point": "History", "first_page": 5, "last_page": 5, "status": "concept", "check": ""},
]}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    (tmp_path / "workspace.toml").write_text(
        'course = "demo"\n[llm.profiles.local]\nprovider = "lmstudio"\nmodel = "m"\n'
        '[llm.roles]\nplan = "local"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    make_lecture(tmp_path, "L1.pdf")
    make_lecture(tmp_path, "L2.pdf")
    return tmp_path


def test_each_lecture_is_asked_once_and_the_draft_never_touches_coverage(workspace,
                                                                          monkeypatch):
    (workspace / "coverage.md").write_text("# Mine\n", encoding="utf-8")
    fake = FakeModel(ANSWER)
    monkeypatch.setattr(cov, "chat", fake)

    assert cov.main([]) == 0
    assert len(fake.calls) == 2
    assert fake.calls[0]["messages"][0] == {"role": "system", "content": cov.PROMPT}
    assert fake.calls[0]["thinking"] is False
    draft = (workspace / cov.DRAFT_FILE).read_text(encoding="utf-8")
    assert "## L1.pdf" in draft and "| 1.2-1.3 | Word count | gap-code [the counts with a" in draft
    assert (workspace / "coverage.md").read_text(encoding="utf-8") == "# Mine\n"

    assert cov.main([]) == 0 and len(fake.calls) == 2  # nothing changed: nobody asked
    make_lecture(workspace, "L3.pdf")
    assert cov.main([]) == 0 and len(fake.calls) == 3  # a new lecture: only it is asked


def test_the_final_file_has_the_kept_header_and_each_lecture_its_heading(workspace,
                                                                         monkeypatch):
    monkeypatch.setattr(cov, "chat", FakeModel(ANSWER))
    cov.main([])
    result = cov.draft(workspace)

    make_lecture(workspace, "L3.pdf")  # not drafted yet
    result = cov.draft(workspace)
    final = cov.render(result, today="2026-11-06", final=True)
    assert final.startswith("# Knowledge-point coverage\n")
    assert "writes this file again" not in final
    assert "Not drafted yet" not in final  # it would go stale in a kept file
    assert "Not drafted yet: L3.pdf." in cov.render(result)
    assert cov.has_section(final, "L1.pdf") and cov.has_section(final, "L2.pdf")
    assert not cov.has_section("### L1.1 Course introduction\n", "L1.pdf")
