# Design Decisions

Living record of project decisions. Update it when a decision changes; note the reason.

## Goals

1. Primary: portfolio project demonstrating LangGraph, RAG and LLM evaluation with real engineering judgment.
2. Secondary: COMP9313 revision. The owner solves generated exercises himself; AI never writes the answers.

## Roles of AI tools

- Claude Code is the development assistant for building this system. It is not part of the runtime.
- The system itself calls an LLM API (provider not yet decided).
- The first exercises (golden set) are generated manually with web chat models, then reviewed and solved by the owner.
- In phases 1-2, Claude Code also acts as the tutor: the owner solves, runs the tests, and asks in
  chat when stuck. Rules live in CLAUDE.md ("Tutoring"): read the code and run the tests first,
  explain the first real error and its concept, direction before code, never touch `student.py`
  or quote `solution/`. Mistakes seen this way feed debugging exercises and mutants. A hint
  command inside the system (LLM reads `student.py` + test output) is a later option.

## Environment

- Requirements are per course, because courses differ (COMP9313 needs Java + Spark, others may not).
  Each workspace declares them in `workspace.toml` (`course` plus a `[requires]` table);
  the repo only holds the generic checker `tutor/envcheck.py`, so it stays course-agnostic.
- Check, don't install. `python -m tutor.envcheck` checks the current workspace, and it also runs
  once before validating a batch (not per exercise: importing pyspark alone takes a second or two).
  `tests/test_env.py` runs the same checker.
- Installation: Python deps as per-course extras, `pip install -e ".[comp9313]"`; system deps (Java,
  Hadoop) as a short per-course section in README. No install script: setup is one-off, and when it breaks,
  asking an AI is faster than maintaining a cross-platform script.
