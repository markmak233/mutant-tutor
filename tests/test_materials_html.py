"""Lectures saved as web pages (materials/html.py): what is kept, what is left out, and how
the sections reach the cache. Every page here is hand-made in the shape of its source (a
Typora export, a Moodle quiz review, a plain page); none holds course material."""

from pathlib import Path

import pytest

from tutor.materials import cache
from tutor.materials.__main__ import main
from tutor.materials.html import (
    TARGET_CHARS,
    lecture_number,
    looks_like_html,
    read_html,
    sections,
    to_markdown,
)
from tutor.materials.slides import lectures_taught

# Typora's export: the notes in #write; each code line in its own pre.CodeMirror-line, next to
# a measuring copy and an input box; the TeX in a math/tex script beside its rendering. A
# browser extension's button sits outside #write.
TYPORA = """<!doctype html><html><head><title>Lecture 07</title><style>p{}</style></head>
<body class="typora-export"><div id="FloaterLogo"><img id="HelpImg"> Translate</div>
<div class="typora-export-content"><div id="write">
<h2>Lecture 07</h2>
<p>Today: <strong>loops</strong> and <code>for</code>.</p>
<p>The sum is <mjx-container class="MathJax"><svg></svg></mjx-container>
<script type="math/tex">\\sum_{i=0}^{n-1} a_i</script> in general:</p>
<script type="math/tex; mode=display">s = a_0 + a_1</script>
<h5>Part 1: counting</h5>
<pre class="md-fences" lang="c"><div class="CodeMirror"><textarea>typed</textarea>
<div class="CodeMirror-measure"><pre>xxxxxxxxxx</pre></div>
<div class="CodeMirror-code">
<pre class=" CodeMirror-line "><span>int main(void) {</span></pre>
<pre class=" CodeMirror-line "><span>    for (int i = 0; i &lt; 3; i++) {</span></pre>
<pre class=" CodeMirror-line "><span>        printf("%d\\n", i);</span></pre>
<pre class=" CodeMirror-line "><span>    }</span></pre>
<pre class=" CodeMirror-line "><span>}</span></pre>
</div></div></pre>
<ul><li>first<ul><li>nested</li></ul></li><li>second</li></ul>
<table><tr><th>x</th><th>y</th></tr><tr><td>1</td><td>2</td></tr></table>
</div></div><script>alert("ran")</script></body></html>"""

# A Moodle quiz review: the signed-in user's name outside role="main"; inside it the
# attempt's summary, then each question with its mark, the student's inputs (one hidden,
# holding the session's key), a drop-down with the student's choice, feedback on that
# answer, and the general feedback with the right answer.
MOODLE = """<!DOCTYPE html><html><body>
<nav class="navbar"><span class="usertext">Pat Example</span></nav>
<div id="page"><div id="region-main"><div role="main">
<table class="quizreviewsummary"><tr><th>Started on</th><td>Monday, 1 March</td></tr>
<tr><th>Grade</th><td>9.00 out of 10.00</td></tr></table>
<form action="processattempt.php">
<div class="que stack adaptivemultipart correct"><div class="info"><h3 class="no">Question
<span class="qno">1</span></h3><div class="state">Correct</div><div class="grade">Mark 8.00
out of 8.00</div></div><div class="content"><div class="formulation clearfix">
<h4 class="accesshide">Question text</h4>
<input type="hidden" name="q1:sequencecheck" value="sesskey=FAKE-SESSKEY-0000">
<div class="qtext"><p><b>Products</b></p><p>If \\(A\\) is \\(m\\times k\\) then
\\[AB = C\\]</p><p>The size is <select name="q1:ans1"><option value="">(No answer given)</option>
<option value="1">2x2</option><option value="2" selected="selected">3x3</option></select>
and the entry is <input type="text" name="q1:ans2" value="student-typed-41"></p>
<div class="stackinputfeedback">Your last answer was interpreted as 41</div>
<div class="stackprtfeedback"><div class="correct">Correct answer, well done.</div>
<div class="gradingdetails">Marks for this submission: 1.00/1.00.</div></div></div></div>
<div class="outcome clearfix"><div class="feedback"><div class="correct">Correct answer,
well done.</div><div class="generalfeedback">Worked: multiply row by column.</div>
<div class="rightanswer">The correct answer is: 7</div></div></div></div></div>
<div class="que multianswer incorrect"><div class="info"><span class="qno">2</span></div>
<div class="content"><div class="qtext"><p>Which products exist?</p></div></div></div>
</form></div></div></div></body></html>"""

