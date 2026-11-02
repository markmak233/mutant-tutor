"""Phase 4's retrieval measurement, on a small made-up corpus and with a fake embedding
model: BM25, vectors, fusion, labels, scores, CLI."""

import hashlib
import json

import pytest

from tutor.llm import Reply
from tutor.materials import cache
from tutor.retrieval.__main__ import main
from tutor.retrieval.bm25 import BM25Index, BM25Retriever, tokenize
from tutor.retrieval.concepts import (
    Concept,
    ConceptRetriever,
    ConceptStore,
    build_lecture,
)
from tutor.retrieval.corpus import Page, load_pages, load_settings
from tutor.retrieval.evaluate import (
    baselines,
    evaluate,
    recall,
    reciprocal_rank,
    within_budget,
)
from tutor.retrieval.hybrid import HybridRetriever, fuse
from tutor.retrieval.labels import Query, load_cross_lecture, parse_coverage, queries_from
from tutor.retrieval.vectors import (
    MAX_BATCH_TEXTS,
    MAX_BATCH_TOKENS,
    EmbeddingStore,
    VectorRetriever,
    _batches,
)

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


# --- vectors and fusion --------------------------------------------------------------------

def fake_embed(texts):
    """A stand-in model: each word switches on one of 64 dimensions, so texts sharing words
    point in similar directions, and the same text always gets the same vector."""
    vectors = []
    for text in texts:
        vector = [0.0] * 64
        for word in text.lower().split():
            vector[int(hashlib.sha256(word.encode()).hexdigest(), 16) % 64] += 1.0
        vectors.append(vector)
    return vectors


class CountingModel:
    """Records every batch it is asked for."""

    def __init__(self):
        self.batches = []

    def __call__(self, texts):
        self.batches.append(list(texts))
        return fake_embed(texts)


def test_vector_search_ranks_by_direction_and_respects_the_scope():
    pages = [page("1", 1, text="joins on keys"), page("2", 1, text="joins on keys and more"),
             page("2", 2, text="sorting values")]
    route = VectorRetriever(pages, fake_embed)
    assert route.search("sorting values")[0].key == ("2", 2)
    assert [p.lecture for p in route.search("joins", {"2"})] == ["2", "2"]
    assert len(route.search("anything")) == 3  # unlike BM25, it ranks every page


def test_vector_length_does_not_count_only_direction():
    pages = [page(number=1, text="joins"), page(number=2, text="joins joins joins"),
             page(number=3, text="other")]
    first, second, _ = VectorRetriever(pages, fake_embed).search("joins")
    assert (first.number, second.number) == (1, 2)  # equal cosine: the earlier page first


def test_batches_keep_within_both_limits_and_lose_nothing():
    many = [f"text {i}" for i in range(MAX_BATCH_TEXTS + 5)]
    assert [len(b) for b in _batches(many)] == [MAX_BATCH_TEXTS, 5]
    big = "x" * 4 * (MAX_BATCH_TOKENS - 10)
    assert [len(b) for b in _batches([big, "y" * 80, "z"])] == [1, 2]


def test_a_text_over_the_limit_still_goes_alone():
    assert [len(b) for b in _batches(["x" * 4 * (MAX_BATCH_TOKENS + 1), "y"])] == [1, 1]


def test_the_store_pays_for_a_text_only_once_across_runs(tmp_path):
    path = tmp_path / "cache" / "vectors.jsonl"
    model = CountingModel()
    store = EmbeddingStore(path, model)
    assert store(["a", "b", "a"]) == fake_embed(["a", "b", "a"])
    assert store(["b"]) == fake_embed(["b"])
    assert model.batches == [["a", "b"]]
    assert store.embedded == 2
    later = CountingModel()
    assert EmbeddingStore(path, later)(["a", "c"]) == fake_embed(["a", "c"])
    assert later.batches == [["c"]]


