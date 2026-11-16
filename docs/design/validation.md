# Design: Validation

The deterministic exercise checks, the learning rules and exam pseudocode. Part of the design record; the index of every part is [README.md](README.md).

## Exercise validation (deterministic, never LLM-judged)

An exercise is valid only if:

0. `metadata.json` matches the `schema.py` model (format).
safe. Every `.py` file that validation runs passes the static safety check (see "Code
   execution safety", layer A); otherwise nothing is run.
1. `reference.py` passes all tests.
2. `skeleton.py` (the pristine skeleton) fails.
3. Every mutant in `mutants/` fails.
4. Reference output matches `oracle.py` (plain Python) on random inputs (differential testing, `hypothesis`).
5. Results do not depend on how the work is distributed. Implemented per framework:
   - PySpark RDD / DataFrame: identical across partition counts 1, 2, 4 (phase 1).
     Catches e.g. a wrong `combOp` in `aggregateByKey`, which never runs with one partition.
   - MRJob: identical with the combiner on and off, since Hadoop may run it 0, 1 or many times.
     The course material recommends the same test (check correctness by removing the
     combiner). Exercises on the `hadoop` runner also pass with 1, 2 and 4 reducers (see
     "Interface extensions").
   Done 2026-10-25 as one rule, "distribution", switched on per exercise in metadata:
   `partition_check` reruns the reference at 1 and 4 partitions / input files / reducers;
   `combiner_check` (MRJob, inline runner) reruns it with `TUTOR_COMBINER=off`, under which
   `testkit.run_mrjob` runs a subclass whose `MRStep`s have no combiner functions (explicit
   `steps()` included). Not on `hadoop`: the task processes run the student's own script, so a
   combiner removed in the test process would still run there; reducer counts cover those
   exercises. Since 2026-10-30 (backlog C1) every MRJob exercise on the inline runner, with
   no metadata flag, also reruns with `TUTOR_VALUE_ORDER=reversed`: reducers and combiners
   get each key's values in reverse order. Hadoop promises no value order; the inline
   runner keeps the input files' order (not sorted, as the backlog entry had guessed:
   values 5, 25 from one file and 30, 10, 200 from another arrived as 5, 25, 30, 10, 200),
   so a reducer taking its first value as the earliest passed every inline run. Reversed,
   not shuffled, so any two values change order and a failure repeats. All 14 week 1-3
   exercises stayed VALID; the one `hadoop` exercise is skipped, as for the combiner. Tests may also call `run_mrjob(..., combiner=False)` themselves, so a student's
   reducer that relies on the combiner (the classic broken mean) fails their own tests, not
   only the reference check. Neither flag: "n/a".
   - Plain Python: not applicable.

Rule 0, format (decided 2026-10-28, see "Batch format contract"; built in phase 2, design
explained before code since it touches `validation/`):
- `src/tutor/schema.py` holds Pydantic models of `metadata.json` and `manifest.json`, each
  with a `schema_version`. One spelling per field: `interface` is `"function"` or `"file"`
  and required; `entry_point` is a list of names (`["StationMax.run"]`,
  `["mapper", "reducer"]`); `source` is `"synthetic"` or an object with slides, PDF pages,
  lab and assumptions.
- Lecture files may have any name (owner, 2026-11-05). When an exercise is saved, code sets
  `source.lecture` to the exact cache file's name, as a model's words once cost a judge its slides:
  a course code and a year read as extra lectures, and 3 exercises of a long lecture were judged
  with none. No field was added, as the schema is sent to the model, so its prompt is unchanged.
  Exercises saved earlier keep their text, where only the number after the word for lecture counts.
  The course order comes from the numbers in the file names ("L2" before "L10"); with a name that has
  no number the order is unknown, so nothing is guessed and only the exercise's own lecture counts as
  taught, with a warning. A named lecture that yields no pages is a warning too. Phase 4's retrieval
  code still reads `Lecture_<n>-` names; generation does not use it.
- The validator checks `metadata.json` first; if it fails, every other rule is SKIPPED,
  since they read their settings from it. The message names the field and the problem.
