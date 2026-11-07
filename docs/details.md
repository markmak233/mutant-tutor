# mutant-tutor in detail

The [README](../README.md) says what this is and how to start. This page is the rest: how an
exercise is checked and generated, the measured results, the retrieval comparison, where
exercise code runs, the layout and the roadmap. Every decision, with the measurements behind
it, is in [`design.md`](design.md).

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
[`docs/design.md`](design.md).

## What an exercise looks like

The learner opens one file: `student.py`, with the question in its docstring and a skeleton
below. `test_student.py` is run, not read; `hints.md` (useful functions with small examples,
then three levels of hints) is opened only when stuck. Everything the validator needs
(reference, skeleton, mutants, oracle, metadata) sits in a separate `solution/` folder.

Two interfaces, mixed in every batch:

- **Function-level**: write `f(rdd) -> rdd`. Algorithm only, quick to start.
- **File-level**: write a program in the shape course assignments use (`run(inputPath, outputPath)`),
  read messy input files, write `part-*` output in an exact format. Ships a `sample_input/`.

Seven synthetic examples live in [`examples/`](../examples/): `station-longest-rise` (plain Python,
no framework), `station-average-rdd` (function-level), `station-max-file` (file-level Spark RDD),
`station-monthly-df` (DataFrame, one ordered output file, a command-line parameter),
`station-condition-share` (MRJob on a local Hadoop: order inversion, where a mutant that forgets
the partitioner passes with one reducer and is caught with two), and two in C:
`station-above-average-c` (a whole program on standard input) and `station-drop-low-c` (a
function called by a given `driver.c`). C tests stay pytest: they compile the code with
AddressSanitizer and UndefinedBehaviorSanitizer and run the program, so a mutant whose array
is too small, or whose loop reads one past the end, is caught by the sanitizer even when its
output happens to be right. C is validated only in a container (`pytest -m docker` runs these
two).

## How exercises are generated

```
plan (reads the course's coverage table) -> the owner approves or sends it back
  -> for each exercise, in parallel:
     generate -> oracle (a second model) -> validate --VALID--> save
        ^                                     |
        +------------ repair <---- failed ----+   (up to 3 rounds, then discard)
```

The validator above is the only judge; the models only write. What makes the loop work,
each added after a measured failure (details and numbers in [`docs/design.md`](design.md)):

- **An independent oracle.** The generating model writes `oracle.py` only as an interface;
  a model from another provider fills it in from the question alone, never seeing the
  reference. Rule 4 then compares two independent readings of the question, and a
  disagreement points at an ambiguous question, not only at a wrong reference. It has
  caught real bugs (a reference that crashed on empty input) that a same-model oracle shared.
- **Repair that can see and decide.** Repair gets pytest's failing assertions, not just a
  count; writes a `diagnosis` before any file; and, when the reference fails a test, is told
  whether the reference agrees with the oracle, i.e. which side is likely wrong. A cheap
  model drafts; a larger one with thinking takes over from the second repair.
- **Small fixes in code, not prompts.** A mutant that cannot be applied is dropped while
  three remain; a whole oracle is cut to an interface; error messages say what to change.
- **Cost.** Prompts are ordered so the provider's cache serves the shared part (system
  prompt, course pages, example exercise): 54-93% of input tokens per batch.