def test_a_failed_batch_keeps_the_earlier_ones(tmp_path):
    path = tmp_path / "vectors.jsonl"
    calls = []

    def fails_on_second(texts):
        calls.append(texts)
        if len(calls) == 2:
            raise RuntimeError("lost the connection")
        return fake_embed(texts)

    texts = [f"text {i}" for i in range(MAX_BATCH_TEXTS + 1)]
    with pytest.raises(RuntimeError):
        EmbeddingStore(path, fails_on_second)(texts)
    resumed = CountingModel()
    EmbeddingStore(path, resumed)(texts)
    assert resumed.batches == [[texts[-1]]]


def test_a_line_cut_off_mid_write_costs_only_that_text(tmp_path):
    path = tmp_path / "vectors.jsonl"
    EmbeddingStore(path, fake_embed)(["kept"])
    with path.open("a", encoding="utf-8") as f:
        f.write('{"hash": "abc", "vect')  # no newline: the process died here
    model = CountingModel()
    store = EmbeddingStore(path, model)
    assert store(["kept", "new"]) == fake_embed(["kept", "new"])
    assert model.batches == [["new"]]
    # The new line did not join the broken one, so it survives the next load too.
    final = CountingModel()
    EmbeddingStore(path, final)(["kept", "new"])
    assert final.batches == []


def test_fusion_prefers_a_page_high_in_both_rankings():
    a, b, c = page(number=1), page(number=2), page(number=3)
    assert fuse([[a, b, c], [b, c, a]])[0] == b  # second and first beats first and third


def test_fusion_keeps_pages_only_one_route_found_and_each_page_once():
    a, b, c = page(number=1), page(number=2), page(number=3)
    assert fuse([[a, b], [c, a]]) == [a, c, b]


def test_fusion_ties_keep_the_order_pages_were_first_seen():
    a, b = page(number=1), page(number=2)
    assert fuse([[a], [b]]) == [a, b]


def test_hybrid_passes_the_scope_to_every_route():
    routes = [FixedRoute([page(number=1)]), FixedRoute([page(number=2)])]
    assert len(HybridRetriever(routes).search("q", {"3"})) == 2
    assert [route.scopes for route in routes] == [[{"3"}], [{"3"}]]


# --- concept index -------------------------------------------------------------------------

class ScriptedModel:
    """Answers each purpose with a fixed reply, and records what it was asked."""

    def __init__(self, **replies):
        self.replies = replies
        self.asked = []

    def __call__(self, purpose, system, user, schema, max_tokens):
        self.asked.append((purpose, user))
        return json.dumps(self.replies[purpose.replace("-", "_")])


def concept(name, pages, aliases=(), summary="what it teaches"):
    return {"name": name, "aliases": list(aliases), "summary": summary, "pages": pages}


def index_reply(*concepts):
    return {"concept_index": {"concepts": list(concepts)}}


def test_building_sends_every_page_and_keeps_what_the_model_wrote():
    pages = [page(number=1, text="first"), page(number=2, text="second")]
    model = ScriptedModel(**index_reply(concept("Joins", [2, 1, 2], ["join"])))
    concepts, dropped = build_lecture(pages, model)
    assert "## p.1\nfirst" in model.asked[0][1] and "## p.2\nsecond" in model.asked[0][1]
    assert concepts == [Concept(name="Joins", aliases=["join"], summary="what it teaches",
                                pages=[1, 2])]  # sorted, each page once
    assert dropped == []


def test_page_numbers_outside_the_lecture_are_dropped_and_said():
    pages = [page(number=1)]
    model = ScriptedModel(**index_reply(concept("Some", [1, 9]), concept("None", [8])))
    concepts, dropped = build_lecture(pages, model)
    assert [(c.name, c.pages) for c in concepts] == [("Some", [1])]
    assert dropped == ["Some: no page [9]", "None: no page [8]", "None: left with no pages"]


def test_a_stored_index_is_reused_until_the_lecture_changes(tmp_path):
    store = ConceptStore(tmp_path / "concepts")
    pages = [page(number=1, text="first")]
    assert store.load(pages) is None
    store.save(pages, [Concept(name="Joins", summary="s", pages=[1])])
    assert [c.name for c in store.load(pages)] == ["Joins"]
    assert store.load([page(number=1, text="changed")]) is None