- Unknown fields are errors (`extra="forbid"`) and types are strict (`"yes"` is not a bool),
  so a typo or an old spelling is reported instead of ignored. Cross-field rules: an mrjob
  exercise names its runner and only it may; `combiner_check` needs the inline runner;
  function and MRJob entry points have no dots, file-level Spark ones are `Class.run`; in a
  manifest, priority marks name listed exercises, each at most once.
- The models are also exported as JSON Schema files in `schemas/` (`python -m tutor.schema
  --export`; a C exercise's model as `metadata-c.schema.json`), so an assistant that is not running Python, and phase 3's structured output,
  read the same definition; a test fails if the exported files differ from the models.
  JSON Schema holds the fields only, not the cross-field rules, so `python -m tutor.schema`
  stays the check that counts.
- `manifest.json` belongs to the batch, not to one exercise, so a separate command,
  `python -m tutor.schema <batch_dir>`, checks it and every exercise's metadata, and that
  the manifest's exercise list matches the folders. The validator's per-exercise structure
  stays as it is.
- Pydantic enters the dependencies in phase 2 instead of 3, because rule 0 needs it now.

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
  The `if __name__ == "__main__":` block is left out of it (the demo below, not the solution,
  may print with a forbidden action such as collect()); the safety check still reads it.
- Rule `main` (owner, 2026-10-29: every program runs with `python xxx.py`, except code a
  framework launches with no main of its own): metadata `standalone` true means
  `student.py` and `reference.py` have an `if __name__ == "__main__":` block. A function
  exercise's block runs the question's example and prints the result (a Spark one builds
  and stops its own session), and the rule runs the reference that way in the isolated
  runner (`execution.run_script`, exit code 0). A file exercise's block takes its paths
  from sys.argv as the slides' standalone programs do (static check only; the tests run
  it). `standalone` false (a Hadoop submission, a project's entry point) and unset (an
  exercise from before the rule) are n/a. The pipeline asks for the block and writes
  `standalone: true` unless the model chose false. The skeleton's block raises
  NotImplementedError until the student writes the function, which is expected.
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
  `run(inputPath, outputPath)` + `sys.argv` (standalone-program style), MRJob a full job class run on
  input files. Practises parsing (blank, malformed, padded lines), output format and output dirs.
  Tests write input files into a temp dir, call the program, read every `part-*` file of the
  output and compare lines exactly (format matters: `s1,20.0` is not `('s1', 20.0)`, cf. the
  `saveAsTextFile` parentheses trap) but as a multiset, since partition order is not
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

