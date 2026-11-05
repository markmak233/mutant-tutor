"""The evaluation report, on a made-up workspace: no real records and no model calls."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from tutor.eval.__main__ import main
from tutor.eval.report import (
    NOT_STARTED,
    PASSED,
    STARTED,
    UNLOGGED,
    Batch,
    collect,
    draft_kills,
    exercise_progress,
    first_failure,
    generation_stats,
    quality_stats,
    render,
    run_results,
    write_report,
)

# Text that stands for an answer: it must never reach a report.
ANSWER = "ANSWER-123.456"
GENERATED = "2026-01-01_generated"
BY_HAND = "2026-01-02_by-hand"


def write_lines(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join((row if isinstance(row, str) else json.dumps(row)) + "\n"
                            for row in rows), encoding="utf-8")


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def add_exercise(root: Path, batch: str, exercise: str, changed: bool = False) -> None:
    """An exercise whose student.py is what was handed out, or has been edited."""
    student = root / "batches" / batch / "exercises" / exercise / "student.py"
    skeleton = root / "batches" / batch / "solutions" / exercise / "skeleton.py"
    student.parent.mkdir(parents=True)
    skeleton.parent.mkdir(parents=True)
    skeleton.write_text("VALUE = None\n", encoding="utf-8")
    student.write_text(f"VALUE = {ANSWER!r}\n" if changed else "VALUE = None\n", encoding="utf-8")


def run(outcome="saved", rounds=0, tokens=100, failure=None, format_error=False, usage=True,
        model="m1"):
    """One line of generation.jsonl."""
    line = {"exercise": "x", "outcome": outcome, "rounds": rounds, "tokens": tokens, "model": model,
            "attempts": [] if outcome == "error" else [
                {"round": 0, "failure": failure, "format_error": format_error}],
            "settings": {}}
    if usage:
        line["usage"] = {"input": 80, "cached": 40, "output": 20}
    return line


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A generated batch holding every state, and a batch written by hand."""
    root = tmp_path / "course"
    root.mkdir()
    (root / "workspace.toml").write_text('course = "demo"\n', encoding="utf-8")
    write_json(root / "batches" / GENERATED / "manifest.json", {
        "exercises": ["solved", "begun", "edited", "untouched", "extra", "dropped"],
        "priority": {"must": ["solved"], "optional": ["extra"], "skip": ["dropped"]}})
    add_exercise(root, GENERATED, "solved", changed=True)
    add_exercise(root, GENERATED, "begun", changed=True)
    add_exercise(root, GENERATED, "edited", changed=True)
    for name in ("untouched", "extra", "dropped"):
        add_exercise(root, GENERATED, name)
    write_lines(root / "batches" / GENERATED / "generation.jsonl", [
        run(rounds=0, tokens=100),
        run(rounds=2, tokens=300, usage=False,
            failure=f"rule 1 (reference passes) FAIL: expected {ANSWER}"),
        run("discarded", rounds=3, tokens=200, model="m2", format_error=True,
            failure=f"format: {ANSWER}"),
        run("error", tokens=0),
    ])
    write_json(root / "batches" / BY_HAND / "manifest.json", {"exercises": ["written"]})
    add_exercise(root, BY_HAND, "written")
    write_lines(root / "attempts" / "attempts.jsonl", [
        {"batch": GENERATED, "exercise": "solved", "result": "failed", "first_error": ANSWER},
        {"batch": GENERATED, "exercise": "solved", "event": "hint", "layer": 1},
        {"batch": GENERATED, "exercise": "solved", "result": "passed"},
        {"batch": GENERATED, "exercise": "begun", "result": "error", "first_error": ANSWER},
    ])
    write_lines(root / "ratings.jsonl", [
        {"batch": GENERATED, "exercise": "solved", "clear": True, "fits": True, "taught": False,
         "targets": True, "remark": ANSWER},
        {"batch": BY_HAND, "exercise": "written", "clear": False, "fits": True, "taught": True,
         "targets": True},
    ])
    return root


def batch_of(root: Path, name: str) -> Batch:
    return next(b for b in collect(root).batches if b.name == name)


def test_each_exercise_gets_the_state_its_records_show(workspace):
    records = collect(workspace)
    progress = exercise_progress(batch_of(workspace, GENERATED), run_results(records.attempts),
                                 {(GENERATED, "solved")})
    by_name = {p.exercise: p for p in progress}

    assert by_name["solved"].status == PASSED
    assert by_name["solved"].runs_to_pass == 2  # the hint request between the runs is not a run
    assert by_name["solved"].rated
    assert by_name["begun"].status == STARTED
    assert by_name["edited"].status == UNLOGGED
    assert by_name["untouched"].status == NOT_STARTED
    assert by_name["untouched"].priority == "must"  # unlisted ones count as must
    assert by_name["extra"].priority == "optional"
    assert by_name["dropped"].priority == "skip"