PLAIN = """<html><body><nav>Home | Courses</nav><main><h1>Topic</h1><p>Some   text
over lines.</p><pre>  indented
    more</pre><img alt="a diagram" src="d.png"><img class="icon" src="i.png"></main>
<footer>Copyright</footer></body></html>"""


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- a Typora export ------------------------------------------------------------------------


def test_typora_notes_keep_code_exactly_and_tex_as_written(tmp_path):
    pages = read_html(_write(tmp_path / "lecture07.html", TYPORA))
    text = "\n".join(p.text for p in pages)

    assert ("```c\nint main(void) {\n    for (int i = 0; i < 3; i++) {\n"
            '        printf("%d\\n", i);\n    }\n}\n```') in text
    assert "$\\sum_{i=0}^{n-1} a_i$" in text and "$$\ns = a_0 + a_1\n$$" in text
    assert "**loops**" in text and "`for`" in text
    assert "- first\n  - nested\n- second" in text
    assert "| x | y |\n| 1 | 2 |" in text


def test_typora_leaves_out_the_editor_scripts_and_what_is_outside_the_notes(tmp_path):
    text = "\n".join(p.text for p in read_html(_write(tmp_path / "lecture07.html", TYPORA)))
    for gone in ("xxxxxxxxxx", "typed", "Translate", 'alert("ran")', "p{}"):
        assert gone not in text


def test_sections_start_at_headings_and_are_labelled_by_the_lecture_number(tmp_path):
    pages = read_html(_write(tmp_path / "lecture07.html", TYPORA))
    assert [p.label for p in pages] == ["7.1", "7.2"]
    assert pages[0].text.startswith("## Lecture 07") and pages[1].text.startswith("##### Part 1")
    assert not any(p.needs_vision for p in pages)  # text only: nothing waits for a model


# --- a Moodle quiz review -------------------------------------------------------------------


def test_moodle_keeps_each_question_its_tex_feedback_and_right_answer(tmp_path):
    pages = read_html(_write(tmp_path / "Products Attempt review.htm", MOODLE))

    assert [p.label for p in pages] == ["1", "2"]  # one section per question; no number
    first = pages[0].text
    assert first.startswith("### Question 1")
    assert "\\(A\\) is \\(m\\times k\\)" in first and "\\[AB = C\\]" in first
    assert "[2x2 / 3x3]" in first  # the choices, not which one was picked
    assert "Worked: multiply row by column." in first and "The correct answer is: 7" in first
    assert pages[1].text.startswith("### Question 2")


def test_moodle_leaves_out_the_students_answers_session_marks_and_name(tmp_path):
    text = "\n".join(p.text for p in read_html(_write(tmp_path / "q.htm", MOODLE)))
    for gone in ("FAKE-SESSKEY", "student-typed-41", "interpreted as", "well done",
                 "Marks for this submission", "Mark 8.00", "out of", "Started on",
                 "Pat Example", "No answer given", "selected", "Correct\n"):
        assert gone not in text, gone


# --- any other page -------------------------------------------------------------------------


def test_a_plain_page_reads_its_main_part_and_marks_its_images(tmp_path):
    text, moodle = to_markdown(PLAIN)
    assert not moodle
    assert "Home" not in text and "Copyright" not in text
    assert "Some text over lines." in text
    assert "```\n  indented\n    more\n```" in text
    assert "[image: a diagram]" in text and "i.png" not in text  # an icon is no content


def test_a_long_section_is_cut_at_a_blank_line_but_never_inside_code():
    paragraph = "word " * 200
    code = "\n".join(f"line {n}" for n in range(1000))  # longer than TARGET_CHARS alone
    text = "\n\n".join([paragraph] * 6 + [f"```\n{code}\n```", paragraph])
    parts = sections(text)
    assert len(parts) > 2 and all(len(p) <= TARGET_CHARS + len(paragraph) + 2
                                  for p in parts if "```" not in p)
    fenced = [p for p in parts if "```" in p]
    assert len(fenced) == 1 and fenced[0].count("```") == 2