- COMP9313: Python 3.11, Java 17 (`JAVA_HOME`), PySpark 3.5.x (decided 2026-10-24: the slides
  name both 3.5.9 and 4.2.0, and the version is pinned per workspace in `workspace.toml`
  anyway, so another course's materials can choose differently). Spark 4.x enables ANSI SQL mode by default,
  which changes DataFrame/SQL error behaviour (casts, overflow, divide by zero); RDD API is unaffected.
  mrjob is no longer actively maintained: works on 3.11, do not move past 3.12.
  (Confirmed: on Python 3.13 mrjob tasks fail with `No module named 'pipes'`.)
- COMP9313 Hadoop: 3.3.6 standalone (same 3.3 line as the course VM's 3.3.2), Apache tarball
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
├── pyproject.toml                       # P1, per-course extras e.g. [comp9313]
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
│   ├── schema.py                        # P3 Pydantic exercise models
│   ├── llm.py                           # P3 thin LLM API client, no LangChain
│   ├── graph/                           # P3 langgraph dependency added here
│   ├── rag/                             # P4
│   └── eval/                            # P5
├── examples/                            # P1 synthetic only, same shape as a real exercise
│   ├── station-average-rdd/             # function-level: per-key average in PySpark RDD
│   │   ├── exercise/
│   │   └── solution/
│   └── station-max-file/                # file-level: CSV input dir with messy lines -> part-* output
└── tests/
    ├── test_env.py                      # P1, runs envcheck for the current workspace
    └── test_validation.py               # P1, runs the validator on examples/
```

`validation/` stays pure Python so the phase 3 `validate` node calls it unchanged.

## Workspace layout (private, never in git)

```
smartcodereview-workspace/
└── comp9313/                            # WORKSPACE_DIR points here; one workspace per course
    ├── workspace.toml                   # course name + [requires]; marks this as a workspace
    ├── lectures/                        # PDFs, read-only
    ├── coding_materials/                # labs and projects as on Ed, read-only
    │   ├── lab4/                        # one flat folder each: problem text, data, expected outputs
    │   └── ass2/
    ├── batches/                         # one directory per generation run
    │   ├── 2026-10-27_p2-golden/
    │   │   ├── manifest.json            # phase, date, method, model, git commit, exercise list
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
- An exercise is identified by `<batch>/<exercise-id>`; the same id may recur across batches.

## Course coding materials

Decided 2026-10-24 after reading Labs 4-5 and Projects 1-2.
- `coding_materials/<labN|assN>/` mirrors Ed: one flat folder per lab or project, no subfolders.
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
- Labs are practice material, so exercises may stay close to them. A project can be in an open
  assessment window (Project 2 is due 2026-11-01): exercises built from a project are variants on
  another domain and data, never the project itself, and the tutor does not write project code.

## Exercise validation (deterministic, never LLM-judged)

An exercise is valid only if:

1. `reference.py` passes all tests.
2. `skeleton.py` (the pristine skeleton) fails.
3. Every mutant in `mutants/` fails.
4. Reference output matches `oracle.py` (plain Python) on random inputs (differential testing, `hypothesis`).
5. Results do not depend on how the work is distributed. Implemented per framework:
   - PySpark RDD / DataFrame: identical across partition counts 1, 2, 4 (phase 1).
     Catches e.g. a wrong `combOp` in `aggregateByKey`, which never runs with one partition.
   - MRJob: identical with the combiner on and off, since Hadoop may run it 0, 1 or many times
     (phase 2, with the first MRJob exercise). The lecture uses the same test ("check the
     correctness by removing the combiner", L2.2 p.38). Exercises on the `hadoop` runner also
     pass with 1, 2 and 4 reducers (see "Interface extensions").
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
  Only fully dynamic access (`getattr` with a built string) escapes. The same function can later
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
- Plain `pytest` excludes tests marked `spark`; `pytest -m spark` validates both examples end
  to end (about 1 min). Run it whenever validation or an example changes.

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
  fails with "Cannot run multiple SparkContexts" instead of its own error. File-level test files
  have an autouse fixture that stops any leftover context after each test.
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

Interface extensions (phase 2a; decided 2026-10-24 from Labs 4-5 and Projects 1-2). Most of this
lands in the tests and metadata, not in the validator:
- One exercise, one implementation. Metadata `api` is `rdd`, `dataframe`, `mrjob` or `python`,
  and an MRJob exercise names one `runner`, `inline` or `hadoop`. A course task that wants an RDD
  and a DataFrame version (Project 2) becomes two exercises with the same question; each stays
  simpler to build and validate. `hadoop` only when the concept needs it (custom partitioner or
  comparator, order inversion, secondary sort), since every job costs 8-9 s.
- MRJob file-level: the student writes the job class in the course template; tests run it on
  input files with `-r inline` or `-r hadoop`. On `hadoop`, `TUTOR_PARTITIONS` also sets
  `mapreduce.job.reduces`, so rule 5 checks 1, 2 and 4 reducers. Hadoop Streaming (separate
  stdin mapper / reducer scripts) is not supported: it teaches the same concepts as mrjob and
  would need a second test harness.
- Parameters, in the course's two forms: positional after input and output
  (`run(inputPath, outputPath, m, k)`, Project 2), or `--jobconf myjob.settings.<name>=<value>`
  read with `jobconf_from_env` (Project 1, Lab 4 top-k). Metadata lists them; tests pass them;
  rule 4 draws parameter values at random as well as inputs.
- Output comparison modes (metadata `output`):
  - `any`: all lines of all `part-*` files as a multiset (the current behaviour, default);
  - `per_file`: each `part-*` file in order, the files as a multiset (secondary sort: a reducer's
    output is ordered only within itself, Project 1);
  - `total`: exactly one non-empty data file, fully in order (Project 2: Spark sort + `coalesce(1)`);
  - options: float tolerance (both projects accept tiny float differences and `3.0` = `3.000`),
    and MRJob's JSON quotes optional (`"zounds,a"` = `zounds,a`).
- `src/tutor/testkit.py` holds this: run a Spark file-level program or an MRJob job (inline /
  hadoop), read the `part-*` files, compare by mode. Each exercise's `test_student.py` and
  `test_oracle.py` import it (the package is installed in the venv that runs the tests), so test
  files stay short, which also leaves the phase 3 generator less boilerplate to get wrong.
  `station-max-file` moves to it.
- API constraints gain `required_any` (one of several groups, e.g. `combiner` or
  `mapper_init` + `mapper_final`, Project 1 accepts either) and use counts (exactly one `MRStep`
  in Project 1, two in the Lab 4 top-k). "Do not buffer all values in the reducer" cannot be
  checked statically and stays a point for review.

Runtime budget: the course does not test performance, so limits only need to be sane.
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
In phase 2 a web chat model drafts 3-5 per exercise and the owner reviews them, rejecting equivalent
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
`sys.executable -m pytest` there and deletes the dir. Results are PASSED (exit 0), FAILED (1),
ERROR (2-5: import/syntax error, no tests) or TIMEOUT; validation relies on FAILED vs ERROR,
since a skeleton must fail its tests, not crash. The child starts in its own session and the
whole process group is killed on timeout and after every run, because PySpark starts a JVM
under pytest that would otherwise be orphaned (a test covers this, and fails if only the parent
is killed). This is isolation, not a sandbox: the child can still touch other files and the network.

## Learning rules

- Editor AI inline completion is off when solving exercises.
- Hints come from `hints.md` (three levels), never from reading `reference.py`.
- `hints.md` starts with "Useful functions": each relevant API (and any forbidden one) with a
  tiny runnable example on different data, so it teaches the tool without giving the answer.
  Examples are run once to check the shown output. Then levels 1-3.
- Solve from `exercises/` only; `solutions/` is for the validator.

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
2a. Harness extensions from Labs 4-5 and Projects 1-2 (see "Interface extensions"), in small
   steps: `testkit.py` with the output modes; parameters and the new API constraints; MRJob
   interface with a synthetic example; DataFrame with a synthetic example.
2. Five golden exercises, solved by the owner. Run the whole loop by hand before automating it.
   - First batch `batches/<date>_p2-golden/`: exercises drafted with web chat models, reviewed by the owner.
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

   - Done when: all five pass validation, the owner has solved them without AI help, attempts are in
     `attempts.jsonl`, and the steps that felt repetitive are noted as input for phase 3.
     These five are the baseline set for phase 5.
2b. Code tracing exercises (raised from "deferred" on 2026-10-24: the lectures ask many "what does
   this print and why" questions, e.g. an accumulator read as 0, closures over driver variables,
   lazy evaluation, groupByKey vs reduceByKey). Design the tracing flow first (run the snippet
   repeatedly and across partitions to prove its output is stable, then compare a normalised
   answer), then a handful of exercises. Before phase 3 so the generator covers this type too.
3. LangGraph generate-validate-repair loop.
4. Hybrid RAG.
5. Evaluation script + README results.

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