Each role (plan, generate, repair, oracle, and vision for reading slides) is a profile in
the workspace's `workspace.toml`, so any OpenAI-compatible models can be combined, local or
cloud. The oracle is best another provider's, by another maker, than the models that
write the exercise; with one key, one provider still works, with a warning that its check is
less independent. In the GUI, New model can fill the form from the public
[models.dev](https://models.dev) catalog (base URL, key name, models newest first, prices),
downloaded only when you click. With no API key at all, a coding assistant on a subscription
(Claude Code, Codex) can answer every call as request files: see
[`docs/assistant-mode.md`](assistant-mode.md).

Measured on one week of stream algorithms (11 exercises, the same plan for every setup, all
11 saved by each): with thinking off, DeepSeek-V4-Flash's first draft was VALID for 2 of 11,
the rest needed one or two repairs; with thinking on, 7 of 11, close to V4-Pro's 8 of 11 in
about 60% of its time (3 min against 5 per exercise). The difference is reasoning, not model
size: most failed first drafts had expected test values traced wrongly by hand. GLM-5.3 as
the oracle wrote 19 of 19 usable oracles in 9 s each. Samples are small; the full table is in
[`docs/design.md`](design.md) ("Week 7 with five arms").

## Does it need RAG?

Not for generating exercises from this course, so retrieval is not wired in. The course is small
(571 pages, about 71k tokens; a lecture 4-11k), and a batch's plan already names each exercise's
pages. The deciding test generated 12 real plan items from each context: the whole lecture, only
the pages labelled for the item (perfect retrieval), and what each of three routes found within
2000 tokens.

| | Whole lecture | Labelled pages | Hybrid + neighbours | Concept index | Agent |
|---|---|---|---|---|---|
| Saved after repair | 12 | 12 | 12 | 12 | 12 |
| VALID at the first try | 8 | 7 | 4 | 8 | 4 |
| Judge finds fully fine | 7 | 5 | 3 | 3 | 5 |
| Serious flags | 5 | 10 | 12 | 10 | 11 |
| Tokens, % of the whole lecture | 100 | 92 | 124 | 86 | 111 |

No route met the rule fixed before the run, and no exercise was fully fine from a route and not
from the whole lecture. Less context did not reliably save money either: more first drafts were cut
off, and repairs cost more than the smaller prompts saved. 15 of the judge's flags, checked by hand
with the arms hidden, were all real. 12 items, each run once, and a judge that reads the whole
lecture in every arm show a direction and not a proof.

The routes themselves find the right pages. Recall within 2000 tokens, under a third of a lecture,
on 53 test queries held back from tuning, each route run once:

| Route | Searching the lecture | Searching the course | Model tokens per query |
|---|---|---|---|
| BM25 | 0.88 | 0.80 | none |
| Vectors | 0.87 | 0.81 | none (embeddings: $0.002, once) |
| Hybrid | 0.89 | 0.85 | none |
| Hybrid + neighbouring pages | 0.93 | 0.90 | none |
| Concept index | 0.88 | 0.89 | 1.6k / 15k |
| Agent (with thinking across the course) | 0.87 | 0.82 | 3.9k / 6.6k |

The routes with a model find no more than those without. The labels are consecutive page ranges,
which may favour adding neighbours, and they were not judged again. Questions spanning several
lectures are harder: on 13 of them the best route, hybrid + neighbours, found 0.64 of the labelled
pages within 4000 tokens. Generation never asks those. These results hold for this course only. The code stays in `src/tutor/retrieval/` for what was not tested, such as hints or a
course with no labels; details are in [`docs/design.md`](design.md) ("RAG").

## Results

From the pipeline's own logs on two courses, with no extra model calls. `python -m tutor.eval report`
writes a private report for each workspace, and every recorded test run rebuilds it. This section is
updated by hand from those reports, last on 2026-11-05.

| Measure | Result |
|---|---|
| VALID with no repair round | 28 of 50 generation runs (56%) |
| Saved as VALID, after up to 3 repair rounds | 48 of 50 (96%) |
| Tokens per saved exercise: generate, repair and oracle, failed runs included | 83k |
| Mutants killed in first drafts | 102 of 103, over 22 drafts |
| Exercises flagged by a held-out check | 8 of 38 |

A 51st run stopped on an error before the validator had its say, so it is not counted.

| What stopped the first draft | Runs |
|---|---|
| Nothing | 28 |
| A reply that did not parse or could not be written out as files | 13 |
| Rule 1: the reference failed its own tests | 6 |
| Rule 3: a mutant survived | 1 |
| Rule 4: the reference disagreed with the oracle | 1 |
| The static safety check | 1 |

| Generating model | Runs | VALID with no repair | Saved | Tokens per saved |
|---|---|---|---|---|
| DeepSeek-V4-Flash (thinking on for 35) | 42 | 25 of 42 | 42 of 42 | 86k |
| GLM-5.3-Flash | 3 | 0 of 3 | 1 of 3 | 216k |
| GPT-6.1-Terra | 5, plus the error | 3 of 5 | 5 of 5 | 31k |

What these numbers cannot show:

- **They are no controlled experiment.** Batches differ in lecture, model and settings, and the last
  two models ran on one week only, so the model table is a lead and no more. Lecture length alone moves
  the cost: 65k tokens per exercise on one course, 143k on the other with its long lecture (a reading of
  the logs, not a test). The full model comparison was not run, as 12 items separate only large
  differences and [the week 7 arms](design.md) had already chosen a generator.
- **The mutant count proves little.** Rule 3 passes on every saved exercise by construction, and the
  same model writes the tests and the mutants.
- **A held-out flag is a finding to read, not a proven defect.** The checks ran on one course only:
  4 of 24 generated exercises and 4 of 14 written by hand.
- **The judge is noisy.** On the course it is calibrated on, it raised a serious point on 14 of 35
  generated exercises and 5 of 14 written by hand, but a plain rerun changed its letters on 3 of 13. On
  the other course, where it is not calibrated, none of the 11 has one now: it raised one, which was real,
  and the question was fixed. That zero means little.
- **The ratings come from one learner,** on the 24 exercises they solved. "Clear" got 14 of 19 generated
  and 5 of 5 written by hand; fits the material, within what was taught and targets the concept got all
  24.

## Where exercise code runs

Validation, generation, held-out checks and hints all run exercise code (reference, skeleton,
mutants, tests), much of it written by a model. Each course chooses where, in its
`workspace.toml`:

```toml
[isolation]
runner = "container"   # or "host"
```

- **`container`**: every test run starts a fresh Docker container from the course's image,
  which `python -m tutor.container build` makes from `[requires]`. The container has no
  network, a read-only system, a non-root user, no view of your home folder or `.env`, and
  limits on memory, processes, CPUs and time; only the run's own throwaway folder is writable.
  If Docker is not running or the image is missing, the command stops with an error and writes
  no verdict; it never falls back to the host. `python -m tutor.container status` says whether
  the image is ready. Validation takes about as long as on the host (measured in
  [design.md](design.md), "Code execution safety").
- **`host`**: a subprocess with a timeout, in a temporary directory, its whole process group
  killed afterwards. That keeps runs apart from each other and from the workspace, but it is
  **not a sandbox**: the code runs with your user's permissions and can read or write other
  files and use the network. Use it only for exercises you wrote or reviewed.

The GUI's Environment page sets this for a course, and builds its image after showing the
recipe and what it downloads. `python -m tutor.workspace init DIR`, run again after changing
`[requires]`, installs its pip packages into the course's own `.venv`.

A course that has not chosen runs on the host and prints a warning. Your own `pytest` runs
while solving stay on the host either way. The container is tested on macOS with Docker
Desktop; Linux and Windows are not tested yet, so use the host there.

Before anything runs, in either mode, a static check reads every exercise file and rejects
code outside an allowlist (modules, `os` / `sys` attributes, `eval` / `exec`, paths outside
the exercise, shell commands through Spark or MRJob). It stops the obvious, but Python is too
dynamic for a static check to prove code safe; the container is the boundary.

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

To manage your courses in the browser (progress, questions, open an exercise in VS Code,
validate a batch in the background, upload and read lectures, create a course, choose its
models and keep your API keys, check its environment, edit `[requires]` or have a model
draft it from the lectures (each entry with the slide that shows it, added only when you
tick it), choose where tests run, install its Python packages and build its test image, have
a model draft `coverage.md` from the lectures and add new lectures to it as they come, plan
a new batch and approve its plan; generating also needs the `gen` extra):

```bash
pip install -e ".[gui]"
python -m tutor.gui             # every course folder next to WORKSPACE_DIR's, or --root DIR
```

It listens on 127.0.0.1 only and opens a link with a token for this launch; other pages
in the browser cannot use it.

A gitleaks pre-commit hook blocks secrets and absolute home-directory paths
(`pre-commit install` once per clone; needs `gitleaks` installed).

## Solving an example, and hints

Solve an example yourself:

```bash
cd examples/station-average-rdd/exercise
pytest test_student.py          # 7 failed -> write average_by_station in student.py -> 7 passed
TUTOR_PARTITIONS=4 pytest test_student.py
cd ../../.. && python -m tutor.hint examples/station-average-rdd/exercise   # when stuck
```

The hint command runs your tests in a temporary copy, then names a known low-level mistake
(a TODO left, full-width punctuation, an RDD used like a list, output as tuples, ...) or,
when your tests fail exactly as one of the exercise's planted mutants does, that mistake;
it never shows the reference.

## Layout

```
src/tutor/
  config.py        find the workspace, refuse unsafe locations, load .env
  envcheck.py      check the machine against the workspace's [requires]
  workspace.py     create a course workspace: folder, its own venv, terminal activation
  execution.py     run pytest in a temp dir with a timeout: in a fresh container if the course
                   asks for one, else a subprocess whose whole process group is killed
  container.py     the course's Docker image from [requires] and the limits each run gets;
                   `python -m tutor.container build | status`
  llm.py           a small client for OpenAI-compatible APIs (LM Studio, GLM, OpenAI,
                   DeepSeek): roles -> profiles, structured output, streaming, call log
  materials/       course PDFs and saved web pages -> a per-page (per-section) markdown cache,
                   image slides via a vision model;
                   `python -m tutor.materials`; `python -m tutor.materials.requires` drafts
                   [requires] from the cached lectures, and `.coverage` coverage.md
  graph/           phase 3 generation with LangGraph: plan -> owner approves -> generate,
                   oracle by a second model, validate, repair, per exercise in parallel;
                   `python -m tutor.graph`
  hint/            hints while solving: rules, then the exercise's known mistakes (its
                   mutants), then a model's hint checked for giving code away; never the
                   reference; `python -m tutor.hint`
  retrieval/       the routes compared in phase 4 and their scores; generation does not use
                   them; `python -m tutor.retrieval`
  eval/            a private report from the pipeline's own records, no model calls, rebuilt
                   after each recorded test run; `python -m tutor.eval`
  gui/             the local web GUI (Flask, server-rendered pages, no npm), reading the same
                   files; `python -m tutor.gui`
  schema.py        the batch format (metadata.json, manifest.json); `python -m tutor.schema`
  validation/      rule 0 (format), a static safety check, rules 1-5 and the api check;
                   `python -m tutor.validation`
schemas/           the same format as JSON Schema files, exported from schema.py
examples/          synthetic exercises (exercise/ for the learner, solution/ for the validator)
tests/             unit tests; `-m spark` for end-to-end runs
docs/design.md     every design decision, with the measurements behind it
```

## Roadmap

Phase numbers match `docs/design.md`.

- **1** ✅ Environment + validation harness, two synthetic examples
- **2a** ✅ Harness extensions: shared test helpers, MRJob (inline and local Hadoop),
  DataFrame, combiner-off and reversed-value-order checks, attempt log, two more synthetic
  examples
- **2** ✅ Golden exercises: a first batch of 10 course exercises, validated, its must
  exercises solved by hand and rated to run the full loop
- **3a** ✅ LLM generation: static check on generated code, slide reading with a cached
  text layer and vision transcription, LangGraph plan → owner approval → generate → oracle
  by a second model → validate → repair, exercises in parallel, three course weeks generated
  (the container for running generated code came after phase 5, see below)
- **3b** ✅ Checks that need a learner's real work: held-out solutions and mistakes, an LLM
  judge of teaching quality calibrated against the learner's ratings
- **3c** ✅ Hint command: rules, mutant matching and a model's hint behind a leak check; a
  judge of hint quality calibrated on simulated students
- **4** ✅ Retrieval compared by measurement: hybrid RAG (BM25 + vectors), agentic search and a
  concept index against the whole lecture, by recall within a token budget and by the exercises
  each one yields; none beat it, so [the whole lecture stayed](#does-it-need-rag)
- **5** ✅ Evaluation from the pipeline's own logs, [results above](#results): validity, what stops
  a first draft, held-out flags, tokens per saved exercise; the full model comparison was not run
- ✅ After 5: [a container for every test run](#where-exercise-code-runs), one fresh Docker
  container per run, chosen per course; every saved exercise validated in it as on the host
- **6** Second course: another language or framework, to prove nothing is shaped around one
  course. In progress: a C course (its lectures already read as web pages); generating and
  validating C exercises is built (pytest drives the compiled program, with sanitizers, in a
  container; one prompt template filled per language), and so is solving them (the
  exercise page, attempts, the report, hints with C rules from the compiler and the
  sanitizers, held-out checks of the learner's own code); the first real batch, 5 exercises,
  came out 5 of 5 VALID, 4 of them after one repair round; then C++ and Java, each when a
  course needs it (the order is in design.md, "Languages in the plan")
- **7** ✅ Local GUI, built ahead of 6: a web app on the CLI's functions, localhost only;
  solving stays in the IDE. The pages, Open in VS Code, background tasks, generation with
  the plan approved in the page, lectures (PDFs, web pages, a whole folder to tick from), a
  new course, models (filled from models.dev) and keys, the environment page with the test
  image, coverage. Next: packaging and release: one install line, one click to start, CI on
  macOS and Windows

Maybe: adaptive review, spaced `review` batches for the weak knowledge points in the
attempt log (batches are started by hand for now).

Decided, to weigh again after phases 4 and 5:
- **2b** Code-tracing exercises ("what does this print and why") and design exercises
- Algorithm-quality warnings, with 2b's design exercises (e.g. how much a combiner cut),
  reported, never failing an exercise
- `v0.1.0`: pinned dependency versions, a changelog and a tag, once the GUI or the whole
  backend is done
