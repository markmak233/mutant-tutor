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
plan (reads the course's coverage table) -> the owner selects items and approves, or sends back
  -> for each selected exercise, in parallel:
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
the workspace's `workspace.toml`, so any OpenAI-compatible models, and Claude through its
own API, can be combined, local or cloud. The oracle is best another provider's, by another maker, than the models that
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

The GUI's Environment page groups course setup into four numbered steps:

1. **What the course needs.** Set the language versions and packages. Each package has one
   version cell: a prefix such as `3.5`, or a numeric comparison such as `>=1,<2`. Saving
   stores prefixes in `[requires]` and comparisons in `[package-bounds]`, moving an entry
   when its form changes. Python, Java, Hadoop and gcc accept prefixes only. Invalid package
   entries stay in the form for correction; unrelated comments and a previous-file backup
   are kept. The separate language list holds Python/C versions.
2. **Install on this machine and check.** The same deterministic Doctor rows as the CLI
   load automatically, with OK, Needs attention, Problem or Skipped results and a fix on
   each row where one is known. Install, image build, Python switch, runner selection and
   host-guide actions reuse the existing controls. A running task disables fix controls.
3. **Where tests run.** Choose the course's runner with its existing confirmation for host
   execution. Selecting a runner does not install its requirements or build an image.
4. **Test image.** Inspect Docker/image readiness and the recipe before building. The
   image record below shows build time and version differences, with Refresh and full history.

`python -m tutor.workspace init DIR`, run again after changing requirements, installs the
declared pip packages into the course's own `.venv`. A missing or broken venv also has a
Python recreation path in step 2. If recovery finds no compatible installed Python, it
shows **No suitable Python here. Download one with uv**. That page keeps its explicit
download consent; choosing the downloaded interpreter for a venv is a separate action.

Environment step 2 also links to **Run tests on this machine**, an optional manual setup
guide. Choose macOS, Linux, Windows or WSL; the page shows the course's declared versions,
Python setup, applicable Java/Hadoop instructions and links to existing C settings.
It neither installs system tools nor checks them on page load. Return to Environment for
the existing check. Recipes not covered are marked explicitly; Linux/WSL fresh installs
remain untested. Container practice through `tutor test` does not require host JVM/compiler
installation. The longer big-data walkthrough remains in [setup/bigdata.md](setup/bigdata.md).

Doctor is now step 2's check; old Doctor bookmarks return there. For a Problem without a
known rule-based fix, **Ask a model for a fix** opens a four-step page:

1. Review the pre-filled failure (the row and latest failed install/build log tail, at most
   64 KiB) and its module, edit as needed, and choose a model profile. **Preview what will
   be sent** shows the complete sanitized data and destination. Only **Send to the model**
   makes the advice request.
2. Read the advice. A newly proposed package alone offers the independent second-provider
   and PyPI review. For a permitted repair, confirm **Test it in a temporary environment**;
   the consent covers up to three model-assisted rounds and temporary public-PyPI installs.
3. Read the verification result. A passed check enables **Show the exact changes**; a failed
   check offers **Back to step 1**. This check does not prove every course exercise works.
4. Review the complete diff and confirm **Apply**. Afterwards, **Undo** restores the original
   setup, or **Keep it** explicitly discards its backups. Both require confirmation.

Selected inputs are kept privately under `logs/doctor`. Current input, destination,
configuration and facts must match the preview before sending/applying; changed data needs
a new preview. New packages are never automatically installed. Oversized previews/diffs
are refused rather than silently cut. Successful worker tasks return to the appropriate
wizard step. Model credentials stay in the worker's private mapping, never the GUI server's
environment. Undo refuses changed configuration; Keep it preserves later owner edits while
discarding originals. Automated tests use model/installer stand-ins.

Environment step 4's **Image build record** shows build time and packages/tools whose
versions differ between the course's venv and image. Differences are informational; missing
measurements stay unknown. **Full image record and history** shows exact image/base content
IDs, available registry digests, build/record times and all recorded distributions. Builds
keep private historical snapshots under `logs/image-builds`; refreshing does not erase
prior builds. A changed image ID or recipe makes the record old, with no current comparison.
Missing or old records retain the **Refresh current image record** action.

New builds capture their actual base through a temporary local snapshot tag; it is removed
afterwards and does not permanently pin course configuration. Old images without provenance
labels have unknown base/build facts; refresh does not infer them from a mutable tag.
The page's refresh action, or `python -m tutor.image_records "$WORKSPACE_DIR"`, reads the
existing image in a network-disabled/read-only temporary container and writes a record.
It never builds, pulls, installs or reads the GUI interpreter's packages as course facts.

Wheels include the GUI assets and shipped synthetic smoke examples. Creating/replacing a
course environment from an installed wheel uses this tool's bundled public source and a
noneditable install; it does not depend on the original checkout or a temporary editable
path, and does not look up an unrelated public package named `tutor`. Source checkouts
retain their existing editable course installs. Private notes/configuration/caches and
linked files are excluded from the controlled source bundle.

A course that has not chosen runs on the host and prints a warning. For container courses,
`tutor test` also runs the learner's tests in that image; ordinary `pytest` stays on the
host for debugging. Docker Desktop on macOS and the Linux CI exercise the container path;
native Windows Docker Desktop still needs checking. There is no fallback to host execution.

Before generated exercises are published, a static check reads every exercise file and rejects
code outside an allowlist (modules, `os` / `sys` attributes, `eval` / `exec`, paths outside
the exercise, shell commands through Spark or MRJob). It stops the obvious, but Python is too
dynamic for a static check to prove code safe; the container is the boundary.

### Checking your setup

`tutor doctor [FOLDER] [--smoke]` and `python -m tutor doctor` use the same command.
FOLDER defaults to the current directory; the nearest ancestor containing workspace.toml
is the course. Outside a course, only tool checks run; finding no course is not a failure.

Checks run in this order:

1. Tool Python (3.11+), installed tutor version, and optional gen/gui extras (without
   importing those heavy packages).
2. Docker availability/version. A problem is a warning for host practice or no course,
   a failure for a container course. No Docker build or pull is started.
3. Registered languages' machine checks: currently C's Linux mmap randomisation warning
   for AddressSanitizer. Python-only courses have no C checks.
4. Course loading and declarations, including unsupported languages/toolchains, then the
   runner choice. A broken workspace skips all remaining course checks.
5. Course venv interpreter/import and sibling tutor command. A broken venv suggests
   replacement; an older venv missing only the script gets a warning.
6. Host platform support and requirements, reusing envcheck and its compatibility rules.
   These check the interpreter running doctor, not a different course venv's packages;
   run doctor from the course venv to check that environment. Container courses skip host
   tool requirements because `tutor test` uses the image.
7. Container image readiness (host courses skip it); Docker failure skips another probe.
8. With `--smoke`, validate a shipped synthetic example on a temporary course copy, then
   remove it, including on failure/interruption. Python uses station-longest-rise; C uses
   station-above-average-c. C smoke needs a container even when host student tests can run.
   Missing packaged examples skip smoke. Validator output appears above its result; no
   report file is written. The current runner is never changed.

Rows are OK, WARN, FAIL or SKIP, followed by a concrete `next:` for each warning/failure.
Checks flush their output and continue past individual errors. Exit codes: **0** no failures
(warnings allowed), **1** at least one failure, **2** usage/nonexistent folder, **130** Ctrl+C.
Default doctor does not load `.env` or check API credentials; export runtime settings in
the calling terminal. It neither installs nor applies fixes. Model-proposed installation
repairs and the optional host setup page belong to doctor step 2.

Optional model advice is now available as an explicitly requested, unverified step:

```bash
tutor doctor --preview-advice --failure failure.log
tutor doctor --advise --failure failure.log
```

Select and review a UTF-8 failure record yourself. Preview displays the sanitized request
without contacting a model; `--advise` sends that record and distribution/Python facts to
the displayed provider/model. Redaction removes known credentials, common auth/URL data,
paths and controls, but cannot prove arbitrary logs are free of private material. No
lectures, student answers or neighbouring files are discovered or added to the request.
The record is limited to 64 KiB, the complete request to 128 KiB; input is never silently
truncated. These advice modes cannot be combined with `--smoke`.

For a course, configure `[llm.roles] doctor = "your-profile"`, or pass `--profile NAME`.
Outside a course, use the existing exported `LLM_*` settings. Only an explicit
`--env-file FILE` reads credentials, into a private mapping where exported values win;
it does not change the process environment or pass keys to child programs. Default checks
still do not read it. Assistant exchange profiles are refused in this read-only mode.

Facts come from the host course's own venv, the container course's ready image, or the
tool interpreter when there is no course. Missing course environments never fall back to
the tool. The image probe uses no course mount, network, build or pull. Image Python is
locked for advice until disposable image/Python verification is implemented. Advice facts
are distinct from the ordinary host check's interpreter, labelled above.

The doctor prompt is new; calibrated generation/judge prompts are unchanged. Requests use
`thinking=False`; a truncated reply gets one retry (2048 then 4096 output tokens). Compatibility
retries may add HTTP calls. Every reply passes the deterministic permission gate. Permitted
changes are WARN, model-proposed and unverified, and never clear earlier FAILs. Unknown
package candidates are withheld pending independent review. No repair is installed,
applied or verified, and no call log or assistant request file is written. Provider/gate
errors are FAIL; invalid inputs exit 2, Ctrl+C 130. The same normal exit/summary rules apply.

Explicit disposable verification checks a selected import and `pip check` before asking
for repairs, then runs that same fixed check in fresh temporary environments:

```bash
tutor doctor --verify-advice --failure failure.log --module your_package
```

This mode downloads public PyPI wheels into temporary venvs (host) or an owned temporary
image (container), never into the original environment. Host package code runs with your
user rights; a venv is not a security sandbox. Container probes retain the isolated
runner's network/filesystem limits. No model-written program or shell command runs.
Only an original failure followed by the same check passing is verified; this does not
prove every course exercise works. A passing baseline spends no model request. Trials
are cumulative, capped at three, stop on repeated proposals/the same missing module
twice, and clean temporary resources on failure/interruption. Image Python stays locked.
Missing wheels/private-index packages are reported; source builds are not enabled.

New-package review is a separate explicit operation, with an independently configured
second provider/model (choose actual independent identities, not proxy aliases):

```bash
tutor doctor --review-new-package --failure failure.log --profile first --second-profile second
```

Both models independently receive the same sanitized facts, with thinking enabled and
without the other's answer. A same-provider or same-host pair is refused. Only matching
normalized names with PyPI release files are displayed, along with release count and
first upload. Agreement/existence is not a security endorsement; no new name is installed.
Tests replace model, HTTP, environment installer and probe callbacks. Original course
files/venv/runner are unchanged by verification or independent review.

To retain a verified proposal, add `--save-repair repair.json` to verification. The new
private file contains structured changes and freshness hashes, without failure text or
paths. It cannot overwrite an existing file or live inside a git repository. Then:

```bash
tutor doctor --preview-repair repair.json
tutor doctor --apply-repair repair.json --confirm
tutor doctor --undo-repair
# After accepting the result, explicitly discard the retained backups instead:
tutor doctor --finish-repair --confirm
```

Application requires the `doctor` extra (TOML comment preservation), gates the saved
changes against current facts, rejects stale hashes and repeats verification before
mutating anything. Host replacement keeps the previous `.venv` and builds at the actual
path (venvs are not relocatable). A failed install/check or interruption restores it.
Container application builds/checks the new image while retaining the old one, then
promotes configuration. Answers, lectures, model settings and runner policy are not edited.
The single retained repair can be undone or finished before another one is applied.
Undo refuses configuration/backup text changed after application. Diagnostics printed
before an apply are retained; run doctor again to check the resulting live environment.

Package comparisons live in a separate optional table:

```toml
[package-bounds]
demo = ">=1,<2"
```

`[requires]` keeps numeric version prefixes. Environment step 1 shows both declarations
in one version table: changing a cell between prefix and comparison moves its entry on Save.
A package identity may occur in only one declaration table; repair preview likewise moves
an affected package into `package-bounds`. Runtimes/toolchains keep prefixes. Bounds use
only numeric `== != >= <= > < ~=` comparisons and comma intersections;
no URLs, wildcard, markers, extras or options. Checks fail conservatively on nonnumeric
installed releases for a bound; pip remains the resolver. Venv installs, image recipes,
doctor and environment checks use both tables. Empty bounds preserve existing image names.

### Testing your work in the course image

From one course exercise folder, run `tutor test`, or pass its directory explicitly:
`tutor test EXERCISE_DIR -k example -vv --timeout 600`. The module form is
`python -m tutor test`. Refresh an existing editable install once (`pip install -e .`)
to create the new console script. No new host dependency is required.

The command uses a temporary copy and shows pytest output as it arrives. It needs an
explicitly configured container course and an already built image; it neither builds an
image nor installs a toolchain. It reads no model credentials. The 300-second default is
actual wall-clock time after image readiness; Ctrl+C or expiry ends the whole container.
Timeout exits 124, terminal Ctrl+C exits 130, and normal runs keep pytest's exit code.

The host appends the same attempts format as the pytest plugin and keeps the code actually
tested for failed/error runs. A skipped test is incomplete, and a timeout is interrupted
unless an observed error/failure takes precedence. Selected tests (`-k`) are recorded with
their selection; success describes those tests, not proof of solving the whole exercise.
Recording errors warn without changing the test result. Test output from subprocesses
may still be buffered by those subprocesses.

Daily container practice needs only the installed tool, Docker running Linux containers
and an IDE on the host; the image contains pytest, course libraries, Java, Hadoop or gcc
as required. Host `pytest` debugging still needs its own pytest/plugin and those libraries
and tools installed locally. Native Windows C host debugging requires WSL. Ordinary
pytest does not gain an overall timeout. Existing answers and saved questions are untouched.

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
a model draft the course's coverage (`coverage.json`, with `coverage.md` rendered from it)
from the lectures, edit its points and draft new or changed lectures into it, plan
a new batch and approve its plan; generating also needs the `gen` extra):

