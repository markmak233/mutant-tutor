# Design Decisions

Living record of project decisions. Update it when a decision changes; note the reason.

## Goals

1. Primary: portfolio project demonstrating LangGraph, RAG and LLM evaluation with real engineering judgment.
2. Secondary: revision for a big data management course. The owner solves the generated exercises; AI never writes the answers.
3. Long-term: general enough for other courses (see "Deferred", second course).

Local-first (decided 2026-10-26): everything except the LLM calls runs on the user's own
machine. No hosted service, no remote or multi-user access, no store or hub where people
download each other's content. Users bring their own API key, or a local model. A local
container for isolation is fine; deploying the tool as a service is not.

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
  command inside the system follows phase 3 (roadmap 3c).
- Batch format contract (decided 2026-10-28): drafting batches with Claude Code stays a
  supported way to make exercises after phase 3, next to the LLM pipeline. What both must
  produce is the batch format, defined once in `schema.py` (see "Exercise validation", rule
  0), so the validator, hints, evaluation and GUI never need to know who wrote a batch;
  `manifest.json` `method` and `model` record it. Reason: the first batches were written by
  hand without any check on metadata, and one assistant in one week produced three
  spellings of the same field. Fields added later (such as 3c's per-mutant misconception
  line) start optional with a default; making one required comes with a migration script
  for existing batches, never by letting them fail.

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
- Big data course (`bigdata`): Python 3.11, Java 17 (`JAVA_HOME`), PySpark 3.5.x (decided 2026-10-24: the course
  material names more than one Spark version, and the version is pinned per workspace in
  `workspace.toml` anyway, so another course's materials can choose differently). Spark 4.x enables ANSI SQL mode by default,
  which changes DataFrame/SQL error behaviour (casts, overflow, divide by zero); RDD API is unaffected.
  mrjob is no longer actively maintained: works on 3.11, do not move past 3.12. On 3.12 it
  also needs setuptools installed: mrjob imports `distutils`, removed in 3.12, and only
  setuptools' shim provides it (3.12 venvs no longer include setuptools). Found by the first
  CI run, 2026-10-26; with setuptools, fast and spark suites (Hadoop included) pass on 3.12.10.
  (Confirmed: on Python 3.13 mrjob tasks fail with `No module named 'pipes'`.)
- Big data course Hadoop: 3.3.6 standalone (the 3.3 line the course uses), Apache tarball
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
├── schemas/                             # P2 metadata / manifest JSON Schema, exported from schema.py
├── .claude/settings.json
├── docs/design.md
├── notes/                               # git-ignored local notes (course memory, research)
├── docs/setup/bigdata.md                # per-course install guide (system deps, .env, pitfalls)
├── pyproject.toml                       # P1, per-course extras e.g. [bigdata]
├── .github/workflows/ci.yml             # fast tests 3.11 + gitleaks on main; spark by hand
├── .gitignore                           # P1, includes *workspace*/ as a backstop
├── .env.example                         # P1, WORKSPACE_DIR=..., HADOOP_HOME=...
├── .pre-commit-config.yaml              # P1, gitleaks
├── src/tutor/
│   ├── __init__.py
│   ├── config.py                        # P1 reads WORKSPACE_DIR, derives subpaths
│   ├── envcheck.py                      # P1 checks the workspace's [requires]
│   ├── execution.py                     # P1 copy to temp dir -> subprocess pytest -> delete
│   ├── validation/                      # P1 the checks; P2 rule 0, P3a the safety check (internals designed before coding)
│   ├── testkit.py                       # P2a run a file-level program, read part-*, compare output
│   ├── attempts.py                      # P2a pytest plugin: one attempts.jsonl line per exercise run
│   ├── schema.py                        # P2 Pydantic models of metadata.json / manifest.json (rule 0),
│   │                                    #   P3 parses LLM output into the same models
│   ├── materials/                       # P3a course files -> per-file md cache (pypdfium2), vision pages
│   ├── llm.py                           # P3a thin OpenAI-compatible client, standard library only, no LangChain
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
    ├── AGENTS.md                        # orientation for an AI coding assistant opened here
    ├── CLAUDE.md                        # one line, `@AGENTS.md`, so Claude Code reads the same file
    ├── coverage.md                      # knowledge points per lecture -> exercise / design / not yet
    ├── lectures/                        # PDFs, read-only
    ├── coding_materials/                # labs and projects, read-only
    │   ├── labN/                        # one flat folder each: problem text, data, expected outputs
    │   └── proj2/
    ├── cache/                           # text of every source file, read once (see "Reading files")
    │   └── lectures/<file>.md           #   mirrors the source path; one section per page or slide
    ├── logs/llm_calls.jsonl             # P3a one line per LLM call (purpose, model, tokens, seconds)
    ├── batches/                         # one directory per generation run
    │   ├── 2026-10-27_p2-golden/
    │   │   ├── manifest.json            # phase, date, mode, sources, method, model, git commit, exercises,
    │   │   │                            #   optional priority (must / optional / skip)
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
    │   │           └── validation.json  # result of the checks
    │   ├── 2026-11-03_p3-graph/
    │   │   └── discarded/               # also: exercises still failing after 3 repairs + log
    │   └── 2026-11-10_p4-rag-2/         # second run on the same day
    ├── attempts/attempts.jsonl          # one line per test run: batch, exercise, time, pass/fail, errors
    ├── ratings.jsonl                    # one teaching-quality rating per solved exercise
    ├── index/                           # P4 BM25 / vector index, rebuildable
    └── runs/<date>_<label>/             # P5 evaluation outputs
```

- Batch name: `YYYY-MM-DD_p<phase>-<label>`, suffix `-2`, `-3` for repeat runs on the same day.
- The phase in the name is a hint only; `manifest.json` records the git commit that produced the batch.
- Assistant orientation (decided 2026-10-27): each workspace has an `AGENTS.md`, so an AI
  coding assistant opened in the workspace (it does not see the repo's instructions) knows
  what the folder is. `AGENTS.md` is the file most assistants read; `CLAUDE.md` only imports
  it, the same split as in this repo. It holds the layout, which Python env runs the tests,
  the tutoring rules and the private-data rules. It is written in English and says nothing about the user's personal
  preferences (such as reply language); those belong in the user's own assistant settings.
  The tutoring rules are copied from the repo's `CLAUDE.md`, so a change to one is made in
  both (kept in both places, confirmed 2026-10-28). An "Authoring exercises" section
  (decided 2026-10-28) holds the rules for writing exercises, general and course-specific:
  layout, the `schema.py` fields, reference style, mutant and test-data rules, the
  framework pitfalls found so far, and that every exercise ends with a validator run.
  Reason: an assistant's memory is kept per opened folder, so an assistant opened in the
  workspace cannot see what a session in the repo learned; this section is the one place
  for those rules, and other notes point to it. The assistant runs exercise tests in a temp copy of the exercise folder, so
  `attempts.jsonl` holds only the learner's own runs. Written by hand for now; when
  creating a workspace is automated (packaging), it comes from a template.
- Material selection (decided 2026-10-24): each batch is generated from materials the owner picks,
  e.g. one week while learning it, or several weeks mixed for review. `manifest.json` `sources`
  lists them (workspace-relative paths, optional page ranges); each exercise's metadata `source`
  narrows it to pages. The label usually names the week: `2026-10-25_p2-w2-mapreduce`.
- Generation takes exactly two inputs, a source list and a mode; any selection UI (terminal
  checklist, tkinter, a local web page) only produces those, so the UI can change without
  touching generation. No UI in phase 2: the owner names the materials in chat.
- Which kinds of material may be sources (decided 2026-10-26): the user chooses per batch,
  with one option in the selection step (a checkbox in the GUI): lectures only, or lectures
  plus other material (labs, project specs, notes). Labs and projects are allowed as
  sources, not only as shape references. No attempt is made to keep graded-task specs out:
  what users do with their exercises is theirs to decide, and any LLM chat can already
  turn a spec into a solution. The choice is recorded in `manifest.json`.
- An exercise is identified by `<batch>/<exercise-id>`; the same id may recur across batches.

## Course coding materials

Course-specific provenance (lecture and slide numbers, lab and project contents, the
course's tool versions, batch sources) is kept out of this public file, in private notes
that are never committed.

Decided 2026-10-24 after reading the course's labs and projects.
- `coding_materials/<labN|projN>/`: one flat folder per lab or project, no subfolders.
  File names are arbitrary (`2.txt` is a problem statement, `pg100.txt` a book used as data), so
  sorting files into `spec/` / `data/` folders by hand would not last. Copied: problem text (web
  pages saved as text), data (CSV, book text), course-provided expected outputs. The owner's own
  `.py` files are usually not copied.
- Readers classify files by rule instead of by folder:
  - never read: archives (`.zip`), SSH keys and `.ssh/`, `.venv/`, `__pycache__/`, hidden files,
    output directories (`_SUCCESS`, `part-*`); none of these enter the workspace, an index or
    an LLM prompt;
  - data, not indexed as text: `.csv`, and text files over about 200 KB;
  - text: other `.txt`, `.md`, `.py`.
- Web pages saved as text carry UI noise ("Spaces: 4 (Auto)", "Terminal") and lose formulas (images
  vanish, inline math comes out one character per line); phase 4 parsing has to clean this.
- A course expected output and the owner's own run look the same on disk, so which is which is
  asked when an exercise needs it.
- Labs and projects are references for what course tasks look like (input formats, parameters,
  output rules, marking points), and can also be generation sources when the user includes
  them (see "Material selection").

Reading files (decided 2026-10-28, pre-phase 3). Every source file is read once into a
markdown cache, which generation, RAG and an assistant drafting exercises by hand all read
instead of the original files.
- Cache: `$WORKSPACE_DIR/cache/<source path>.md`, one file per source file, one section per
  page or slide, headed with the location (`## p.12 · slide 2.10`), followed by a comment
  with how it was read (text layer or vision model, model name, date) and a hash of that
  page's content. A page whose hash is unchanged is never read again; a changed file costs
  only its changed pages. A transcription also records the prompt version
  (`vision.PROMPT_VERSION`, raised with every prompt change; a test fails if the prompt
  changes without it), and a vision run transcribes pages made with an older prompt again,
  except pages marked `corrected` (fixed by hand against the slide), which are listed
  instead of overwritten (2026-10-28, after 130 pages were left on an old prompt). The hash is of the page content, not of a rendered image, so a
  library upgrade does not trigger a re-read.
- Pages that need the vision model (`materials/pdf.py`, revised 2026-10-28 on real data):
  ignore images that sit at the same position on at least 30% of the pages (logo, template)
  and images with 5 or more text-layer characters on top of them (a title's shadow effect,
  a text box's background: the text layer already has their words); flag a page whose
  remaining images cover at least 5% of it. An image inside a form XObject reports bounds in
  the form's coordinates, so the form's bounds on the page stand for it. History: the first
  rule (25%, no text rule) missed a slide of three small code screenshots; 5% without the
  text rule flagged every section-divider slide. The final rule flagged 162 of 462 pages,
  caught every code and pseudocode image checked by eye, and left out a code box whose code
  was in the text layer.
- Transcription (`materials/vision.py`): a flagged page is rendered to PNG and sent with its
  text layer; the prompt asks for code, pseudocode and commands verbatim in fenced blocks
  (pseudocode tagged `text`, symbols such as ← ∈ kept), diagrams as structured text, tables
  as markdown, decoration (photos, logos, word clouds) as one line, `[?]` for what cannot be
  read, and no invented links. Python blocks are checked with `ast.parse` (a block that does
  not parse gets a note, not a rejection). Thinking is turned off (`reasoning_effort:
  "none"`; transcription is copying) and output is capped at 2048 tokens. A runaway
  repetition (a unit repeated 8 or more times over at least 400 characters, e.g. `&nbsp;`
  filling a blank area) is cut with a note, since at temperature 0 a rerun loops the same
  way; output that hits the cap without a loop is kept as `partial` and done again on the
  next run. If the model fails, pages done so far are kept and the rest stay `pending`.
- Measured on the first course (Gemma 4 26B-A4B, local): thinking on took 25-80 s per page
  with about 85% of output tokens spent on hidden reasoning; thinking off took 4-11 s with
  equal or better quality (code, pseudocode and commands exact on the six pages read by
  hand). All 162 image pages: median 7.2 s, about 21 minutes. Weaknesses seen: symbols
  rewritten to ASCII (fixed by the prompt), an empty `...` continuation line dropped, one
  wrong description of an example. The pages read by hand stay the reference set for
  comparing models.
- Commands: `python -m tutor.materials <file or folder>` (text layer only; image pages
  `pending`), `--vision` to transcribe pending and partial pages with `LLM_MODEL`; calls go to
  `$WORKSPACE_DIR/logs/llm_calls.jsonl`.
- PDF library: pypdfium2 (Apache / BSD) for text and rendering, plus pdfplumber (MIT) only if
  RAG needs per-character font data. Compared on the first course's 9 lecture PDFs with
  PyMuPDF and pdfplumber: pypdfium2 was the only one to merge the characters these slides
  draw twice (shadow / bold) and keep diagram rows together; all three found the same slide
  footers; speed does not matter at this size. PyMuPDF (the earlier choice) is AGPL, which
  would follow the code into a packaged GUI and make the repo's MIT label misleading for
  reuse, and it offered nothing the other two lack here.
- Other formats, each library added only when a course has such files (pip-installable,
  permissive licence, as optional extras such as `tutor[office]`; a missing one is reported
  with the extra to install, not a crash):
  - `.pptx`: python-pptx (MIT): text, tables, speaker notes, and pictures as their original
    bytes with position, which go to the vision model directly. Diagrams drawn from shapes
    and SmartArt are lost without rendering, which needs LibreOffice or an export to PDF by
    the user; envcheck reports `soffice` as optional.
  - `.docx`: python-docx or mammoth (MIT / BSD); saved web pages: BeautifulSoup + markdownify
    (MIT), keeping LaTeX formulas (not html2text: GPL); `.ipynb`: the standard library's
    `json` (markdown, code and outputs per cell; output images to the vision model);
    images: Pillow, with pillow-heif for phone photos; `.xlsx`: openpyxl, read as data like
    CSV; EPUB: `zipfile` + BeautifulSoup (not ebooklib: AGPL); captions (`.vtt`, `.srt`):
    standard library; lecture recordings: faster-whisper, much later.
  - Not read: `.doc`, `.ppt`, Keynote and Google formats (the user exports to PDF, PPTX or
    DOCX); archives, as above. Office files are zip packages, so file and unpacked sizes are
    capped before parsing.
  - Materials container (owner, 2026-10-28): the reader runs on the host first, PDF only
    (pypdfium2 needs no system install). When office formats need LibreOffice, the reader
    can also run in a container with no network: source files mounted read-only, the md
    cache and page images written out, the vision model called from the host on those
    images. It isolates parser exploits in crafted files and bundles LibreOffice. It does
    not stop prompt injection: that text is meant to reach the LLM wherever it is parsed,
    so layer A and the test-run container answer it.
  - All-in-one converters (MarkItDown, Docling, Unstructured) were considered: they hide
    page and slide numbers, which sources must record, or bring heavy layout models.
    MarkItDown may serve as a fallback for formats without a reader.

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
   exercises. Tests may also call `run_mrjob(..., combiner=False)` themselves, so a student's
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
- The validator checks `metadata.json` first; if it fails, every other rule is SKIPPED,
  since they read their settings from it. The message names the field and the problem.
- Unknown fields are errors (`extra="forbid"`) and types are strict (`"yes"` is not a bool),
  so a typo or an old spelling is reported instead of ignored. Cross-field rules: an mrjob
  exercise names its runner and only it may; `combiner_check` needs the inline runner;
  function and MRJob entry points have no dots, file-level Spark ones are `Class.run`; in a
  manifest, priority marks name listed exercises, each at most once.
- The models are also exported as JSON Schema files in `schemas/` (`python -m tutor.schema
  --export`), so an assistant that is not running Python, and phase 3's structured output,
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
- clear: one reading of the question, inputs and outputs fully specified;
- fits the material: matches how the lectures and labs present the idea (the stripes
  case below is a failure of this);
- within what has been taught: no API or idea from later weeks; partly checkable
  deterministically with a per-week list of allowed APIs (api check);
- targets the concept: solving it needs the knowledge point it is filed under, not a
  workaround.
The owner rates the first batch after solving each exercise (one line per exercise, a few
seconds), optionally with a free-text remark ("too easy", "unclear input format"). Format
fixed at the first rating (2026-10-28): y or n per point, in the order above (`pairs yyyy
too easy`), since yes/no labels compare cleanly with a judge's and need no scale; the
assistant writes one JSON line per exercise to the workspace's `ratings.jsonl` (batch,
exercise, date, clear, fits, taught, targets, remark), one file for all batches so later
plans read every rating at once. Those ratings are the labels an LLM judge is checked against before
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

## Generation pipeline (LangGraph)

```
plan -> [interrupt: owner approves / edits, adds preferences] -> for each plan item:
  retrieve_context -> generate -> validate --pass--> save
                         ^           |
                         +-- repair <-+ fail, round < 3
                                      + fail, round = 3 --> discard + log
```

Design approved 2026-10-28 (owner):
- Pluggable: nodes are plain functions (state in, partial state out) in `graph/nodes.py`,
  calling `tutor.llm`; only `graph/build.py` imports `langgraph` (optional dependency group
  `gen`), and nothing outside `graph/` depends on it. No LangChain. LangGraph is used for
  what is tedious by hand: interrupt and resume with a SQLite checkpointer in the workspace
  (approve the plan tomorrow), conditional edges, and state history for debugging.
- State: sources, preferences, plan (items: knowledge point, slide range, type, interface,
  framework), and per item the context, draft files, validation report, repair round;
  outcomes per item for the phase 5 metrics.
- Nodes: `plan` (LLM; coverage.md gaps + slide titles of the sources + preferences ->
  BatchPlan, then interrupt), `retrieve_context` (no LLM, see below), `generate` (LLM,
  structured output), `validate` (no LLM: `tutor.validation` as it is, rule 0 to api),
  `repair` (LLM; gets the draft and the failing rules' details, not the passing ones, so
  independent failures such as rule 3 and api are fixed in one round), `save` (moves
  the exercise into `batches/`, updates the manifest; a `coverage.md` change is proposed to
  the owner, never written).
- Drafts are written to `batches/.drafts/` and kept when discarded, to study failures.
- The draft (`graph/state.py` ExerciseDraft, sent as a JSON Schema; GLM enforces it) asks
  only for what cannot be derived: skeleton.py is student.py, and a mutant is a list of
  exact replacements in the reference (`old` must occur once) plus its bug, applied by
  code. So every mutant differs from the reference by exactly the stated change, its
  docstring is uniform, and the reply is shorter than 3-6 copies of the reference.
- Prompt: a stable system part (the repo's format rules, with the safe-check allowlist
  read from `safety.py`; the `tutor.testkit` API read from the module; the example
  exercise closest to the item's framework and interface; the workspace's
  `conventions.md`, the same file a hand-drafting assistant reads), then the item and its
  course pages, then, when repairing, the last reply and the failures.
- Stops: 3 repair rounds per item, a token budget per batch, an LLM error stops the current
  item and keeps the finished ones.
- Roles in `[llm.roles]`: `plan` = GLM-5.3-Flash with thinking low (see the experiment
  below); `generate` = DeepSeek since 2026-10-29 (the week 4 rerun below); `oracle` = a
  different model family (GPT, decided 2026-10-28 in place of local Gemma; GLM-5.3-Flash
  once the OpenAI key expires), since the same model writing reference and oracle is not
  an independent check. The CLI refuses an oracle from the generating model's provider.
- Experiment 2026-10-28, one item (a closure / accumulator debugging exercise from the
  week 4 slides), same validator:

  | Model, thinking | Rounds | Time | Tokens | Mutants |
  |---|---|---|---|---|
  | GLM-5.3-Flash, on | 4 (2 cut off at 32k output) | 28 min | 164k | - |
  | GLM-5.3, on | stopped after 2 cut-off rounds | 19 min | 92k | - |
  | GLM-5.3-FlashX, on (old prompt) | 1 | 4.3 min | 46k | good |
  | GLM-5.3-Flash, low (old prompt) | 2 | 1.7 min | 39k | arbitrary edits |
  | GLM-5.3-Flash, low | 1 | 1.3 min | 18k | good, two slips |
  | GLM-5.3, low | 2 | 2.5 min | 42k | best: 3 misconceptions, 1 slip |
  | gpt-6.1-terra, default | 2 | 1.2 min | 38k (~$0.06) | good, 3 misconceptions |

  With thinking on, GLM spent its whole output on reasoning and was cut off before the
  answer, so thinking is low by default (`--thinking on` to override). The prompt change
  that mattered: mutants must be misconceptions of the knowledge point (at most one plain
  slip), and `source` must cite the pages (a model copied "synthetic" from the example; a
  code check now rejects it). One item per model, so a direction, not a measurement; phase
  5 repeats it on whole batches.
- A reply that cannot be read at all (cut off, not JSON) is not "repaired": the next round
  asks the item again with what went wrong, since an empty draft gives repair nothing.
- First real batch (week 4, 6 items, GLM-5.3-Flash, 2026-10-28) and what it changed: one
  saved, two discarded after three rounds each on the same mutant error (`old` not found:
  the model wrote other quotes or indentation than its own reference, and the error said
  only "not found"), and one item crashed the whole run, so collect never ran and two
  items never started. The crash was local, not the model: the example loader read
  macOS's `.DS_Store` (binary, git-ignored, so never in CI) from the file-level example;
  it was first blamed on a GLM reply, until GPT failed at the same byte. Fixes: the
  example loader reads only the exercise's own text files; any exception in an item is
  that item's error (logged, the others finish, collect runs); the client decodes replies
  with replacement (harmless either way); the item graph no longer inherits the batch's
  checkpointer (it saved every step's models in the batch's SQLite); a mutant error lists
  every failing mutant with the closest line of the reference, copied exactly; `resume`
  continues a run that stopped midway without redoing finished items (tested), and
  `resume --profile` finishes it with another model; the manifest names every model that
  generated its exercises.
- Week 4 again with four models (2026-10-28/29, same plan items; small samples): GPT 6/6
  VALID (about $0.07 each), DeepSeek 5/6 (about 0.06 RMB each, the fastest), GLM-5.3-Flash
  saved 4 and discarded 2, GLM-5.3 discarded 3 of 3. DeepSeek became the `generate` role.
  The four discards that were not the model's fault changed two things:
  - GLM-5.3 lost all three to one mutant each whose `old` it invented (a "placeholder"
    line not in its reference) and sent back unchanged through three repairs. A mutant
    that cannot be applied is now left out while at least three others remain
    (`MIN_MUTANTS`, the schema's minimum): a VALID draft is saved without it, and a draft
    repaired for another reason is asked to fix it too. The round's `dropped` list goes to
    `generation.jsonl`. Cost: an exercise may have one mutant fewer, never fewer than
    three.
  - DeepSeek put the API calls it wanted used into `required_any`, which lists names the
    code defines, and could not read that from "must define one of". The constraint
    fields now carry descriptions, which reach the model in the draft's JSON Schema, and
    the api messages say the names are defined, not called. There is still no "must call"
    constraint: what an exercise must use is checked by its tests and `forbidden_calls`.
- What repair is told (2026-10-29). A week 4 item came back word for word through all
  three repairs: rule 1 said only pytest's last line ("2 failed, 3 passed"), so the model
  could not see what to fix, and at temperature 0 an unchanged prompt returned the same
  reply. Now: a failing rule 1, 2 (crash), 4 or 5 carries `failure_excerpt` of pytest's
  output (each failing test, its line in the test file and the `E` lines, at most 2500
  characters, absolute paths cut to the file name since the text goes to a model; the CLI
  report still shows the one-line detail); repair round N asks at temperature 0.2 * N; and
  a reply identical to the previous one is recorded (`unchanged` in the attempts) and the
  next repair is told so.
- Prompt rules added for failures seen on week 4 (2026-10-29), each tied to one: test
  expected values are worked out by hand from the question on small data, never taken
  from the reference (a test said 2 bids where the data had 3); a rule 1 failure tells
  repair that the test can be the wrong side (three repairs had edited a right reference);
  the reference is written as a good student would, never forcing an API into a role it
  does not fit (GPT's reference sent a countByKey result back to the cluster to join).
  Rules stay few and concrete: a longer prompt is followed less closely, and what code can
  check (constraints, mutants) is checked in code. After the first rule the same item
  passed rule 1 at once, and the independent oracle caught a real bug in its reference
  (`reduce()` on an empty input, which the question says gives no lines). The constraint
  descriptions had named "the entry point" as a required definition, and a model then
  wrote `AuctionStats.run`, which the api check did not know: it now accepts
  `Class.method`, the form `entry_point` uses.
- Prompt order and cache accounting (2026-10-29). Providers cache the longest prompt
  prefix they have seen, and the item's own lines came before its course pages, so items
  of one lecture shared only the system prompt (DeepSeek: about 6k of 16k input tokens
  cached on a first call). Now the system prompt holds what the whole course shares
  (rules, testkit API, conventions) and the user message gives the course pages, then the
  example exercise (chosen by framework and interface), then the item; a batch starts its
  items sorted by lecture, framework and interface. Each item counts input, cached input
  and output (`usage` in generation.jsonl), and the CLI prints the batch's share served
  from the cache. Considered: a summary of the lecture instead of its pages, to shrink
  the prompt; rejected, since exercises must match the slides' code and cite their pages,
  a summary is one more model output that can be wrong for every item after it, and with
  caching the pages cost little (output tokens dominate the cost).
- Streaming (2026-10-29): generate, repair and oracle calls receive the reply in pieces,
  so `timeout_s` bounds a pause, not a whole long reply, and the CLI shows
  `receiving: N characters` as it arrives; usage comes from the last piece
  (`stream_options` only for OpenAI and DeepSeek, which document it). Plan and vision
  calls are unchanged. The first real run showed why it is worth having: DeepSeek ran away
  for 32,000 output tokens (cut off, retried) and the progress lines made it visible.
  A rule 3 failure with a survivor now reminds repair to catch a bug in how the result is
  computed with a spy, or to replace a mutant whose result equals the reference's (a
  `groupByKey` then sum mutant had survived three repairs).
- Who is wrong when rule 1 fails (2026-10-29). A reference and a test disagreeing can be
  either side's fault, and DeepSeek, unable to tell, sent the same draft back word for
  word (copying is near-certain even at temperature 0.6). The oracle does not use the
  tests, so the pipeline validates with `oracle_on_failure`: rule 4 still runs after a
  rule 1 failure. If the reference agrees with the oracle (an independent reading when
  the `oracle` role is set), repair is told the tests' expected values are the likely
  mistake; if not, the disagreement and the oracle's code go to repair as usual. The CLI
  keeps skipping rules 2-5 after rule 1. Cost: one rule 4 run (15-30 s) per such round.
- A `repair` role (2026-10-29): a larger model takes over from the second repair
  (`stronger_repair_from`), since the cheap model fails at diagnosis, not at writing: on
  the hardest week 4 item DeepSeek-Flash sent drafts back unchanged or half fixed, while
  GPT generating fixed it in one repair. Each attempt records its model; the manifest
  names the repair model; the oracle must come from another provider than both writers.
  First try with DeepSeek-V4-Pro (thinking off) did not fix that item either, and lost a
  round copying the oracle's code, which rule 4's failure shows, into the oracle
  interface; the message now says not to.
- Planning thinks (2026-10-29). The plan is one short reply per batch, so reasoning costs
  little (about $0.03 with DeepSeek-V4-Pro). Week 5 planned three ways: GLM-5.3-Flash at its
  lowest reasoning repeated a word count the coverage table had dropped; Pro with thinking
  off wrote vague checks and gave one file-level item of six against a preference to mix;
  Pro with thinking on dropped the repeat, chose the slides' "one problem in DataFrame and
  SQL" practice, mixed the interfaces and named edge cases, but left out the schema point.
  `plan_thinking` is on by default; `--plan-profile` picks the planner for one batch. The
  first week 5 batch (GLM plan, Flash writing, Pro repairing with thinking, GPT oracle)
  saved 6 of 6: two after a Flash repair, four rescued by one Pro repair each, about $0.39
  in all at peak prices.
- A diagnosis first, and the oracle's own mistakes (2026-10-29). The draft's first field
  is `diagnosis`, filled by repairs before any file: what failed, the right result worked
  out by hand, which file is wrong and why. A scratchpad that works without thinking, and
  a log of what the model believed. With Flash alone repairing two week 5 items that had
  needed Pro, one passed (three bugs, each named and fixed in turn) and the other made
  progress every round instead of being copied back. The Pro-written week 5 batch (2 of 5)
  showed two faults of the pipeline: Pro wrote whole oracles where an interface was asked
  for, on every first draft, and the oracle model imported `io`, which the safety check
  refuses, after which repair asked the generating model, which cannot change that code,
  three times. Now a whole oracle is cut to an interface in code (signatures and
  docstrings stay, the oracle model never sees the bodies); the oracle model is given the
  import allowlist, its oracle is safety-checked in the node, and it rewrites its own
  oracle once when told what is wrong. The rule 4 note goes with a real disagreement only,
  not with a differential test that cannot run.
- Batch (step 2, approved 2026-10-28): `plan` (LLM) reads coverage.md, one line per cached
  page (PDF page, footer, title), the existing exercise ids and the owner's preferences,
  and returns `count` PlanItems with a reason each; code checks the count, unique and new
  ids, lecture names and slide ranges, and asks again (twice at most) with the problems.
  `approve` is a LangGraph `interrupt`: the CLI shows the plan and writes it to
  `.drafts/<batch>/plan.json`; the owner approves (with any edits made to that file) or
  sends it back with feedback, which goes to `plan`. A SQLite checkpointer in
  `.drafts/<batch>/` keeps the paused batch, so `python -m tutor.graph resume` continues it
  in another terminal or another day. Approved items fan out with `Send` and run the item
  graph in parallel, at most `--parallel` at once (default: CPU cores / 4, 1-4: each
  validation runs Spark on every core). One failing item (model unreachable, name clash)
  does not stop the others. `collect` writes the manifest once (parallel items never
  write it) with the preferences in `notes`, and `coverage-proposal.md`, the coverage.md
  changes for the owner to apply. Which types a batch mixes (e.g. debugging exercises) is
  left to the preferences, not a fixed rule.
- Logs: `llm_calls.jsonl` with the node as purpose; one `generation.jsonl` per batch
  (item, rounds, outcome, failure reasons).
- Built in three steps: one item generate -> validate -> repair -> save (done); then plan
  with the interrupt, preferences and parallel items (done); then the oracle in its own
  context (done 2026-10-29, below; without an `oracle` role `generate` writes the whole
  oracle, as hand drafting does).
- The independent oracle (approved 2026-10-29). The generating model still writes
  `test_oracle.py` (how random inputs are built and fed to the program, which is tied to
  Spark and the file format), but `oracle.py` only as an interface: signatures, a
  docstring per function saying what goes in and out, `raise NotImplementedError`. A
  `write_oracle` node between generate / repair and validate gives the oracle model the
  question (student.py's docstring), that interface and the sample data, never the
  reference, tests, mutants or hints, and puts its oracle.py into the draft. It is written
  again only when one of its inputs changes (a hash), so most items cost one extra short
  call. When rule 4 fails, repair is told that the oracle is an independent reading of
  the question and gets its code: if the reference is right, the question is ambiguous
  there and is the thing to fix, which is what an independent reader is for. An
  interface with bodies, or an oracle the model could not write (missing or unwritten
  functions, a reply that does not fit), is a failure of the round that goes to repair
  without validating. Considered: the oracle model also writing `test_oracle.py`;
  rejected because Spark and file-level harnesses are where generation fails most, and
  the independence that matters is in the oracle's logic. The manifest and
  `generation.jsonl` name the oracle model.
- The LLM never touches the disk or runs code: nodes read the cache and write files, and
  generated code reaches `batches/` only after `validate` (including `safe`).
- LLM output parsed into Pydantic models, the same `schema.py` models that rule 0 checks;
  format failures are counted apart from validation failures.
- `validate` is pure Python and acts as the judge; repair receives the failure report.
- `retrieve_context` before RAG (phase 3, decided 2026-10-28): the cache of the batch's
  `sources`, whole (a lecture is roughly 15-25k tokens). When that does not fit the model's
  context, a slice instead: the slides that `coverage.md` lists for the batch plan's
  knowledge points. A slice is chosen by label, not by a query, so it is deterministic and the owner
  sees exactly which pages went in. For a local model, raising the context length it is
  loaded with (8192 on the owner's Mac so far; the model supports more, at the cost of
  memory) comes before slicing.
- Every generated batch mixes function-level and file-level exercises (see "Exercise interfaces").

## RAG

- Reads the per-file md cache (see "Reading files"), not the PDFs; chunking by section / code
  block, keeping source file, page and slide.
- Retrieval filters by the batch's `sources` first, so a week-2 batch only draws on week-2
  material; searching the whole course is the fallback, not the default.
- Hybrid retrieval (BM25 + vectors): API names like `aggregateByKey` are exact-match problems
  that pure vector search handles poorly.
- Evaluate with 20-30 hand-labelled queries: recall@k for vector-only vs BM25-only vs hybrid.
  The labels already exist by then (2026-10-28): each knowledge point in `coverage.md` with
  its slides, and each exercise's `source.pdf_pages`, is a query with its relevant pages.
- Chunks are the cache's page sections, the same unit phase 3's slices use, so the
  chunker is not new work.
- Vector store and embedding model not yet decided (Chroma or FAISS candidates).

## Evaluation metrics (README results table)

- First-pass validity rate; validity after repair loop.
- Mutant kill rate.
- Tokens / cost per valid exercise.
- With RAG vs without RAG; across models.

## LLM engineering candidates (review at the start of each phase)

Proposed 2026-10-26 from a survey of current LLM application engineering, mapped onto this
project. Not decided yet: at the start of the phase named in brackets, review each item with
the owner and then move it into that phase's plan, the backlog, or "Decisions not to do".
Already decided elsewhere and not repeated here: LangGraph loop, Pydantic parsing, the
deterministic judge, hybrid RAG with recall@k, the phase 5 metrics, the hint leak guard.

- [3] Model access, decided in principle 2026-10-26 (details reviewed at phase 3 start):
  - one provider-agnostic client: Anthropic, OpenAI, and local models through an
    OpenAI-compatible endpoint (Ollama, LM Studio, llama.cpp; Apple silicon runs them);
  - bring your own key: from `.env` or the OS keychain, never logged, never written into
    the workspace, an export or an error report;
  - README states the data flow: with an API model, course material and the learner's
    code go to that provider; a local model keeps them on the machine;
  - cost estimate before a batch is generated, and a budget cap that stops the run (the
    user pays), using the call log below.
  - Local first (decided 2026-10-28, owner): development and vision transcription start on
    a local model through LM Studio's OpenAI-compatible server; a cloud provider is added
    later through the same client. Local vision model: Gemma 4 26B-A4B (mixture of
    experts, about 4B active, about 15 GB, on the owner's M2 Pro with 32 GB); Qwen3.8 27B
    was dropped (owner: too power-hungry). A 15 GB model and Spark validation compete for
    memory, so generation and validation do not run at the same time.
  - First cloud provider (owner, 2026-10-28): Zhipu's bigmodel.cn, OpenAI-compatible at
    `https://open.bigmodel.cn/api/paas/v4`, with GLM-5.3-Flash (native image input, base64
    data URLs, 1M context; the owner has free tokens for it). How to minimise thinking
    differs by provider and model: LM Studio takes `reasoning_effort: "none"`; GLM takes
    `thinking: {"type": "disabled"}`, but GLM-5.3 models reject it and accept only
    `reasoning_effort` low / high / max, so the client tries `disabled` and falls back to
    `low` on a 400. With a cloud model the slide images go to that provider; the README
    states this data flow.
  - Several models (owner, 2026-10-28): keys stay in `.env`, one per provider
    (`ZHIPU_API_KEY=...`); everything else is named profiles in the workspace's
    `workspace.toml` (`[llm.profiles.<name>]`: provider, model, optional base_url,
    api_key_env, reasoning_effort, timeout_s), and `[llm.roles]` maps a task (vision,
    generate, oracle, ...) to a profile, so the oracle can come from a different model than
    the exercise and phase 5 can compare models; `--profile` overrides for one run. The
    provider (`lmstudio`, `glm`, `custom`) supplies the default base URL and the thinking
    switches. A profile holding an `api_key` field or a key-like value is refused, unknown
    fields are errors, and a missing variable is named in the message. Without an `[llm]`
    section, `LLM_PROVIDER` / `LLM_MODEL` / `LLM_BASE_URL` / `LLM_API_KEY` act as one
    profile. Like `[safety]`, the section is the owner's: generation never writes it. Apple Intelligence's on-device model is reachable too, through third-party
    bridges that serve it as an OpenAI-compatible local API (apfel and similar; not an
    Apple interface), so the same client can call it with no extra code. It is not a default:
    about 4096 tokens for input and output together (one week's material and the format spec
    do not fit), no image input found, one fixed small model, and rate limits for
    background use. It may serve as one more local arm in the phase 5 model comparison or for
    short text tasks.
- [3] Summary of recently covered material in the generation prompt (ContentGen found it
  improved generated questions); built from `coverage.md` and the batch's sources.
- [3] Call log: one JSONL line per LLM call (node, model, input / output / cached tokens,
  latency, repair round, outcome, failure reason). The phase 5 cost metric needs it; same
  idea as `attempts.jsonl`. A tracing UI (e.g. Langfuse) only if the log is not enough.
- [3] Prompt caching: the generate prompt has a long stable prefix (exercise format spec,
  example exercises, the week's material), resent on up to 3 repair rounds. Measure cost
  with and without it.
- [3, prerequisite] Image slides through a vision model, transcribed once and stored in the
  workspace with slide footer and PDF page; the input side of RAG. Decided 2026-10-28, see
  "Reading files".
- [3] Native structured output (JSON schema / tool use) instead of free text parsed after
  the fact. Count format failures apart from validation failures, or the validity metric
  mixes two causes.
- [3] Human-in-the-loop: a LangGraph interrupt after a batch plan (knowledge points,
  exercise types, sources) so the owner approves before generation, as batches are approved
  now.
- [3] Preference chat at generation (owner, 2026-10-26, in place of measuring manual edits):
  a free-text box (a CLI prompt first, a GUI chat box later) where the user states
  preferences before and during generation: focus topics, difficulty, interface mix, "more
  combiner practice", "avoid X", or "make exercise 3 harder". Preferences feed the batch
  plan and the repair / regenerate step and are stored in `manifest.json`, so a batch can
  be explained and rerun. They steer content only: validity stays with the deterministic
  validator, and a request outside what has been taught (e.g. an API from a later week) is
  allowed only when the user asks for it explicitly, and is flagged in the manifest. The
  number of follow-up requests per batch and per exercise is logged: a cheap proxy for the
  manual effort the ablation below does not measure. Shape decided 2026-10-26:
  - per batch: three points to speak, before the plan ("more combiner practice"), at the
    plan (swap an exercise, which doubles as the human-in-the-loop approval) and after
    generation ("exercise 3 harder", "exercise 6 unclear, redo"); a redone exercise is
    validated again like any other;
  - standing preferences: habits that hold across batches (course tokenizer line in text
    skeletons, interface mix, reference written as numbered steps) live in one
    machine-readable workspace file (e.g. `preferences.toml`) that every generation reads,
    instead of being scattered over notes the generator cannot see;
  - feedback after solving is not a separate feature: the one-line teaching-quality rating
    (see "Validity vs teaching quality") gains an optional free-text remark, and the next
    batch's plan reads the ratings and remarks of earlier batches.
- [3] Batch API for whole-batch generation: not interactive, usually much cheaper.
- Review of the [3] items (2026-10-28, owner adopted the proposals): adopted, as part of 3a:
  model access (local first; cost estimate and budget cap once a cloud provider is added),
  the recent-material summary, the call log, prompt caching as a prompt layout with a long
  stable prefix now (its cost effect measured once a cloud provider is used), structured
  output (JSON for the plan, metadata and manifest; if one large JSON with every code file
  fails often on local models, code files come as separate outputs and are assembled and
  checked in code; format failures counted apart from validation failures), plan approval,
  and the preference chat (CLI first). Batch API: backlog until a cloud provider is used.
  Isolation and vision transcription: decided separately (see their items).
- [3] Revisit isolation: phase 3 runs LLM-generated code, and the runner is not a sandbox
  (see "Code execution safety"). Decide whether validation of generated batches moves into
  a local container, which changes the "Docker only for reproducibility" decision there
  (allowed by "Local-first": it stays on the machine). Main reason, see the phase 3 note on
  malicious material. Also consider a static check on generated code that rejects network
  access, subprocesses and reads or writes outside the exercise directory. Decided
  2026-10-28: static check first, container later (see "Code execution safety").
- [3c] Calibrated LLM judge for hint quality only (leaks the answer? right direction?),
  checked against owner-labelled hints before its scores are trusted. Never for validity.
- [4] Reranker after hybrid retrieval, as one more arm of the recall@k comparison.
- [5] Personal workflow cost (Codex review 4): steps and minutes from adding lecture files
  to the first exercise, owner minutes spent fixing each exercise, validation wait, and
  whether a failed run can resume. The first batch is the hand-made baseline, taken from
  this document's batch notes (fixes: page references, stripes, an equivalent mutant,
  too-regular data) and `attempts.jsonl`, with no new logging for the owner.
- [5] Local model as one more arm of the model comparison: validity, repair rounds and
  cost of a local model (run on the owner's Mac) against API models, judged by the same
  deterministic validator.
- [5] Long-context baseline: "without RAG" means the whole week's material in the prompt
  (with caching), not no material, so the comparison shows whether retrieval earns its keep.
  A third arm (2026-10-28): the labelled page slice from `coverage.md`, i.e. perfect
  retrieval, the upper bound that shows how far RAG falls short of it.
- [after 3, optional] MCP server exposing `validate` (and later `hint`) so any coding agent
  can check the exercises it writes.
- Not proposed: fine-tuning or RL (too little data; model + validator suffices), multi-agent
  debate, GraphRAG, a hosted vector database (a few hundred pages fit local FAISS / Chroma),
  an LLM judging validity (already in "Decisions not to do").

## Code execution safety

Subprocess + timeout + temporary working directory. Docker was first meant only for
environment reproducibility (Java/Spark); since 2026-10-28 a container is also the planned
second isolation layer (see "Where isolation goes").

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

The README states this boundary too ("Running exercise code is not sandboxed", 2026-10-26),
since anyone who clones the repo validates code on their own machine.

Where isolation goes (decided 2026-10-28; built with the [3] isolation item): whatever
phase 3 adds (a container, a static check against network, subprocesses and files outside
the exercise) sits at the validator's entry, `validate()` and `python -m tutor.validation`,
not in the LangGraph `validate` node. A batch drafted with Claude Code is LLM-written code
from the same material, so it gets the same protection, and running the validator by hand
cannot skip it.

Isolation layers (decided 2026-10-28, owner): the material is files the user downloaded
themselves, so a cheap layer comes first and a stronger one later.
- A, static safety check (rule `safe`, `validation/safety.py`; done 2026-10-28). Every `.py`
  file validation runs (the exercise's own files except `student.py`, which is the
  learner's and is replaced in every run, and every solution file) is read with `ast` and
  never run. An allowlist, not a denylist: a denylist is never complete, and the first
  course's 255 exercise files used only 17 modules. Six rules: imports from the allowlist
  only; `os` / `sys` limited to `os.environ`, `os.path`, `sys.argv / exit / stdin / stdout /
  stderr / executable`; no `eval`, `exec`, `compile`, `__import__`, `breakpoint`; no dunder
  attribute outside a short list (`__name__`, `__module__`, `__qualname__`, `__init__`,
  `__class__`, `__doc__`), and `getattr` / `setattr` / `delattr` take a literal name; no
  literal path starting with `/` or `~` or containing `..` in a file-opening call
  (`open`, `Path`, Spark readers), no `home()` / `expanduser()`; no shell commands through
  the frameworks (`RDD.pipe`, MRJob `*_cmd` / `*_pre_filter`). The exercise folder may not
  hold `conftest.py`, `pytest.ini`, `setup.cfg`, `tox.ini` or `pyproject.toml`, which pytest
  runs on its own. Both checks cover every file a run copies in, subfolders included
  (pytest collects tests and conftest.py recursively; checked only at the top until
  2026-10-29, found in an outside review). A failure makes the exercise INVALID and skips every rule that runs
  code. A file with a syntax error is not a finding: Python cannot run it either, and the
  rules that run code report it.
  - The allowlist has three layers: a core in code (pure-computation standard library,
    `pytest`, `hypothesis`, `tutor`, the exercise's `student` / `oracle`); the modules of
    the exercise's `framework` (`mrjob`; `pyspark`); and a course's extra modules in
    `workspace.toml` `[safety] allow_imports`, found by walking up from the exercise. Only
    the owner edits `workspace.toml` and generation never writes it, so generated code
    (whose metadata an LLM writes) cannot widen its own allowlist.
  - It is the only layer that also covers the learner's own `pytest test_student.py`,
    which runs generated tests outside the validator. Python is too dynamic for a static
    check to prove code safe: it is a filter that stops the obvious, not a sandbox.
- B, test-run container: deferred to the generalization phase (second course) and the GUI
  (owner, 2026-10-28), when courses with riskier libraries and other users arrive. Until
  then phase 3 runs with layer A and material the user trusts, and a course whose modules
  are rated medium or high risk (below) is refused rather than run unprotected. Draft:
  the host orchestrates (validator logic, reports, attempts, hints, GUI, every LLM call)
  and the container only executes. The one seam is `execution.run_pytest`: its subprocess
  becomes `docker exec` in a long-lived per-course container (Docker or Podman, one
  implementation for macOS, Linux and WSL) with the exercise mounted read-only and a
  writable temp dir, `--network none`, a non-root user, a read-only root file system,
  memory / CPU / process limits, and a whitelist of environment variables (never `.env` or
  API keys). Results come back as they do now (exit code, JUnit XML, output), so `checks.py`
  does not change. The image is built from `workspace.toml` `[requires]`, with envcheck run
  inside and outside so both use the same versions. On medium / high risk courses the
  learner's runs go through a command that runs in the container, and attempts are written
  on the host from its result. Measure the overhead first; if it is too slow on macOS, the
  fallback is the OS sandboxes coding assistants use (Seatbelt on macOS, bubblewrap on
  Linux). Costs: debugging needs an editor that attaches to the container; the Docker VM
  takes memory next to a local LLM.
- Risk tiers for a course's modules (design for the generalization phase): pure
  computation (numpy, networkx) is low and runs on the host; file- or URL-capable libraries
  (pandas `read_csv` takes URLs, numpy `load` unpickles) are medium; system-level ones
  (`subprocess`, `os.fork`, sockets, an operating-systems course) are high and run only in
  the container. Which modules a course needs is hard to list by hand, so an LLM may draft
  `[requires]` and `[safety] allow_imports` at workspace creation, each entry with its
  source (lab, page), and the owner accepts entry by entry, as for the GUI's `[requires]`
  draft. A generated exercise that needs an unlisted module fails `safe` and yields a
  proposal to the owner, never an automatic widening.
- Other languages (generalization phase, not built): a `language` field in metadata
  (optional, default `python`); tests stay pytest and run the compiled program as a
  subprocess, as file-level exercises already do; one safety checker per language chosen
  by `check_safety` (tree-sitter for most languages, sqlglot for SQL), with per-language
  allowlists. A few languages built in, others configured by the owner in `workspace.toml`
  (extension, compile and run commands, allowlist); those commands run, so generation never
  writes them. A language without a built-in checker needs the container.

## CI

GitHub Actions, `.github/workflows/ci.yml` (decided 2026-10-26, before going public):
- Runs when work reaches `main` (a push after merging `dev`, or a pull request into `main`)
  and by hand (`workflow_dispatch`), not on every `dev` push: each step is already checked
  locally with `pytest` and `ruff check .`, and the badge then shows the state of `main`,
  the branch visitors see.
- `test`: Python 3.11, `ruff check .` and the fast `pytest`. The first run also had 3.12,
  which failed on mrjob's `distutils` import (see "Environment"); the owner dropped it from
  CI for now (2026-10-26), since 3.11 is what the project uses. To bring it back, add
  `setuptools` to the `bigdata` extras and 3.12 to the matrix.
  No JDK: run in a clean clone with a fake `java` that logs every call, the fast suite
  started no JVM (2026-10-26).
- `secrets`: gitleaks 8.30.1 (the local version) over the whole history with `.gitleaks.toml`,
  because GitHub's own secret scanning is not available while the repo is private, and a
  local pre-commit hook can be skipped.
- `spark`: `pytest -m spark` with Temurin JDK 17 on Python 3.11, only on a manual run
  (added 2026-10-26 after a Codex review asked for integration tests apart from the fast
  ones). About 3 min without Hadoop, measured in a fresh venv: 3 passed, the 5 Hadoop tests
  skipped. Use it after changing the validator or testkit, or to show the examples validate
  on a clean machine.
- Not in CI: the Hadoop tests (Hadoop is a 700 MB download and each job starts a JVM for
  about 7 s). Run `pytest -m spark` locally with `HADOOP_HOME` set before merging changes
  to `run_mrjob`.

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
- Priority marks (decided 2026-10-28, after the owner found the first batch of 10 too much):
  `manifest.json` may hold `priority` with `must`, `optional` and `skip` lists of exercise
  ids; an exercise not listed counts as must. Claude proposes the marks (exam weight, what
  later weeks build on, overlap with exercises already solved), the owner decides. Marks
  live only in the manifest: student.py is never edited for them and folders are not
  renamed, so attempts keep their ids. "Solved" in a batch's done condition means its must
  exercises; skipped ones need no work and no rating.
- `attempts.jsonl` gets one line per exercise per test run (time, batch, exercise, mode,
  result, pass / fail / error counts, first error, partitions, duration). Nothing reads it in
  phase 2; it is kept so review batches can later target weak topics, and as material for
  hint rules and mutants (roadmap 3c). Done 2026-10-25 as a pytest plugin, `tutor/attempts.py`,
  registered through the `pytest11` entry point, so it loads in every pytest run of the venv
  with no file in the workspace (a per-batch conftest.py was the alternative). It writes only
  for test files under `$WORKSPACE_DIR/batches/<batch>/exercises/<exercise>/`, so validator
  runs (temp dirs) and the repo's tests leave no trace; mode comes from the batch's
  `manifest.json`; a student.py that does not import counts as "error", not "failed"; any
  recording problem is a warning and never changes the test result. The entry point name
  equals the module name, so an explicit `-p tutor.attempts` is not registered twice. Adding
  the entry point needed one `pip install -e .`.

## Roadmap (in priority order)

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
2a. Harness extensions from the course's labs and projects (see "Interface extensions"), in small
   steps: `testkit.py` with the output modes; parameters and the new API constraints; MRJob
   interface with a synthetic example; DataFrame with a synthetic example; rule 5's combiner
   check; `attempts.jsonl`. **Done 2026-10-25.**
2. Golden exercises, solved by the owner. Run the whole loop by hand before automating it.
   - Batches from materials the owner picks (see "Material selection"), drafted by Claude Code and
     checked by the validator; the owner solves them in the batch's mode.
   - First batch (approved 2026-10-25): `2026-10-25_p2-w1w2-mapreduce`, mode `learn`, sources
     the first two weeks' lectures. Sized to cover every codable knowledge point, 9 exercises (10 since
     `mean-in-mapper` was added for the last gap), no Hadoop runner:
     `hdfs-bookkeeping` (python: blocks, replication vs erasure coding, NameNode metadata,
     heartbeats, under-replicated blocks), `mapreduce-by-hand` (python: map, per-map combine,
     hash partition, sort, group, reduce into R parts), `streaming-reducer` (python: Hadoop
     Streaming reducer over sorted lines without buffering), `word-count-mrjob`,
     `max-temperature` (NCDC fixed-width parsing, max combiner), `in-mapper-combining`,
     `mean-combiner-debug` (debugging: a broken mean combiner, combiner check), `cooccurrence-pairs` and
     `cooccurrence-stripes` (same question, two implementations). Slides that are images (code and
     pseudocode) were first taken from Lin & Dyer ch. 3, then read as rendered page images
     (macOS PDFKit) on 2026-10-25.
     Points that cannot be coded (history, definitions, architecture and design trade-offs)
     are left for concept questions. This batch replaces "five" as the size.
   - Generated 2026-10-25: all 9 VALID (2-6 s each; 5 mutants each, hints examples run).
     Lessons for phase 3: tests twice let a mutant live because the data was too regular
     (keys arriving already sorted; every station with equal counts per map task, where a
     mean of means equals the mean), and one drafted mutant was equivalent to the reference.
     Exercise data must break the regularities the bug relies on.
   - Revised 2026-10-25 after a second Codex review, against the slides (before the owner
     started): page references mixed PDF page numbers with slide footers, so every
     reference now gives both; each
     exercise lists its teaching assumptions in the question and in metadata `source`
     (`slides`, `pdf_pages`, `lab`, `assumptions`), e.g. the simulator's fixed value order
     (real Hadoop does not order values) and the three tokenizers the slides use. Reading the
     image slides found a real conflict: a slide emits one stripe per occurrence, which the stripes test had rejected (and a mutant enforced); the test now
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
     | 1 | RDD per-key average, no `groupByKey` | implementation | function | partition check, forbidden-API check |
     | 2 | DataFrame average word length per first letter, no `df.rdd` | completion | file (CSV out) | DataFrame comparison, forbidden-API check |
     | 3 | MRJob co-occurrence, pairs or stripes | implementation | file | MRJob runner, combiner check |
     | 4 | MRJob in-mapper combining | implementation | file | required-API check (`mapper_init` / `mapper_final`) |
     | 5 | MRJob mean with a broken combiner | debugging | file | buggy skeleton must fail, combiner check |

     `aggregateByKey` is listed but not taught, so it is not in the golden set.
     Order inversion and secondary sort go into a later batch: they need the Hadoop runner,
     which the golden set should not depend on.

   - Done when: every exercise of the first batch passes validation, the owner has solved
     its must exercises (see "Priority marks"; review-mode ones without asking for help),
     attempts are in `attempts.jsonl`, and
     the steps that felt repetitive are noted as input for phase 3. This batch is the
     baseline set for phase 5. While solving, two light additions (see "Validity vs
     teaching quality" and "Independent checks"): a one-line teaching-quality rating per
     exercise, and the owner's solutions and mistakes kept as the first held-out set.
     Phase 3a does not wait for this (decided 2026-10-28: it needs no solving data, so it
     runs alongside the owner's daily solving); 3b does.
   - Batch format contract (decided 2026-10-28, before `v0.1.0`; see "Batch format
     contract" and rule 0), in steps: `schema.py`, `python -m tutor.schema` and the JSON
     Schema export; the existing workspace batches migrated (21 metadata files: `entry_point`
     as a list, `function` replaced by `interface` + `entry_point`; `exercises/` untouched,
     all 21 VALID again); rule 0 in `validate()`, the examples' metadata in the one
     spelling, tests for a missing, mistyped and unknown field; the workspace `AGENTS.md`
     "Authoring exercises" section. **Done 2026-10-28.**
   - Then release `v0.1.0` (decided 2026-10-26, from a Codex review before going public):
     a `constraints.txt` with the dependency versions CI passed on, a short CHANGELOG, and
     a git tag. Not earlier: before phase 2 is solved, the whole loop has not run once.
     README for the release: one positioning line (private lectures -> runnable exercises
     -> pre-release checks on reference / skeleton / mutants / oracle -> distribution-
     semantics checks -> local practice feedback; AI exercises, IDE practice, autograding
     and no-answer hints alone all have precedents) and a built / planned table, so
     generation, RAG and hints are never read as existing features (Codex review 4).
2b. Code tracing and design exercises (raised from "deferred" on 2026-10-24; scope revised
   2026-10-25). The course is examined in writing, so beyond runnable
   code two more types (concept questions are out of scope: the owner generates those in chat
   for exam revision):
   - Code tracing ("what does this print and why"). Answers are one of: an exact output (or
     an unordered set of lines); an error and its type; "nothing has run yet" (lazy
     evaluation); "not determined" (order or value depends on partitions or scheduling).
     Each answer also asks for the reason. The validator proves the answer type by running
     the snippet: the error type is raised; a determined output is the same over repeated
     runs and partition counts; a "not determined" one is shown to differ. Lecture code that
     is illustrative only (e.g. a lambda containing an assignment, a syntax error) is
     rewritten as runnable code that shows the same point, with the slide it comes from
     noted in the exercise's private metadata.
   - Design exercises: a fixed template (intermediate keys and values, partitioning, sort
     order, why the combiner is safe, memory needed, then pseudocode), checked against
     rubric points and the exercise's `pseudocode.md`. A code exercise can gain a design
     part after it is solved.
   Before the generation loop (3a) covers every exercise type: the loop starts with code
   exercises, and these two types join once 2b is done (changed 2026-10-28; it was "before
   phase 3", which the split of phase 3 made too strict).
3. LLM generation, in three parts (split 2026-10-28, owner: most of phase 3 needs no
   solving data, so it does not wait for phase 2 to be solved).
3a. No solving data needed; runs alongside the owner's solving. First review the [3] items
   of "LLM engineering candidates". In order: isolation layer A (static check); the
   materials reader with the md cache and vision transcription ("Reading files"), with a
   minimal `llm.py`; the LangGraph generate-validate-repair loop with the [3] items adopted
   (model access, structured output, call log, caching, plan approval, preference chat),
   code exercises first; the oracle written in a separate context (see "Independent
   checks"). Isolation layer B moved to the generalization phase (2026-10-28).
   Prerequisites (from the 2026-10-25 slide review):
   image-only slides are read as images (a multimodal model or OCR) before anything is
   generated from them, and every source records slide footer, PDF page and any lab or
   textbook origin, with the exercise's teaching assumptions stated.
   Note, malicious material (2026-10-26): phase 3 feeds document text to the LLM and then
   runs the code it writes. A crafted PDF or web page can carry instructions (prompt
   injection) that make the generated reference, tests or mutants read files such as
   `~/.ssh` or send data out, and the validator would execute them with the user's rights.
   Before phase 3 ships: layer A checks every generated file before it runs, and users
   are told to generate only from material they trust (changed 2026-10-28: the test-run
   container, layer B, comes with the generalization phase; until then medium and high
   risk courses are refused, see "Code execution safety").
3b. Needs the owner's solving data; starts when the first batch's must exercises are solved:
   the held-out set of "Independent checks" (the owner's passing solutions must pass, their
   mistakes must fail), and an LLM judge of teaching quality checked against the owner's
   ratings before it is trusted (never for validity).
3c. Hint command, `python -m tutor.hint <exercise>` (decided 2026-10-24, after 3a since it
   needs the LLM client, and after 3b since its rules come from the owner's real mistakes;
   numbered 3b until 2026-10-28; first review the [3c] items of "LLM engineering candidates").
   Three layers, cheapest and most certain first:
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
   Layers 1 and 2 built 2026-10-29 (`tutor/hint/`, owner-approved design): the student's
   tests run in a temporary copy (student.py untouched, not an attempt); rules see the
   source and the first failures (eight from tutoring: full-width punctuation, syntax, a
   forbidden API, Python version mismatch, a TODO left, an RDD used like a list, a
   SparkContext left running, tuple output, nothing saved to outputPath). A run's
   signature is which tests fail with which `E` lines (addresses and timings normalised);
   each mutant's signature is computed once and cached in `solutions/<id>/.hints/`, keyed
   by a hash of the tests and the mutant. An exact match quotes the mutant's bug ("<the
   bug>, <what goes wrong>"); the same failing tests with other values is offered as a
   guess, the mutant whose assertion lines read most alike first (difflib ratio), since a
   mistake whose output order varies never matches exactly, even against itself. Several
   exact matches are all named ("it is one of: ..."). In a review batch only the "what goes
   wrong" half is shown. Mutants the tests
   cannot tell apart are reported (an exercise lacking a test). Each request is a line in
   `attempts.jsonl` (event "hint", layer, rule or mutant). First request on a Spark
   exercise about a minute (every mutant runs once), later ones the student's run only.
   Evaluation 2026-10-29, each of the 32 mutants of the six week-5 Spark exercises as a
   simulated student: first 29/32 named; the misses were one pair the tests cannot tell
   apart and two no-coalesce mutants whose output order varies. With the two rules above
   (name every exact match, rank guesses by similarity): 30/32 named, the other 2 named
   among the indistinguishable pair, none wrong. Real students' code will differ more from
   a mutant than a mutant does from itself, so this is an upper bound for layer 2.
4. Hybrid RAG. First review the [4] items of "LLM engineering candidates".
5. Evaluation script + README results. First review the [5] items of "LLM engineering
   candidates".

## Backlog (decided, not scheduled)

- Batch API for whole-batch generation, once a cloud provider is used (see the [3] review in
  "LLM engineering candidates").

- C1 value-order check: rule 5 also reruns with each key's values shuffled (a runtime
  subclass, like the combiner switch), catching reducers that rely on value order, the
  reason secondary sort exists. First verify that mrjob's inline runner delivers values
  sorted (it sorts whole map-output lines), which would hide such bugs today.
- C2 algorithm-quality warnings (see "Correctness vs algorithm quality"), later Hadoop
  counters (map output records, combine input / output) to report how much a combiner cut.
- C3 tokenizer: the course's labs hand out a fixed tokenizing line (a `re.split` over
  punctuation, recorded in the private course notes), so new exercises that split text give that line in the skeleton, as the labs do,
  instead of defining their own word rule (owner, 2026-10-25). Existing exercises are not
  converted (see "Decisions not to do").
- Workspace `coverage.md`: before each batch, list the lectures' knowledge points with their
  status and their slide ranges (footer numbers; the cache headers map them to PDF pages).
  Since 2026-10-28 these ranges also select phase 3's slices and label phase 4's retrieval
  test, so the footer reading must be right (a year in the footer was once read as the
  slide number); the next batch is chosen from the gaps (Codex's list: secondary sort, inverted
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
- No Hadoop Streaming interface: the course covers streaming for understanding only, and mrjob teaches the same concepts; streaming appears only as plain-Python
  exercises (see "Interface extensions").
- No timeout on the learner's own `pytest` runs: the isolated runner protects validation of
  generated code; in the terminal Ctrl+C stops a loop (see "Code execution safety").
- No wall-clock performance scoring: efficiency is judged as algorithm design, not seconds
  on a laptop (see "Correctness vs algorithm quality").
- No `aggregateByKey` in the first golden exercises: listed but not taught yet.
- No sharing of exercise banks (export / import between users, or a hub to download
  them). Generated exercises derive from course material, so sharing them distributes
  that material; and a hub contradicts "Local-first". Decided 2026-10-26.
- No full ablation study (decided 2026-10-26; Codex review 4 suggested a ladder: one-shot
  prompt + validator, then + repair, then + RAG, each with validity gain, manual edits
  saved and cost). Too much for one person who still has the first batch to solve. Phase
  5 keeps the arms already planned (first-pass validity, after repair, with / without RAG,
  long-context baseline, models), which cover most of the ladder; no separate study of
  manual edit time per arm. Instead, user effort shows up as preference / revision
  requests in the generation chat (see the [3] preference-chat candidate), counted per
  batch.
- No license change and no closed generator (decided 2026-10-26, misuse discussion). The
  license stays MIT: commercial reuse is legal anyway, and AGPL would put off some readers
  of a portfolio. The phase 3 generator stays public too: hiding it would remove half of
  the project's point, and an LLM chat can do the misuse it would prevent. Misuse is
  handled by describing the intended use positively (README "Intended use", no
  prohibitions), by MIT's as-is clause, and by protecting users (phase 3 note on malicious
  material).
- No CONTRIBUTING.md or SECURITY.md (decided 2026-10-26, before going public; a Codex review
  suggested both without knowing the repo's intent). This is a personal portfolio and
  revision tool, not a project seeking contributors: setup and checks are in README and
  `docs/setup/`, the synthetic-data rule in AGENTS.md. Vulnerability reports go through
  GitHub's private vulnerability reporting (repo settings, once public), which keeps them
  out of public issues; nobody should post keys or course material in an issue anyway.

## Deferred

SQLite, CLI, Docker (beyond the planned isolation container, see "Code execution safety"), error taxonomy (derive categories from real attempt data later).
Adaptive learning and exam mode are listed under the later phases below.

Later phases, after phase 5. Order decided 2026-10-26: second course, then adaptive review
(just before the GUI or built together with it), then the local GUI, then packaging and
release; exam mode stays open.
- Second course: use the tool for another course (other language or framework) to prove
  workspace, testkit and setup recipes are not shaped around one course. A long-term goal.
  Brings isolation layer B, the module risk tiers and other languages (see "Code execution
  safety").
- Adaptive review: use `attempts.jsonl` (failures, run counts, hints used) and `coverage.md`
  to find weak knowledge points and schedule `review`-mode batches with spaced repetition.
- Packaging and release: `pipx install`, one command to start, CI on macOS and Windows
  (WSL) as well, versioned releases after `v0.1.0`.
- Exam mode (timed, no hints): not excluded, reconsider later. Practice batches stay free
  of exam pressure (a write-everything-before-the-first-test-run rule was turned down,
  2026-10-24).

Local GUI (after the second course and adaptive review, just before packaging and release;
discussed 2026-10-26). It sits on top of the files and functions the earlier phases build,
so it comes late; the adaptive review screens can be built with it.
- Shape: a local web app (FastAPI backend reusing the CLI functions, which a later MCP server
  could share too, plus a light frontend), bound to `127.0.0.1` with a per-launch token or a
  Host header check so other web pages cannot drive it.
- Frontend without an npm build chain (candidate, owner 2026-10-27; confirm when the GUI
  phase starts): server-rendered Jinja2 templates plus htmx as one vendored JS file.
  Reason: most security alerts in web projects come from deep npm dependency trees and
  build tools; without npm, Dependabot only watches the Python side. A desktop shell is no
  better (Electron ships Chromium and still uses npm). The screens are manage-and-browse
  pages that do not need a SPA framework. What still needs care: the Python server deps
  (FastAPI, Starlette, uvicorn, `python-multipart` for uploads), and escaping everything not
  written by us (lecture text, LLM hints, learner code) to prevent XSS.
- Manage, do not edit: drag lectures and lab files into the workspace, pick sources and start
  generation with the preference chat box (approving the batch plan there if phase 3 adopts
  the human-in-the-loop item),
  browse batches with status from `validation.json`, progress from `attempts.jsonl`, mode and
  sources from `manifest.json`, coverage from `coverage.md`.
- Solving stays in the IDE: an "Open in IDE" button runs `code <exercise dir>` (or a
  `vscode://file/...` link; JetBrains has its own launcher). The GUI never runs the learner's
  tests; the IDE's `pytest` and the attempts plugin stay the one record of attempts.
- Shows only `exercises/`; `solutions/` (reference, pseudocode) at most after the exercise is
  solved and on explicit request, matching the tutoring rule.
- Files stay the source of truth; no database (SQLite remains deferred).
- Local only, per "Local-first": no accounts, no hosting. A settings page picks the model
  (API or local) and stores the user's own key in `.env` or the OS keychain, never shown
  again in full, with the data-flow note next to it.
- Environment page:
  - Infer requirements from course material: an LLM reads lectures and labs for named tools
    and versions and drafts `[requires]`; the owner confirms before `workspace.toml` is
    written.
  - Show `tutor.envcheck` results with a fix hint and a link to `docs/setup/<course>.md`.
  - Installation is not only pip: a course may need a JDK, Hadoop, a database, Node, R or a
    specific Python, and each can be installed several ways (brew, apt, winget, a tarball,
    SDKMAN, conda, a container). The method is chosen per case from what the page detects:
    OS and architecture, WSL or not, which package managers exist, conda vs venv, what is
    already installed (e.g. a JDK 21 next to the wanted 17) and whether admin rights are
    needed. Recipes are curated per component and platform, built from commands verified in
    `docs/setup/`; the LLM only picks among them, and when several fit, the page shows the
    options with their trade-offs instead of deciding silently.
  - Every command is shown before it runs, with a "check before executing" reminder and,
    for each one: what it does, where it installs, whether it needs sudo / admin, what it
    changes outside the project (PATH, JAVA_HOME, shell profile), the download source, and
    how to undo it.
  - Python packages into the project venv: one click, after the same review (a broken venv
    is deleted and rebuilt). Everything else, the user runs in their own terminal, then
    clicks "Check again". The GUI never runs system installs, and never runs LLM-written
    commands (arbitrary commands; hallucinated package names can be squatted). A component
    or platform missing from the recipes is reported as needing a new recipe.
  - This partly changes "Check, don't install" (see "Environment"): confirm with the owner
    when the GUI phase starts. The per-course test-run container (layer B, "Code execution
    safety") arrives with the generalization phase, before the GUI; whether setup also uses
    it is decided when the GUI phase starts.

## Open questions

- The embedding model (phase 4). LLMs: local Gemma 4 and bigmodel.cn GLM-5.3-Flash first
  (decided 2026-10-28, see the [3] model access item); others through the same client.

## Pseudocode

The course is examined in writing, so exam answers are written, not run. The course
pseudocode (Lin & Dyer style: `class Mapper / method Map / Emit`) maps one-to-one onto MRJob
`mapper` / `combiner` / `reducer`, so MRJob exercises double as pseudocode practice.
Each MRJob exercise keeps `solutions/<id>/pseudocode.md`: the reference solution written in exam
pseudocode, for comparison after solving.
