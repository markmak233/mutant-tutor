"""coverage.json: reading the two shapes coverage.md had, rendering, merging a new draft,
marking covered points, and the command line. No model is called."""

import json
from pathlib import Path

import pytest

from tutor.materials import coverage_store as cs

KNOWN = {"Lecture_1.1-Intro": "Lecture_1.1-Intro.pdf",
         "Lecture_8.1-Similar_I": "Lecture_8.1-Similar_I.pdf",
         "Lecture_8.2-Similar_II": "Lecture_8.2-Similar_II.pdf"}

HAND_WRITTEN = """# Demo course coverage

What each lecture teaches. Read before choosing the next batch.

Status:
- `ex:<id>`: exercise in a workspace batch

## Week 1

### L1.1 Course introduction
| Slides | Point | Status |
|---|---|---|
| 1.3-1.5 | What big data is | concept |
| 1.6 | Relative frequencies f(wj\\|wi), pairs | gap-code (stripes: inline) |
| 1.7 | Practice: enrolments | dropped (repeat of set logic) |

## Week 8

Both lectures number their slides 8.x: always give the lecture with the slides.

### L8.1 Finding Similar Items I (shingling)
| Slides | Point | Status |
|---|---|---|
| 8.3-8.9 | Near-neighbour problems | concept |
| 8.10-8.12 | Grid index; kNN ring by ring | ex:grid-knn (p3-w8) |
| 8.13-8.16 | Min-hash signatures | ex:minhash-sig, ex:minhash-est (p3-w8) |
| 8.17-8.19 | Project 3 | n/a (graded project) |
| 8.20 | Banding | gap-code [the band count that gives the s-curve] |
| odd row with two cells |

## Labs (references only)
| Lab | Content | Status |
|---|---|---|
| Lab 4 | Secondary sort | ex:reverse-graph (p2-w3) |
"""

DRAFTED = """# Knowledge-point coverage

Status:
- `gap-code [what an exercise could check]`: not covered yet

## Lecture_8.2-Similar_II.pdf

| Slides | Point | Status |
|---|---|---|
| 8.30-8.35 | LSH for min-hash | gap-code [bands and rows] |
| 8.36 | Distance measures | gap-tracing |

Pages no point covers: p.1-2, p.9.
"""


def test_a_hand_written_file_is_read_by_its_lecture_numbers_keeping_every_line():
    store, report = cs.from_markdown(HAND_WRITTEN, KNOWN)

    assert list(store.lectures) == ["Lecture_1.1-Intro", "Lecture_8.1-Similar_I"]
    assert store.course_notes.startswith("What each lecture teaches")
    assert "`ex:<id>`" in store.course_notes
    first = store.lectures["Lecture_1.1-Intro"]
    assert first.title == "L1.1 Course introduction" and first.file == "Lecture_1.1-Intro.pdf"
    assert [p.slides for p in first.points] == ["1.3-1.5", "1.6", "1.7"]
    assert first.points[1].point == "Relative frequencies f(wj|wi), pairs"  # the escaped pipe
    assert (first.points[1].status, first.points[1].note) == ("gap-code", "stripes: inline")
    assert first.points[2].note == "dropped (repeat of set logic)"  # no known status: kept whole
    similar = store.lectures["Lecture_8.1-Similar_I"]
    assert similar.notes.startswith("Both lectures number their slides 8.x")  # the week's note
    assert similar.title == "L8.1 Finding Similar Items I (shingling)"
    covered = similar.points[1]
    assert covered.covered_by == [cs.Covered(mark="ex:grid-knn", batch="p3-w8")]
    assert [c.mark for c in similar.points[2].covered_by] == ["ex:minhash-sig", "ex:minhash-est"]
    assert similar.points[2].covered_by[0].batch == "p3-w8"
    assert (similar.points[3].status, similar.points[3].note) == ("n/a", "graded project")
    assert (similar.points[4].status, similar.points[4].check) == (
        "gap-code", "the band count that gives the s-curve")
    assert "| odd row with two cells |" in similar.notes  # not lost
    assert store.appendix.startswith("## Labs (references only)")  # not a lecture's rows
    assert len(similar.points) == 5
    assert any("1 cell(s) kept as a note" in line for line in report)
    assert any("2 covered row(s)" in line and "Lecture_8.1" in line for line in report)


