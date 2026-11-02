"""Phase 4's retrieval measurement, on a small made-up corpus and with a fake embedding
model: BM25, vectors, fusion, labels, scores, CLI."""

import hashlib
import json

import pytest

from tutor.llm import LLMConfig, Reply
from tutor.materials import cache
from tutor.retrieval.__main__ import main
from tutor.retrieval.agent import (
    MAX_READ,
    MAX_STEPS,
    SEARCH_RESULTS,
    AgentRetriever,
    tool_settings,
)
from tutor.retrieval.asker import ModelAsker, first_run_cost, settings_folder
from tutor.retrieval.bm25 import BM25Index, BM25Retriever, tokenize
from tutor.retrieval.concepts import (
    Concept,
    ConceptRetriever,
    ConceptStore,
    build_lecture,
)
from tutor.retrieval.corpus import Page, load_pages, load_settings
from tutor.retrieval.evaluate import (
    ADOPT_MARGIN,
    baselines,
    compare,
    evaluate,
    recall,
    reciprocal_rank,
    within_budget,
)
from tutor.retrieval.hybrid import HybridRetriever, NeighbourRetriever, fuse
from tutor.retrieval.labels import (
    Query,
    freeze,
    load_cross_lecture,
    load_frozen,
    parse_coverage,
    queries_from,
)
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


# --- agentic search ------------------------------------------------------------------------

class ActionQueue:
    """A model that replies with the given actions in turn, and records what it was shown."""

    def __init__(self, *actions):
        self.actions = list(actions)
        self.prompts = []

    def __call__(self, purpose, system, user, schema, max_tokens):
        self.prompts.append(user)
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action if isinstance(action, str) else json.dumps(action)


def search_for(words, lecture=None):
    return {"action": "search", "words": words, "lecture": lecture}


def read_pages(*pages):
    return {"action": "read", "pages": list(pages)}


def finish_with(*pages):
    return {"action": "finish", "pages": list(pages)}


AGENT_PAGES = [page("3", 1, "3.1", "3.1\nJoins on a key\nReduce side joins"),
               page("3", 2, "3.2", "Sorting values"),
               page("4", 1, None, "# Joins in Spark\nbroadcast")]


def fresh_state(query="q", scope=("3", "4")):
    return {"query": query, "scope": list(scope), "log": [], "steps": 0, "action": None,
            "read": [], "seen": [], "trace": [], "failed": 0}


def drive(route, state):
    """The loop LangGraph runs, by hand, so these tests also run where it is not installed."""
    while True:
        state = {**state, **route.decide(state)}
        if route.next_node(state) == "end":
            return state
        state = {**state, **route.act(state)}


def agent(tmp_path, *actions, pages=AGENT_PAGES, run=0):
    model = ActionQueue(*actions)
    return AgentRetriever(pages, model, tmp_path / "agent", run=run), model


def test_a_search_shows_id_slide_and_first_real_line_never_the_text(tmp_path):
    route, model = agent(tmp_path, search_for("joins"), finish_with())
    drive(route, fresh_state())
    shown = model.prompts[1]
    assert "3:1 | 3.1 | Joins on a key" in shown  # the footer line is skipped
    assert "4:1 | - | Joins in Spark" in shown  # no footer, and the heading marks are gone
    assert "Reduce side joins" not in shown


def test_a_search_is_limited_to_the_scope_and_to_its_lecture(tmp_path):
    route, model = agent(tmp_path, search_for("joins"), search_for("joins", "4"),
                         search_for("joins", "3"), finish_with())
    drive(route, fresh_state(scope=["3"]))
    assert "4:1" not in model.prompts[1]
    assert "you may only search 3" in model.prompts[2]
    assert "3:1" in model.prompts[3]


def test_a_search_without_words_reaches_no_search_route(tmp_path):
    other = FixedRoute([AGENT_PAGES[1]])
    model = ActionQueue(search_for("  "), finish_with())
    route = AgentRetriever(AGENT_PAGES, model, tmp_path / "agent", search_route=other)
    state = drive(route, fresh_state())
    assert other.scopes == [] and state["seen"] == []
    assert "it needs `words`" in model.prompts[1]