def test_the_progress_table_adds_up_and_lists_what_is_left(workspace):
    text = render(collect(workspace), "demo")

    # 6 exercises: 1 skipped, and 1 passed, 1 started, 1 changed, 2 not started; 1 rated.
    assert f"| {GENERATED} | 6 | 1 | 1 | 1 | 1 | 2 | 1 |" in text
    assert (f"- {GENERATED}: must: begun (started), edited (changed, no run logged), untouched; "
            "optional: extra") in text
    assert "1 exercises were changed with no run logged (0 of them rated)" in text


def test_a_rated_exercise_is_done_even_if_its_runs_were_never_logged(workspace):
    write_lines(workspace / "ratings.jsonl", [{"batch": GENERATED, "exercise": "edited"}])
    text = render(collect(workspace), "demo")

    assert "edited (changed" not in text.split("### Still to solve")[1].split("##")[0]
    assert "1 exercises were changed with no run logged (1 of them rated)" in text


def test_a_batch_written_by_hand_is_not_read_as_edited(workspace):
    """Its student.py differs from the skeleton from the start, so that says nothing."""
    student = workspace / "batches" / BY_HAND / "exercises" / "written" / "student.py"
    student.write_text('"""The question, which the skeleton does not have."""\n', encoding="utf-8")

    progress = exercise_progress(batch_of(workspace, BY_HAND), {}, set())

    assert [p.status for p in progress] == [NOT_STARTED]
    assert "1 batches were written by hand" in render(collect(workspace), "demo")


def test_generation_rates_leave_out_a_run_that_stopped_on_an_error(workspace):
    stats = generation_stats(batch_of(workspace, GENERATED).runs)

    assert (stats.runs, stats.saved, stats.discarded, stats.errors) == (4, 2, 1, 1)
    assert (stats.validated, stats.first_pass) == (3, 1)
    assert stats.tokens == 600  # failed runs are part of what the saved exercises cost
    assert stats.no_usage == 1
    assert (stats.input, stats.cached) == (240, 120)


def test_generation_is_reported_per_batch_and_per_model(workspace):
    text = render(collect(workspace), "demo")

    assert (f"| {GENERATED} | 4 | 2 | 1 | 1 | 1/3 (33%) | 2/3 (67%) | 300 | 50% |") in text
    assert "| m2 | 1 | 0 | 1 | 0 | 0/1 (0%) | 0/1 (0%) | - | 50% |" in text
    assert "1 generation runs do not split their tokens" in text


def test_a_first_failure_keeps_the_check_name_and_drops_its_detail():
    assert first_failure(run()) is None
    assert first_failure(run("error")) is None
    named = first_failure(run(failure=f"rule safe (static safety) FAIL: {ANSWER}"))
    assert named == "rule safe (static safety)"
    assert first_failure(run(failure=f"something new: {ANSWER}")) == "other"
    assert first_failure(run(failure=f"format: {ANSWER}", format_error=True)).startswith("format")


def test_nothing_that_could_hold_an_answer_reaches_the_report(workspace):
    """A hand-calculation answer can sit in first_error, a failure's detail, a remark about a
    rating or the owner's own student.py."""
    assert ANSWER not in render(collect(workspace), "demo")


def test_first_draft_kills_count_only_the_drafts_that_reached_rule_3(workspace):
    drafts = workspace / "batches" / ".drafts" / GENERATED
    write_lines(drafts / "a" / "round-0" / "report.txt", ["   3 ok    mutants killed: 5/5 killed"])
    write_lines(drafts / "b" / "round-0" / "report.txt", ["   3 FAIL  mutants killed: 3/4 killed"])
    write_lines(drafts / "c" / "round-0" / "report.txt", ["   3 skip  mutants killed: not run"])
    write_lines(drafts / "b" / "round-1" / "report.txt", ["   3 ok    mutants killed: 4/4 killed"])
    write_lines(drafts / "replaced" / "a" / "round-0" / "report.txt",
                ["   3 ok    mutants killed: 9/9 killed"])

    kills = draft_kills(workspace, [batch_of(workspace, GENERATED)])

    assert (kills.drafts, kills.reached, kills.killed, kills.mutants) == (3, 2, 8, 9)
    assert "8/9 (89%), over 2 drafts" in render(collect(workspace), "demo")


