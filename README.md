# mutant-tutor

[![CI](https://github.com/markmak233/mutant-tutor/actions/workflows/ci.yml/badge.svg)](https://github.com/markmak233/mutant-tutor/actions/workflows/ci.yml)

A coding-practice system for a big-data course: programming exercises (PySpark, MRJob) that a
learner solves locally against automated tests, where **every exercise is validated by
deterministic checks before anyone sees it**. No LLM judges whether an exercise is correct.

It started as revision for a big data management course, inspired by its course
materials (none are included here), and is built as an AI engineering portfolio project:
the next phases generate exercises with an LLM (LangGraph generate → validate → repair
loop) and ground them in course material (hybrid RAG).

**Status:** phases 1 and 2a (validation harness, MRJob / DataFrame / Hadoop support) are
complete, and a first batch of hand-checked course exercises is validated. Generation and RAG
are next.

## Why validation comes first

LLM-written exercises fail in quiet ways: a reference solution that is wrong on inputs the
tests never try, tests that an empty skeleton passes, tests too weak to catch a typical bug,
code that only works with one Spark partition. A generator is only as good as the judge that
rejects its bad output, so the judge was built first and is plain Python.

An exercise is valid only if all of these hold:

| Rule | Check | What it catches |
|---|---|---|
| 1 | The reference solution passes the tests | broken reference or broken tests |
| 2 | The skeleton *fails* the tests (fails, not crashes) | tests that check nothing; a skeleton that does not import |
| 3 | Every planted mutant (one deliberate bug each) fails the tests | tests too weak to catch a known mistake |
| 4 | The reference matches a plain-Python oracle on random inputs (hypothesis) | a reference that is only right on the inputs the tests list |
| 5 | The reference passes with 1, 2 and 4 partitions / input files / reducers, and (MRJob) with the combiner off | code that depends on how data is distributed |
| api | Static `ast` check of forbidden / required APIs (e.g. no `groupByKey`) | a solution that passes by breaking the exercise's constraints |

```
$ python -m tutor.validation examples/station-max-file/exercise examples/station-max-file/solution
station-max-file
  ok    1    reference passes    8.4s  4 passed
  ok    2    skeleton fails      3.9s  4 failed
  ok    3    mutants killed     30.3s  4/4 killed
  ok    4    matches oracle     14.8s  1 passed
  ok    5    distribution       16.9s  reference passes with 1, 2, 4 partitions
  ok    api  api constraints     0.0s  reference respects forbidden_calls=['groupByKey'], ...
VALID
report saved to examples/station-max-file/solution/validation.json
```

Each check is itself tested against an exercise built to fail it, e.g. a reference that is
correct below 1000 passes rule 1 and is caught by rule 4 at exactly `a=1000`.

VALID means the exercise passed these checks, not that it is proven correct. Rule 4 compares
against an oracle on random inputs, not all inputs; rule 3 only catches the mutants someone
planted; the api check reads the source and can be dodged by dynamic code. Each rule removes
a known way an exercise goes wrong, and the gaps that remain are listed in
[`docs/design.md`](docs/design.md).

## What an exercise looks like

The learner opens one file: `student.py`, with the question in its docstring and a skeleton
below. `test_student.py` is run, not read; `hints.md` (useful functions with small examples,
then three levels of hints) is opened only when stuck. Everything the validator needs
(reference, skeleton, mutants, oracle, metadata) sits in a separate `solution/` folder.

Two interfaces, mixed in every batch:

- **Function-level**: write `f(rdd) -> rdd`. Algorithm only, quick to start.
- **File-level**: write a program in the shape course assignments use (`run(inputPath, outputPath)`),
  read messy input files, write `part-*` output in an exact format. Ships a `sample_input/`.

Four synthetic examples live in [`examples/`](examples/): `station-average-rdd` (function-level),
`station-max-file` (file-level Spark RDD), `station-monthly-df` (DataFrame, one ordered output
file, a command-line parameter) and `station-condition-share` (MRJob on a local Hadoop:
order inversion, where a mutant that forgets the partitioner passes with one reducer and is
caught with two).

## Quick start

> **Setting up from scratch?** Follow [`docs/setup/bigdata.md`](docs/setup/bigdata.md):
> installing Python, Java and Hadoop, filling in `.env`, checking it works, and common problems.

Requirements: Python 3.11 (tested in CI; 3.12 works after `pip install setuptools`, mrjob
breaks on 3.13), Java 17. Optional: a standalone
Hadoop 3.3 for MRJob exercises that need custom partitioning or sort order.

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[bigdata,dev]"
cp .env.example .env            # set JAVA_HOME; comment out WORKSPACE_DIR / HADOOP_HOME if unused

pytest                          # fast unit tests (~30 s)
pytest -m spark                 # validate every example end to end (~3 min, ~8 min with Hadoop)
python -m tutor.validation examples/station-average-rdd/exercise examples/station-average-rdd/solution
```

Solve an example yourself:

```bash
cd examples/station-average-rdd/exercise
pytest test_student.py          # 7 failed -> write average_by_station in student.py -> 7 passed
TUTOR_PARTITIONS=4 pytest test_student.py
```

## Private course material stays out of the repo

The repository holds code only. Lectures, generated exercises and attempts live in a
per-course **workspace** outside any git repository, found through `WORKSPACE_DIR`; the code
refuses a workspace that sits inside a git repo. A workspace is a folder with a `workspace.toml`
declaring the course and its environment, which `python -m tutor.envcheck` checks:

```toml
course = "bigdata"

[requires]
python = "3.11"
java = "17"
pyspark = "3.5"
mrjob = "0.7"
```

A gitleaks pre-commit hook blocks secrets and absolute home-directory paths
(`pre-commit install` once per clone; needs `gitleaks` installed).

## Intended use

The aim is to help students understand their course topics better: turn the lecture
material you study from into exercises you can run, check and learn from at your own
pace (automatic generation is phase 3; today exercises are drafted with an AI assistant
and checked by the validator). The material and the exercises stay on your own machine;
only LLM calls leave it. This is a personal project, not affiliated with any university or course, and
it is provided as is (see [LICENSE](LICENSE)).

## Running exercise code is not sandboxed

The validator runs exercise code (reference, skeleton, mutants, tests) in a subprocess with a
timeout, in a temporary directory, and kills the whole process group afterwards. That keeps
runs apart from each other and from the workspace, but it is **not a sandbox**: the code runs
with your user's permissions and can read or write other files and use the network. Only
validate exercises you wrote or reviewed. Phase 3 runs LLM-generated code the same way, so
review it first or run the validator in a container or VM.

## Layout

```
src/tutor/
  config.py        find the workspace, refuse unsafe locations, load .env
  envcheck.py      check the machine against the workspace's [requires]
  execution.py     run pytest in a temp dir, subprocess + timeout, kill the whole process group
  validation/      rules 1-5 and the api check; `python -m tutor.validation`
examples/          synthetic exercises (exercise/ for the learner, solution/ for the validator)
tests/             unit tests; `-m spark` for end-to-end runs
docs/design.md     every design decision, with the measurements behind it
```

## Roadmap

Phase numbers match `docs/design.md`.

- **1** ✅ Environment + validation harness, two synthetic examples
- **2a** ✅ Harness extensions: shared test helpers, MRJob (inline and local Hadoop),
  DataFrame, combiner-off check, attempt log, two more synthetic examples
- **2** Golden exercises: a first batch of 10 course exercises (validated; being solved by
  hand to run the full loop)
- **2b** Code-tracing exercises ("what does this print and why")
- **3** LLM generation: LangGraph generate → validate → repair loop, Pydantic-parsed output
- **4** Hybrid RAG over course material (BM25 + vectors), evaluated with recall@k
- **5** Evaluation: validity rate, mutant kill rate, cost per valid exercise, with vs without RAG

## How it is built

Developed with Claude Code as the coding assistant, working in small reviewed steps; the
working rules are in [`CLAUDE.md`](CLAUDE.md) and [`AGENTS.md`](AGENTS.md), and the reasoning
behind each decision, including what was measured and what did not work, is in
[`docs/design.md`](docs/design.md).