def test_a_search_says_when_nothing_matches(tmp_path):
    route, model = agent(tmp_path, search_for("nothing here"), finish_with())
    drive(route, fresh_state())
    assert "no page has these words" in model.prompts[1]


def test_a_search_shows_a_limited_number_of_pages(tmp_path):
    pages = [page(number=n, text="same words") for n in range(1, SEARCH_RESULTS + 5)]
    route, model = agent(tmp_path, search_for("same"), finish_with(), pages=pages)
    drive(route, fresh_state(scope=["3"]))
    assert model.prompts[1].count("| - |") == SEARCH_RESULTS


def test_read_returns_the_text_of_known_pages_within_the_limit(tmp_path):
    pages = [page(number=n, text=f"text of {n}") for n in range(1, MAX_READ + 3)]
    ids = [f"3:{n}" for n in range(1, MAX_READ + 3)]
    route, model = agent(tmp_path, read_pages(*ids), finish_with(), pages=pages)
    state = drive(route, fresh_state(scope=["3"]))
    assert f"text of {MAX_READ}" in model.prompts[1]
    assert f"text of {MAX_READ + 1}" not in model.prompts[1]
    assert state["read"] == ids[:MAX_READ]


def test_read_refuses_a_made_up_or_out_of_scope_page_and_says_so(tmp_path):
    route, model = agent(tmp_path, read_pages("3:2", "4:1", "9:9"), finish_with())
    state = drive(route, fresh_state(scope=["3"]))
    assert "Sorting values" in model.prompts[1] and "broadcast" not in model.prompts[1]
    assert "No such pages here: 4:1, 9:9" in model.prompts[1]
    assert state["read"] == ["3:2"]


def test_finish_keeps_the_models_order_and_only_real_pages_each_once(tmp_path):
    route, _ = agent(tmp_path, finish_with("3:2", "9:9", "3:1", "3:2", "4:1"))
    state = drive(route, fresh_state(scope=["3"]))
    assert route.finish_pages(state) == (["3:2", "3:1"], True)


def test_out_of_turns_returns_the_pages_read_and_warns_on_the_last_turn(tmp_path):
    actions = [read_pages("3:1")] + [search_for(f"try {n}") for n in range(MAX_STEPS)]
    route, model = agent(tmp_path, *actions)
    state = drive(route, fresh_state())
    assert state["steps"] == MAX_STEPS
    assert route.finish_pages(state) == (["3:1"], False)
    assert len(model.prompts) == MAX_STEPS
    assert "finish now" in model.prompts[-1] and "finish now" not in model.prompts[-2]


def test_an_unreadable_reply_costs_the_turn_and_the_model_is_told(tmp_path):
    route, model = agent(tmp_path, "not json", {"action": "dance"},
                         ValueError("agent-step: the reply was cut off"), finish_with("3:1"))
    state = drive(route, fresh_state())
    assert state["steps"] == 4
    assert model.prompts[3].count("could not be read as an action") == 3
    assert route.finish_pages(state) == (["3:1"], True)


def test_the_prompt_starts_the_same_each_turn_so_a_cache_can_reuse_it(tmp_path):
    route, model = agent(tmp_path, search_for("joins"), finish_with())
    drive(route, fresh_state())
    first, second = model.prompts
    assert second.startswith(first[:first.index("Action 1 of")].rstrip())


def test_the_route_runs_the_loop_through_langgraph_and_returns_pages(tmp_path):
    pytest.importorskip("langgraph")
    route, model = agent(tmp_path, search_for("joins"), read_pages("3:1"),
                         finish_with("3:1", "4:1"))
    assert [p.key for p in route.search("q")] == [("3", 1), ("4", 1)]
    assert route.used[0]["finished"] and route.used[0]["steps"] == 3
    assert len(model.prompts) == 3