def test_a_drafted_file_is_read_by_its_file_name_headings_with_the_uncovered_pages():
    store, report = cs.from_markdown(DRAFTED, KNOWN)

    lecture = store.lectures["Lecture_8.2-Similar_II"]
    assert lecture.title == "" and lecture.uncovered == [1, 2, 9]
    assert [(p.status, p.check) for p in lecture.points] == [
        ("gap-code", "bands and rows"), ("gap-tracing", "")]
    assert report == []


def test_ids_come_from_what_a_point_is_not_where_it_stands():
    a = cs.point_id("L1", "1.3-1.5", "What big data is")
    assert a == cs.point_id("L1", "1.3-1.5 ", "What  big data is")  # spacing does not count
    assert a != cs.point_id("L2", "1.3-1.5", "What big data is")
    assert a != cs.point_id("L1", "1.6", "What big data is")


def test_rendering_is_the_files_shape_and_reads_back_to_the_same_data():
    store, _ = cs.from_markdown(HAND_WRITTEN, KNOWN)

    text = cs.render(store)

    assert text.startswith("# Knowledge-point coverage\n\nRendered from coverage.json")
    assert "\n## Lecture_8.1-Similar_I\n\n**L8.1 Finding Similar Items I (shingling)**\n" in text
    assert "| 8.13-8.16 | Min-hash signatures | ex:minhash-sig, ex:minhash-est (p3-w8) |" in text
    assert "| 1.6 | Relative frequencies f(wj\\|wi), pairs | gap-code (stripes: inline) |" in text
    assert "| 8.20 | Banding | gap-code [the band count that gives the s-curve] |" in text
    assert text.rstrip().endswith("| Lab 4 | Secondary sort | ex:reverse-graph (p2-w3) |")
    again, _ = cs.from_markdown(text, KNOWN)
    # The same data back, but for owner_edited: the rendered file names its lectures the
    # drafts' way, and the flag is not written into it (coverage.json keeps it).
    assert [x.model_dump(exclude={"points": {"__all__": {"owner_edited"}}})
            for x in again.lectures.values()] == [
        x.model_dump(exclude={"points": {"__all__": {"owner_edited"}}})
        for x in store.lectures.values()]
    assert cs.render(again) == text  # stable


def test_removed_points_leave_the_table_and_are_listed_below_it():
    store, _ = cs.from_markdown(DRAFTED, KNOWN)
    store.lectures["Lecture_8.2-Similar_II"].points[1].removed = True

    text = cs.render(store)

    assert "| 8.36 |" not in text
    assert ("No longer found in the lecture (confirm on the Coverage page): "
            "Distance measures (8.36).") in text


def test_a_new_draft_keeps_the_owners_points_adds_new_ones_and_marks_vanished_ones():
    store, _ = cs.from_markdown(DRAFTED, KNOWN)
    name = "Lecture_8.2-Similar_II"
    lecture = store.lectures[name]
    lecture.points[0].owner_edited = True
    lecture.points[0].check = "the owner's own check"
    drafted = [cs.Point(id="", slides="8.30-8.35", point="LSH for min-hash", check="model's",
                        first_page=30, last_page=35),
               cs.Point(id="", slides="8.37-8.40", point="Jaccard and cosine", status="concept",
                        first_page=37, last_page=40)]

    counts = cs.merge_draft(name, lecture, drafted, "hash-2", "p: sonnet", [41], today="2026-11-10")

    assert counts == {"kept": 1, "added": 1, "removed": 1}
    by_slides = {p.slides: p for p in lecture.points}
    kept = by_slides["8.30-8.35"]
    assert kept.check == "the owner's own check" and kept.owner_edited  # not overwritten
    assert (kept.first_page, kept.last_page) == (30, 35)  # the pages filled in
    assert by_slides["8.37-8.40"].id == cs.point_id(name, "8.37-8.40", "Jaccard and cosine")
    assert by_slides["8.36"].removed is True
    assert (lecture.source_hash, lecture.model, lecture.drafted) == (
        "hash-2", "p: sonnet", "2026-11-10")
    assert lecture.uncovered == [41]


def test_a_hand_written_files_rows_are_the_owners_and_a_drafts_rows_are_not():
    hand, report = cs.from_markdown(HAND_WRITTEN, KNOWN)
    drafted, _ = cs.from_markdown(DRAFTED, KNOWN)

    assert all(p.owner_edited for lecture in hand.lectures.values() for p in lecture.points)
    assert any("hand-written file" in line for line in report)
    assert not any(p.owner_edited for lecture in drafted.lectures.values()
                   for p in lecture.points)