```bash
pip install -e ".[gui]"
python -m tutor.gui             # every course folder next to WORKSPACE_DIR's, or --root DIR
```

It listens on 127.0.0.1 only and opens a link with a token for this launch; other pages
in the browser cannot use it.

On the Plan page all proposed items start checked. Uncheck anything to defer, then use
**Approve and generate selected**; at least one item is required. Every note stays visible.
The selection is saved with the full approved plan, so Stop/Continue and assistant resumes
keep that subset. A changed plan requires a fresh review. Deferred rows say **Left out by
you**. After the run finishes and the course is idle, **Generate** starts one deferred item
as a new paid generation, using its approved notes and the saved settings. The page shows
the effective models from current course settings. **Try all again** includes failures only.

From a terminal, choose IDs with:

```bash
python -m tutor.graph resume --batch demo --approve --only=alpha,charlie
```

Omit --only to approve all items. Supply it only at the approval pause, never on Continue;
an empty, repeated or unknown ID list is refused. plan.json remains the full plan.

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
  test_command.py  tutor test: live learner tests in the image, host-owned attempts
  attempt_records.py / attempt_transport.py
                   shared attempt writer and bounded run-local pytest events
  container.py     the course's Docker image from [requires] and the limits each run gets;
                   `python -m tutor.container build | status`
  llm.py           a small client for OpenAI-compatible APIs (LM Studio, GLM, OpenAI,
                   DeepSeek): roles -> profiles, structured output, streaming, call log
  llm_anthropic.py Claude's own Messages API behind the same llm.chat (provider "anthropic")
  materials/       course PDFs and saved web pages -> a per-page (per-section) markdown cache,
                   image slides via a vision model;
                   `python -m tutor.materials`; `python -m tutor.materials.requires` drafts
                   [requires] from the cached lectures, `.coverage` a coverage draft, and
                   `.coverage_store` keeps coverage.json (coverage.md rendered from it)
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
