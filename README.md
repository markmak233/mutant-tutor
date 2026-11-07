# mutant-tutor

[![CI](https://github.com/markmak233/mutant-tutor/actions/workflows/ci.yml/badge.svg)](https://github.com/markmak233/mutant-tutor/actions/workflows/ci.yml)

Turn your own course's lectures into programming exercises that you solve in your IDE
against automated tests, where **every exercise is checked by deterministic rules before you
see it**: the reference solution passes its tests, the empty skeleton fails them, every
planted bug (a mutant) is caught, and the reference agrees with an oracle written from the
question alone. No LLM judges whether an exercise is correct.

Everything runs on your machine. A local web GUI takes a course's lectures (PDFs or saved web
pages), its models and its environment; a LangGraph pipeline plans a batch, which you approve,
then generates, validates and repairs each exercise; the exercises land as folders you open
in VS Code. Courses in Python (plain Python, PySpark, MRJob) and, newly, C, its first
batches still to be measured; C++ and Java are planned.

It started as revision for a big data management course (none of its material is included)
and is built as an AI engineering portfolio project.

## What it does

- **Checked before you see it.** Five rules and a static API check, each tested against an
  exercise built to fail it ([how](docs/details.md#why-validation-comes-first)). 48 of 50
  generation runs ended VALID, 28 of them with no repair ([results](docs/details.md#results)).
- **An independent oracle.** A second model writes the oracle from the question alone, never
  seeing the reference, so a disagreement points at an ambiguous question as well as a wrong
  answer.
- **Hints that never show the answer.** `python -m tutor.hint` runs your tests and names a
  known mistake, or the planted mutant your code fails like.
- **Your models.** Any OpenAI-compatible model, local or cloud, one per role (plan, generate,
  repair, oracle, ...); the GUI fills a model's settings from the public
  [models.dev](https://models.dev) list. With no API key, a coding assistant on a
  subscription can answer instead ([assistant mode](docs/assistant-mode.md)).
- **Generated code kept apart.** Each test run can start a fresh Docker container with no
  network and no view of your files ([where code runs](docs/details.md#where-exercise-code-runs)).
- **No retrieval needed, by measurement.** Hybrid RAG, an agent and a concept index were
  compared with sending the whole lecture, which stayed
  ([the comparison](docs/details.md#does-it-need-rag)).

## An exercise

You open `student.py`: the question in its docstring, a skeleton below. You run
`test_student.py`, and open `hints.md` only when stuck. What the validator needs (reference,
mutants, oracle) sits in a separate `solution/` folder. Seven synthetic examples, two of them
in C, are in [`examples/`](examples/).

## Quick start

Requirements: Python 3.11 (tested in CI; mrjob breaks on 3.13) and, for the big-data
examples, Java 17. Setting up from scratch: [`docs/setup/bigdata.md`](docs/setup/bigdata.md).

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[bigdata,dev]"
cp .env.example .env            # set JAVA_HOME; comment out WORKSPACE_DIR / HADOOP_HOME if unused

pytest                          # fast unit tests (~30 s)
python -m tutor.validation examples/station-average-rdd/exercise examples/station-average-rdd/solution
```

The GUI (courses, lectures, models and keys, environment, planning and approving a batch;
generating also needs the `gen` extra):

```bash
pip install -e ".[gui]"
python -m tutor.gui             # every course folder next to WORKSPACE_DIR's, or --root DIR
```

It listens on 127.0.0.1 only, with a token for each launch.

## Intended use

The aim is to help students understand their course topics better: turn the lecture
material you study from into exercises you can run, check and learn from at your own
pace: exercises are generated from the slides and checked by the validator before you
see them. The material and the exercises stay on your own machine, in a per-course workspace
outside any git repository (the repository holds code only); only LLM calls (the slides'
text and images, the generated code, and your code when you ask a model for a hint) leave
it. This is a personal project, not affiliated with any university or course, and it is
provided as is (see [LICENSE](LICENSE)).

## Status and more

Done: validation, generation, held-out checks, hints, the retrieval comparison, the
evaluation, a container per test run, and the local GUI. In progress: a C course (its
exercises are generated and validated, with sanitizers, in a container; the learner's pages
and hints come next); then packaging and release.

- [`docs/details.md`](docs/details.md): how validation and generation work, the results, the
  RAG comparison, where exercise code runs, the layout and the roadmap
- [`docs/design.md`](docs/design.md): every design decision, with the measurements behind it
- [`docs/assistant-mode.md`](docs/assistant-mode.md): a coding assistant instead of an API

Developed with Claude Code as the coding assistant, in small reviewed steps; the working
rules are in [`CLAUDE.md`](CLAUDE.md) and [`AGENTS.md`](AGENTS.md).