def _model_lecture() -> tuple[str, cs.Lecture]:
    """A lecture as a draft left it: three model rows, one practised, one edited later."""
    name = "Lecture_8.1-Similar_I"
    mark = cs.Covered(mark="ex:grid-knn", batch="w8")
    rows = [("8.3-8.9", "Near-neighbour problems", {}),
            ("8.10-8.12", "Grid index", {"covered_by": [mark]}),
            ("8.20", "Banding", {"owner_edited": True, "check": "mine"}),
            ("8.21", "Old wording", {})]
    lecture = cs.Lecture(points=[cs.Point(id=cs.point_id(name, s, p), slides=s, point=p, **f)
                                 for s, p, f in rows])
    return name, lecture


def test_an_incremental_draft_marks_vanished_model_rows_and_keeps_the_rest():
    name, lecture = _model_lecture()
    lecture.points[0].removed = True  # marked by an earlier draft, found again now
    drafted = [cs.Point(id="", slides="8.3-8.9", point="Near-neighbour problems"),
               cs.Point(id="", slides="8.10-8.12", point="Grid index, reworded")]

    counts = cs.merge_draft(name, lecture, drafted, "h", "m", [])

    by_slides = {p.point: p for p in lecture.points}
    assert by_slides["Near-neighbour problems"].removed is False  # found again
    assert by_slides["Grid index"].removed is False and by_slides["Grid index"].covered_by
    assert by_slides["Grid index, reworded"].removed is False  # added beside it
    assert by_slides["Banding"].removed is False  # the owner's
    assert by_slides["Old wording"].removed is True  # the owner confirms
    assert counts == {"kept": 3, "added": 1, "removed": 1}


def test_a_whole_redraft_replaces_model_rows_and_moves_the_marks_to_the_new_row():
    name, lecture = _model_lecture()
    drafted = [cs.Point(id="", slides="8.3-8.9", point="Near neighbours, reworded"),
               cs.Point(id="", slides="8.10-8.12", point="Grid index, reworded"),
               cs.Point(id="", slides="8.22", point="Something new")]

    counts = cs.merge_draft(name, lecture, drafted, "h", "m", [], replace=True)

    points = {p.point: p for p in lecture.points}
    assert set(points) == {"Near neighbours, reworded", "Grid index, reworded", "Banding",
                           "Something new"}  # "Old wording" went; no removal to confirm
    assert not any(p.removed for p in lecture.points)
    assert points["Grid index, reworded"].covered_by == [cs.Covered(mark="ex:grid-knn", batch="w8")]
    assert points["Banding"].check == "mine"  # the owner's row stays
    assert counts == {"kept": 1, "added": 3, "removed": 3}


def test_a_whole_redraft_keeps_a_practised_row_with_no_new_row_at_its_slides():
    name, lecture = _model_lecture()
    drafted = [cs.Point(id="", slides="8.10-8.11", point="Grid index, other slides")]

    cs.merge_draft(name, lecture, drafted, "h", "m", [], replace=True)

    points = {p.point: p for p in lecture.points}
    assert "Grid index" in points and points["Grid index"].covered_by  # kept, marks on it
    assert "Old wording" not in points and "Banding" in points


def test_a_batch_marks_the_one_point_its_exercise_covers_or_leaves_it_to_a_proposal():
    store, _ = cs.from_markdown(HAND_WRITTEN, KNOWN)

    assert cs.mark_covered(store, "Lecture_8.1-Similar_I", "8.20", "ex:banding-rows", "w8b")
    # Marked twice: recorded once.
    assert cs.mark_covered(store, "Lecture_8.1-Similar_I", "8.20", "ex:banding-rows", "w8b")
    point = next(p for p in store.lectures["Lecture_8.1-Similar_I"].points if p.slides == "8.20")
    assert point.covered_by == [cs.Covered(mark="ex:banding-rows", batch="w8b")]
    assert cs.status_cell(point) == "ex:banding-rows (w8b)"
    assert not cs.mark_covered(store, "Lecture_8.1-Similar_I", "8.99", "ex:x", "w8b")  # no slides
    assert not cs.mark_covered(store, "Lecture_9.1-Nope", "9.1", "ex:x", "w8b")