def test_a_run_is_paid_for_once_across_runs_of_the_program(tmp_path):
    pytest.importorskip("langgraph")
    route, _ = agent(tmp_path, finish_with("3:1"))
    first = route.search("q", {"3"})
    assert route.search("q", {"3"}) == first  # the queue is empty: a call would fail
    again, model = agent(tmp_path)
    assert again.search("q", {"3"}) == first
    assert model.prompts == []


def test_another_scope_or_a_repeated_measurement_asks_again(tmp_path):
    pytest.importorskip("langgraph")
    route, _ = agent(tmp_path, finish_with("3:1"), finish_with("4:1"))
    assert route.search("q", {"3"})[0].key == ("3", 1)
    assert route.search("q")[0].key == ("4", 1)
    repeat, model = agent(tmp_path, finish_with("3:2"), run=1)
    assert repeat.search("q", {"3"})[0].key == ("3", 2)
    assert len(model.prompts) == 1


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

    monkeypatch.setattr("tutor.retrieval.asker.llm.chat", chat)
    return purposes


def test_build_concepts_writes_each_lecture_once_and_says_what_it_used(
        workspace, with_concept, capsys):
    assert main(["--build-concepts"]) == 0
    out = capsys.readouterr().out
    assert "L3: 1 concepts, 2 of 3 pages covered" in out
    assert "model calls 1, tokens in 100, out 10" in out
    assert list((workspace / "cache" / "retrieval" / "concepts").glob("org_small-*/L3.json"))
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


def test_asking_for_the_agent_route_without_its_role_is_an_error(workspace, capsys):
    assert main(["--route", "agent"]) == 1
    assert "agent" in capsys.readouterr().err


def test_the_agent_route_runs_and_says_what_it_used(workspace, monkeypatch, capsys):
    pytest.importorskip("langgraph")
    with (workspace / "workspace.toml").open("a", encoding="utf-8") as f:
        f.write('[llm.profiles.small]\nprovider = "custom"\nbase_url = "http://x"\n'
                'model = "org/small"\n[llm.roles]\nagent = "small"\n')

    def chat(config, messages, *, purpose, **kwargs):
        return Reply(json.dumps(finish_with("3:1")), 100, 10, 0.1)

    monkeypatch.setattr("tutor.retrieval.asker.llm.chat", chat)
    assert main([]) == 0  # the role alone puts it among the default routes
    out = capsys.readouterr().out
    assert "lecture  agent" in out and "course   agent" in out
    assert "agent model calls this run" in out and "steps each" in out
    assert list((workspace / "cache" / "retrieval" / "agent").glob("org_small-*/runs.jsonl"))


def without_unfindable_rows(workspace):
    """Freezing refuses a row with no page, as the fixture's coverage.md has on purpose."""
    path = workspace / "coverage.md"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    path.write_text("".join(line for line in lines if "Missing" not in line), encoding="utf-8")


def test_the_final_splits_need_frozen_queries(workspace, capsys):
    for split in ("test", "cross"):
        assert main(["--split", split]) == 1
        assert "--freeze-queries" in capsys.readouterr().err


def test_cross_searches_the_whole_course_only(workspace, capsys):
    without_unfindable_rows(workspace)
    write_cross_lecture(workspace, {"query": "joins and sorting", "slides": {"3": "3.1-3.2"}})
    assert main(["--freeze-queries"]) == 0
    capsys.readouterr()
    assert main(["--split", "cross"]) == 0
    out = capsys.readouterr().out
    table = out.split("tokens per query")[0]  # the sizes line below says "whole lecture"
    assert "course  bm25" in table and "lecture" not in table


def test_cross_without_its_file_says_so(workspace, capsys):
    without_unfindable_rows(workspace)
    assert main(["--freeze-queries"]) == 0
    capsys.readouterr()
    assert main(["--split", "cross"]) == 1
    assert "no cross queries" in capsys.readouterr().err