def concept_route(tmp_path, picks, pages=None, run=0, indexed=("3", "4")):
    """A route over two lectures: L3 has pages 1-3 and L4 page 1."""
    pages = pages or [page("3", 1), page("3", 2), page("3", 3), page("4", 1)]
    store = ConceptStore(tmp_path / "concepts")
    indexes = {"3": [Concept(name="Joins", aliases=["join"], summary="s", pages=[2, 3]),
                     Concept(name="Sorting", summary="t", pages=[1, 2])],
               "4": [Concept(name="Spark", summary="u", pages=[1])]}
    for lecture in indexed:
        store.save([p for p in pages if p.lecture == lecture], indexes[lecture])
    model = ScriptedModel(concept_pick={"ids": picks})
    return ConceptRetriever(pages, store, model, run=run), model


def test_a_query_gets_the_pages_of_the_picked_topics_in_order_each_once(tmp_path):
    route, model = concept_route(tmp_path, ["3/1", "3/0"])
    assert [p.key for p in route.search("q", {"3"})] == [("3", 1), ("3", 2), ("3", 3)]
    assert "Joins (join)" in model.asked[0][1] and "Request: q" in model.asked[0][1]


def test_the_model_sees_the_index_of_the_scope_only(tmp_path):
    route, model = concept_route(tmp_path, ["3/0"])
    route.search("q", {"3"})
    assert "Spark" not in model.asked[0][1]
    route.search("q")
    assert "Spark" in model.asked[1][1]


def test_an_id_the_model_made_up_finds_nothing(tmp_path):
    route, _ = concept_route(tmp_path, ["9/9"])
    assert route.search("q") == []


def test_a_pick_is_paid_for_once_across_runs_of_the_program(tmp_path):
    route, model = concept_route(tmp_path, ["3/0"])
    first = route.search("q", {"3"})
    route.search("q", {"3"})
    assert len(model.asked) == 1
    again, later = concept_route(tmp_path, ["4/0"])
    assert again.search("q", {"3"}) == first  # the stored pick, not the new reply
    assert later.asked == []


def test_a_repeated_measurement_asks_the_model_again(tmp_path):
    route, _ = concept_route(tmp_path, ["3/0"])
    route.search("q", {"3"})
    repeat, model = concept_route(tmp_path, ["3/1"], run=1)
    assert [p.number for p in repeat.search("q", {"3"})] == [1, 2]
    assert len(model.asked) == 1


def test_searching_a_lecture_without_an_index_is_an_error_not_a_miss(tmp_path):
    route, _ = concept_route(tmp_path, ["3/0"], indexed=("3",))
    assert route.search("q", {"3"})
    with pytest.raises(ValueError, match=r"no concept index for lectures \['4'\]"):
        route.search("q")


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


