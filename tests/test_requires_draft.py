"""Drafting [requires] from the lectures (tutor.materials.requires): the model is a stand-in,
so these test what the program decides about its answers, and that each lecture is asked
once. Lecture text here is made up."""

import json
from pathlib import Path

import pytest

from tutor.llm import LLMConfig, LLMError, Reply
from tutor.materials import cache
from tutor.materials import requires as req

PAGES = {
    1: "Setting up\npip install pyspark==3.5.1\nYou need Java 17 (JDK 17) and Hadoop 3.3.",
    2: "import cv2\nimg = cv2.imread(path)\nimport os, json",
    3: "Sixty slides of JavaScript history; also python3 -m venv .venv",
}


def entries(pages: dict[int, str]) -> list[cache.Entry]:
    return [cache.Entry(n, None, "text", f"h{n}", body) for n, body in pages.items()]


def make_lecture(workspace: Path, name: str, pages: dict[int, str] = PAGES) -> Path:
    path = workspace / "cache" / "lectures" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cache.render(name, entries(pages)), encoding="utf-8")
    return path


def found(**kw) -> req.Found:
    return req.Found(**{"kind": "pip", "name": "pyspark", "page": 1,
                        "quote": "pip install pyspark==3.5.1", **kw})


def pages() -> dict[int, cache.Entry]:
    return {e.number: e for e in entries(PAGES)}


# --- what the program keeps of an answer -------------------------------------------------------


def test_an_entry_whose_quote_is_on_its_page_is_kept_with_its_version_cleaned():
    kept, dropped = req.check(found(version="3.5.x"), "L1", pages())
    assert dropped is None and kept.version == "3.5"


@pytest.mark.parametrize("entry, reason", [
    (found(page=9), "no page 9"),
    (found(quote="pip install pyspark==4.0"), "not on page 1"),  # made up
    (found(name="os", import_name="os", page=2, quote="import os, json"), "standard library"),
    (found(name="numpy", page=1), "names neither"),  # on the page, but about pyspark
    (found(name="six", page=3, quote="Sixty slides"), "names neither"),  # not a whole word
    (found(kind="java", name="Java", page=3, quote="Sixty slides of JavaScript history"),
     "does not name java"),
    (found(name="bad name", page=1), "not a package name"),
])
def test_an_entry_the_page_does_not_bear_out_is_dropped(entry, reason):
    kept, dropped = req.check(entry, "L1", pages())
    assert kept is None and reason in dropped.reason and dropped.lecture == "L1"


def test_software_only_shown_is_matched_loosely():
    """"other" is never installed, so a plural in the quote does not drop it."""
    page = {1: cache.Entry(1, None, "text", "h", "Use Jupyter notebooks for the labs.")}
    kept, _ = req.check(found(kind="other", name="Jupyter Notebook", page=1,
                              quote="Jupyter notebooks"), "L1", page)
    assert kept is not None


def test_the_quote_is_matched_whatever_its_spacing_or_backticks():
    entry = found(quote="`pip   install\npyspark==3.5.1`")
    assert req.check(entry, "L1", pages())[0] is not None


def test_special_kinds_take_the_name_requires_knows_and_java_reads_as_envcheck_does():
    java, _ = req.check(found(kind="java", name="OpenJDK", version="1.8",
                              quote="Java 17 (JDK 17)"), "L1", pages())
    python, _ = req.check(found(kind="python", name="Python", page=3,
                                quote="python3 -m venv .venv"), "L1", pages())
    assert (java.name, java.version) == ("java", "8")
    assert python.name == "python" and python.version == ""


def test_a_pip_name_the_quotes_do_not_show_is_flagged_as_the_models_mapping():
    """`import cv2` shows the module; that pip calls it opencv-python is the model's word."""
    cv, _ = req.check(found(name="opencv-python", import_name="cv2", page=2,
                            quote="import cv2"), "L2", pages())
    spark, _ = req.check(found(), "L1", pages())
    merged = {c.name: c for c in req.merge([(cv, "L2"), (spark, "L1")])}
    assert merged["opencv-python"].mapped and not merged["pyspark"].mapped


def test_one_piece_of_software_across_lectures_is_one_candidate_with_every_source():
    a = found(version="3.5")
    b = found(version="3.4")
    c = found(name="PySpark", version="3.5")
    [candidate] = req.merge([(a, "L1"), (b, "L2"), (c, "L3")])
    assert candidate.versions == ["3.5", "3.4"]  # most often stated first
    assert [s.lecture for s in candidate.sources] == ["L1", "L2", "L3"]


# --- asking, once per lecture ------------------------------------------------------------------