def test_status_cells_render_as_the_file_wrote_them():
    def cell(**fields):
        return cs.status_cell(cs.Point(id="x", slides="1", point="p", **fields))

    assert cell(status="gap-code", check="c") == "gap-code [c]"
    assert cell(status="n/a", note="lab") == "n/a (lab)"
    two = cs.Point(id="x", slides="1", point="p", covered_by=[
        cs.Covered(mark="ex:a", batch="b1"), cs.Covered(mark="ex:b", batch="b1"),
        cs.Covered(mark="example:c", batch="")])
    assert cs.status_cell(two) == "ex:a, ex:b (b1), example:c"


def _course(tmp_path: Path) -> Path:
    for file in KNOWN.values():
        (tmp_path / "cache" / "lectures").mkdir(parents=True, exist_ok=True)
        (tmp_path / "cache" / "lectures" / f"{file}.md").write_text(
            "## p.1 · slide 1.1\ntext\n", encoding="utf-8")
    return tmp_path


def test_the_planner_gets_the_rendered_table_when_the_data_exists_else_the_file(tmp_path):
    workspace = _course(tmp_path)
    assert cs.planner_text(workspace) == "(none)"
    (workspace / "coverage.md").write_text(HAND_WRITTEN, encoding="utf-8")
    assert cs.planner_text(workspace) == HAND_WRITTEN  # not migrated: as written

    assert cs.migrate(workspace, out=lambda *a: None) == 0

    store = cs.load(workspace)
    assert store is not None and cs.planner_text(workspace) == cs.render(store)
    assert (workspace / "coverage.md").read_text(encoding="utf-8") == cs.render(store)
    assert (workspace / "coverage.md.bak").read_text(encoding="utf-8") == HAND_WRITTEN
    assert cs.migrate(workspace, out=lambda *a: None) == 1  # once only
    assert cs.missing_lectures(store, list(KNOWN)) == ["Lecture_8.2-Similar_II"]
    # The file's lectures count as drafted from the current text: a draft asks only for
    # the ones it lacks.
    from tutor.materials import cache, coverage
    for name, lecture in store.lectures.items():
        current = coverage.lecture_hash(cache.load(workspace / "cache" / "lectures"
                                                   / f"{KNOWN[name]}.md"))
        assert lecture.source_hash == current


def test_the_rendered_file_is_known_to_be_stale_when_edited_by_hand(tmp_path):
    workspace = _course(tmp_path)
    (workspace / "coverage.md").write_text(DRAFTED, encoding="utf-8")
    cs.migrate(workspace, out=lambda *a: None)
    assert not cs.rendered_is_stale(workspace)

    with (workspace / "coverage.md").open("a", encoding="utf-8") as f:
        f.write("| 8.50 | A row typed by hand | gap-code |\n")

    assert cs.rendered_is_stale(workspace)
    assert "8.50" not in cs.planner_text(workspace)  # the data, not the edited file


def test_the_command_line_migrates_and_renders(tmp_path, monkeypatch, capsys):
    workspace = _course(tmp_path)
    (workspace / "coverage.md").write_text(DRAFTED, encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_DIR", str(workspace))
    (workspace / "workspace.toml").write_text('[course]\ncode = "X"\n', encoding="utf-8")

    assert cs.main(["migrate"]) == 0
    assert "1 lectures, 2 points" in capsys.readouterr().out
    assert json.loads((workspace / "coverage.json").read_text(encoding="utf-8"))["version"] == 1
    (workspace / "coverage.md").unlink()
    assert cs.main(["render"]) == 0
    assert (workspace / "coverage.md").read_text(encoding="utf-8").startswith("# Knowledge-point")


def test_the_saved_data_reads_back_whole(tmp_path):
    store, _ = cs.from_markdown(HAND_WRITTEN, KNOWN)
    cs.save(tmp_path, store)
    assert cs.load(tmp_path) == store
    assert cs.load(tmp_path / "elsewhere") is None


@pytest.mark.parametrize(("cell", "expected"), [
    ("gap-code", {"status": "gap-code", "check": "", "note": ""}),
    ("gap-code [a [b] c]", {"status": "gap-code", "check": "a [b] c", "note": ""}),
    ("gap-code / gap-tracing (week 4)",
     {"status": "gap-code", "check": "", "note": "week 4 / gap-tracing"}),
    ("n/a", {"status": "n/a", "check": "", "note": ""}),
    ("something else", {"status": "concept", "note": "something else"}),
])
def test_status_cells_are_parsed(cell, expected):
    assert cs.parse_status(cell) == expected