# --- what the Codex review of 2026-11-02 asked for ------------------------------------------

def test_a_changed_setting_gets_its_own_folder_so_nothing_old_is_replayed(tmp_path, monkeypatch):
    base = settings_folder(tmp_path, {"model": "org/m", **tool_settings("bm25")})
    assert base.name.startswith("org_m-")
    assert json.loads((base / "settings.json").read_text(encoding="utf-8"))["search"] == "bm25"
    assert settings_folder(tmp_path, {"model": "org/m", **tool_settings("hybrid")}) != base
    monkeypatch.setattr("tutor.retrieval.agent.MAX_READ", 1)
    assert settings_folder(tmp_path, {"model": "org/m", **tool_settings("bm25")}) != base
    monkeypatch.undo()
    assert settings_folder(tmp_path, {"model": "org/m", **tool_settings("bm25")}) == base


def test_the_askers_settings_say_how_hard_it_thinks():
    config = LLMConfig("p", "glm", "http://x", "m", thinking_off=({"reasoning_effort": "low"},),
                       thinking_on={"reasoning_effort": "high"})
    off, on = ModelAsker(config).settings, ModelAsker(config, thinking=True).settings
    assert off["thinking"] is False and off["request"] == [{"reasoning_effort": "low"}]
    assert on["thinking"] is True and on["request"] == {"reasoning_effort": "high"}


class CountingQueue(ActionQueue):
    """A model that also counts tokens, as the real asker does."""

    def __init__(self, *actions):
        super().__init__(*actions)
        self.input_tokens = self.output_tokens = 0

    def __call__(self, *args):
        self.input_tokens += 100
        self.output_tokens += 10
        return super().__call__(*args)


def test_a_stored_run_keeps_its_trace_and_what_it_cost(tmp_path):
    pytest.importorskip("langgraph")
    model = CountingQueue(search_for("joins"), "not json", read_pages("3:1"), finish_with("3:1"))
    route = AgentRetriever(AGENT_PAGES, model, tmp_path / "agent")
    route.search("q", {"3"})
    row = route.used[0]
    assert row["seen"] == ["3:1"] and row["read"] == ["3:1"] and row["pages"] == ["3:1"]
    assert [step["action"] for step in row["trace"]] == ["search", "read", "finish"]
    assert (row["failed"], row["finished"], row["steps"]) == (1, True, 4)
    assert (row["tokens_in"], row["tokens_out"]) == (400, 40)
    stored = AgentRetriever(AGENT_PAGES, CountingQueue(), tmp_path / "agent")
    stored.search("q", {"3"})
    assert first_run_cost(stored.used).startswith("first run, per query: 400 tokens in, 40 out")


def test_first_run_cost_says_when_it_was_not_recorded():
    assert first_run_cost([{"ids": []}]) == "cost not recorded"


def test_the_agent_searches_with_the_route_it_is_given(tmp_path):
    other = FixedRoute([AGENT_PAGES[1]])
    model = ActionQueue(search_for("joins"), finish_with())
    route = AgentRetriever(AGENT_PAGES, model, tmp_path / "agent", search_route=other)
    state = drive(route, fresh_state(scope=["3"]))
    assert state["seen"] == ["3:2"] and other.scopes == [{"3"}]


def test_neighbours_follow_each_page_within_its_lecture_and_each_page_once():
    pages = [page("3", n) for n in (1, 2, 3, 4)] + [page("4", 1)]
    route = NeighbourRetriever(FixedRoute([pages[1], pages[2], pages[4]]), pages, "near")
    assert [p.key for p in route.search("q")] == [("3", 2), ("3", 1), ("3", 3), ("3", 4), ("4", 1)]
    assert route.name == "near"


def scored(**recalls):
    return [{"id": i, "recall_within": {2000: value}} for i, value in recalls.items()]


