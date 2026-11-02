"""Phase 4's retrieval measurement, on a small made-up corpus: BM25, labels, scores, CLI."""

import json

import pytest

from tutor.materials import cache
from tutor.retrieval.__main__ import main
from tutor.retrieval.bm25 import BM25Index, BM25Retriever, tokenize
from tutor.retrieval.corpus import Page, load_pages, load_settings
from tutor.retrieval.evaluate import (
    baselines,
    evaluate,
    recall,
    reciprocal_rank,
    within_budget,
)
from tutor.retrieval.labels import Query, load_cross_lecture, parse_coverage, queries_from

COVERAGE = """\
# Coverage

## Week 3

### L3 Joins
| Slides | Point | Status |
|---|---|---|
| 3.2-3.3 | Reduce-side joins | gap-code |
| 3.5, 3.7 | Broadcast joins | ex:broadcast (batch) |
| 3.1 | Course admin | n/a |
| 3.4 | An old point | dropped |
| 3.99 | A point on a slide nobody has | concept |

### L3.2 Sorting
| Slides | Point | Status |
|---|---|---|
| 3.2 | Secondary sort | gap-code |

## Labs

### Lab tables are not labels
| Slides | Point | Status |
|---|---|---|
| 9.1 | Lab point | ex:lab |
"""


def page(lecture="3", number=1, slide=None, text="word"):
    return Page(lecture, number, slide, text)


def write_lecture(workspace, name, bodies, read="text"):
    """A cached lecture: bodies[0] on page 1 with footer x.1, and so on."""
    entries = [cache.Entry(i, f"{name}.{i}", read, f"hash{i}", body)
               for i, body in enumerate(bodies, 1)]
    path = workspace / "cache" / "lectures" / f"Lecture_{name}-Topic.pdf.md"
    cache.save(path, cache.render(path.name, entries))


# --- tokenizer and BM25 -------------------------------------------------------------------

def test_tokenize_lowercases_words_and_keeps_an_api_name_whole():
    assert tokenize("Map, then reduce.") == ["map", "then", "reduce"]
    assert tokenize("aggregateByKey(zero)") == ["aggregatebykey", "zero"]


def test_a_rare_term_outweighs_a_common_one():
    docs = [["common", "rare"], ["common", "other"], ["common", "more"], ["common", "again"]]
    scores = BM25Index(docs).scores(["common", "rare"])
    assert max(scores, key=scores.get) == 0


def test_repeats_add_less_and_less():
    single, double, triple = (BM25Index([["t"] * n, ["x"] * n, ["y"] * n], b=0).scores(["t"])[0]
                              for n in (1, 2, 3))
    assert double > single
    assert triple - double < double - single


def test_a_shorter_page_with_the_same_mentions_scores_higher():
    scores = BM25Index([["t", "a"], ["t", "a", "b", "c", "d", "e", "f", "g"], ["z"]]).scores(["t"])
    assert scores[0] > scores[1]


def test_a_query_term_repeated_counts_once():
    index = BM25Index([["t"], ["x"], ["y"]])
    assert index.scores(["t", "t"]) == index.scores(["t"])


def test_an_exact_api_name_finds_its_page():
    pages = [page(number=1, text="Reduce the keys by grouping them"),
             page(number=2, text="Use aggregateByKey with a zero value"),
             page(number=3, text="A page about something else")]
    assert BM25Retriever(pages).search("aggregateByKey")[0].number == 2


def test_the_scope_filters_results_and_pages_sharing_no_term_are_left_out():
    pages = [page("1", 1, text="joins here"), page("2", 1, text="joins there"),
             page("2", 2, text="unrelated")]
    route = BM25Retriever(pages)
    assert [p.lecture for p in route.search("joins", {"2"})] == ["2"]
    assert len(route.search("joins")) == 2
    assert route.search("nothing matches this") == []


def test_ties_keep_page_order_so_a_run_repeats():
    pages = [page(number=n, text="same words") for n in (1, 2, 3)]
    assert [p.number for p in BM25Retriever(pages).search("same")] == [1, 2, 3]


# --- labels --------------------------------------------------------------------------------

def test_rows_come_only_from_lecture_headings():
    rows = parse_coverage(COVERAGE)
    assert [(r.lecture, r.point) for r in rows if r.point != "An old point"][:2] == [
        ("3", "Reduce-side joins"), ("3", "Broadcast joins")]
    assert {r.lecture for r in rows} == {"3", "3.2"}  # L3.2 is not read as L3; labs are skipped
    assert "Lab point" not in {r.point for r in rows}


def lecture_three():
    # Page 3 has no footer: it counts when it falls between two labelled pages.
    return [page("3", 1, "3.1"), page("3", 2, "3.2"), page("3", 3, None), page("3", 4, "3.3"),
            page("3", 5, "3.5"), page("3", 6, "3.6"), page("3", 7, "3.7")]


