import json

import pytest

from tutor.schema import SCHEMA_DIR, Manifest, Metadata, check_batch, json_schemas, main, problems


def metadata(**changes):
    """A valid file-level MRJob metadata.json, with `changes` applied (None deletes)."""
    data = {
        "id": "word-count",
        "title": "Word count",
        "topic": "mrjob/basics",
        "type": "implementation",
        "interface": "file",
        "difficulty": "easy",
        "framework": "mrjob",
        "runner": "inline",
        "entry_point": ["WordCount"],
        "combiner_check": True,
        "source": "synthetic",
    }
    data.update(changes)
    return {k: v for k, v in data.items() if v is not None}


def manifest(**changes):
    data = {
        "phase": 2,
        "date": "2026-10-28",
        "label": "w1-mapreduce",
        "mode": "learn",
        "sources": [{"path": "lectures/week1.pdf", "slides": "1.1-1.9", "pdf_pages": "1-9"}],
        "method": "drafted by hand",
        "model": "none",
        "git_commit": "abc1234",
        "exercises": ["word-count", "max-temperature"],
    }
    data.update(changes)
    return {k: v for k, v in data.items() if v is not None}


def test_valid_metadata_and_manifest_have_no_problems():
    assert problems(Metadata, metadata()) == []
    assert problems(Manifest, manifest()) == []


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"interface": None}, "interface: Field required"),
        ({"entry_point": "WordCount"}, "entry_point: Input should be a valid list"),
        ({"partition_check": "yes"}, "partition_check: Input should be a valid boolean"),
        # The old spelling of a function-level exercise, and a typo: both are caught,
        # not ignored.
        ({"function": "word_count"}, "function: Extra inputs are not permitted"),
        ({"partion_check": True}, "partion_check: Extra inputs are not permitted"),
        ({"id": "Word_Count"}, "id: String should match pattern"),
        # Generated metadata once named a call signature as an entry point.
        ({"entry_point": ["WordCount", "average(sc, rdd)"]},
         "entry_point.1: String should match pattern"),
    ],
)
def test_metadata_field_problems_name_the_field(changes, expected):
    found = problems(Metadata, metadata(**changes))
    assert any(line.startswith(expected) for line in found), found


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"runner": None, "combiner_check": None}, "an mrjob exercise names its runner"),
        ({"framework": "pyspark-rdd", "combiner_check": None}, "runner is only for mrjob"),
        ({"runner": "hadoop"}, "combiner_check needs an mrjob exercise on the inline runner"),
        ({"entry_point": ["WordCount.run"]}, "no dots"),
        ({"interface": "function", "framework": "python", "runner": None,
          "combiner_check": None, "entry_point": ["jobs.run_job"]}, "no dots"),
        ({"framework": "pyspark-df", "runner": None, "combiner_check": None,
          "entry_point": ["Monthly"]}, '"Class.run"'),
    ],
)
def test_metadata_fields_must_agree(changes, expected):
    found = problems(Metadata, metadata(**changes))
    assert any(expected in line for line in found), found


def test_spark_file_entry_points_and_workspace_sources_are_accepted():
    source = {"lecture": "L5", "slides": "5.1-5.9", "pdf_pages": "1-9", "assumptions": []}
    data = metadata(framework="pyspark-df", runner=None, combiner_check=None,
                    entry_point=["AvgDF.run", "AvgSQL.run"], source=source)
    assert problems(Metadata, data) == []


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"mode": "exam"}, "mode: Input should be 'learn' or 'review'"),
        ({"date": "28/10/2026"}, "date:"),
        ({"exercises": ["word-count", "word-count"]}, "lists an id twice"),
        ({"priority": {"must": ["word-count"], "skip": ["tf-idf"]}}, "not in the batch"),
        ({"priority": {"must": ["word-count"], "skip": ["word-count"]}}, "more than one mark"),
    ],
)
def test_manifest_problems(changes, expected):
    found = problems(Manifest, manifest(**changes))
    assert any(expected in line for line in found), found


def make_batch(root, exercise_ids, metadata_ids=None):
    """A batch folder with a manifest listing `exercise_ids` and one exercise and
    solution folder per id in `metadata_ids` (default: the same ids)."""
    root.mkdir()
    (root / "manifest.json").write_text(json.dumps(manifest(exercises=exercise_ids)))
    for exercise_id in metadata_ids or exercise_ids:
        (root / "exercises" / exercise_id).mkdir(parents=True)
        solution = root / "solutions" / exercise_id
        solution.mkdir(parents=True)
        (solution / "metadata.json").write_text(json.dumps(metadata(id=exercise_id)))
    return root


def test_check_batch_accepts_a_consistent_batch(tmp_path):
    assert check_batch(make_batch(tmp_path / "b", ["word-count", "max-temperature"])) == []


def test_check_batch_reports_folders_the_manifest_does_not_list(tmp_path):
    batch = make_batch(tmp_path / "b", ["word-count"], ["word-count", "tf-idf"])
    assert check_batch(batch) == ["manifest exercises and exercises/ differ: ['tf-idf']"]


def test_check_batch_reports_metadata_problems_with_their_path(tmp_path):
    batch = make_batch(tmp_path / "b", ["word-count"])
    path = batch / "solutions" / "word-count" / "metadata.json"
    path.write_text(json.dumps(metadata(id="other-id", interface=None)))
    assert check_batch(batch) == [
        "solutions/word-count/metadata.json: interface: Field required",
        "solutions/word-count/metadata.json: id 'other-id' differs from its folder name",
    ]


def test_check_batch_reports_missing_and_broken_files(tmp_path):
    batch = make_batch(tmp_path / "b", ["word-count"])
    (batch / "manifest.json").unlink()
    (batch / "solutions" / "word-count" / "metadata.json").write_text("{")
    found = check_batch(batch)
    assert found[0] == "missing manifest.json"
    assert found[1].startswith("solutions/word-count/metadata.json: not valid JSON")


def test_exported_schema_files_match_the_models():
    """schemas/ is what assistants without Python read; regenerate it with
    `python -m tutor.schema --export` after changing a model."""
    for name, schema in json_schemas().items():
        assert json.loads((SCHEMA_DIR / name).read_text()) == schema, name


def test_main_exit_codes(tmp_path, capsys):
    good = make_batch(tmp_path / "good", ["word-count"])
    bad = make_batch(tmp_path / "bad", ["word-count"], ["word-count", "tf-idf"])
    assert main([str(good)]) == 0
    assert main([str(good), str(bad)]) == 1
    assert main([]) == 2
    assert main(["--export", str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / "metadata.schema.json").is_file()
    assert "INVALID (1 problems)" in capsys.readouterr().out
