"""question.md: the question in student.py's docstring, as Markdown."""

import json
from pathlib import Path

from tutor.question import main, question_markdown, run_block, write_question

QUESTION = '''"""Average temperature per station with Spark SQL

Concept:
A DataFrame becomes queryable by name only after createOrReplaceTempView.

Task:
Implement average_by_station_sql(spark, readings, min_readings).

Input:  spark      a SparkSession
        readings   a DataFrame (station, temperature)
Example (min_readings = 2):
    input   [("s1", 10.0), ("s1", 20.0)]

    output  [("s1", 15.0)]
Constraints:
- Use spark.sql; no groupBy,
  no agg.
Keep a combiner: a sentence with a colon stays a sentence.
input:  [1, 2]
output: [3]
Run the tests:    pytest test_student.py
"""

def average_by_station_sql(spark, readings, min_readings):
    raise NotImplementedError
'''


def test_labels_become_headings_and_only_examples_and_commands_code_blocks():
    md = question_markdown(QUESTION)
    assert md.startswith("# Average temperature per station with Spark SQL\n")
    for heading in ("Concept", "Task", "Input", "Example (min_readings = 2)", "Constraints",
                    "Run the tests"):
        assert f"\n## {heading}\n" in md
    # outside Example and Run, indented text is plain lines, not a code block
    assert "spark      a SparkSession  \nreadings   a DataFrame" in md
    assert md.count("```text") == 1  # the example only
    # one code block across the blank line inside the indented example
    assert '```text\ninput   [("s1", 10.0), ("s1", 20.0)]\n\noutput  [("s1", 15.0)]\n```' in md
    assert "- Use spark.sql; no groupBy,  \nno agg." in md  # a wrapped list item, not code
    assert "## Keep a combiner" not in md
    # unindented lines keep their line breaks instead of running together
    assert "input:  [1, 2]  \noutput: [3]" in md
    assert "`pytest test_student.py`" in md


def test_no_docstring_or_broken_code_gives_no_question():
    assert question_markdown("def f():\n    pass\n") == ""
    assert question_markdown('"""Half\n') == ""


def test_a_batch_gets_question_md_from_student_py_or_the_skeleton(tmp_path):
    for exercise_id, student in (("a", QUESTION), ("b", "def broken(:\n")):
        (tmp_path / "exercises" / exercise_id).mkdir(parents=True)
        (tmp_path / "exercises" / exercise_id / "student.py").write_text(student, encoding="utf-8")
        (tmp_path / "solutions" / exercise_id).mkdir(parents=True)
        (tmp_path / "solutions" / exercise_id / "skeleton.py").write_text('"""B question"""\n',
                                                                          encoding="utf-8")
    assert main([str(tmp_path)]) == 0
    question_a = tmp_path / "exercises" / "a" / "question.md"
    assert question_a.read_text(encoding="utf-8").startswith("# Average")
    # the owner's half-edited student.py does not parse: the skeleton's question is used
    assert (tmp_path / "exercises" / "b" / "question.md").read_text(encoding="utf-8").startswith(
        "# B question\n")


def test_how_to_run_names_the_exercise_folder(tmp_path):
    outside = tmp_path / "batches" / "b1" / "exercises" / "sum-list"
    block = run_block(outside)
    assert f'cd "{outside.resolve()}"' in block  # a workspace exercise: its absolute path
    assert "pytest test_student.py" in block and "python -m tutor.hint" in block
    assert "`hints.md` in this folder" in block  # never left to the model to mention
    inside = Path(__file__).resolve().parents[1] / "examples" / "station-average-rdd" / "exercise"
    assert 'cd "examples/station-average-rdd/exercise"' in run_block(inside)  # never absolute


def test_a_draft_question_has_no_how_to_run(tmp_path):
    write_question(QUESTION, tmp_path)
    assert "How to run" not in (tmp_path / "question.md").read_text(encoding="utf-8")
    write_question(QUESTION, tmp_path, location=tmp_path)
    assert "## How to run" in (tmp_path / "question.md").read_text(encoding="utf-8")



# --- the Markdown layout (docs/design.md, "question.md format") --------------------------

MARKDOWN = 'r"""' + r"""# Count the bits

## Concept

### Why it is needed

Counting needs $O(\log n)$ bits.

```text
## not a heading inside a code block
```

## Task

Implement `count(bits)`.

## Slides

Lecture 2, slides 2.4-2.5.

## Run

```bash
python student.py sample_input out
pytest test_student.py
```

`sample_input/` holds the example.
""" + '"""\n\ndef count(bits):\n    raise NotImplementedError\n'
METADATA = {"source": {"lecture": "Lecture 2", "slides": "2.4-2.5"},
            "type": "implementation", "difficulty": "easy"}


def test_the_markdown_layout_gets_its_frame_from_code(tmp_path):
    write_question(MARKDOWN, tmp_path, location=tmp_path, metadata=METADATA, mode="learn")
    md = (tmp_path / "question.md").read_text(encoding="utf-8")

    assert md.startswith("# Count the bits\n\n"
                         "> **Lecture 2** · slides 2.4-2.5 · learn mode · implementation · easy\n")
    assert ("**Contents:** [Concept](#concept) · [Task](#task) · [Slides](#slides) · "
            "[How to run](#how-to-run)") in md
    assert "$O(\\log n)$" in md  # the raw docstring kept the backslash
    assert "## not a heading inside a code block" in md
    # Rules: after the contents, after Concept, before "How to run"; Run folded into it.
    assert md.count("\n---\n") == 3 and "## Run" not in md
    assert ("pytest test_student.py      # the tests\npython student.py sample_input out\n```"
            in md)
    assert "`sample_input/` holds the example." in md.split("## How to run")[1]


def test_a_markdown_draft_has_no_how_to_run_and_works_without_metadata():
    md = question_markdown(MARKDOWN)
    assert md.startswith("# Count the bits\n\n**Contents:** [Concept](#concept)")
    assert "How to run" not in md


def test_the_examples_question_md_matches_their_student_py():
    """question.md is rendered from student.py: a change to one without the other fails."""
    root = Path(__file__).resolve().parents[1] / "examples"
    for example in sorted(p for p in root.iterdir() if (p / "exercise").is_dir()):
        exercise = example / "exercise"
        metadata = json.loads((example / "solution" / "metadata.json").read_text(encoding="utf-8"))
        rendered = exercise / "question.md"
        before = rendered.read_text(encoding="utf-8")
        write_question((exercise / "student.py").read_text(encoding="utf-8"), exercise,
                       location=exercise, metadata=metadata, mode="learn")
        try:
            assert rendered.read_text(encoding="utf-8") == before, example.name
        finally:
            rendered.write_text(before, encoding="utf-8")



def test_a_formulas_bar_in_a_table_row_does_not_split_the_cell():
    doc = ('r"""# Sizes\n\n## Input\n\n| Parameter | Meaning |\n|---|---|\n'
           '| `m` | items, $m = |S|$ |\n\nOutside a table $|S|$ stays.\n"""\n')
    md = question_markdown(doc)
    assert "| `m` | items, $m = \\vert S\\vert $ |" in md
    assert "Outside a table $|S|$ stays." in md
