# Design Decisions

Living record of project decisions. Update it when a decision changes; note the reason.

## Goals

1. Primary: portfolio project demonstrating LangGraph, RAG and LLM evaluation with real engineering judgment.
2. Secondary: revision for a big data management course. The owner solves the generated exercises; AI never writes the answers.

## Roles of AI tools

- Claude Code is the development assistant for building this system. It is not part of the runtime.
- The system itself calls an LLM API (provider not yet decided).
- The first exercises (golden set) are drafted by Claude Code in chat from the materials the owner
  picks, checked by the validator, then solved by the owner (decided 2026-10-24; replaces web chat
  models, because Claude Code can run the validator while drafting).
- In phases 1-2, Claude Code also acts as the tutor: the owner solves, runs the tests, and asks in
  chat when stuck. Rules live in CLAUDE.md ("Tutoring"): read the code and run the tests first,
  explain the first real error and its concept, direction before code, never touch `student.py`
  or quote `solution/`. Mistakes seen this way feed debugging exercises and mutants. A hint
  command inside the system follows phase 3 (roadmap 3b).

## Environment

- Requirements are per course, because courses differ (the big data course needs Java + Spark, others may not).
  Each workspace declares them in `workspace.toml` (`course` plus a `[requires]` table);
  the repo only holds the generic checker `tutor/envcheck.py`, so it stays course-agnostic.
- Check, don't install. `python -m tutor.envcheck` checks the current workspace, and it also runs
  once before validating a batch (not per exercise: importing pyspark alone takes a second or two).
  `tests/test_env.py` runs the same checker.
- Installation: Python deps as per-course extras, `pip install -e ".[bigdata]"`; system deps (Java,
  Hadoop) in a per-course guide `docs/setup/<course>.md`, linked from README (decided 2026-10-25:
  README stays course-agnostic, and each course's system deps and pitfalls differ; the guide
  marks which platforms were actually tested). No install script: setup is one-off, and when it breaks,
  asking an AI is faster than maintaining a cross-platform script.