def test_quality_counts_the_judge_held_out_checks_and_ratings(workspace):
    solutions = workspace / "batches" / GENERATED / "solutions"
    write_json(solutions / "solved" / "quality.json", {"serious": ["clear"]})
    write_json(solutions / "begun" / "quality.json", {"serious": []})
    write_json(solutions / "solved" / "heldout.json", {"flagged": 1})
    write_json(solutions / "begun" / "heldout.json", {"flagged": 0})
    records = collect(workspace)

    stats = quality_stats([batch_of(workspace, GENERATED)], records.ratings)

    assert (stats.judged, stats.serious, stats.heldout, stats.flagged) == (2, 1, 2, 1)
    assert stats.rated == 1
    assert stats.points == {"clear": 1, "fits": 1, "targets": 1}
    text = render(records, "demo")
    assert "| All by hand | - | - | 1 | 0/1 (0%) | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) |" in text


def test_a_batch_written_by_hand_is_not_counted_as_generated(workspace):
    text = render(collect(workspace), "demo")

    assert f"| {BY_HAND} | 1 | 0 | 0 | 0 | 0 | 1 | 1 |" in text  # it is still to be solved
    assert f"| {BY_HAND} | 0 |" not in text.split("## Generation")[1].split("## Checks")[0]


def test_trial_folders_are_not_batches(workspace):
    write_json(workspace / "batches" / ".experiments" / "trial" / "manifest.json",
               {"exercises": ["trial-exercise"]})
    write_lines(workspace / "batches" / ".experiments" / "trial" / "generation.jsonl", [run()])

    assert [b.name for b in collect(workspace).batches] == [GENERATED, BY_HAND]


def test_damaged_records_are_reported_and_do_not_stop_the_report(workspace):
    with (workspace / "attempts" / "attempts.jsonl").open("a", encoding="utf-8") as f:
        f.write("not json\n[1, 2]\n")
    broken = workspace / "batches" / "2026-01-03_broken"
    broken.mkdir()
    (broken / "manifest.json").write_text("{", encoding="utf-8")

    text = render(collect(workspace), "demo")

    assert "attempts/attempts.jsonl: 2 unreadable line(s) left out." in text
    assert "2026-01-03_broken: no readable manifest.json" in text


def test_a_test_run_of_an_unknown_exercise_is_reported(workspace):
    write_lines(workspace / "attempts" / "attempts.jsonl",
                [{"batch": "gone", "exercise": "x", "result": "passed"}])

    assert "1 test runs name an exercise no batch lists." in render(collect(workspace), "demo")


def test_model_calls_add_up_by_purpose_and_model(workspace):
    write_lines(workspace / "logs" / "llm_calls.jsonl", [
        {"purpose": "judge", "model": "m1", "input_tokens": 100, "cached_tokens": 25,
         "output_tokens": 10},
        {"purpose": "judge", "model": "m1", "input_tokens": 100, "cached_tokens": None,
         "output_tokens": 10},
    ])

    assert "| judge | m1 | 2 | 200 | 12% | 20 |" in render(collect(workspace), "demo")


def test_an_empty_workspace_still_gets_a_report(tmp_path):
    text = render(collect(tmp_path), "demo")

    assert "No batch here was generated" in text
    assert "## What these numbers cannot show" in text


def test_write_report_leaves_only_the_report(workspace):
    path = write_report(workspace, "demo", datetime(2026, 1, 5, 9, 30))

    assert path == workspace / "reports" / "report.md"
    assert "Built 2026-01-05 09:30" in path.read_text(encoding="utf-8")
    assert [p.name for p in path.parent.iterdir()] == ["report.md"]


def test_the_command_writes_each_named_workspace(workspace, capsys):
    assert main(["report", str(workspace)]) == 0

    assert (workspace / "reports" / "report.md").is_file()
    assert "report.md" in capsys.readouterr().out


def test_the_command_defaults_to_the_workspace_in_the_environment(workspace, monkeypatch):
    monkeypatch.chdir(workspace)  # away from the repo's .env
    monkeypatch.setenv("WORKSPACE_DIR", str(workspace))

    assert main(["report"]) == 0
    assert (workspace / "reports" / "report.md").is_file()


def test_the_command_refuses_a_folder_that_is_not_a_workspace(tmp_path, capsys):
    assert main(["report", str(tmp_path)]) == 2
    assert "eval:" in capsys.readouterr().err
    assert not (tmp_path / "reports").exists()