Interface extensions (phase 2a; decided 2026-10-24 from the course's labs and projects). Most of this
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
  `--jobconf myjob.settings.<name>=<value>` read with `jobconf_from_env` (MRJob, e.g. a top-k job). Tests pass them
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
    format is checked. Numbers are compared as decimals (as floats two 17-digit counts that
    differ by one were equal), and unordered lines are matched one to one by augmenting
    paths, not by taking the first line in reach, which could reject correct output in
    another order (both from an outside review, 2026-10-29).
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
  descending, `>` for `>=`, `count("*")` counting non-numeric rows, month without the year,
  and (since 2026-10-28) no `coalesce(1)`. Full validation about 75 s. That last mutant at
  first survived: on small data adaptive query execution merges the shuffle partitions into
  one, so a missing `coalesce(1)` still wrote one file. DataFrame programs therefore set
  `spark.sql.adaptive.enabled=false` in their session, as on a cluster with real data where
  the partitions would not merge, and the mutant is killed.
  - Rule 4 lesson: with temperatures drawn from a wide range, equal averages almost never
    occurred, and the reversed tie-break mutant passed the hypothesis test (rule 3 still
    killed it with a hand-written tie). Drawing from a few multiples of 2.5 makes ties
    common and keeps sums exact in binary, so Spark and Python averages are bit-identical;
    rule 4 then kills 4 of 4 mutants tried on its own. Generators must make the edge cases
    of the ordering (ties) likely, not merely possible.
  "Do not buffer all values in the reducer" cannot be checked statically and stays a point for review.

Correctness vs algorithm quality (revised 2026-10-25, Codex review against the slides): the
course does assess efficiency, not as seconds on a laptop but as algorithm design (an efficient top-k over several reducers; combiners, no buffering in
the reducer, several reducers). So the two are judged separately:
- correctness: the validator, deterministic, decides VALID / INVALID;
- algorithm quality: a per-exercise checklist (all data into one reducer? a key's values
  buffered in the reducer? an unnecessary global sort? does the combiner cut the shuffle?),
  plus static warnings that never fail an exercise: `list()` / `sorted()` / `len(list())`
  over a reducer's values, an `MRStep` pinned to one reducer. Warnings go on the api line of
  the validation report, like `count_uses` (owner's choice). Backlog C2.

Validity vs teaching quality (decided 2026-10-26, Codex review 4). VALID says the tests,
reference and mutants agree; it does not say the exercise is good to learn from. Teaching
quality is a separate score that never replaces or overrides validity, on four points:
- clear: someone who has never met the content can learn it from the question, with the
  "Useful functions" part of `hints.md` where the API syntax is taught: after reading
  them they understand it and have at least an idea how to start (owner, 2026-10-31; it
  read "one reading of the question, inputs and outputs fully specified" until the judge,
  held to that, missed all five of the owner's n: the owner had meant understandable, not
  specified);
- fits the material: matches how the lectures and labs present the idea (the stripes
  case below is a failure of this);
- within what has been taught: no API or idea from later weeks; partly checkable
  deterministically with a per-week list of allowed APIs (api check);
- targets the concept: solving it needs the knowledge point it is filed under, not a
  workaround.
The owner rates the first batch after solving each exercise (one line per exercise, a few
seconds), optionally with a free-text remark ("too easy", "unclear input format"). Format
fixed at the first rating (2026-10-28): y or n per point, in the order above (`pairs yyyy
too easy`), since yes/no labels compare cleanly with a judge's and need no scale; one JSON
line per exercise goes to the workspace's `ratings.jsonl` (batch, exercise, date, clear,
fits, taught, targets, remark), one file for all batches so later plans read every rating
at once. The assistant wrote those lines at first; since 2026-11-15 (owner) the exercise
page asks for them once a logged run has passed (`gui/ratings.py`): a yes/no pair per point,
all four required, and an optional remark. Opening it again shows the stored rating in the
form, and saving replaces that exercise's line in place (keys the page does not set stay;
the file before is `ratings.jsonl.bak`), so an exercise never counts twice. The page shows
no judge verdict beside it, since these ratings are what the judge is checked against. Those ratings are the labels an LLM judge is checked against before
it is trusted (roadmap 3b), and later batches' plans read them (see the [3] preference chat). ContentGen (SIGCSE TS 2026) separates
correctness from contextual fit in the same way, and found that a summary of recently
covered material in the prompt improved generated questions (a [3] candidate).

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

Independent checks (decided in principle 2026-10-26, Codex review 4; the separate oracle
is built in 3a, the held-out set in 3b once the owner's solutions and mistakes exist; design
explained first since it touches `validation/`). When question, reference,
tests, mutants and oracle come from one generation, they can agree with each other and
still share one misreading of the requirement; killing every planted mutant only shows
the planted bugs are caught. This already happened by hand: the stripes test rejected the
lecture's per-occurrence stripes and a mutant enforced the same misreading,
so the exercise was VALID and wrong until the slides were checked. So keep, per exercise
or knowledge point, a held-out set that generation and repair never see:
- other correct solutions (different style or the lecture's own form) must PASS: catches
  tests that reject correct answers. Sources: the owner's passing `student.py` (free once
  an exercise is solved), the lecture or lab version, a solution from another model;
- independent wrong solutions must FAIL: catches weak tests beyond the planted mutants.
  Sources: the owner's real mistakes (the tutoring rule already lists them), a catalogue
  of common MapReduce / Spark mistakes, mutants written by another model;
- in phase 3 the oracle is written by a different model or a separate context that never
  sees the reference, so rule 4 compares two independent readings.
The first batch seeds the set: the owner's solutions and mistakes, no extra work beyond
solving.