- Big data course (`bigdata`): Python 3.11, Java 17 (`JAVA_HOME`), PySpark 3.5.x (decided 2026-10-24: the slides
  name both 3.5.9 and 4.2.0, and the version is pinned per workspace in `workspace.toml`
  anyway, so another course's materials can choose differently). Spark 4.x enables ANSI SQL mode by default,
  which changes DataFrame/SQL error behaviour (casts, overflow, divide by zero); RDD API is unaffected.
  mrjob is no longer actively maintained: works on 3.11, do not move past 3.12.
  (Confirmed: on Python 3.13 mrjob tasks fail with `No module named 'pipes'`.)
- Big data course Hadoop: 3.3.6 standalone (same 3.3 line as the course VM's 3.3.2), Apache tarball
  unpacked to `~/opt/hadoop-3.3.6`, located via `HADOOP_HOME` in `.env`. Not `brew install hadoop`:
  its `yarn` binary clashes with the JavaScript `yarn`. Runs on Java 17, so one JDK serves
  both Spark and Hadoop. Hadoop launches task scripts with the `python3` on `PATH`, so jobs must
  pass `--python-bin` pointing at the project's Python 3.11.

## Public repo vs private data

- `tutor/` is the public repo and holds code only. Private material lives in per-course workspaces
  under a sibling `smartcodereview-workspace/` directory (lectures, generated batches, attempts), located only via
  env var `WORKSPACE_DIR`. `LECTURE_DIR` is an optional override, default `$WORKSPACE_DIR/lectures`.
  Switching course = pointing `WORKSPACE_DIR` at another workspace.
- `config.py` refuses a `WORKSPACE_DIR` that has no `workspace.toml` (wrong directory) or that sits
  inside any git repository (same check for `LECTURE_DIR`). This is stricter than relying on `.gitignore`.
- `.env` is read from the current directory by a ~10-line stdlib loader (no `python-dotenv`);
  variables already exported in the shell win. Run commands from the repo root.
- Public demo corpus for RAG: Apache Spark documentation (Apache 2.0). Same code, different data source.
- Exercises generated from lectures are private by default; only hand-written synthetic examples go in `examples/`.
- Safety net: pre-commit + gitleaks, `.claude/settings.json` deny rules, project-only API key with usage limit.
  Remember `.gitignore` does not remove files already in git history.
- gitleaks runs as a pre-commit `local` / `system` hook on staged changes, using the brew-installed
  binary (no Go build). `.gitleaks.toml` extends the default rules with `home-directory-path`
  (`/Users/<name>/`, `/home/<name>/`), since default rules only know secrets. History was scanned
  clean on 2026-10-24; rescan with `gitleaks git .` after any history-sensitive change.
  `.env.example` is marked `skip-worktree` locally so filled-in values never get staged.

## Repository layout

Directories and dependencies are added only in the phase that needs them; no empty placeholders.
LangChain / LangGraph do not appear before phase 3.

```
smartCodingReview/                       # public repo (the `tutor/` above), code only
├── AGENTS.md
├── CLAUDE.md
├── README.md
├── .claude/settings.json
├── docs/design.md
├── docs/setup/bigdata.md                # per-course install guide (system deps, .env, pitfalls)
├── pyproject.toml                       # P1, per-course extras e.g. [bigdata]
├── .gitignore                           # P1, includes *workspace*/ as a backstop
├── .env.example                         # P1, WORKSPACE_DIR=..., HADOOP_HOME=...
├── .pre-commit-config.yaml              # P1, gitleaks
├── src/tutor/
│   ├── __init__.py
│   ├── config.py                        # P1 reads WORKSPACE_DIR, derives subpaths
│   ├── envcheck.py                      # P1 checks the workspace's [requires]
│   ├── execution.py                     # P1 copy to temp dir -> subprocess pytest -> delete
│   ├── validation/                      # P1 the five checks (internals designed before coding)
│   ├── testkit.py                       # P2a run a file-level program, read part-*, compare output
│   ├── attempts.py                      # P2a pytest plugin: one attempts.jsonl line per exercise run
│   ├── schema.py                        # P3 Pydantic exercise models
│   ├── llm.py                           # P3 thin LLM API client, no LangChain
│   ├── graph/                           # P3 langgraph dependency added here
│   ├── rag/                             # P4
│   └── eval/                            # P5
├── examples/                            # P1 synthetic only, same shape as a real exercise
│   ├── station-average-rdd/             # function-level: per-key average in PySpark RDD
│   │   ├── exercise/
│   │   └── solution/
│   ├── station-max-file/                # file-level: CSV input dir with messy lines -> part-* output
│   ├── station-monthly-df/              # DataFrame: groupBy/agg, orderBy, one CSV file, parameter
│   └── station-condition-share/         # MRJob on Hadoop: order inversion, --jobconf tau
└── tests/
    ├── test_env.py                      # P1, runs envcheck for the current workspace
    └── test_validation.py               # P1, runs the validator on examples/
```

`validation/` stays pure Python so the phase 3 `validate` node calls it unchanged.

## Workspace layout (private, never in git)

```
smartcodereview-workspace/
└── bigdata/                             # WORKSPACE_DIR points here; one workspace per course
    ├── workspace.toml                   # course name + [requires]; marks this as a workspace
    ├── coverage.md                      # knowledge points per lecture -> exercise / design / not yet
    ├── lectures/                        # PDFs, read-only
    ├── coding_materials/                # labs and projects as on Ed, read-only
    │   ├── lab4/                        # one flat folder each: problem text, data, expected outputs
    │   └── proj2/
    ├── batches/                         # one directory per generation run
    │   ├── 2026-10-27_p2-golden/
    │   │   ├── manifest.json            # phase, date, mode, sources, method, model, git commit, exercises
    │   │   ├── exercises/               # the only place the owner looks while solving
    │   │   │   └── <exercise-id>/
    │   │   │       ├── student.py       # the one file to open: question in the module docstring,
    │   │   │       │                    #   skeleton below; owner writes the answer here
    │   │   │       ├── test_student.py  # run it, no need to read it
    │   │   │       ├── sample_input/    # file-level only: the docstring example as input files
    │   │   │       └── hints.md         # three levels; separate so it is opened only when stuck
    │   │   └── solutions/               # read by the validator only
    │   │       └── <exercise-id>/
    │   │           ├── metadata.json    # topic, type, difficulty, forbidden calls, source
    │   │           ├── skeleton.py      # pristine skeleton, so re-validation survives edits to student.py
    │   │           ├── reference.py
    │   │           ├── oracle.py
    │   │           ├── test_oracle.py   # rule 4: hypothesis, reference vs oracle on random inputs
    │   │           ├── mutants/
    │   │           ├── pseudocode.md    # MRJob exercises: same solution in exam pseudocode
    │   │           └── validation.json  # result of the five checks
    │   ├── 2026-11-03_p3-graph/
    │   │   └── discarded/               # also: exercises still failing after 3 repairs + log
    │   └── 2026-11-10_p4-rag-2/         # second run on the same day
    ├── attempts/attempts.jsonl          # one line per test run: batch, exercise, time, pass/fail, errors
    ├── index/                           # P4 BM25 / vector index, rebuildable
    └── runs/<date>_<label>/             # P5 evaluation outputs
```

- Batch name: `YYYY-MM-DD_p<phase>-<label>`, suffix `-2`, `-3` for repeat runs on the same day.
- The phase in the name is a hint only; `manifest.json` records the git commit that produced the batch.
- Material selection (decided 2026-10-24): each batch is generated from materials the owner picks,
  e.g. one week while learning it, or several weeks mixed for review. `manifest.json` `sources`
  lists them (workspace-relative paths, optional page ranges); each exercise's metadata `source`
  narrows it to pages. The label usually names the week: `2026-10-25_p2-w2-mapreduce`.
- Generation takes exactly two inputs, a source list and a mode; any selection UI (terminal
  checklist, tkinter, a local web page) only produces those, so the UI can change without
  touching generation. No UI in phase 2: the owner names the materials in chat.
- An exercise is identified by `<batch>/<exercise-id>`; the same id may recur across batches.

## Course coding materials

Decided 2026-10-24 after reading Labs 4-5 and the two course projects.
- `coding_materials/<labN|projN>/` mirrors Ed: one flat folder per lab or project, no subfolders.
  File names are arbitrary (`2.txt` is a problem statement, `pg100.txt` a book used as data), so
  sorting files into `spec/` / `data/` folders by hand would not last. Copied: problem text (Ed
  pages saved as text), data (CSV, book text), course-provided expected outputs. The owner's own
  `.py` files are usually not copied.
- Readers classify files by rule instead of by folder:
  - never read: archives (`.zip`), SSH keys and `.ssh/`, `.venv/`, `__pycache__/`, hidden files,
    output directories (`_SUCCESS`, `part-*`). Course folders can hold a zipped VM home directory
    with SSH keys; these never enter the workspace, an index or an LLM prompt;
  - data, not indexed as text: `.csv`, and text files over about 200 KB;
  - text: other `.txt`, `.md`, `.py`.
- Ed text copies carry UI noise ("Spaces: 4 (Auto)", "Terminal") and lose formulas (images
  vanish, inline math comes out one character per line); phase 4 parsing has to clean this.
- A course expected output and the owner's own run look the same on disk, so which is which is
  asked when an exercise needs it.
- Labs and projects are references for what course tasks look like (input formats, parameters,
  output rules, marking points); the owner has already submitted the projects.

## Exercise validation (deterministic, never LLM-judged)

An exercise is valid only if:

1. `reference.py` passes all tests.
2. `skeleton.py` (the pristine skeleton) fails.
3. Every mutant in `mutants/` fails.
4. Reference output matches `oracle.py` (plain Python) on random inputs (differential testing, `hypothesis`).
5. Results do not depend on how the work is distributed. Implemented per framework:
   - PySpark RDD / DataFrame: identical across partition counts 1, 2, 4 (phase 1).
     Catches e.g. a wrong `combOp` in `aggregateByKey`, which never runs with one partition.
   - MRJob: identical with the combiner on and off, since Hadoop may run it 0, 1 or many times.
     The lecture uses the same test ("check the correctness by removing the combiner", L2.2
     p.38). Exercises on the `hadoop` runner also pass with 1, 2 and 4 reducers (see
     "Interface extensions").
   Done 2026-10-25 as one rule, "distribution", switched on per exercise in metadata:
   `partition_check` reruns the reference at 1 and 4 partitions / input files / reducers;
   `combiner_check` (MRJob, inline runner) reruns it with `TUTOR_COMBINER=off`, under which
   `testkit.run_mrjob` runs a subclass whose `MRStep`s have no combiner functions (explicit
   `steps()` included). Not on `hadoop`: the task processes run the student's own script, so a
   combiner removed in the test process would still run there; reducer counts cover those
   exercises. Tests may also call `run_mrjob(..., combiner=False)` themselves, so a student's
   reducer that relies on the combiner (the lecture's "mean v1") fails their own tests, not
   only the reference check. Neither flag: "n/a".
   - Plain Python: not applicable.

Rules 1-3 (phase 1f) live in `src/tutor/validation/`: `exercise.py` finds the files from an
exercise dir + solution dir (examples and workspace batches name them differently), `checks.py`
runs the exercise's own test file with reference, skeleton and each mutant copied in as
`student.py`, `__main__.py` is `python -m tutor.validation <exercise_dir> <solution_dir>`.
- Rule 2 needs FAILED: a skeleton that passes means the tests check nothing; one that ERRORs
  never ran a test. Rule 3 names surviving mutants (tests miss that bug) and mutants that
  ERROR (useless as mutants); a TIMEOUT counts as killed; zero mutants fails the rule.
- If rule 1 fails, rules 2-3 are SKIPPED: with a broken reference they would prove nothing.
- Runs are sequential (each starts a JVM). Measured 2026-10-24 for rules 1-3:
  `station-average-rdd` about 19 s, `station-max-file` about 45 s.

Rule 5 and the API check (phase 1g):
- Every validation run sets `TUTOR_PARTITIONS` explicitly (`run_pytest(..., env=...)`), 2 by
  default. Rule 5 (only if metadata has `"partition_check": true`, otherwise "n/a", which does
  not affect validity) reruns the reference at 1 and 4; 2 is already covered by rule 1.
- Rule 3 runs mutants at metadata `mutant_partitions`, default `[2]`; a mutant counts as killed
  if it fails at any listed count. Most exercises keep the default; one or two per batch can
  ask for `[2, 4]` to require bugs that only show at 4 partitions.
- `validation/api.py` checks the reference against metadata `constraints` with `ast`:
  `forbidden_calls` (any attribute access counts, so `g = rdd.groupByKey; g()` is caught),
  `forbidden_imports` (submodules included; `from pyspark import sql` counts), and
  `required_definitions`. Unknown keys are an error, so a typo cannot silently check nothing.
  Bare names count too when the code does not bind them itself (`f = sorted; f(x)`), and a
  keyword only defines a name inside `MRStep(...)` with a real value (`dict(combiner=None)` does
  not); both gaps were found by a Codex review on 2026-10-25. Still a static aid, not a proof:
  `getattr` with a built string, `exec` and the like escape it. The same function can later
  check student answers. It runs even when rule 1 fails, since it does not depend on the tests.
- Measured 2026-10-24 for rules 1-3, 5 and api: `station-average-rdd` about 29 s,
  `station-max-file` about 61 s.

Rule 4 (phase 1g2): each exercise ships `solution/test_oracle.py`, a hypothesis test that imports
`student` (the reference is copied in under that name) and `oracle`, and compares them on random
inputs, with the partition count / number of input files also drawn at random. The validator
runs it like any other test file, minus `test_student.py` (`ExerciseFiles.differential_files()`),
so it still does not care whether an exercise is function-level or file-level. On failure the
report quotes hypothesis' "Failing test case". Missing oracle files fail the rule. The point of
rule 4: a reference that is right on every input the tests list but wrong elsewhere passes
rule 1 and is caught here (unit test: correct below 1000, caught at exactly `a=1000`).
Checked that it bites: the average-of-averages mutant is caught with the shrunk input
`[('s1', 0.0), ('s1', 0.0), ('s1', 1.0)]` (0.5 instead of 0.333).
Full validation (all rules), measured 2026-10-24: `station-average-rdd` about 36 s,
`station-max-file` about 74 s.
- Plain `pytest` excludes tests marked `spark`; `pytest -m spark` validates every example end
  to end and runs the Hadoop testkit tests (about 7.5 min, 4 of them for the Hadoop example).
  Run it whenever validation, testkit or an example changes.

Distributed output is compared by the modes under "Interface extensions" (multiset by default).
API constraints checked statically with `ast`, both forbidden calls (e.g. `groupByKey`, `df.rdd`;
the course itself bans `df.rdd` in the DataFrame project, and `forbidden_calls` already catches
it because any attribute access counts) and required ones (e.g. `mapper_init` / `mapper_final`
for in-mapper combining).

mrjob local runners (`-r inline`, `-r local`) ignore `mapreduce.job.reduces`, the key-field
partitioner and the key comparator: they sort all map output as text and split it into reducer
tasks by size (verified 2026-10-24 with mrjob 0.7.4, and in `mrjob/sim.py`). So patterns that rely
on partitioning, custom sort order or reducer state across keys (order inversion, secondary sort)
cannot be validated with these runners.

Those exercises are validated with `-r hadoop` against the local standalone Hadoop instead
(verified 2026-10-24, Hadoop 3.3.6: order inversion with 2 reducers and secondary sort with
`-k2,2nr` both correct, about 8-9 s per job; the inline runner stays the default for every other
MRJob exercise because it is much faster). Mutants for these patterns: in mrjob,
`SORT_VALUES = True` is what switches on `KeyFieldBasedPartitioner` with `-k1,1`, and the
`mapreduce.map.output.key.field.separator` line decides what "field 1" is. Removing either one
was caught (59 of 123 output lines wrong). Removing `keypartitioner.options` was not caught,
because `SORT_VALUES` already sets it, so it is not a valid mutant.

Exercise types validated this way: implementation, completion, debugging (the skeleton is the buggy code).
Code tracing / output prediction needs a different flow (run the snippet repeatedly and across
partitions to prove the output is stable, then compare normalised answers) and is deferred.

Exercise interfaces. Labs and projects are mostly "read input files, write an output directory",
and projects lose marks on parsing and output format, which a function-level exercise skips.
So there are two interfaces, and generated batches include both:
- Function-level: the student writes `f(rdd) -> rdd` (or DataFrame / plain Python). Algorithm
  only, quick to start; good for a single concept such as partitioning or combining.
- File-level: the student writes a program in the course's shape: Spark
  `run(inputPath, outputPath)` + `sys.argv` (L5 standalone code), MRJob a full job class run on
  input files. Practises parsing (blank, malformed, padded lines), output format and output dirs.
  Tests write input files into a temp dir, call the program, read every `part-*` file of the
  output and compare lines exactly (format matters: `s1,20.0` is not `('s1', 20.0)`, cf. the
  `saveAsTextFile` parentheses trap in L5) but as a multiset, since partition order is not
  fixed. The question pins down number formatting so exact comparison is fair.
Rule 5 stays one mechanism for both: the validator sets `TUTOR_PARTITIONS`; function-level tests
use it as `numSlices`, file-level tests split the input into that many files (Spark makes at least
one partition per file; MRJob gives each file its own map task), which also mirrors an HDFS
input directory. The validator itself does not care which interface an exercise uses.

Measured on `station-max-file` (2026-10-24):
- `sc.textFile` asks for at least 2 partitions, so even a single small input file is split in
  two; the per-partition-max mutant is caught at 1, 2 and 4 files. "N files" means at least N
  partitions, not exactly N.
- File-level programs create and stop their own SparkContext, so each test pays the start-up:
  about 8-9 s for 4 tests. Keep file-level tests few and data-rich.
- A program that crashes before `sc.stop()` leaves its context alive, and every later test then
  fails with "Cannot run multiple SparkContexts" instead of its own error. `testkit.run_program`
  stops any leftover context or session after every run (it was an autouse fixture in each test
  file before phase 2a).
- Skeletons import what the student will need, so ruff's unused-import rule is switched off for
  `exercise/student.py` and `solution/skeleton.py` only.
- File-level skeletons give the boilerplate assignments usually give: `SparkConf` /
  `SparkContext` creation, `sc.stop()`, the `sys.argv` entry point. The student writes the
  processing in between (a `raise NotImplementedError` marks the spot).
- File-level tests assert `outputPath/_SUCCESS` exists, so a program that saves nothing cannot
  pass the "empty output" case.
- File-level exercises ship `exercise/sample_input/` (the docstring example as files), so the
  program can be run by hand: `python student.py sample_input sample_output` from the exercise
  folder with the venv activated (an activated venv also puts its `python3` first on PATH, which
  Spark workers use). `sample_output/` is git-ignored.

Interface extensions (phase 2a; decided 2026-10-24 from Labs 4-5 and the course projects). Most of this
lands in the tests, not in the validator. Comparison mode and parameters are written only in the
test code: tests run where `solutions/` (and so metadata.json) is not available, and keeping them
in two places would drift. Metadata keeps what the validator reads (constraints,
`partition_check`, `mutant_partitions`) plus descriptive fields.
- One exercise, one implementation. Metadata `framework` is `pyspark-rdd`, `pyspark-df`, `mrjob`
  or `python`, and an MRJob exercise names one `runner`, `inline` or `hadoop`. A course task that wants an RDD
  and a DataFrame version (as a Spark project can) becomes two exercises with the same question; each stays
  simpler to build and validate. `hadoop` only when the concept needs it (custom partitioner or
  comparator, order inversion, secondary sort), since every job costs 8-9 s.
- MRJob file-level: the student writes the job class in the course template; tests run it on
  input files with `-r inline` or `-r hadoop`. On `hadoop`, `TUTOR_PARTITIONS` also sets
  `mapreduce.job.reduces`, so rule 5 checks 1, 2 and 4 reducers. Hadoop Streaming (separate
  stdin mapper / reducer scripts) is not supported: it teaches the same concepts as mrjob and
  would need a second test harness.
- `testkit.run_mrjob(job_cls, input_dir, output_dir, runner=..., jobconf=...)` (done 2026-10-25)
  runs the job in-process through `make_runner()`, as the course's command line would.
  Measured with mrjob 0.7.4 and Hadoop 3.3.6 (2026-10-25), and kept as `spark`-marked tests:
  - jobconf precedence: an `MRStep(jobconf=...)` beats the command line's `--jobconf`, which
    beats the class `JOBCONF`. So rule 5's command-line reducer count overrides a count fixed
    in `JOBCONF` (as course templates often do), while a step that pins one reducer (a global top-k) keeps it.
  - Multi-step jobs (`steps()` returning several `MRStep`s) run on both runners; each step is a
    separate Hadoop job: about 7 s for one step, 12 s for two.
  - Hadoop retries every failed task 4 times by default; `run_mrjob` sets map / reduce
    `maxattempts=1` so skeletons and mutants fail in seconds.
  - mrjob cannot read the Python traceback from local Hadoop task logs (only "subprocess
    failed with code 1"), so on failure `run_mrjob` reruns the input on inline (about 1 s): if
    that raises, the learner sees the real error and line; if it runs, the message points at
    Hadoop-only settings (partitioner, key field separator, sort order).
  - The inline runner's reducer task count is the CPU core count and it splits reducer input
    by size, whatever `mapreduce.job.reduces` says, so reducer state across keys can break on
    inline too; only Hadoop runs it as written.
  - Map tasks on inline: by default 2 x CPU cores, split by size, so on this 12-core machine
    24 tiny tasks; a combiner rarely saw two values of one key, a max-temperature mutant
    whose combiner took the minimum survived, and whether combiner bugs were caught depended
    on the machine. `run_mrjob` now passes `--num-cores 1` to inline runs: every input file
    is one map task (a single file is split in two), so `TUTOR_PARTITIONS` decides the map
    tasks and the mutant is killed at 1, 2 and 4 files (found and fixed 2026-10-25).
  - A job file without `if __name__ == "__main__": Job.run()` works inline but writes nothing,
    silently, on Hadoop (the task processes start it as a script). `run_mrjob` checks for the
    entry point first and says what to add.
  - `HADOOP_HOME` / `JAVA_HOME` come from the shell or the repo's `.env` (loaded by path,
    since tests run from a workspace folder); missing ones give a clear error.
  - metadata `timeout_s` sets the per-run timeout for all rules (default 30 s).
- Parameters, in the course's two forms: positional after input and output
  (`run(inputPath, outputPath, <params>)`, Spark programs), or
  `--jobconf myjob.settings.<name>=<value>` read with `jobconf_from_env` (MRJob, Lab 4 top-k). Tests pass them
  (`run_program(Program, inp, out, m, k)`, as strings like `sys.argv`); rule 4 draws parameter
  values at random as well as inputs.
- Output comparison modes (`testkit.assert_output(..., order=...)`):
  - `any`: all lines of all `part-*` files as a multiset (the current behaviour, default);
  - `per_file`: each `part-*` file in order, the files as a multiset (secondary sort: a reducer's
    output is ordered only within itself);
  - `total`: exactly one non-empty data file, fully in order (Spark sort + `coalesce(1)`);
  - options: `float_tol` (course tasks accept tiny float differences and `3.0` = `3.000`),
    and `optional_quotes` for MRJob's JSON keys (`"zounds,a"` = `zounds,a`). With `float_tol`
    a line is split into fields at tabs and commas; only a field that is a number on both
    sides gets the tolerance, every other field must match exactly. The first version
    compared every number in the line by value, so `s01,2024-1` passed for `s1,2024-01`
    (Codex review, 2026-10-25). 0, the default, compares characters, so a required number
    format is checked.
  - `per_file` is checked without knowing which key went to which reducer: `expected` is the
    fully sorted output and each part file must be a subsequence of it.
  - Mismatch messages are for the learner, who sees them first: unexpected and missing lines
    listed separately, or the file and line that is out of order.
- `src/tutor/testkit.py` holds this: `write_inputs` (split lines into `TUTOR_PARTITIONS` files,
  optional header per file), `run_program` (call `run(...)`, assert `_SUCCESS`, stop leftover
  Spark), `read_output`, `assert_output`; `run_mrjob` follows with the MRJob step. Each
  exercise's `test_student.py` and `test_oracle.py` import it (the package is installed in the
  venv that runs the tests), so test files stay short, which also leaves the phase 3 generator
  less boilerplate to get wrong. Cost: an exercise folder needs that venv to run. Done for
  `station-max-file` (2026-10-24; validation unchanged, about 77 s).
- API constraints gain `required_any` (one of several groups, e.g. `combiner` or
  `mapper_init` + `mapper_final`; course tasks accept either). Use counts are reported, not
  limited (`count_uses: ["MRStep"]` -> "uses: MRStep x2" on the api line; decided 2026-10-24).
  A name counts as defined if a function has it or a call passes it as a keyword, since
  multi-step jobs write `MRStep(combiner=self.add_up)`; `required_definitions` uses the same
  rule. A flat `required_any` list is an error, not "each name is a group". Done 2026-10-25.
- `examples/station-condition-share/` (done 2026-10-25): the first MRJob example, on Hadoop.
  Share of each weather condition per station with order inversion (`(s, *)` total sorts
  first; `SORT_VALUES` + key field separator `,` partition by station), tau as
  `--jobconf myjob.settings.tau`, compared `per_file` with `float_tol` and `optional_quotes`;
  ships `solution/pseudocode.md`. Three tests, each data-rich (every Hadoop run costs 7 s).
  Mutants: no `SORT_VALUES`, no key separator, total key `~` (sorts last), `>` for `>=`, a
  combiner that emits 1. The first two pass with one reducer and fail at 2, which is what
  rule 5 and the Hadoop runner are for. Rule 4: 4 hypothesis examples with random tau and
  reducer count, station names chosen to test prefix order (`s1` / `s10`, `n` / `north`).
  Full validation about 3 min 50 s (rule 3 alone 111 s); metadata `timeout_s` 150.
- `examples/station-monthly-df/` (done 2026-10-25): the first DataFrame example. Monthly
  average per station from CSV files with a header in each, `min_readings` as the third
  positional argument, output ordered (station asc, average desc, month asc) in one file:
  `order="total"` with `float_tol`. Constraints: `forbidden_calls` `sql`, `rdd`, `collect`,
  `toPandas`, `sorted`; no pandas / numpy. Mutants: average ascending, tie-break month
  descending, `>` for `>=`, `count("*")` counting non-numeric rows, month without the year.
  Full validation about 70 s. "Forgot `coalesce(1)`" is not a mutant: on small data adaptive
  query execution merges the shuffle partitions into one anyway, so it cannot be killed
  reliably.
  - Rule 4 lesson: with temperatures drawn from a wide range, equal averages almost never
    occurred, and the reversed tie-break mutant passed the hypothesis test (rule 3 still
    killed it with a hand-written tie). Drawing from a few multiples of 2.5 makes ties
    common and keeps sums exact in binary, so Spark and Python averages are bit-identical;
    rule 4 then kills 4 of 4 mutants tried on its own. Generators must make the edge cases
    of the ordering (ties) likely, not merely possible.
  "Do not buffer all values in the reducer" cannot be checked statically and stays a point for review.

Correctness vs algorithm quality (revised 2026-10-25, Codex review against the slides): the
course does assess efficiency, not as seconds on a laptop but as algorithm design (L3.2 slide
3.42 asks for an efficient top-k over several reducers; the course values combiners, no
buffering in the reducer, several reducers). So the two are judged separately:
- correctness: the validator, deterministic, decides VALID / INVALID;
- algorithm quality: a per-exercise checklist (all data into one reducer? a key's values
  buffered in the reducer? an unnecessary global sort? does the combiner cut the shuffle?),
  plus static warnings that never fail an exercise: `list()` / `sorted()` / `len(list())`
  over a reducer's values, an `MRStep` pinned to one reducer. Warnings go on the api line of
  the validation report, like `count_uses` (owner's choice). Backlog C2.

Runtime budget: wall-clock limits only need to be sane.
- Every subprocess run (a student test run, or one validation step) times out at 30 s.
- One SparkSession per validation process; `local[2]`, UI off, `spark.sql.shuffle.partitions=4`
  (the default 200 makes small DataFrame jobs slow).
- `hypothesis` settings for Spark differential tests: `deadline=None` (the default 200 ms fails
  every Spark job), `derandomize=True` so validation is reproducible. Function-level: one shared
  SparkContext, `max_examples=25`, shrinking on (cheap, gives minimal counterexamples).
  File-level: every example starts the program's own SparkContext (about 1.5 s), so
  `max_examples=8` with large inputs (up to 40 mixed lines) and shrinking off: with it, a failing
  mutant took over 120 s to report, without it 5-7 s. Measured 2026-10-24.
- Spark tests read `TUTOR_PARTITIONS` (default 2) as the `numSlices` of their input, so rule 5
  reruns the same tests with 1, 2 and 4; passing at every count is stronger than "same result".
  Measured on `station-average-rdd`: the per-partition-average mutant passes at 1 and 4 partitions
  and is caught only at 2, so one partition count is not enough. One pytest run with Spark takes
  about 4-5 s (SparkContext start included).
- Spark workers start the `python3` on PATH unless `PYSPARK_PYTHON` is set, which fails with
  PYTHON_VERSION_MISMATCH here (Anaconda 3.13 vs venv 3.11). The runner sets it to
  `sys.executable`, and exercise test fixtures `setdefault` it so direct pytest runs work too.

Mutants: the reference with one deliberate bug (e.g. `add` -> `max`, `>` -> `>=`, missing empty-input case).
In phase 2 Claude Code drafts 3-5 per exercise and the validator plus the owner review them, rejecting equivalent
mutants (unkillable) and ones that crash on import; any bug the owner makes while solving is added as
a mutant. Phase 3 moves drafting to the LLM pipeline.

## Generation pipeline (LangGraph)

```
retrieve_context -> generate_exercise -> validate --pass--> save
                          ^                  |
                          +---- repair <--fail (max 3)
                                             +--over limit--> discard + log
```

- LLM output parsed into Pydantic models.
- `validate` is pure Python and acts as the judge; repair receives the failure report.
- Every generated batch mixes function-level and file-level exercises (see "Exercise interfaces").

## RAG

- PyMuPDF parsing, chunking by section / code block, keep source file and page.
- Retrieval filters by the batch's `sources` first, so a week-2 batch only draws on week-2
  material; searching the whole course is the fallback, not the default.
- Hybrid retrieval (BM25 + vectors): API names like `aggregateByKey` are exact-match problems
  that pure vector search handles poorly.
- Evaluate with 20-30 hand-labelled queries: recall@k for vector-only vs BM25-only vs hybrid.
- Vector store and embedding model not yet decided (Chroma or FAISS candidates).

## Evaluation metrics (README results table)

- First-pass validity rate; validity after repair loop.
- Mutant kill rate.
- Tokens / cost per valid exercise.
- With RAG vs without RAG; across models.

## Code execution safety

Subprocess + timeout + temporary working directory. Docker only if environment reproducibility
(Java/Spark) becomes a problem, not for isolation.

`execution.run_pytest(files, timeout_s=30)` copies a name -> source mapping into a fresh temp
dir (so the same tests can run against reference, skeleton or a mutant as `student.py`), runs
`sys.executable -m pytest` there and deletes the dir. Results are PASSED, FAILED, ERROR or
TIMEOUT; validation relies on FAILED vs ERROR, since a skeleton must fail its tests, not crash.
The exit code alone was not enough (Codex review, 2026-10-25): pytest exits 0 when every test
is skipped and 1 when only a fixture breaks, so a skipping test file let a reference or oracle
"pass" and a mutant that only broke setup counted as killed. Each run now writes pytest's JUnit
XML in the temp dir and the counts decide: PASSED needs exit 0, at least one passed test and
no skipped one; FAILED needs at least one failed assertion; everything else (import errors,
no tests, skips, setup/teardown errors only) is ERROR. The child starts in its own session and the
whole process group is killed on timeout and after every run, because PySpark starts a JVM
under pytest that would otherwise be orphaned (a test covers this, and fails if only the parent
is killed). This is isolation, not a sandbox: the child can still touch other files and the network.

Scope: the runner protects validation, which runs generated code. A learner running
`pytest test_student.py` runs their own code in the terminal, in-process and without a
timeout: Ctrl+C stops an endless loop, and testkit stops a leftover SparkContext after each
program. Accepted for now (raised in the same review); `pytest-timeout` is the fix if hangs
become a problem.

## Learning rules

- Editor AI inline completion is off when solving exercises.
- Hints come from `hints.md` (three levels), never from reading `reference.py`.
- `hints.md` starts with "Useful functions": each relevant API (and any forbidden one) with a
  tiny runnable example on different data, so it teaches the tool without giving the answer.
  Examples are run once to check the shown output. Then levels 1-3.
- Solve from `exercises/` only; `solutions/` is for the validator.
- `reference.py` doubles as the worked answer the owner reads after solving (decided
  2026-10-25), so it is written in numbered steps, not one long chain: each `# Step N:`
  comment says what the step does and why, and shows what a record looks like after it on
  the question's example data (`#   ('s1', (30.0, 2))`, a small table for DataFrames, the
  key / value flow for MRJob). Mutants are the reference with one change, so a diff against
  the reference shows only the bug.
- Two modes, chosen per batch at generation and stored in `manifest.json` `mode` (decided
  2026-10-24). The lectures are dense and a single pass does not stick, so the system serves
  both first learning and later review. Validation is identical in both; scaffolding and help differ:
  - `learn`: one week's material, small steps close to the slides, the docstring names the slide
    pages, a fuller skeleton. The tutor explains concepts proactively and gives a line-level fix
    when asked.
  - `review`: one or several weeks mixed, fuller tasks leaning file-level (closer to labs and
    projects), a bare skeleton. The tutor helps only when asked, first naming just the kind of
    error, and leaves the fix to the owner.
- `attempts.jsonl` gets one line per exercise per test run (time, batch, exercise, mode,
  result, pass / fail / error counts, first error, partitions, duration). Nothing reads it in
  phase 2; it is kept so review batches can later target weak topics, and as material for
  hint rules and mutants (roadmap 3b). Done 2026-10-25 as a pytest plugin, `tutor/attempts.py`,
  registered through the `pytest11` entry point, so it loads in every pytest run of the venv
  with no file in the workspace (a per-batch conftest.py was the alternative). It writes only
  for test files under `$WORKSPACE_DIR/batches/<batch>/exercises/<exercise>/`, so validator
  runs (temp dirs) and the repo's tests leave no trace; mode comes from the batch's
  `manifest.json`; a student.py that does not import counts as "error", not "failed"; any
  recording problem is a warning and never changes the test result. The entry point name
  equals the module name, so an explicit `-p tutor.attempts` is not registered twice. Adding
  the entry point needed one `pip install -e .`.

## Roadmap (reading week, in priority order)

1. Environment + validation harness. No LLM involved. **Done 2026-10-24.** The "broken variant
   per rule" lives in `tests/test_validation.py` as tiny pure-Python exercises built to fail
   exactly one check each (broken reference, passing / crashing skeleton, surviving / unrunnable
   mutant, reference wrong beyond the tested inputs, reference wrong at 4 partitions, API
   violations), plus the examples' own mutants; timings are recorded under "Exercise validation".
   - `pyproject.toml`, `.gitignore`, `.env.example`, gitleaks pre-commit.
   - `config.py` (workspace checks), `envcheck.py`, `execution.py`, `validation/` (Spark partition check only).
   - Synthetic `examples/station-average-rdd/` (function-level, kept deliberately simple to start
     with) and `examples/station-max-file/` (file-level: input files with messy lines, exact output
     format, input split into `TUTOR_PARTITIONS` files), plus one broken variant per rule, so each
     check is shown to both pass and fail.
   - README rewritten as the public repository introduction (what it is, how to set it up and
     run the validator, the design in brief), replacing the original Codex-era planning brief.
   - Done when: `pytest` and `ruff check .` pass; the valid example passes all checks and every
     broken variant fails the check it targets; measured validation time recorded; README updated.
2a. Harness extensions from Labs 4-5 and the course projects (see "Interface extensions"), in small
   steps: `testkit.py` with the output modes; parameters and the new API constraints; MRJob
   interface with a synthetic example; DataFrame with a synthetic example; rule 5's combiner
   check; `attempts.jsonl`. **Done 2026-10-25.**
2. Golden exercises, solved by the owner. Run the whole loop by hand before automating it.
   - Batches from materials the owner picks (see "Material selection"), drafted by Claude Code and
     checked by the validator; the owner solves them in the batch's mode.
   - First batch (approved 2026-10-25): `2026-10-25_p2-w1w2-mapreduce`, mode `learn`, sources
     L1.1, L1.2, L2.1, L2.2 (the only week 1-2 lab material is a zipped VM home with SSH keys,
     never read). Sized to cover every codable knowledge point, 9 exercises (10 since
     `mean-in-mapper`, Mean v4, was added for the last gap), no Hadoop runner:
     `hdfs-bookkeeping` (python: blocks, replication vs erasure coding, NameNode metadata,
     heartbeats, under-replicated blocks), `mapreduce-by-hand` (python: map, per-map combine,
     hash partition, sort, group, reduce into R parts), `streaming-reducer` (python: Hadoop
     Streaming reducer over sorted lines without buffering), `word-count-mrjob`,
     `max-temperature` (NCDC fixed-width parsing, max combiner), `in-mapper-combining`,
     `mean-combiner-debug` (debugging: Mean v2 to v3, combiner check), `cooccurrence-pairs` and
     `cooccurrence-stripes` (same question, two implementations). Slides that are images (Word
     Count v1/v2, Mean v1-v4, stripes pseudocode, streaming and MRJob code) were first taken from
     Lin & Dyer ch. 3, then read as rendered page images (macOS PDFKit) on 2026-10-25.
     Not codable, left for concept questions (2b): 3V/6V, Hadoop history and distributions,
     YARN, HDFS design assumptions, data locality and split size, reducer-count heuristics,
     NAS/SAN vs DFS, MRJob runners, RDBMS vs Hadoop. This batch replaces "five" as the size.
   - Generated 2026-10-25: all 9 VALID (2-6 s each; 5 mutants each, hints examples run).
     Lessons for phase 3: tests twice let a mutant live because the data was too regular
     (keys arriving already sorted; every station with equal counts per map task, where a
     mean of means equals the mean), and one drafted mutant was equivalent to the reference.
     Exercise data must break the regularities the bug relies on.
   - Revised 2026-10-25 after a second Codex review, against the slides (before the owner
     started): page references mixed PDF page numbers with slide footers (2.26 for the
     combiner-design slide 2.31; 1.17 for 1.18), so every reference now gives both; each
     exercise lists its teaching assumptions in the question and in metadata `source`
     (`slides`, `pdf_pages`, `lab`, `assumptions`), e.g. the simulator's fixed value order
     (real Hadoop does not order values, L3.1 slide 3.27) and the three tokenizers the slides
     use. Reading the image slides found a real conflict: slide 2.49 emits one stripe per
     occurrence, which the stripes test had rejected (and a mutant enforced); the test now
     accepts both forms and that mutant was dropped. Lesson for phase 3/4: image-only slides
     must be read as images before generating, and sources need slide and PDF page numbers.
   - `python -m tutor.validation` saves its report as `<solution_dir>/validation.json`
     (2026-10-25): exercise id, valid, time, `tool_commit` (`git describe --always --dirty`
     of the validator, the counterpart of manifest.json's generator commit) and every rule's
     status, detail and duration. Only the CLI writes it; `validate()` stays side-effect
     free for the phase 3 graph. Git-ignored, since it changes on every run.
   - Coverage, each also exercising a different part of the validator (final topics picked from lectures):

     | # | Topic | Type | Interface | Validator path |
     |---|---|---|---|---|
     | 1 | RDD per-key average, no `groupByKey` (L5 practice 2) | implementation | function | partition check, forbidden-API check |
     | 2 | DataFrame average word length per first letter, no `df.rdd` (L5 practice 3) | completion | file (CSV out) | DataFrame comparison, forbidden-API check |
     | 3 | MRJob co-occurrence, pairs or stripes (L2.2, Lab 3) | implementation | file | MRJob runner, combiner check |
     | 4 | MRJob in-mapper combining (L2.2, Lab 2) | implementation | file | required-API check (`mapper_init` / `mapper_final`) |
     | 5 | MRJob mean with a broken combiner (L2.2 "Computing the Mean" v1-v4) | debugging | file | buggy skeleton must fail, combiner check |

     `aggregateByKey` is listed but not taught (L4.1), so it is not in the golden set.
     Order inversion and secondary sort go into a later batch: they need the Hadoop runner,
     which the golden set should not depend on.

   - Done when: every exercise of the first batch passes validation, the owner has solved
     them (review-mode ones without asking for help), attempts are in `attempts.jsonl`, and
     the steps that felt repetitive are noted as input for phase 3. This batch is the
     baseline set for phase 5.
2b. Code tracing and design exercises (raised from "deferred" on 2026-10-24; scope revised
   2026-10-25). The final exam is a 2-hour written exam (L1.1 slide 1.20), so beyond runnable
   code two more types (concept questions are out of scope: the owner generates those in chat
   for exam revision):
   - Code tracing ("what does this print and why"). Answers are one of: an exact output (or
     an unordered set of lines); an error and its type; "nothing has run yet" (lazy
     evaluation); "not determined" (order or value depends on partitions or scheduling).
     Each answer also asks for the reason. The validator proves the answer type by running
     the snippet: the error type is raised; a determined output is the same over repeated
     runs and partition counts; a "not determined" one is shown to differ. Lecture code that
     is illustrative only (L4.2 slide 4.16: `lambda x: counter += x` is a syntax error, and
     `"..." + counter` a TypeError) is rewritten as runnable code that shows the same point,
     with the slide it comes from noted.
   - Design exercises: a fixed template (intermediate keys and values, partitioning, sort
     order, why the combiner is safe, memory needed, then pseudocode), checked against
     rubric points and the exercise's `pseudocode.md`. A code exercise can gain a design
     part after it is solved.
   Before phase 3 so the generator covers these types too.
3. LangGraph generate-validate-repair loop. Prerequisites (from the 2026-10-25 slide review):
   image-only slides are read as images (a multimodal model or OCR) before anything is
   generated from them, and every source records slide footer, PDF page and any lab or
   textbook origin, with the exercise's teaching assumptions stated.
3b. Hint command, `python -m tutor.hint <exercise>` (decided 2026-10-24, after phase 3 since it
   needs the LLM client). Three layers, cheapest and most certain first:
   - rules, no LLM: known low-level errors (syntax, full-width punctuation, `NotImplementedError`
     left in, PYTHON_VERSION_MISMATCH, leftover SparkContext, tuple-formatted output) -> line and cause;
   - mutant matching, no LLM: the same tests fail with the same values as a known mutant -> that
     mutant's misconception note (each mutant gains one line: misconception + direction);
   - LLM, only when both miss: gets `student.py`, the question, `hints.md`, constraints and the
     first real error (Py4J / Java stack and home paths stripped), never `reference.py`. Direction
     for concept errors, a line only for low-level ones, more specific on repeat requests. A
     deterministic guard rejects replies with long code blocks or code too close to the reference.
   - Evaluation: mutants act as simulated students with a known bug: diagnosis hit rate, leak
     rate, share solved without the LLM, cost per hint. Real tutoring mistakes from phases 1-2
     seed the rules and mutants. Hints only on request, logged in `attempts.jsonl`.
4. Hybrid RAG.
5. Evaluation script + README results.

## Backlog (decided, not scheduled)

- C1 value-order check: rule 5 also reruns with each key's values shuffled (a runtime
  subclass, like the combiner switch), catching reducers that rely on value order, the
  reason secondary sort exists. First verify that mrjob's inline runner delivers values
  sorted (it sorts whole map-output lines), which would hide such bugs today.
- C2 algorithm-quality warnings (see "Correctness vs algorithm quality"), later Hadoop
  counters (map output records, combine input / output) to report how much a combiner cut.
- C3 tokenizer: the course's labs hand out the tokenizing line
  (`re.split(r"[ *$&#/\t\n\f\"\'\\,.:;?!\[\](){}<>~\-_]", line.lower())`, also L2.2 slide
  2.43), so new exercises that split text give that line in the skeleton, as the labs do,
  instead of defining their own word rule (owner, 2026-10-25). Existing exercises are not
  converted (see "Decisions not to do").
- Workspace `coverage.md`: before each batch, list the lectures' knowledge points with their
  status; the next batch is chosen from the gaps (Codex's list: secondary sort, inverted
  index / TF-DF, multi-step MRJob and top-k, Spark lineage / cache / repartition, SQL views
  and UDFs; SQL is restricted per exercise, never globally).

## Decisions not to do (and why)

Deliberate omissions, listed so that a later review (human or model) does not report them as
gaps. Each was decided by the owner; change one only with the owner.

- No concept questions in the system (short answers such as "why is the Secondary NameNode not
  a backup?"). The owner generates those in chat when revising for the exam. The system
  spends its effort where deterministic validation adds value: runnable code exercises and
  design exercises (2b). Decided 2026-10-25.
- Existing exercises are not reworked when a later convention appears (e.g. the course
  tokenizer, backlog C3). New practice comes as new exercises; old ones keep working, and
  their differences from the course are stated as teaching assumptions in the question and
  metadata `source.assumptions`. The owner prefers more exercises over edited ones, and
  edits would mean re-validating and re-reading material already solved. Decided 2026-10-25.
  (Exception: an exercise that teaches something wrong, as the stripes test did, is fixed.)
- No LLM judges whether an exercise is valid; only the deterministic validator does (see
  "Exercise validation"). LLM feedback on the learner's answers (hints, rubric feedback) is
  a different thing and allowed.
- No Hadoop Streaming interface: the course covers streaming for understanding only (L2.1
  slide 2.29), and mrjob teaches the same concepts; streaming appears only as plain-Python
  exercises (see "Interface extensions").
- No timeout on the learner's own `pytest` runs: the isolated runner protects validation of
  generated code; in the terminal Ctrl+C stops a loop (see "Code execution safety").
- No wall-clock performance scoring: efficiency is judged as algorithm design, not seconds
  on a laptop (see "Correctness vs algorithm quality").
- No `aggregateByKey` in the first golden exercises: listed but not taught in L4.1.

## Deferred

SQLite, CLI, adaptive learning, exam mode, Docker, error taxonomy
(derive categories from real attempt data later).

## Open questions

- LLM provider and embedding model.

## Pseudocode

The final exam is a 2-hour offline written exam, so answers are written, not run. The course
pseudocode (Lin & Dyer style: `class Mapper / method Map / Emit`) maps one-to-one onto MRJob
`mapper` / `combiner` / `reducer`, so MRJob exercises double as pseudocode practice.
Each MRJob exercise keeps `solutions/<id>/pseudocode.md`: the reference solution written in exam
pseudocode, for comparison after solving.