def test_a_heading_alone_joins_the_section_after_it():
    assert sections("# Title\n\n## Part\n\ntext") == ["# Title\n\n## Part\n\ntext"]


def test_deep_markup_and_a_broken_copy_do_not_crash(tmp_path):
    deep = "<html><body>" + "<div>" * 3000 + "deep text" + "</div>" * 3000 + "</body></html>"
    assert "deep text" in read_html(_write(tmp_path / "deep.html", deep))[0].text
    nul = tmp_path / "broken.htm"
    nul.write_bytes(b"\x00" * 5000)  # a copy that lost its content
    assert read_html(nul) == []


def test_a_page_not_in_utf8_is_read(tmp_path):
    path = tmp_path / "old.html"
    path.write_bytes("<html><body><p>caf\xe9 \x93quoted\x94</p></body></html>".encode("latin-1"))
    assert "café “quoted”" in read_html(path)[0].text


@pytest.mark.parametrize(("name", "number"), [
    ("lecture01.html", "1"), ("Lecture_5.2-Topic.html", "5.2"), ("notes.html", None),
    ("Dot Product Attempt review(4).htm", None), ("10 Matrices (2).htm", "10")])
def test_the_lecture_number_comes_from_the_file_name(name, number):
    assert lecture_number(Path(name)) == number


def test_an_upload_is_known_as_html_by_its_start():
    assert looks_like_html(b"\xef\xbb\xbf<!DOCTYPE html><html>")
    assert looks_like_html(b"<!-- saved from url=(0040)https://example.org/ -->\n<html lang=en>")
    assert not looks_like_html(b"%PDF-1.7") and not looks_like_html(b"\x00" * 100)


# --- in the cache, and for what reads it ----------------------------------------------------


def test_the_read_command_caches_a_web_page_by_sections(tmp_path, monkeypatch, capsys):
    (tmp_path / "workspace.toml").write_text('course = "demo"\n', encoding="utf-8")
    _write(tmp_path / "lectures" / "lecture07.html", TYPORA)
    _write(tmp_path / "lectures" / "broken.htm", "")  # no text: said so, nothing cached
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))

    assert main([str(tmp_path / "lectures")]) == 1
    captured = capsys.readouterr()
    assert "lecture07.html: 2 sections, 2 read now" in captured.out
    assert "broken.htm: no text found" in captured.err
    cached = cache.load(tmp_path / "cache" / "lectures" / "lecture07.html.md")
    assert [(e.number, e.label, e.read) for e in cached.values()] == [
        (1, "7.1", "text"), (2, "7.2", "text")]
    assert not (tmp_path / "cache" / "lectures" / "broken.htm.md").exists()
    main([str(tmp_path / "lectures" / "lecture07.html")])
    assert "0 read now" in capsys.readouterr().out


def test_a_lecture_is_found_by_its_name_whatever_its_extension(tmp_path):
    folder = tmp_path / "cache" / "lectures"
    for name in ("Lecture_1-A.pdf.md", "Lecture_2-B.html.md", "Lecture_3-C.htm.md", "notes.md"):
        _write(folder / name, "")
    assert cache.lecture_cache(tmp_path, "Lecture_2-B") == folder / "Lecture_2-B.html.md"
    assert cache.lecture_cache(tmp_path, "Lecture_9-Z") == folder / "Lecture_9-Z.pdf.md"
    assert [cache.lecture_name(p) for p in cache.lecture_caches(tmp_path)] == [
        "Lecture_1-A", "Lecture_2-B", "Lecture_3-C"]  # notes.md is no lecture's cache
    assert lectures_taught(tmp_path, {"lecture": "Lecture_2-B"}) == ["Lecture_1-A",
                                                                     "Lecture_2-B"]
    assert cache.is_lecture(Path("x.HTM")) and not cache.is_lecture(Path(".x.html"))