def test_a_range_includes_pages_without_a_footer_and_commas_add_ranges():
    queries, _ = queries_from(parse_coverage(COVERAGE), lecture_three())
    by_text = {q.text: sorted(n for _, n in q.relevant) for q in queries}
    assert by_text["Reduce-side joins"] == [2, 3, 4]
    assert by_text["Broadcast joins"] == [5, 7]


def test_admin_and_dropped_rows_are_not_queries():
    queries, _ = queries_from(parse_coverage(COVERAGE), lecture_three())
    assert not {"Course admin", "An old point"} & {q.text for q in queries}


def test_slides_compare_as_numbers_not_text():
    pages = [page("3", 1, "3.9"), page("3", 2, "3.10"), page("3", 3, "3.11")]
    rows = parse_coverage("### L3 T\n| 3.9-3.10 | Point | gap-code |\n")
    queries, _ = queries_from(rows, pages)
    assert sorted(n for _, n in queries[0].relevant) == [1, 2]


def test_a_footer_that_is_not_a_slide_number_is_ignored():
    pages = [page("3", 1, "2026"), page("3", 2, "3.2")]
    rows = parse_coverage("### L3 T\n| 3.2 | Point | gap-code |\n")
    assert queries_from(rows, pages)[1] == []


def test_rows_with_no_page_are_reported_not_dropped_silently():
    _, problems = queries_from(parse_coverage(COVERAGE), lecture_three())
    assert any("3.99" in p for p in problems)
    assert any("L3.2" in p for p in problems)  # its lecture is not in the corpus


def many_rows(lecture="3", count=30):
    return parse_coverage(f"### L{lecture} T\n" + "".join(
        f"| 3.1 | Point {i} | gap-code |\n" for i in range(count)))


def test_the_split_keeps_about_a_third_for_dev():
    queries, _ = queries_from(many_rows(count=300), [page("3", 1, "3.1")])
    assert 70 < sum(q.split == "dev" for q in queries) < 130


def test_adding_rows_never_moves_a_query_between_splits():
    pages = [page("3", 1, "3.1")]
    before, _ = queries_from(many_rows(count=30), pages)
    after, _ = queries_from(list(reversed(many_rows(count=60))), pages)
    splits = {q.id: q.split for q in after}
    assert all(splits[q.id] == q.split for q in before)