def test_compare_counts_query_by_query_and_applies_the_margin():
    base = scored(a=0.5, b=0.5, c=0.5, d=0.5)
    just_enough = compare(base, scored(a=0.6, b=0.6, c=0.6, d=0.5))
    assert (just_enough["better"], just_enough["worse"], just_enough["same"]) == (3, 0, 1)
    assert just_enough["adopt"] is (3 >= ADOPT_MARGIN)
    assert just_enough["mean_change"] == pytest.approx(0.075)
    one_big_swing = compare(base, scored(a=1.0, b=0.5, c=0.5, d=0.5))
    assert one_big_swing["mean_change"] > 0 and one_big_swing["adopt"] is False


def test_compare_reads_rows_that_went_through_json():
    rows = json.loads(json.dumps(scored(a=0.5)))
    assert compare(rows, rows)["same"] == 1


def test_compare_refuses_runs_of_different_queries():
    with pytest.raises(ValueError, match="same queries"):
        compare(scored(a=0.5), scored(b=0.5))


def test_evaluate_reports_what_each_query_actually_returned():
    pages = [page(number=n, text="x" * 40) for n in (1, 2)]  # 10 tokens each
    result = evaluate(FixedRoute(pages), [query([1])], "course")
    assert (result.pages_returned, result.tokens_returned) == (2, 20)
    assert result.per_query[0]["pages"] == [["3", 1], ["3", 2]]
    assert result.per_query[0]["recall_within"][2000] == 1.0


def test_freezing_snapshots_labels_that_rewording_cannot_move(tmp_path):
    pages = [page("3", 1, "3.1"), page("3", 2, "3.2")]
    (tmp_path / "coverage.md").write_text("### L3 T\n| 3.1 | Joins | gap-code |\n",
                                          encoding="utf-8")
    write_cross_lecture(tmp_path, {"query": "spans", "slides": {"3": "3.1-3.2"}})
    frozen = freeze(tmp_path, pages)
    (tmp_path / "coverage.md").write_text("### L3 T\n| 3.2 | Reworded | gap-code |\n",
                                          encoding="utf-8")
    assert load_frozen(tmp_path) == frozen
    assert {q.text for q in frozen} == {"Joins", "spans"}


def test_freezing_refuses_to_overwrite_or_to_keep_a_row_with_no_page(tmp_path):
    pages = [page("3", 1, "3.1")]
    assert load_frozen(tmp_path) is None
    (tmp_path / "coverage.md").write_text("### L3 T\n| 3.9 | Lost | gap-code |\n", encoding="utf-8")
    with pytest.raises(ValueError, match="fix these"):
        freeze(tmp_path, pages)
    (tmp_path / "coverage.md").write_text("### L3 T\n| 3.1 | Fine | gap-code |\n", encoding="utf-8")
    freeze(tmp_path, pages)
    with pytest.raises(ValueError, match="already"):
        freeze(tmp_path, pages)


def test_a_new_run_number_measures_again_and_the_same_one_does_not(
        workspace, with_concept, capsys):
    assert main(["--build-concepts"]) == 0
    with_concept.clear()
    assert main(["--route", "concept"]) == 0
    asked = with_concept.count("concept-pick")
    assert asked > 0
    assert main(["--route", "concept"]) == 0
    assert with_concept.count("concept-pick") == asked
    assert main(["--route", "concept", "--run", "1"]) == 0
    assert with_concept.count("concept-pick") == 2 * asked
    capsys.readouterr()


def test_saved_runs_can_be_compared_from_the_command_line(workspace, capsys):
    assert main(["--route", "bm25", "--save", "first"]) == 0
    assert main(["--route", "bm25", "--save", "second"]) == 0
    capsys.readouterr()
    assert main(["--compare", "first/bm25", "second/bm25"]) == 0
    out = capsys.readouterr().out
    assert "0 better, 0 worse" in out and "keep the simpler setting" in out
    assert main(["--compare", "first/vector", "second/bm25"]) == 1
    assert main(["--compare", "nothing/bm25", "second/bm25"]) == 1