def test_without_an_embedding_role_only_bm25_runs(workspace, capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "vector" not in out and "hybrid" not in out and "embedding model" not in out


def test_asking_for_a_route_that_cannot_run_is_an_error(workspace, capsys):
    assert main(["--route", "hybrid"]) == 1
    assert "embedding" in capsys.readouterr().err


@pytest.fixture
def with_embedding(workspace, monkeypatch):
    """The workspace with an embedding role, and a fake model behind the real client call."""
    with (workspace / "workspace.toml").open("a", encoding="utf-8") as f:
        f.write('[llm.profiles.embed]\nprovider = "custom"\nbase_url = "http://x"\n'
                'model = "org/model"\n[llm.roles]\nembedding = "embed"\n')
    calls = []

    def embed(config, texts, *, purpose, log_path=None, dimensions=None):
        calls.append((purpose, dimensions, len(texts)))
        return fake_embed(texts)

    monkeypatch.setattr("tutor.retrieval.__main__.llm.embed", embed)
    return calls


def test_the_command_runs_every_route_and_pays_only_once(workspace, with_embedding, capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    for route in ("bm25", "vector", "hybrid"):
        assert f"lecture  {route}" in out and f"course   {route}" in out
    assert "embedding model this run: 0" not in out
    assert all(purpose == "embedding" and size == 1024 for purpose, size, _ in with_embedding)
    assert (workspace / "cache" / "retrieval" / "org_model-1024.jsonl").is_file()
    with_embedding.clear()
    assert main([]) == 0
    assert "embedding model this run: 0" in capsys.readouterr().out
    assert with_embedding == []


def test_a_single_route_can_be_asked_for(workspace, with_embedding, capsys):
    assert main(["--route", "vector"]) == 0
    out = capsys.readouterr().out
    assert "course   vector" in out and "bm25" not in out


def test_bm25_alone_spends_nothing_even_with_a_role(workspace, with_embedding, capsys):
    assert main(["--route", "bm25"]) == 0
    assert with_embedding == []


@pytest.fixture
def with_concept(workspace, monkeypatch):
    """The workspace with a concept role, and a fake model behind the real chat call."""
    with (workspace / "workspace.toml").open("a", encoding="utf-8") as f:
        f.write('[llm.profiles.small]\nprovider = "custom"\nbase_url = "http://x"\n'
                'model = "org/small"\n[llm.roles]\nconcept = "small"\n')
    purposes = []

    def chat(config, messages, *, purpose, **kwargs):
        purposes.append(purpose)
        reply = ({"concepts": [concept("Joins", [1, 2])]} if purpose == "concept-index"
                 else {"ids": ["3/0"]})
        return Reply(json.dumps(reply), 100, 10, 0.1)

    monkeypatch.setattr("tutor.retrieval.concepts.llm.chat", chat)
    return purposes


def test_build_concepts_writes_each_lecture_once_and_says_what_it_used(
        workspace, with_concept, capsys):
    assert main(["--build-concepts"]) == 0
    out = capsys.readouterr().out
    assert "L3: 1 concepts, 2 of 3 pages covered" in out
    assert "model calls 1, tokens in 100, out 10" in out
    assert (workspace / "cache" / "retrieval" / "concepts" / "org_small" / "L3.json").is_file()
    assert main(["--build-concepts", "3"]) == 0
    assert "L3: already indexed" in capsys.readouterr().out
    assert with_concept == ["concept-index"]


def test_build_concepts_refuses_a_lecture_it_does_not_have(workspace, with_concept, capsys):
    assert main(["--build-concepts", "9"]) == 1
    assert "no such lectures" in capsys.readouterr().err
    assert with_concept == []


def test_the_concept_route_joins_the_default_once_every_lecture_is_indexed(
        workspace, with_concept, capsys):
    assert main([]) == 0
    assert "concept" not in capsys.readouterr().out
    assert main(["--build-concepts"]) == 0
    capsys.readouterr()
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "lecture  concept" in out and "concept model calls this run" in out


def test_asking_for_the_concept_route_without_its_role_is_an_error(workspace, capsys):
    assert main(["--route", "concept"]) == 1
    assert "concept" in capsys.readouterr().err


def test_lecture_limits_the_queries_and_the_scope(workspace, capsys):
    assert main(["--lecture", "3"]) == 0
    table = capsys.readouterr().out.split("tokens per query")[0]
    assert "lecture  bm25" in table and "course" not in table
    assert main(["--lecture", "9"]) == 1


def test_cross_searches_the_whole_course_only(workspace, capsys):
    write_cross_lecture(workspace, {"query": "joins and sorting", "slides": {"3": "3.1-3.2"}})
    assert main(["--split", "cross"]) == 0
    out = capsys.readouterr().out
    table = out.split("tokens per query")[0]  # the sizes line below says "whole lecture"
    assert "course  bm25" in table and "lecture" not in table


def test_cross_without_its_file_says_so(workspace, capsys):
    assert main(["--split", "cross"]) == 1
    assert "no cross queries" in capsys.readouterr().err