def write_cross_lecture(workspace, *rows):
    path = workspace / "retrieval" / "cross-lecture.jsonl"
    path.parent.mkdir(exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_cross_lecture_queries_span_lectures_and_report_what_is_missing(tmp_path):
    pages = [page("3", 2, "3.2"), page("3", 3, None), page("3", 4, "3.3"), page("4", 1, "4.1")]
    write_cross_lecture(
        tmp_path,
        {"query": "spans two", "slides": {"3": "3.2-3.3", "4": "4.1"}},
        {"query": "bad slide", "slides": {"3": "3.2", "4": "4.9"}},
    )
    queries, problems = load_cross_lecture(tmp_path, pages)
    assert [q.text for q in queries] == ["spans two"]
    assert queries[0].relevant == {("3", 2), ("3", 3), ("3", 4), ("4", 1)}
    assert queries[0].split == "cross"
    assert queries[0].lectures == {"3", "4"}
    assert problems == ["bad slide: no page has L4 4.9"]


def test_no_cross_lecture_file_means_no_such_queries(tmp_path):
    assert load_cross_lecture(tmp_path, [page()]) == ([], [])


# --- scores --------------------------------------------------------------------------------

def query(numbers, lecture="3", text="q"):
    return Query("id", text, frozenset((lecture, n) for n in numbers), "dev")


def test_recall_is_the_share_of_relevant_pages_found():
    assert recall([("3", 1), ("3", 9)], {("3", 1), ("3", 2)}) == 0.5


def test_reciprocal_rank_is_about_the_first_relevant_page():
    relevant = {("3", 2), ("3", 3)}
    assert reciprocal_rank([("3", 9), ("3", 3), ("3", 2)], relevant) == 0.5
    assert reciprocal_rank([("3", 9)], relevant) == 0.0


def test_the_budget_stops_at_the_first_page_that_does_not_fit():
    pages = [page(number=1, text="x" * 40), page(number=2, text="x" * 400),
             page(number=3, text="x" * 4)]  # 10, 100 and 1 tokens
    assert [p.number for p in within_budget(pages, 50)] == [1]  # page 3 is not reached
    assert [p.number for p in within_budget(pages, 111)] == [1, 2, 3]
    assert within_budget(pages, 5) == []


class FixedRoute:
    """A route that returns the same pages, to test the scoring on its own."""

    name = "fixed"

    def __init__(self, pages):
        self.pages = pages
        self.scopes = []

    def search(self, query, lectures=None):
        self.scopes.append(lectures)
        return self.pages


def test_evaluate_averages_over_queries_and_ranks():
    pages = [page(number=n, text="x" * 40) for n in range(1, 12)]  # 10 tokens each
    result = evaluate(FixedRoute(pages), [query([1]), query([11])], "course")
    assert result.queries == 2
    assert result.recall_at[3] == 0.5  # page 11 is past every k
    assert result.recall_at[10] == 0.5
    assert result.mrr == pytest.approx((1 + 1 / 11) / 2)
    assert result.recall_within[500] == 1.0  # all 11 pages fit


def test_the_scope_decides_what_the_route_may_search():
    route = FixedRoute([page()])
    evaluate(route, [query([1], lecture="3")], "lecture")
    evaluate(route, [query([1], lecture="3")], "course")
    assert route.scopes == [{"3"}, None]


def test_baselines_are_the_whole_lecture_and_the_labelled_pages():
    pages = [page(number=1, text="x" * 40), page(number=2, text="x" * 400),
             page("4", 1, text="x" * 4000)]
    sizes = baselines(pages, [query([1])])
    assert sizes == {"whole lecture": 110, "labelled pages": 10}


# --- corpus and command line ---------------------------------------------------------------

def test_load_pages_is_in_course_order_and_skips_unread_pages(tmp_path):
    write_lecture(tmp_path, "10", ["ten"])
    write_lecture(tmp_path, "2.1", ["two one"])
    write_lecture(tmp_path, "9", ["half read"], read="partial")
    assert [(p.lecture, p.number, p.slide) for p in load_pages(tmp_path)] == [
        ("2.1", 1, "2.1.1"), ("10", 1, "10.1")]


def test_exclude_leaves_a_lecture_out_and_a_typo_is_an_error(tmp_path):
    write_lecture(tmp_path, "5", ["five"])
    write_lecture(tmp_path, "5.2", ["copy"])
    assert {p.lecture for p in load_pages(tmp_path, ["5.2"])} == {"5"}
    with pytest.raises(ValueError, match="52"):
        load_pages(tmp_path, ["52"])


def test_settings_come_from_the_workspace_and_reject_a_typo(tmp_path):
    assert load_settings(tmp_path).exclude_lectures == []
    (tmp_path / "workspace.toml").write_text('[retrieval]\nexclude_lectures = ["5.2"]\n',
                                             encoding="utf-8")
    assert load_settings(tmp_path).exclude_lectures == ["5.2"]
    (tmp_path / "workspace.toml").write_text('[retrieval]\nexclude_lecture = ["5.2"]\n',
                                             encoding="utf-8")
    with pytest.raises(ValueError, match="exclude_lecture"):
        load_settings(tmp_path)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    (tmp_path / "workspace.toml").write_text('course = "demo"\n', encoding="utf-8")
    write_lecture(tmp_path, "3", ["joins on a shared key", "sorting values", "other content"])
    # Enough rows that some land in each split (the hash decides, and it never changes).
    rows = "".join(f"| 3.{i % 3 + 1} | {text} | gap-code |\n" for i, text in enumerate(
        ("joins", "sorting", "content", "shared key", "values", "other", "words", "pages")))
    (tmp_path / "coverage.md").write_text(f"### L3 Topic\n{rows}| 3.9 | Missing | concept |\n",
                                          encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr("tutor.retrieval.__main__.load_env", lambda: None)
    return tmp_path


def test_the_command_prints_both_scopes_and_what_it_could_not_measure(workspace, capsys):
    assert main([]) == 0
    out = capsys.readouterr()
    assert "lecture  bm25" in out.out and "course   bm25" in out.out
    assert "tokens per query: whole lecture" in out.out
    assert "not measured: L3 3.9" in out.err


def test_the_command_reports_a_missing_coverage_file(workspace, capsys):
    (workspace / "coverage.md").unlink()
    assert main([]) == 1
    assert "coverage.md" in capsys.readouterr().err


def test_cross_searches_the_whole_course_only(workspace, capsys):
    write_cross_lecture(workspace, {"query": "joins and sorting", "slides": {"3": "3.1-3.2"}})
    assert main(["--split", "cross"]) == 0
    out = capsys.readouterr().out
    table = out.split("tokens per query")[0]  # the sizes line below says "whole lecture"
    assert "course  bm25" in table and "lecture" not in table


def test_cross_without_its_file_says_so(workspace, capsys):
    assert main(["--split", "cross"]) == 1
    assert "no cross queries" in capsys.readouterr().err