How it is built (3b design, owner 2026-10-31): the held-out check adds no new kind of
check. It runs the validator's own rules with another candidate in place of the reference
or a mutant (`validation/heldout.py`; rules 1, 4 and 5 take an optional candidate, rule
3's per-file verdict is `mutant_verdict`):

| Candidate | Must | Rules reused | A failure means |
|---|---|---|---|
| a correct solution (the owner's passing `student.py`) | pass at the default partitions, then at every rule 5 condition (1/2/4 partitions, combiner off, values reversed) | 1, 5 | the tests reject a correct answer, or the owner's code has a distribution bug their own run (2 partitions) could not show |
| the same | agree with the oracle on random inputs | 4 | the tests let a wrong solution through, or the oracle is wrong |
| a wrong solution (a `student.py` the attempts plugin kept from a `failed` run) | still be caught | 3 | the tests became weaker since |

- Report only: a finding is for the owner to look at. It never changes VALID or
  `validation.json`, and never reaches repair, or the set would stop being held out. It
  measures how often a VALID exercise is still wrong (phase 5).
- Candidates come from `attempts.jsonl`: the current `student.py` if a run of it ever
  passed (the log keeps no copy of passing code; a file changed since shows up as failing
  its tests, worded "changed since, or the tests did"), and each distinct copy kept from a
  `failed` run. `error` runs are left out: the tests never ran.
- The exercise's files pass rule 0 and the safety check first, as for validation; the
  owner's code skips the safety check, as in their own runs.
- `python -m tutor.validation.heldout <batch_dir> [<id> ...]` prints one line per check
  and writes `solutions/<id>/heldout.json`.
- First run, 2026-10-31: weeks 1-2, 5 and 7, 17 correct solutions and 2 mistakes, nothing
  flagged, about 2 minutes (the Spark week 100 s). Expected limit: the owner's solution
  follows the question, so a question that misreads the slides is not caught this way
  (a week 7 question counts a bucket in full where the slides halve it). Step 2 below
  targets that.

Step 2 (built 2026-10-31): another model writes candidates from the question alone.
`tutor/heldout_writer.py` (outside `validation/`, which stays free of LLMs) asks it, per
exercise, for `correct.py` (the question as written), `slides.py` (the task the way the
slides do it, with one sentence on where the question departs from them) and up to three
`wrong_NN.py` (a typical student mistake each, with what it gets wrong). It sees the
question, the original skeleton (never the owner's `student.py`), the sample data and the
lectures the exercise names (whole when they fit in 120k characters, the exercise's own
pages marked); never the reference, tests, mutants, oracle or hints. The files and a
`meta.json` (model, notes, inputs hash) go to `solutions/<id>/heldout/model/`; an
exercise is written again only when its inputs or the model change, or with `--force`.
- One call per file. Asked for all five in one reply, GLM-5.3 returned placeholders
  without thinking and spent its whole output on reasoning with it. A `plan` field comes
  before `code` (without it GLM wrote its reasoning into `code`, as repairs without
  `diagnosis` did). The task comes last in the prompt, so an exercise's calls share one
  prefix (DeepSeek served 79% of the input from its cache; OpenAI reported none).
- A reply is sent back (two retries, then the file is dropped) when the code does not
  parse, lacks an entry point, has an entry point that does nothing (`...`, `pass`, a
  bare name: GLM once lost every newline, so a function parsed as `def f(): n` with the
  rest a comment), fails the static safety check, or repeats a mistake already written
  (DeepSeek-V4-Pro did that even when told the earlier ones).
- The model must be another family than the batch's generator (owner, 2026-10-31): the
  `heldout` role is deepseek-pro, for the Claude-made phase 2 batches; DeepSeek-made
  batches run with `--profile gpt-terra`. To save the OpenAI credit, only week 7 did
  (11 exercises, about $1.7); weeks 4 and 5 used deepseek-pro, the generator's family.
  glm-full was tried first and could not do it.

`validation/heldout.py` runs them after the static safety check. A model's file may
itself be wrong, so where it and the tests disagree, the oracle (an independent reading
of the question) decides:

| Model's file | Outcome | Reported |
|---|---|---|
| correct | fails the tests, agrees with the oracle | FAIL: the tests may reject a correct answer |
| correct | fails the tests, disagrees with the oracle | n/a: the model's own mistake |
| correct | passes the tests | as the owner's: rules 5 and 4 next |
| slides | fails the tests | FAIL, with the model's note on where the question departs |
| wrong | caught | ok |
| wrong | passes the tests, disagrees with the oracle | FAIL: the tests miss this mistake |
| wrong | passes the tests, agrees with the oracle | n/a: not a mistake after all |

The oracle does not judge `slides.py`: it was written from the question, so it shares any
departure from the slides. A slides flag is right when the question states the
departure as an assumption; the owner checks.

First run with model files, 2026-10-31 (all five batches, 38 exercises, about 8 minutes):
11 flags. Week 7 (gpt-terra): the tests miss three mistakes (`counting-bloom-filter`:
repeated hash positions counted once; `lossy-counting`: two), and two questions depart
from the slides without saying so (`dgim-query-estimate`, the oldest bucket; and
`dgim-bucket-update`, whose window boundary is one position off the slides' wording);
`fixed-proportion-sampling`'s departure is stated. Week 3: four slides
flags, all stated (letter names, a tie-break, top-k, pairs without a common friend). Week
5: one slides flag with no difference named, likely the model's own mistake. Weeks 1-2
and 4: none. The oracle mattered: 15 of DeepSeek-V4-Pro's 57 "mistakes" (12 in weeks
4-5) were correct solutions, which would have been false "tests miss a bug" flags;
gpt-terra wrote one such in 33.
Fixed the same day (owner), by the rule that an exercise teaching something wrong is
fixed: `lossy-counting` got two tests and `counting-bloom-filter` one (neither solved
yet), after which the held-out check flags nothing there; the two DGIM questions
(solved, so tests unchanged) got an assumption naming the departure from the slides.
Their slides flags remain, as they should: the departure is now stated.

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
    when asked. One knowledge point per exercise (decided 2026-10-29): a slide example that
    chains several ideas becomes several small exercises, or one exercise in parts with their
    own tests. The owner spent long on W5 `df-udf-closure-debug`: it marked one bug, but the
    fix also needed a split, a lowercase and a return type the skeleton never had. In both
    modes a debugging skeleton is the reference with only the bug changed. The plan model
    sees each existing exercise's title and topic, not only its id, and repeats a solved
    task with another API only when the new API is the whole point (W5 had planned a fourth
    word count and a Spark SQL exercise that redid cleaning and per-key averages).
  - The question (both modes, decided 2026-10-29) is a title and fixed sections: Concept
    (learn only), Symptom (debugging), Task, Input, Output, Example, Constraints, Slides,
    Run. Explanation goes in Concept, never into Task, which stays one or two sentences.
    Until 2026-10-31 each was a label alone on a line (`Task:`, the plain layout); since
    then generated questions are Markdown headings (see "question.md format" below).
  - Concept (owner, 2026-10-30) is the knowledge point's condensed lecture, so the student
    need not go back and forth between slides and question: at most `concept_lines`
    lines (a generation setting, default 50), opening with an introduction of at most 3
    lines (what the technique does and why it is needed), then how it works in the
    slides' words, naming the slides each part comes from; a slide's own example may be
    shown on other data than the exercise's. Never solution code or how to write this
    exercise: that is the hints' job, in levels. Pseudocode may appear (owner, 2026-10-30):
    the algorithm's steps as the slides give them, language-neutral, in a fenced `text`
    block (`for i = 1..d: increment CMS[i][h_i(x)]`); not runnable Python, and not the
    exercise's function names, signature or edge cases. Decided after week 7 was written,
    which was left as it is. It was at most 3 lines from 2026-10-29,
    after three week 5 questions were rated unclear, "explanations too long"; the owner
    later explained the trouble was reading a docstring without layout, with the concept
    mixed into the task, not the length: week 1-2 questions were longer (314 words on
    average against 275) and all rated clear. Those three ratings carry a note saying so,
    so the teaching-quality judge (3b) is not calibrated against them as unclear content.
    The examples' Concepts show the shape (general knowledge in place of slides).
  - `question.md` next to student.py is the same text as Markdown, made by code from the
    docstring (no model, so they never disagree), labels as headings (the plain layout the
    pipeline still writes; the format below replaces it for hand-made questions); existing batches
    get it with `python -m tutor.question BATCH_DIR`. Code blocks only in Example and Run,
    where there is something to copy; elsewhere indented text becomes plain lines (owner,
    2026-10-30: a page of grey boxes is harder to read). It ends with "How to run"
    (owner, 2026-10-30): `cd` into the exercise's folder, `pytest test_student.py`,
    `python student.py`, `hints.md` and the hint command, with the real folder: absolute
    for a workspace exercise (private; it pastes anywhere), from the repository's folder
    for an example (public files hold no absolute path). The hints line is written by
    code because a model may forget it (week 7 blind comparison). A draft has no such
    block, since its folder is temporary; `save` writes it as it moves the exercise into
    the batch. Not hints.md's top, which the owner first suggested: hints.md is opened only
    when stuck. No validation check of the layout yet: older exercises do not follow it
    and are not reworked. The four `examples/` questions were rewritten in the layout
    (2026-10-30), since the prompt's example outweighed its rules: on week 7, DeepSeek
    Flash without thinking followed the layout in 5 of 11 questions and copied the
    example's "Run the example:" lines.
  - `question.md` format (owner, 2026-10-30, on the week 7 questions): use what Markdown
    offers, with a visible hierarchy, since the student reads question.md, not the
    docstring. Top to bottom:
    1. `# title`; a quote line with lecture, slides, mode, type and difficulty; a contents
       line linking the sections; a rule (`---`).
    2. `## Concept` in `###` parts: "Why it is needed" (the introduction, at most 3 lines),
       then one part per idea, its slides in the heading. Steps as numbered lists (nested
       for sub-steps) or pseudocode in a `text` block (see Concept above), properties as
       bullets, comparisons and traces as tables, the key
       claim in a quote. Proofs, analyses, failed attempts and optional extensions folded
       in `<details>` with a `<summary>`, so the main line stays short.
    3. A rule; `## Task` (one or two sentences, names as inline code); `## Input` as a
       parameter table; `## Output`; `## Example`, a code block with the input and output,
       plus a step-by-step table when the example is a trace; `## Constraints` as a
       numbered list with the key words bold; `## Slides` with the assumptions in a quote.
    4. A rule; `## How to run`: one bash block (`cd`, `pytest test_student.py`,
       `python student.py`) and a "Stuck?" quote with the hint command in a bash block. The
       separate `Run` section is folded in: the two repeated each other.
    Math is LaTeX: `$...$` inline, `$$...$$` on its own lines for a display formula (not
    inside a list item, where renderers differ), variables included ($n$, $k$). `\lbrace`
    and `\rbrace`, not `\{ \}` (a Markdown escape on some renderers). Python expressions
    that say what the code computes (`len(stream) / k`) stay inline code. Paragraphs are
    plain Markdown paragraphs, without the hard line breaks of the plain layout. Checked
    with KaTeX, which VS Code's preview uses: week 7's 358 formulas, no failure. Not used:
    GitHub alerts (`> [!NOTE]`) and footnotes, which VS Code's preview may not render.
    Status: week 7 (`2026-10-30_p3-w7-low`) was written by hand in this format, its
    Concepts from the slides; `python -m tutor.question` would overwrite it, so it is
    never re-rendered.
    How the pipeline produces it (owner, 2026-10-31): student.py's docstring is itself the
    Markdown question, so question.md is still made by code from the one source and the
    two never disagree (a model writing question.md separately was the other option,
    against that rule). The model writes the `# title` and the `## ` sections; code adds
    the line under the title (from metadata.json and the batch mode), the contents line,
    the rules and "How to run", into which a `## Run` section's commands and notes are
    folded (`tutor/question.py`; a docstring not starting with `# ` is the plain layout,
    rendered as before). The docstring is a raw string so LaTeX keeps its backslashes: in
    a plain one Python reads `\frac` as a form feed and `\neq` as a line break, without
    an error, so a draft whose docstring holds a backslash and is not raw is a format
    failure sent to repair. The four examples were rewritten in the layout, since a model
    follows the example more than the rules, and a test keeps their question.md equal to
    what student.py renders. The prompt also carries the 3b findings: an algorithm's
    starting state as the slides give it, no claim stronger than the slides, every
    departure from the slides as an assumption in Slides, and the Example's output
    exactly what the reference gives and the tests expect.
    Trial, 2026-10-31 (one week 7 item, the false positive rate of a Bloom filter,
    DeepSeek Flash with thinking): VALID in round 0, a raw docstring, every section in
    place, Concept in slide-headed parts with a pseudocode block and a table, four
    assumptions stated in Slides, 60 formulas rendering. One flaw: `$m = |S|$` in a table
    row, where every `|` ends a cell; the renderer now writes `\vert` there. Kept in the
    private workspace's experiments, not among the batches.
  - `review`: one or several weeks mixed, fuller tasks leaning file-level (closer to labs and
    projects), a bare skeleton. The tutor helps only when asked, first naming just the kind of
    error, and leaves the fix to the owner. Exercises may combine knowledge points, as a
    final exam does.
- Priority marks (decided 2026-10-28, after the owner found the first batch of 10 too much):
  `manifest.json` may hold `priority` with `must`, `optional` and `skip` lists of exercise
  ids; an exercise not listed counts as must. Claude proposes the marks (exam weight, what
  later weeks build on, overlap with exercises already solved), the owner decides. Marks
  live only in the manifest: student.py is never edited for them and folders are not
  renamed, so attempts keep their ids. "Solved" in a batch's done condition means its must
  exercises; skipped ones need no work and no rating.
- `attempts.jsonl` gets one line per exercise per test run (time, batch, exercise, mode,
  result, pass / fail / error / skip counts, first error, partitions, duration). The result
  is "passed" only when every test ran and passed: a skipped test makes it "incomplete" and
  Ctrl+C "interrupted" (both counted as passed or not at all until an outside review,
  2026-10-29). A failed or errored run also keeps the student.py it tested, once per
  content, as `attempts/code/<batch>/<exercise>/<hash>.py`, named in the line's `code`
  field (owner, 2026-10-29: failures only): without the code a real mistake could not be
  replayed as a held-out wrong solution (roadmap 3b); the first one, a `selectExpr`
  without `AS`, was lost that way. Nothing reads it in
  phase 2; it is kept so review batches can later target weak topics, and as material for
  hint rules and mutants (roadmap 3c). Done 2026-10-25 as a pytest plugin, `tutor/attempts.py`,
  registered through the `pytest11` entry point, so it loads in every pytest run of the venv
  with no file in the workspace (a per-batch conftest.py was the alternative). It writes only
  for test files under `$WORKSPACE_DIR/batches/<batch>/exercises/<exercise>/`, so validator
  runs (temp dirs) and the repo's tests leave no trace; mode comes from the batch's
  `manifest.json`; a student.py that does not import counts as "error", not "failed"; any
  recording problem is a warning and never changes the test result. The entry point name
  equals the module name, so an explicit `-p tutor.attempts` is not registered twice. Adding
  the entry point needed one `pip install -e .`. A pytest from another environment has no plugin, so
  it leaves no line and no warning: an empty log does not mean no work was done (see "Public repo vs
  private data" for how a workspace's terminals avoid this).
  Since 2026-11-08, `tutor test` shares the host writer in `attempt_records.py`. The same
  pytest hooks explicitly loaded in the image export bounded JSONL observations, without
  loading .env or writing workspace records there. The host records those observations,
  freezes staged code before launch for snapshots, and rebuilds the report. Timeout/Ctrl+C
  preserves completed observations and adds `stop_reason`; existing failure/error precedence
  stays. A selected run adds `selection`. Missing/malformed bookkeeping warns and cannot
  create a false passed attempt; pytest's test result itself is unaffected.

## Pseudocode

The course is examined in writing, so exam answers are written, not run. The course
pseudocode (Lin & Dyer style: `class Mapper / method Map / Emit`) maps one-to-one onto MRJob
`mapper` / `combiner` / `reducer`, so MRJob exercises double as pseudocode practice.
Each MRJob exercise keeps `solutions/<id>/pseudocode.md`: the reference solution written in exam
pseudocode, for comparison after solving.
