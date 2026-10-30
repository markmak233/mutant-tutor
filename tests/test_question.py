"""question.md: the question in student.py's docstring, as Markdown."""

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


def test_labels_become_headings_and_indented_data_code_blocks():
    md = question_markdown(QUESTION)
    assert md.startswith("# Average temperature per station with Spark SQL\n")
    for heading in ("Concept", "Task", "Input", "Example (min_readings = 2)", "Constraints",
                    "Run the tests"):
        assert f"\n## {heading}\n" in md
    # text aligned under a label's inline text stays aligned, in one code block
    assert "```text\nspark      a SparkSession\nreadings   a DataFrame" in md
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
        (tmp_path / "exercises" / exercise_id / "student.py").write_text(student)
        (tmp_path / "solutions" / exercise_id).mkdir(parents=True)
        (tmp_path / "solutions" / exercise_id / "skeleton.py").write_text('"""B question"""\n')
    assert main([str(tmp_path)]) == 0
    assert (tmp_path / "exercises" / "a" / "question.md").read_text().startswith("# Average")
    # the owner's half-edited student.py does not parse: the skeleton's question is used
    assert (tmp_path / "exercises" / "b" / "question.md").read_text().startswith(
        "# B question\n")


def test_how_to_run_names_the_exercise_folder(tmp_path):
    outside = tmp_path / "batches" / "b1" / "exercises" / "sum-list"
    block = run_block(outside)
    assert f'cd "{outside.resolve()}"' in block  # a workspace exercise: its absolute path
    assert "pytest test_student.py" in block and "python -m tutor.hint" in block
    inside = Path(__file__).resolve().parents[1] / "examples" / "station-average-rdd" / "exercise"
    assert 'cd "examples/station-average-rdd/exercise"' in run_block(inside)  # never absolute


def test_a_draft_question_has_no_how_to_run(tmp_path):
    write_question(QUESTION, tmp_path)
    assert "How to run" not in (tmp_path / "question.md").read_text()
    write_question(QUESTION, tmp_path, location=tmp_path)
    assert "## How to run" in (tmp_path / "question.md").read_text()