class FakeModel:
    """Stands in for llm.chat: answers each lecture with `answer`, records each request."""

    def __init__(self, answer: dict | str, finish: str | None = "stop"):
        self.answer = answer
        self.finish = finish
        self.calls: list[dict] = []

    def __call__(self, config, messages, **kw):
        self.calls.append({"messages": messages, **kw})
        text = self.answer if isinstance(self.answer, str) else json.dumps(self.answer)
        return Reply(text, 1000, 50, 0.1, self.finish)


ANSWER = {"found": [
    {"kind": "pip", "name": "pyspark", "import_name": "", "version": "3.5", "page": 1,
     "quote": "pip install pyspark==3.5.1"},
    {"kind": "pip", "name": "numpy", "import_name": "numpy", "version": "", "page": 2,
     "quote": "import numpy as np"},  # not on the page
]}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    (tmp_path / "workspace.toml").write_text(
        'course = "demo"\n[llm.profiles.local]\nprovider = "lmstudio"\nmodel = "m"\n'
        '[llm.roles]\ngenerate = "local"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    make_lecture(tmp_path, "L1.pdf")
    make_lecture(tmp_path, "L2.pdf")
    return tmp_path


def test_each_lecture_is_asked_once_and_the_answer_stored_as_given(workspace, monkeypatch):
    fake = FakeModel(ANSWER)
    monkeypatch.setattr(req, "chat", fake)

    assert req.main([]) == 0
    assert len(fake.calls) == 2
    stored = json.loads((workspace / "cache" / "requires" / "L1.pdf.json").read_text(
        encoding="utf-8"))
    assert stored["found"] == ANSWER["found"] and stored["model"] == "local: m"

    assert req.main([]) == 0 and len(fake.calls) == 2  # nothing changed: nobody asked


def test_the_instructions_come_first_and_alike_for_every_lecture(workspace, monkeypatch):
    """So a provider's prompt cache can serve them; thinking off, a schema, a limit."""
    fake = FakeModel(ANSWER)
    monkeypatch.setattr(req, "chat", fake)
    req.main([])
    first, second = (call["messages"] for call in fake.calls)
    assert first[0] == second[0] == {"role": "system", "content": req.PROMPT}
    # Only what builds, runs or tests the code on the student's machine (owner, 2026-11-07).
    assert "PuTTY" in req.PROMPT and "editors and IDEs" in req.PROMPT
    assert req.PROMPT_VERSION == 2  # a changed prompt asks every lecture again
    assert first[1]["content"].startswith("Lecture file: L1.pdf\n\n## p.1\n")
    assert fake.calls[0]["thinking"] is False and fake.calls[0]["response_schema"]
    assert fake.calls[0]["max_tokens"] == req.MAX_TOKENS


def test_the_draft_checks_what_was_stored_and_a_changed_lecture_is_asked_again(workspace,
                                                                               monkeypatch):
    monkeypatch.setattr(req, "chat", FakeModel(ANSWER))
    req.main([])

    draft = req.draft(workspace)
    assert [c.name for c in draft.candidates] == ["pyspark"]
    assert len(draft.dropped) == 2 and "not on page 2" in draft.dropped[0].reason
    assert draft.done == ["L1.pdf", "L2.pdf"] and draft.todo == []

    make_lecture(workspace, "L2.pdf", {**PAGES, 4: "A new page"})
    draft = req.draft(workspace)
    assert draft.done == ["L1.pdf"] and [name for name, _ in draft.todo] == ["L2.pdf"]


def test_a_cut_off_or_unreadable_reply_is_not_stored(workspace, monkeypatch, capsys):
    monkeypatch.setattr(req, "chat", FakeModel(ANSWER, finish="length"))
    assert req.main([]) == 1
    assert not (workspace / "cache" / "requires").exists()
    monkeypatch.setattr(req, "chat", FakeModel("not json"))
    assert req.main([]) == 1
    assert "asks again" in capsys.readouterr().out


def test_a_model_error_stops_the_run(workspace, monkeypatch):
    def failing(*args, **kwargs):
        raise LLMError("401 wrong key")

    monkeypatch.setattr(req, "chat", failing)
    assert req.main([]) == 1


def test_the_model_is_setups_else_plans_else_generates(tmp_path):
    def toml(roles: str) -> Path:
        (tmp_path / "workspace.toml").write_text(
            'course = "d"\n[llm.profiles.a]\nprovider = "lmstudio"\nmodel = "a"\n'
            '[llm.profiles.b]\nprovider = "lmstudio"\nmodel = "b"\n'
            f"[llm.roles]\n{roles}\n", encoding="utf-8")
        return tmp_path

    assert req.model(toml('generate = "a"')).name == "a"
    assert req.model(toml('generate = "a"\nplan = "b"')).name == "b"
    assert req.model(toml('plan = "a"\nsetup = "b"')).name == "b"
    assert isinstance(req.model(toml('plan = "a"'), profile="b"), LLMConfig)
