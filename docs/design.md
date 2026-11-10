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
  `tests/test_env.py` runs the same checker. One exception (owner, 2026-11-05): `python -m tutor.workspace
  init` creates a course's own `.venv` and installs Python packages into it, and changes nothing outside
  the course's folder. The GUI may run that command and `python -m tutor.container build`, whose
  recipe is our code's (owner, 2026-11-06). System software is still only checked.
- Installation: Python deps were per-course extras, `pip install -e ".[bigdata]"`; since each course
  has its own venv, `python -m tutor.workspace init` installs `[requires]`'s pip packages into it
  (`pyspark = "3.5"` as `pyspark==3.5.*`, the prefix envcheck accepts, in the same pip call as this
  package; owner, 2026-11-06), and envcheck's hint names that command. System deps (Java,
  Hadoop) in a per-course guide `docs/setup/<course>.md`, linked from README (decided 2026-10-25:
  README stays course-agnostic, and each course's system deps and pitfalls differ; the guide
  marks which platforms were actually tested). No install script: setup is one-off, and when it breaks,
  asking an AI is faster than maintaining a cross-platform script. Relaxed for the release
  (owner, 2026-11-07): one `uv` line installs the tool, and `tutor doctor` checks and says what
  to do next, never installing system software (Docker, Java, a compiler), which needs
  administrator rights or virtualisation settings and differs by platform and, as languages
  are added, by language (see "Roadmap", 6).
- Big data course (`bigdata`): Python 3.11, Java 17 (`JAVA_HOME`), PySpark 3.5.x (decided 2026-10-24: the course
  material names more than one Spark version, and the version is pinned per workspace in
  `workspace.toml` anyway, so another course's materials can choose differently). Spark 4.x enables ANSI SQL mode by default,
  which changes DataFrame/SQL error behaviour (casts, overflow, divide by zero); RDD API is unaffected.
  mrjob is no longer actively maintained: it imports `distutils` (removed in 3.12) and `pipes`
  (removed in 3.13). Found by the first CI run, 2026-10-26, and on 3.13. Since 2026-11-08 the
  `bigdata` extra, a course's `.venv` and its image add setuptools (whose shim provides
  `distutils`) and standard-pipes (the removed module as it was) where the Python needs them
  (see "Version conflicts"); fast and spark suites (Hadoop included) pass on 3.12.10 and
  3.13.3. PySpark 3.5 does not work on 3.14, and a course asking for both is refused.
- Big data course Hadoop: 3.3.6 standalone (the 3.3 line the course uses), Apache tarball
  unpacked to `~/opt/hadoop-3.3.6`, located via `HADOOP_HOME` in `.env`. Not `brew install hadoop`:
  its `yarn` binary clashes with the JavaScript `yarn`. Runs on Java 17, so one JDK serves
  both Spark and Hadoop. Hadoop launches task scripts with the `python3` on `PATH`, so jobs must
  pass `--python-bin` pointing at the project's Python 3.11.
- Platforms (decided 2026-10-31, owner): macOS, Linux and native Windows from one code base;
  only a few functions branch on the platform (process tree kill, see "Code execution
  safety"). Exception: the Hadoop runner is POSIX only (macOS, Linux, WSL). Measured on
  Windows 11 with Hadoop 3.3.6 + winutils: Hadoop itself runs, but mrjob's Hadoop runner
  hits one Windows gap after another (a space in `JAVA_HOME` breaks `hadoop.cmd`, `bin/hadoop`
  is a bash script, the streaming jar is not found, tasks start with `/bin/sh`), and the
  last could not be worked around: `hadoop.cmd` splits the comma-separated `-files` argument
  into several. mrjob is unmaintained, so on Windows the Hadoop tests and exercises are
  skipped with a pointer to WSL; the inline runner and Spark work there. Where (2026-11-01):
  `run_mrjob(runner="hadoop")` skips the calling test (`testkit.HADOOP_UNSUPPORTED`), so a
  learner sees one line instead of a `hadoop.cmd` error; the validator runs rules 0, safe
  and api on such an exercise and reports every rule that runs tests as skipped with that
  reason, so it is not VALID. Decided (owner): no separate "cannot check here" verdict for
  now. Known cost: generating a `hadoop` exercise on Windows would send it to repair until
  the rounds run out, so generate those in WSL. `tutor.envcheck` adds a `winutils` line on
  Windows when the course requires `pyspark` or `hadoop` (both files, and the folder on `PATH`).
  Every text file is read and written as UTF-8 explicitly: Windows' default is the ANSI
  code page, so a `·` or any Chinese character failed or was garbled (16 of 468 tests on
  2026-10-31). ruff's `unspecified-encoding` rule found 10 of the 296 calls, since it can
  only type a few receivers, so `tests/test_encoding.py` checks the source with `ast`, and
  catches a missing encoding on Linux CI too. Test runs get `PYTHONIOENCODING=utf-8` for
  their output pipe only; the code under test opens files with the platform default, as
  in the learner's own runs.
  Spark on Windows reads and writes files through Hadoop's native library, so it needs
  `HADOOP_HOME` with `winutils.exe` and `hadoop.dll`, and that `bin` folder on `PATH`
  (measured: without `PATH`, every file-level exercise failed with `UnsatisfiedLinkError`).

## Public repo vs private data

- `tutor/` is the public repo and holds code only. Private material lives in per-course workspaces
  under a sibling `smartcodereview-workspace/` directory (lectures, generated batches, attempts), located only via
  env var `WORKSPACE_DIR`. `LECTURE_DIR` is an optional override, default `$WORKSPACE_DIR/lectures`.
  Switching course = pointing `WORKSPACE_DIR` at another workspace.
- Each workspace has its own `.venv`, as a package one course needs can change another's results
  (numpy changes PySpark's `StatCounter`). Its VS Code terminals activate it through files kept in the
  folder (owner, 2026-11-04, for every workspace from now on): `.vscode/settings.json` sets `ZDOTDIR`
  through `terminal.integrated.env.osx` to `.vscode/zsh`, whose `.zshrc` loads `~/.zshrc` and then the
  venv, and whose `.zprofile` forwards `~/.zprofile`.
  - Why: seven solved exercises left no line in `attempts.jsonl`. The terminal was on conda's base
    environment, where pytest runs normally but the attempts plugin is not installed, so nothing
    warned. VS Code had the right interpreter selected, yet its own activation did not take in old or
    new terminals; the cause was not found. Trusting `(.venv)` in the prompt had already failed once.
  - Kept in the folder at the owner's request, so `~/.zshrc` is untouched. A `chpwd` hook in `~/.zshrc`
    worked in a scratch shell and should also cover other terminal apps; it was not chosen because it
    is global config. This covers VS Code terminals on macOS only.
  - Not added, tested in a scratch folder only: `required_plugins = tutor` in a workspace `pytest.ini`
    stops a run outside the venv with exit 4. It is the fallback if the gap returns.
  - `.env` names one workspace, and the plugin skips tests outside it without a word, so these
    terminals also set `WORKSPACE_DIR` and `LECTURE_DIR` to their folder.
  - `python -m tutor.workspace init` writes all of this and keeps any file that exists (owner, 2026-11-05).
    Checked end to end in a scratch folder, with a real venv and install, about 11 s; `docs/setup/bigdata.md`
    has the step.
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
├── .github/workflows/ci.yml             # fast tests 3.11-3.14, macOS/Windows, docker, gitleaks; spark by hand
├── .gitignore                           # P1, includes *workspace*/ as a backstop
├── .env.example                         # P1, WORKSPACE_DIR=..., HADOOP_HOME=...
├── .pre-commit-config.yaml              # P1, gitleaks
├── src/tutor/
│   ├── __init__.py
│   ├── config.py                        # P1 reads WORKSPACE_DIR, derives subpaths
│   ├── envcheck.py                      # P1 checks the workspace's [requires]; owns its rules
│   ├── workspace.py                     # creates a course workspace: folder, .venv, terminal files
│   ├── execution.py                     # P1 copy to temp dir -> subprocess pytest -> delete
│   ├── processes.py                     # a run's processes, ended together (process group / Job Object)
│   ├── container.py                     # the test image: build, run limits, status; image_recipe.py its Dockerfile
│   ├── validation/                      # P1 the checks; P2 rule 0, P3a the safety check (internals designed before coding); C: csource.py
│   ├── testkit.py                       # P2a run a file-level program, read part-*, compare output
│   ├── testkit_c.py                     # C exercises' tests: compile once, run, sanitizers
│   ├── languages.py                     # a course's languages: [languages.c], not-yet-supported ones, flags in lectures
│   ├── attempts.py                      # pytest hooks: host records or run-local event export
│   ├── attempt_records.py               # shared host attempt writer and failing-code snapshots
│   ├── attempt_transport.py             # bounded, versioned run-local pytest events
│   ├── test_command.py                  # tutor test: learner tests in the course image
│   ├── schema.py                        # P2 Pydantic models of metadata.json / manifest.json (rule 0),
│   │                                    #   P3 parses LLM output into the same models
│   ├── materials/                       # P3a course files -> per-file md cache (pypdfium2), vision pages; [requires] draft
│   ├── llm.py                           # P3a thin OpenAI-compatible client, standard library only, no LangChain
│   ├── llm_anthropic.py                 # Claude's Messages API behind llm.chat (provider "anthropic")
│   ├── graph/                           # P3 langgraph dependency added here; languages.py: per-language profiles;
│   │                                    #   nodes.py an item's nodes, oracle.py, drafts.py, batch.py, build.py the wiring;
│   │                                    #   prompts.py the templates, prompt_parts/ one module per language
│   ├── gui/                             # P7 the local web GUI: app.py the request checks, routes/ one module per
│   │                                    #   page area, the other modules what each page reads and writes
│   ├── retrieval/                       # P4 the page corpus, labels, scores and each route
│   └── eval/                            # P5 a report from the pipeline's own records, no model calls
├── examples/                            # P1 synthetic only, same shape as a real exercise
│   ├── station-longest-rise/            # plain Python, no framework: running state per key
│   ├── station-average-rdd/             # function-level: per-key average in PySpark RDD
│   │   ├── exercise/
│   │   └── solution/
│   ├── station-max-file/                # file-level: CSV input dir with messy lines -> part-* output
│   ├── station-monthly-df/              # DataFrame: groupBy/agg, orderBy, one CSV file, parameter
│   ├── station-condition-share/         # MRJob on Hadoop: order inversion, --jobconf tau
│   ├── station-above-average-c/         # C, a whole program: standard input -> output and exit status
│   └── station-drop-low-c/              # C, a function and a given driver.c: an array changed in place
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
    ├── .venv/                           # this course's Python, shared by all its exercises
    ├── .vscode/                         # settings.json, zsh/: terminals here activate .venv
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
    │   │   │       ├── question.md      # the docstring as Markdown, made by code (tutor/question.py)
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
    ├── attempts/code/                   # the student.py of each failed run, once per content
    ├── ratings.jsonl                    # one teaching-quality rating per solved exercise
    ├── index/                           # P4 BM25 / vector index, rebuildable
    └── reports/report.md                # P5 rebuilt after each recorded run; names exercises, so private
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
- Cache: `$WORKSPACE_DIR/cache/<source path>.md`; the lecture folder (`LECTURE_DIR` or
  `$WORKSPACE_DIR/lectures`) maps to `cache/lectures/` wherever it is, so the pipeline finds
  a lecture's cache by name (an outside `LECTURE_DIR` passed the config check but could not
  be cached until an outside review, 2026-10-29). One file per source file, one section per
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
  was in the text layer. A repeated image must also be the same image (by its data hash),
  and a page with images but no text layer at all (a scan) always needs vision: three
  different full-page scans in one box counted as decoration and left their pages empty
  (outside review, 2026-10-29; the 462 lecture pages were flagged exactly as before).
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
  `$WORKSPACE_DIR/logs/llm_calls.jsonl`. Error bodies quoted there and in error messages
  have the profile's key and anything key-like (`Bearer ...`, `sk-...`) replaced first, in
  case a server echoes the request (outside review, 2026-10-29).
- PDF library: pypdfium2 (Apache / BSD) for text and rendering, plus pdfplumber (MIT) only if
  RAG needs per-character font data. Compared on the first course's 9 lecture PDFs with
  PyMuPDF and pdfplumber: pypdfium2 was the only one to merge the characters these slides
  draw twice (shadow / bold) and keep diagram rows together; all three found the same slide
  footers; speed does not matter at this size. PyMuPDF (the earlier choice) is AGPL, which
  would follow the code into a packaged GUI and make the repo's MIT label misleading for
  reuse, and it offered nothing the other two lack here.
- Web pages (`.html`, `.htm`; `materials/html.py`, owner 2026-11-07, for a course whose notes
  are a Typora export and one whose material is Moodle quiz reviews saved from the browser).
  A page has no pages, so it is cut into sections, each cached as one page:
  `## p.N · slide L.N`, N the section's number and L the number in the file name
  (lecture10.html -> 10.1, 10.2; none in the name: 1, 2), a dotted number as a slide footer
  is, so plans, coverage, generation and the judge read it unchanged. A heading starts a
  section, a Moodle question starts one, and a section past 3000 characters is cut at a
  blank line, never inside a code block. Text only, as Markdown: code blocks exactly (Typora
  keeps each line in its editor's `pre.CodeMirror-line`, beside measuring copies left out),
  formulas as their TeX source (Typora's `math/tex` scripts; Moodle writes `\( \)` in the
  text), lists, tables; an image leaves `[image: name]`, and no image is read yet. Only the
  content region is read (Typora's `#write`, Moodle's `role="main"`, else `<main>`,
  `<article>`, `<body>`), and form fields never: a saved quiz holds the student's answers in
  them and the session's key (sesskey) in hidden ones; no attribute is copied, so no link
  either. A Moodle review also leaves out the attempt's summary (times, grade), each mark
  and state, and the feedback on the student's input, and keeps the question, its choices
  (not the one picked), the general feedback and the right answer. Nothing in the page runs
  or is fetched. A lecture is named by its file name without the extension, so `L4.pdf` and
  `L4.html` together are refused at upload. The standard library's parser, not BeautifulSoup
  and markdownify as planned below: each source needed its own rules (Typora's editor lines,
  Moodle's fields) that a generic converter does not know, and it adds no dependency. Tried
  on the owner's two courses: 34 Typora lectures (96k characters) and 22 quiz reviews, every
  code block and formula kept; two quiz files were 218k NUL bytes (broken copies), which
  now read as having no text. The generate prompt still says "one section per PDF page";
  left as is, since it is calibrated and a section is cited the same way.
- Other formats, each library added only when a course has such files (pip-installable,
  permissive licence, as optional extras such as `tutor[office]`; a missing one is reported
  with the extra to install, not a crash):
  - `.pptx`: python-pptx (MIT): text, tables, speaker notes, and pictures as their original
    bytes with position, which go to the vision model directly. Diagrams drawn from shapes
    and SmartArt are lost without rendering, which needs LibreOffice or an export to PDF by
    the user; envcheck reports `soffice` as optional.
  - `.docx`: python-docx or mammoth (MIT / BSD); saved web pages: built (see "Web pages"
    above), with the standard library; `.ipynb`: the standard library's
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

## Generation pipeline (LangGraph)

```
plan -> [interrupt: owner approves / edits, adds preferences] -> for each plan item:
  retrieve_context -> generate -> validate --pass--> save
                         ^           |
                         +-- repair <-+ fail, round < 3
                                      + fail, round = 3 --> discard + log
```

Design approved 2026-10-28 (owner):
- Pluggable: nodes are plain functions (state in, partial state out), calling `tutor.llm`:
  an item's in `graph/nodes.py`, the oracle's in `oracle.py`, a round's draft and its
  validation in `drafts.py`, the batch's in `batch.py` (one file until 2026-11-08); only `graph/build.py` imports `langgraph` (optional dependency group
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
  below); `generate` = DeepSeek-V4-Flash since 2026-10-29 (the week 4 rerun below), with
  thinking on since 2026-10-30 (the week 7 comparison below); `repair` = V4-Pro from the
  second repair on; `oracle` = a different model family (GPT, decided 2026-10-28 in place
  of local Gemma; GLM-5.3-Flash at high effort once the OpenAI key expires, see the
  week 4 cross experiment below), since the same
  model writing reference and oracle is not an independent check. The CLI refuses an oracle from the generating model's provider.
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
  VALID (about $0.07 each), DeepSeek 5/6 (about $0.01 each, the fastest), GLM-5.3-Flash
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
- A plain-Python example (2026-11-05): plain-Python items saw the Spark function-level example until
  then, so `examples/station-longest-rise` was written for them, about 1000 tokens shorter. Spark items
  keep theirs, so their prompts do not change. Rerunning 4 items of one long lecture with the same
  settings took VALID first drafts from 2 to 4 of 4 and tokens from 590k to 394k. That shows it did no
  harm, not that it helped: the two that passed both times cost about the same (201k and 203k), one
  earlier failure was a `getattr` the course conventions have since ruled out, and the other a reply
  that did not fit the schema, which may simply not recur.
- Week 7 with five arms (2026-10-29/30): the same plan of 11 plain-Python items from
  lectures 7.1-7.2 (stream algorithms) for every arm, arm D on 4 of them. Repairs from the
  second on are V4-Pro with thinking in every API arm; "s/item" is one item at a time.

  | Arm | Generate (thinking) | Oracle | First-round VALID | Saved | Rounds/item | s/item | Layout |
  |---|---|---|---|---|---|---|---|
  | A | V4-Flash, off | GPT | 2/11 | 11/11 | 2.3 | (parallel) | 5/11 |
  | B | V4-Flash, off | GLM-5.3 | 1/11 | 11/11 | 2.4 | 116 | 9/11 |
  | C | V4-Pro, on | GPT | 8/11 | 11/11 | 1.4 | 296 | 7/11 |
  | D | Claude Code (assistant mode) | GPT | 4/4 | 4/4 | 1.0 | 60-120 | 4/4 |
  | E | V4-Flash, on | GPT | 7/11 | 11/11 | 1.2 | 180 | 10/11 |

  - Thinking mattered, not model size: with thinking off, most first drafts failed rule 1
    on expected values traced by hand (DGIM bucket states, lossy-counting boundaries);
    Flash with thinking came close to Pro in about 60% of its time. E needed Pro once
    (33k of 699k tokens).
  - The time is reasoning: a Flash draft took 129 s and about 27k output tokens with
    thinking (about 210 tokens/s), 16 s and 4k without. GLM-5.3 wrote oracles in 9 s
    without thinking; V4-Pro with thinking took 280 s per draft and 120 s per repair.
  - GLM-5.3 as the oracle: 19 calls, all usable; its one rule 4 failure was the generating
    model's own test_oracle.py.
  - Layout: counted before the examples were rewritten in it (see "Learning rules").
  - Decided (owner, 2026-10-30): arm E's shape, Flash with thinking, Pro only when Flash's
    repair fails, so the time stays bounded; GLM-5.3 as the oracle after 2026-11-04
    (replaced 2026-11-01 by GLM-5.3-Flash, see the week 4 cross experiment). The
    oracle's thinking became its own setting, `oracle_thinking`, off by default: it
    followed `thinking`, so turning generation thinking on would have made GLM think.
  - Speed, measured 2026-10-30 on the same 11 items (owner asked for tests, not guesses):

    | Setup | Wall time | First-round VALID | Flash draft | Output tokens | Layout |
    |---|---|---|---|---|---|
    | E, one at a time (high effort) | 1983 s | 7/11 | 129 s | - | 10/11 |
    | high effort, 8 in parallel | 441 s | 8/11 | 119 s | 369k | 10/11 |
    | low effort, 8 in parallel | 211 s | 7/11 | 90 s | 261k | 11/11 |

    Eight requests at once did not slow DeepSeek (about 215 tokens/s each), so a batch
    takes about as long as its slowest item. DeepSeek's `reasoning_effort` has three
    levels, low / high / max, default high (medium counts as high); a profile's new
    `reasoning_effort_on` sets the level used when thinking is on. Low drafted 24% faster,
    repaired in 31 s instead of 104, with 29% fewer output tokens and the same first-round
    rate on this sample (one run each; part of the 211 against 441 s is that no low item
    needed a third round). Question quality per setup goes to the owner's blind comparison.
    The examples' rewrite showed too: 10 and 11 of 11 questions in the layout, against 5
    of 11 for arm A.
  - Validation runs its independent checks at once (after the reference passes: skeleton,
    each mutant, oracle, the partition runs, main), under process-wide caps on child runs:
    heavy runs, which start a JVM (Spark, the Hadoop runner, any framework not yet
    classified), default a third of the cores (`TUTOR_MAX_HEAVY_RUNS`); light runs (plain
    Python, MRJob inline) half the cores (`TUTOR_MAX_RUNS`). Heavy or light is derived from
    metadata `framework` and `runner`, never declared by an exercise (a model could get
    it wrong); a test fails when the format allows a framework the table does not list. A Spark exercise took 47 s
    instead of 96, a file-level one 27 instead of 65. The cap matters: with 8 or 12 runs
    at once the exercise's hypothesis run passed its 30 s timeout and the exercise came
    out INVALID; 4 and 6 took the same time, 4 with 10 s to spare. Checked: all 70 week 7
    exercises (plain Python, every arm) still VALID; `pytest -m spark` passes in 217 s
    instead of 462; the Hadoop exercise of week 3 took 94 s instead of 211; and 2 Hadoop
    and 13 Spark exercises validated at once in one process, as a batch does, came out
    VALID twice (about 400 s, against over 20 minutes one after another).
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
  (`stream_options` only for OpenAI and DeepSeek, which document it). A stream that ends
  with neither `[DONE]` nor a finish reason is an error, not a reply (outside review,
  2026-10-29): a cut-off draft was counted as the model's format error. Plan and vision
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
  names the repair model; the oracle must come from another provider than both writers
  (since 2026-11-07 best, not must: see "One provider's oracle" under the Models page).
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
- Week 4 cross experiment (2026-11-01, native Windows; owner asked for it). One plan by
  GPT, then the same 4 RDD items in two arms, 4 at once each. DeepSeek writing with a GLM-5.3
  oracle saved 4 of 4, 2 at the first round; its 3 first-round failures were all timeouts
  (Spark on Windows, 4 exercises validating at once), fixed by one repair each. GLM-5.3
  writing with a DeepSeek oracle failed on content (a mutant patch whose `old` text did not
  occur, a wrong expected value, a surviving mutant, an oracle interface without the
  question) and stopped after 2 items when GLM-5.3's quota ran out. So the planned oracle
  for after 2026-11-04 had no quota. GLM-5.3-Flash rewrote the 4 oracles from the same
  question and interface: at `reasoning_effort` high it matched GLM-5.3 exactly (reference
  agrees 4 of 4; 14 of 18 mutants disagree with it, the 4 others change no output and are
  killed by the tests) in 3-6 s each; thinking at the model's default (no effort field,
  the deepest) gave the same result in 6-50 s. Decided (owner): the oracle becomes
  GLM-5.3-Flash at high effort when the OpenAI key expires, through its own profile
  (`glm-flash-oracle`, `reasoning_effort_on = "high"`, `oracle_thinking` on), since the plan
  role uses `glm-flash` with the model's default. A FAIL of rule 4 on a mutant is not
  "caught" by itself: when the check was first run without `JAVA_HOME`, 13 of 18 "caught"
  were Spark failing to start.
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
  sends it back with feedback, which goes to `plan`. A resume rewrites plan.json only when
  the model has planned again (`plan.model.json` keeps its last plan), so the owner's
  edits survive a pause; a batch paused before that file existed keeps its plan.json, and
  the CLI says when plan.json differs from the model's latest plan. Every row must have a
  unique, identifiable ID; selected rows, edited or not, must pass the schema and blocking
  checks (new ids, since two items with one id would share a drafts folder; known lectures;
  slide ranges), in the CLI and again in `approve`; a count other than the one
  asked for is the owner's choice (both found in an outside review, 2026-10-29). A SQLite checkpointer in
  `.drafts/<batch>/` keeps the paused batch, so `python -m tutor.graph resume` continues it
  in another terminal or another day. Approved items fan out with `Send` and run the item
  graph in parallel, at most `--parallel` at once (default: CPU cores / 4, 1-4: each
  validation runs Spark on every core). One failing item (model unreachable, name clash)
  does not stop the others. `save` copies both parts into hidden staging folders and
  renames them into place, so a failed copy leaves nothing behind; an item saved before
  whose result was lost (a resume runs it again) counts as saved, its folders untouched
  (outside review, 2026-10-29). `collect` writes the manifest once (parallel items never
  write it) with the preferences in `notes`, and `coverage-proposal.md`, the coverage.md
  changes for the owner to apply. Listing a saved item again changes nothing, and the file
  is replaced in one step, so a resume that runs `collect` again cannot fail on it (outside
  review, 2026-10-29). Which types a batch mixes (e.g. debugging exercises) is
  left to the preferences, not a fixed rule.
- Logs: `llm_calls.jsonl` with the node as purpose; one `generation.jsonl` per batch
  (item, rounds, outcome, failure reasons, and the settings it ran with).
- Generation settings (owner-approved 2026-10-29; owner: keep tuning possible later,
  including from a GUI): every tunable value is a field of `GenerationSettings`
  (`graph/settings.py`) with its default, description and bounds: repair rounds, the round
  the repair model takes over from, the temperature step, thinking per role, token limits,
  the per-item token budget, context size, plan size, parallelism, mode. Three sources,
  later ones winning: the defaults (the measured values; `Deps` takes its defaults from
  them, so tests and scripts run like the CLI), workspace.toml `[generation]` (the owner by
  hand; the GUI's settings page writes only [llm] so far; the pipeline reads
  workspace.toml and never writes it), and the
  run's flags (`--set name=value`, plus the older named flags). Unknown names and values
  out of range are errors that name them. A batch saves what it resolved in run.json, so
  every resume runs with the same values even if `[generation]` changed; `resume --set`
  changes them for the rest of the batch. Each item's log line carries them, so a model
  comparison can tell its arms apart, and an arm is just a set of `--set` values.
  `schemas/generation-settings.schema.json` is the form a GUI renders (descriptions,
  types, bounds). Per-model values (`reasoning_effort`, `timeout_s`) stay in
  `[llm.profiles]`.
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
  `sources`, whole (a lecture is 4-11k tokens at 4 characters per token). When that does not fit the model's
  context, a slice instead: the slides that `coverage.md` lists for the batch plan's
  knowledge points. A slice is chosen by label, not by a query, so it is deterministic and the owner
  sees exactly which pages went in. For a local model, raising the context length it is
  loaded with (8192 on the owner's Mac so far; the model supports more, at the cost of
  memory) comes before slicing. A page of the item's slides that is `pending` or `partial`
  stops the item with its page numbers (a formula or diagram may be missing, and no test
  can show the question is faithful to the slide); the item's pages run from its first
  slide's PDF page to its last, footer or not, and in a lecture without footers every page
  is the item's. Such pages elsewhere are left out. Slides
  the lecture has no footer for stop the item too (both from an outside review, 2026-10-29).
- Every generated batch mixes function-level and file-level exercises (see "Exercise interfaces").

## RAG

Decided 2026-11-02 (owner): phase 4 is a measured comparison, not a commitment to RAG. A
course is small (about 570 pages, 70k tokens at 4 characters per token; a lecture 4-11k), and
generation already sends the whole lecture, so a route has to beat that to be used. Saying
"I measured whether retrieval earns its keep" shows more judgment than having a vector
database. Code in `src/tutor/retrieval/`; every route is one `search(query, lectures)` that
returns pages best first.

Outcome (owner, 2026-11-02): generation keeps the whole lecture and no route is wired in. Perfect
retrieval did not beat the whole lecture on generation ("Does retrieval help generation at all?"
below), so the rest of the plan was cut down to one final retrieval table ("Final table" below). A
smaller test with the three real routes (owner, 2026-11-03) agrees: none beats it. The code stays
for what was not tested: hints, questions of unknown source, or a course with no `coverage.md`.

Routes:
- Hybrid RAG: BM25 + vectors, fused by reciprocal rank fusion. API names like
  `aggregateByKey` are exact-match problems that pure vector search handles poorly.
- Agentic search: a LangGraph loop in which a model searches (BM25: id, footer, first line of
  the top 10), reads up to 5 pages at a time and finishes with its pages, at most 6 steps. It
  chooses each action as JSON through the client's structured output, which every profile
  supports, not through native tool calling, which would extend the client and differs by
  provider. Evidence for this route is from coding agents and chat memory, not course slides.
- Concept index, a small form of the "LLM wiki" pattern: one model call per lecture compiles
  knowledge points -> pages; at query time a model sees only that index. For a second course
  it is also an automatic first draft of `coverage.md`.

Baselines, already in the pipeline: the whole lecture, and the labelled page slice (perfect
retrieval by the labels, which is a reference, not a proven upper bound on what generation can
produce from it). GraphRAG (through LightRAG) stays out unless the hybrid and
agentic routes clearly fail the cross-lecture queries below: its strength is reasoning across
several places (Han et al. 2025, arXiv 2502.11371), while a generation query is mostly "which
pages teach this point". Routes read the page cache only, never `coverage.md`, which holds
the answers they are measured against.

Measurement, as planned. The outcome above dropped the pooling and the repeats:
- Unit: the cache's page sections, the same unit phase 3's slices use, so there is no chunker.
- Labels: each `coverage.md` row is a query (its knowledge point, in the words a batch plan
  uses) with relevant pages (its slides). 71 rows qualify, against the 20-30 first planned. The
  hash of each row alone splits them into dev (18, for tuning) and test (53, only for the final
  comparison), so adding rows or lectures later never moves a query from one to the other.
  Rewording a row would move it, so `--freeze-queries` snapshots every query and label in the
  workspace, and the test and cross-lecture splits refuse to run without it. Only 1 of the 53
  test queries shares a page with a dev query. `source.pdf_pages` of exercises adds nothing:
  they come from the same rows.
- Cross-lecture queries: 13 written by hand in the workspace's `retrieval/cross-lecture.jsonl`,
  labelled from `coverage.md`'s rows, frozen by the owner 2026-11-02 before any route ran on
  them, and run last, like the test split.
- Scope: the batch's lecture first, as generation does, and also the whole course, which is
  harder and is what cross-lecture queries and hints need. The lecture scope comes from the
  labels: fair for generation, where the batch names its lecture, but prior knowledge for a
  question of unknown source. Test queries from lectures with no dev query are also reported
  apart, as the nearest thing here to unseen material.
- Scores: recall@k and MRR assume a ranked list. Agentic search and the concept index return
  a set of no fixed size, and returning more pages always raises recall, so every route is
  also scored by recall within a token budget (500 to 4000), results taken in order. A route
  may stop early, so what it actually returned is reported too.
- Fixed before the test split: routes are chosen by recall within 2000 tokens searching the
  lecture (about twice the labelled pages, under a third of a lecture); the rest is supporting
  evidence. On dev, a change replaces a simpler setting only if at least 3 more queries improve
  than worsen: one query moves a mean by up to 1/18, so means alone would chase noise.
- Costs apart: tokens above measure pages delivered, by a rough estimate, not a run's cost. A
  stored answer takes no time, so each keeps its first run's tokens and seconds, reported apart
  from the cost of building an index and from generation. Each stored agent run also keeps its
  trace, which tells a page never shown from one shown and not chosen.
- Incomplete labels: a label is a slide range written for choosing batches, not a page-by-page
  judgement, so it favours finding consecutive pages and may overlook a good page elsewhere.
  Before the final table, pool for each query what every route and repeat returns within 2000
  tokens, with the labelled pages. A judge of another model family, not told which route
  returned a page, rules on all of them, existing labels included; the owner checks a sample.
  Strict and pooled recall are reported side by side. Pooled recall is still no absolute truth.
- Stability: routes that call a model run 3 times on the test split, each with its own `--run`
  number (the same number reads the stored answers), and every run is reported.
- Reach: these results hold for this course. They say nothing about another course.
- Generation: the same plan of about 12 items with each context, counting first-pass VALID,
  VALID after repair and cost per item, plus the judge's `fits` and `taught`, since material
  likely affects those more than validity. 12 paired items show only large differences, so
  counts are reported, not significance.
- Decision rule, fixed before the numbers: a route that does not beat the whole lecture on
  generation results or cost is not wired in. The result goes in the README either way.

Choices:
- Vector store: plain Python, a JSON-lines file of vectors, no Chroma, FAISS or numpy.
  Measured 2026-11-02 on an M2 Pro, 1024 dimensions, ranking every page: 571 pages 8 ms per
  query, 6000 pages 86 ms. numpy was approved first, and would be far faster at scale (60,000
  pages 4.9 ms, 2 million 168 ms and 8.2 GB, the limit being memory), but a course does not
  need it, and installing it where Spark runs changes PySpark: `StatCounter` then returns
  numpy values, in the validator's environment and not in the learner's. numpy or FAISS can
  replace the scoring inside `VectorRetriever` if a query ever passes about 100 ms.
- Embeddings: a model reached through the `embedding` role, by any provider with an
  OpenAI-compatible `/embeddings` endpoint. For now OpenAI's `text-embedding-3-small` at 1024
  dimensions: 83k tokens for a course, $0.002 (2026-11-02). Zhipu's `embedding-3`, planned first
  through the existing key, refused: it is outside the free quota and the account has no
  balance. Each text is embedded once and kept in `cache/retrieval/`, so repeated runs cost
  nothing and new lectures, changed pages or new queries pay only for themselves. Each model
  has its own file, as vectors from two models cannot be compared; another model means
  embedding the course again, which costs next to nothing.
  A local model, tried 2026-11-02 with LM Studio's bundled `nomic-embed-text` v1.5 (84 MB, 768
  dimensions): 12 s for all 571 pages, 12 ms per query, and on dev 0.85 against OpenAI's 0.87
  within 2000 tokens searching the lecture, 0.70 against 0.74 the course. Not wired in: it saves
  next to nothing, its one use is not needing a key, and it would need its task prefixes and a
  store name that does not claim 1024 dimensions.
- Reranker: not in the first round (see the [4] candidate).
- A lecture file repeating another's slides under other footers would count as wrong when
  found: the workspace's `[retrieval] exclude_lectures` leaves it out. It cannot be detected by
  exact comparison, as the footers make every page differ.

Dev scores (2026-11-02). 18 queries, which only guide the next steps; the test split decides.
The labelled pages average 994 tokens per query, the whole lecture 6783.

| Searching | Route | MRR | Recall within 1000 tokens | 2000 | 4000 |
|---|---|---|---|---|---|
| the lecture | BM25 | 0.78 | 0.66 | 0.81 | 0.89 |
| the lecture | vector | 0.89 | 0.70 | 0.87 | 0.97 |
| the lecture | hybrid | 0.91 | 0.68 | 0.85 | 0.97 |
| the lecture | hybrid + neighbours | 0.88 | 0.71 | 0.89 | 0.99 |
| the lecture | concept | 0.92 | 0.79 | 0.89 | 0.90 |
| the lecture | agent | 0.97 | 0.77 | 0.87 | 0.87 |
| the lecture | agent + thinking | 0.97 | 0.75 | 0.87 | 0.88 |
| the lecture | agent + hybrid search | 0.97 | 0.78 | 0.89 | 0.89 |
| the course | BM25 | 0.67 | 0.52 | 0.67 | 0.78 |
| the course | vector | 0.79 | 0.62 | 0.74 | 0.86 |
| the course | hybrid | 0.81 | 0.59 | 0.74 | 0.82 |
| the course | hybrid + neighbours | 0.75 | 0.65 | 0.80 | 0.91 |
| the course | concept | 0.82 | 0.71 | 0.82 | 0.82 |
| the course | agent | 0.74 | 0.53 | 0.57 | 0.57 |
| the course | agent + thinking | 0.89 | 0.73 | 0.81 | 0.81 |
| the course | agent + hybrid search | 0.89 | 0.63 | 0.69 | 0.69 |

Query by query at 2000 tokens, against the adoption rule. Searching the lecture nothing clears
it: thinking 6 better and 5 worse against the plain agent, hybrid search 6 and 4, hybrid against
vector 3 and 5, concept against vector 6 and 4. So the agent keeps BM25 and no thinking there,
and three model-driven routes tie a rule with no model at all: hybrid + neighbours took 8 better
and 3 worse against hybrid, at no cost. Neighbours may only fit the labels, which are consecutive
ranges, and the pooled labels will tell. Searching the course, thinking clears the rule by far
(10 better, 2 worse, mean +0.24), at about 5.5k tokens in, 880 out and 25 s per query; hybrid
search also clears it (9 and 4, +0.12). The trace says why: 63 of the 64 pages the plain agent
missed across the course were never shown by a search, so it needs better searches, not better
choices. The same plain agent run twice moved by 0.05 to 0.07 on these means, so differences of
that size are noise.

Frozen 2026-11-02 (owner), no more tuning: the plain agent searching a lecture, the agent with
thinking across the course and on the cross-lecture queries, both with BM25 search, 10 results
and the second prompt. The corpus is frozen too: a lecture arriving later stays out of these
measurements, as it would change every course-wide score. If measured at all, it is measured
apart as unseen material, with labels written before any route runs on it. Once such a lecture
is in the cache, repeating these numbers needs it in `exclude_lectures`.

Does retrieval help generation at all? Tested first, as it decides whether the costly final
retrieval run is worth doing (owner, 2026-11-02): the labelled slice is what a perfect route would
return, so if it does not beat the whole lecture, no route can be expected to. Same 12 plan items,
sampled by hash from three real batches, two arms, $0.81 in all:

| | Whole lecture | Labelled slice |
|---|---|---|
| Saved after repair | 12 of 12 | 12 of 12 |
| VALID at the first try | 8 | 7 |
| Exercises the judge finds fully fine | 7 | 5 |
| `clear` flagged | 4 | 6 |
| `fits the material` flagged | 2 | 5 |
| Serious flags | 5 | 10 |
| Tokens, all rounds | 764k | 703k |

The slice saves 8% of the tokens, about $0.02 on this run, and does no better on validity. The
judge finds it worse on `fits`: 4 items better with the whole lecture, 1 with the slice. 12 items
and an imperfect judge, which itself reads the whole lecture, show a direction and not a proof.
Its flags were read only on the four slice items it rated below the whole lecture: three are real
misstatements in the exercise's own explanation, the fourth was not checked, and the whole-lecture
arm's flags were not read. A model that sees less around a page may overstate more easily, which
is a reading and not a measurement. Most of the slice's first-try failures were replies cut off at
the output limit (4 against 1), not wrong content (1 against 3).

Then with the real routes (owner, 2026-11-03, about $1.72 before any cache discount): the same 12
items and pipeline, the context being what a route returns within 2000 tokens for the item's
knowledge point, searching its lecture, in page order. The plan line, its slide range included, is
the same in every arm. A private script replaced the context step, so `graph/` did not change. The
rule was written down first: a route wins only if it is VALID at the first try on at least as many
items, and the judge finds at least 3 more items fully fine, or at most 1 fewer with at most 70% of
the tokens, the route's own model tokens included.

Why these arms. The whole lecture is what generation sends today, so it is what a route must beat.
The labelled slice is what a perfect route would return. Hybrid + neighbours is the best route of
the final table below and needs no model. Concept and the agent are the two other kinds of route
this phase set out to compare, at their frozen settings. 2000 tokens is the budget the routes were
to be chosen by. 12 items and not all 23 of the three batches: the owner chose the smaller run on
cost, which also reuses the first two arms. Pooling and repeats stayed out, as seeing what a route's
pages generate answers the question more directly than judging those pages again.

| | Whole lecture | Labelled slice | Hybrid + neighbours | Concept | Agent |
|---|---|---|---|---|---|
| Context tokens per item, mean | 8183 | 772 | 1926 | 1142 | 815 |
| Labelled pages covered | all | all | 0.94 | 0.89 | 0.81 |
| Saved after repair | 12 | 12 | 12 | 12 | 12 |
| VALID at the first try | 8 | 7 | 4 | 8 | 4 |
| First try cut off / wrong | 1 / 3 | 4 / 1 | 5 / 3 | 0 / 4 | 5 / 3 |
| Items repaired / repair rounds | 4 / 4 | 5 / 5 | 8 / 11 | 4 / 5 | 8 / 9 |
| Exercises the judge finds fully fine | 7 | 5 | 3 | 3 | 5 |
| `clear` flagged | 4 | 6 | 7 | 7 | 7 |
| `fits the material` flagged | 2 | 5 | 5 | 3 | 4 |
| `taught` flagged | 0 | 0 | 0 | 0 | 0 |
| `targets` flagged | 1 | 0 | 1 | 1 | 1 |
| Serious flags | 5 | 10 | 12 | 10 | 11 |
| Tokens, all rounds | 764k | 703k | 946k | 637k | 808k |
| The route's own model tokens | - | - | - | 22k | 43k |
| Tokens, % of the whole lecture | 100 | 92 | 124 | 86 | 111 |

Tokens count generation, repair and the oracle. Costs at list prices before any cache discount
(DeepSeek-V4-Flash $0.14 in, $0.28 out; V4-Pro $1.74, $3.48; GPT-6.1-Terra $1, $5; from third-party
price pages): the first two arms $0.81 (generate 0.24, repair 0.07, oracle 0.09, judge 0.41), the
three routes $1.72 (generate 0.34, repair 0.60, of which 0.47 on V4-Pro, oracle 0.16, judge 0.62).
The exercises, verdicts, a per-item table and each query's retrieval results stay in the private
workspace's `experiments/`, as they are course content.

Why retrieval has little to save here. An exercise takes about 64k tokens with the whole lecture,
and the lecture is about 8k of them; over half of the generation tokens are the model's output, the
dearer side. Even a perfect route can save about 7k tokens per exercise, about $0.001, and one extra
repair costs more than that.

No route wins. No exercise was fully fine from a route and not from the whole lecture, while the
reverse held for 4 items with hybrid + neighbours, 4 with concept and 2 with the agent. Two routes
cost more than the whole lecture: more first drafts were cut off at the output limit (5 each against
1), and the repairs, five of them on the stronger model, cost more than the smaller context saves.
Concept comes nearest on cost, with as many VALID at the first try and 14% fewer tokens, but 4 fewer
fully fine. Why less material leads to worse explanations or more cut-offs was not measured.

Flags were checked with the arms hidden: 3 from each arm, 15 in shuffled order, each against the
exercise and the course, verdicts stored before the arms were revealed. All 15 are real defects, 2
of them overrated as serious (one from the whole lecture, one from hybrid + neighbours). That tests
what the judge says, not what it misses. 12 items show a direction only. 4 were fully fine in no
arm and 2 in every arm, so for those the item decides rather than the context. Each arm ran once,
and the judge reads the whole lecture in every arm.

Final table (2026-11-03), the test split with the frozen settings: 53 queries, 1.45M GLM-5.3-Flash
tokens. Simplified by the outcome above: every route ran once and the labels were not pooled, so
these are strict scores against slide ranges, and a few hundredths may be noise. The labelled pages
average 734 tokens per query, the whole lecture 6931. The CLI runs both scopes for every route, so a
private script ran each agent setting on its own scope.

| Searching | Route | MRR | Recall within 1000 tokens | 2000 | 4000 | Model tokens per query | Seconds |
|---|---|---|---|---|---|---|---|
| the lecture | BM25 | 0.89 | 0.75 | 0.88 | 0.94 | - | - |
| the lecture | vector | 0.92 | 0.77 | 0.87 | 0.92 | - | - |
| the lecture | hybrid | 0.94 | 0.77 | 0.89 | 0.94 | - | - |
| the lecture | hybrid + neighbours | 0.94 | 0.81 | 0.93 | 0.98 | - | - |
| the lecture | concept | 0.85 | 0.81 | 0.88 | 0.89 | 1.6k | 2.7 |
| the lecture | agent | 0.94 | 0.82 | 0.87 | 0.87 | 3.9k | 13.7 |
| the course | BM25 | 0.81 | 0.63 | 0.80 | 0.86 | - | - |
| the course | vector | 0.88 | 0.72 | 0.81 | 0.89 | - | - |
| the course | hybrid | 0.88 | 0.70 | 0.85 | 0.89 | - | - |
| the course | hybrid + neighbours | 0.86 | 0.75 | 0.90 | 0.95 | - | - |
| the course | concept | 0.85 | 0.80 | 0.89 | 0.89 | 15.2k | 5.1 |
| the course | agent + thinking | 0.87 | 0.72 | 0.82 | 0.82 | 6.6k | 28.0 |

Query by query at 2000 tokens. Searching the lecture, the score fixed in advance, no route with a
model beats the best route without one: hybrid + neighbours took 11 better and 4 worse against
hybrid, and concept against it took 7 and 11, the agent 1 and 18. Nor do they beat plain vector:
concept 10 and 10, the agent 7 and 11. Vector's lead over BM25 on dev did not hold (9 and 8), while
hybrid does beat vector (6 and 1, mean +0.017). Searching the course, concept ties hybrid +
neighbours (8 and 9) and beats vector (16 and 7, +0.074); the thinking agent falls behind (7 and 16
against hybrid + neighbours). The margin of 3 was set for 18 queries and is a low bar on 53: the
thinking agent clears it against vector across the course with 13 and 10 and a mean of +0.005, which
is nothing.

So a later use should start from hybrid + neighbours, which needs no model, unless neighbours only
fit the labels, which are consecutive ranges; that was for pooling to test, and it was dropped. The
routes with a model still hold up in little room, returning 900-1200 tokens: within 1000 tokens,
concept finds 0.80 across the course against 0.75, the agent 0.82 in the lecture against 0.81. On
the 11 test queries from lectures with no dev query, the nearest thing here to unseen material,
routes score 0.92 to 0.99 at 2000 tokens searching the lecture, the agent lowest.

One of the 106 concept picks was cut off at its output limit. It counts as a miss and stays stored
as one, as asking again until a reply works would flatter the route. Every agent run finished; two
provider errors stopped the run, which resumed from the stored answers.

Cross-lecture queries (2026-11-03): the 13 frozen queries, searching the course, run once, 0.29M
GLM-5.3-Flash tokens. Their labelled pages average 14.7 pages and 2059 tokens, which fills a
2000-token budget, so 4000 is the fairer score. The lectures holding them average 15.8k tokens.

| Route | MRR | Recall within 2000 tokens | 4000 |
|---|---|---|---|
| BM25 | 0.82 | 0.35 | 0.47 |
| vector | 0.94 | 0.37 | 0.54 |
| hybrid | 0.94 | 0.37 | 0.51 |
| hybrid + neighbours | 0.93 | 0.46 | 0.64 |
| concept | 0.84 | 0.46 | 0.55 |
| agent + thinking | 0.83 | 0.33 | 0.34 |

Routes start well: a relevant page comes near the top, and only the agent, on one query, found none
within 2000 tokens. But at best they find about two thirds of what a query needs, and no route found
all of it for more than one query. The agent stops early, at about 1100 tokens. Query by query at
2000 tokens, against hybrid + neighbours: hybrid 2 better and 10 worse, concept 4 and 6, the agent 2
and 8. 13 queries show a direction. One concept request had no reply in 900 s and ran again.

Hybrid and the agent fall well short here, which is the condition set above for trying LightRAG. It
is still not built: generation does not look across lectures, so nothing here would use it. It is
worth trying if hints or revision come to need that lookup.

The concept route returns about 1100 tokens (7-8 pages) and stops there, so a larger budget
adds nothing: so far it finds more than the others in little room, and less in a lot of room.
Its index cost GLM-5.3-Flash about 90k tokens in and 21k out for the course (`concept` role).
A lecture's index is about a fifth of the lecture, the course's about 16k tokens, which every
course-wide query sends, at 2-3 s per query against milliseconds for the others. It holds 220
points covering 496 of 571 pages; on the lecture checked, the rest were title, section and
reference pages, and 3 of the 125 pages the dev queries label are in no point.

The agent route ranks a relevant page first most often: it returns a small set too, and took
4.8 steps and about 4.6k tokens per query with GLM-5.3-Flash, at 14 s. Its first prompt scored
0.69 and 0.53 at 4000 tokens: 46 of the 51 pages it then missed within a lecture were within two
pages of one it returned, so the prompt now asks for the pages around a good one. Tuning stopped
there, as 18 queries are easy to fit. On the same dev queries the plain agent does not beat
ranking with hybrid directly: 5 better and 5 worse searching the lecture, 2 and 9 the course.

BM25 is hand-written, as the formula is short. Splitting camelCase, stop words, plural stripping
and other `k1` / `b` values each moved its scores by a query or two, which is noise, so the
tokenizer stays plain lowercase words.

Where retrieval could still earn its place (owner, 2026-11-03; not scheduled). Generation is the
easy case for doing without it: the plan names the pages and a lecture fits. These lack one or both:
- A chat or question window, the clearest case: a question may come from any lecture, the context
  goes again every turn, and an answer can cite its pages. Not on the roadmap, and phase 7's GUI
  only wraps the existing functions, so it would be added on its own.
- Material too large for a prompt: a textbook, several courses, or the public Spark documentation
  corpus planned for a demo.
- A local model with a small context: Gemma was loaded with 8192 tokens, which a course overflows.
- A new course with no `coverage.md`: the plan cannot name pages until something finds them, and
  the concept index is also a first draft of that file.
- Revision across lectures, which is the routes' weak spot (0.64 at best above) and the case for
  trying LightRAG.

A chat window should be measured first too, not assumed. This course is about 71k tokens, so
sending all of it costs about $0.01 of input per turn on DeepSeek-V4-Flash before any cache
discount, against $0.0003 for 2000 tokens: about 35 times more, but little money for light use.
Compare the whole course with caching against hybrid + neighbours on about 20 questions, by whether
answers are right and cite the right pages. Start from hybrid + neighbours, which needs no model and
found 0.90 across the course. The generation test's warning may carry over, as less context brought
more misstatements; that is untested for answers.

## Hand-calculation exercises

Decided 2026-11-05 (owner), for a course examined closed-book with a calculator: the learner works numbers
out by hand and writes them into `student.py` as named constants. No rule changes, as the format already
fits. The tests compare each constant within a tolerance, a mutant is the numbers one specific mistake gives,
and the oracle computes them from the formula, so a wrong answer key fails rule 4. A hand-made one and 9
generated ones are VALID. The format lives in the workspace's `conventions.md`, not in the prompts, as other
courses still write code.

A failing test's traceback prints the expected value, which here is the answer: the generation setting
`hide_answers` writes a `pytest.ini` in the batch folder, so a run shows only each test's name and result. It
is set per run, as a code batch of the same course needs its tracebacks, and not inferred, as no field marks
an exercise as hand calculation. The answers are still in `test_student.py`, and in `first_error` in the
attempt log. Multiple choice and short answer are outside this: the validator cannot check them.

## Evaluation metrics (the results table)

Decided 2026-11-05 (owner), for phase 5's light version. The numbers are in `docs/details.md`,
"Results" (in the README until 2026-11-07; the README links to them).

- First-pass validity rate; validity after repair loop.
- What stopped the first draft, by rule. This replaces the mutant kill rate, which is 100% on saved
  exercises by rule 3. The kill rate on first drafts, tried in its place, came to 102 of 103 over 22
  drafts, and the same model writes the tests and the mutants, so it says little too: it stays as one
  line with that limit.
- Held-out flag rate: how often a VALID exercise gets a finding to read (see "Independent checks").
- Tokens per saved exercise, and no money: prices change, a price table would need upkeep, and some
  models here run on a free tier, so an account's spend would not show what a run costs.
- By model, labelled as no comparison. The full comparison was not run (owner, 2026-11-04): 12 items
  separate only large differences, and week 7 had already chosen a generator. With vs without retrieval
  was settled in phase 4 (see "RAG").
- The judge and the owner's ratings, with the judge's noise stated beside them.

`python -m tutor.eval report` reads a workspace's records, calls no model and writes its private
`reports/report.md`: progress, generation, first drafts, checks on saved exercises and model calls, then
what the numbers cannot show and the gaps in the records. The attempts plugin rebuilds it after every
recorded run (owner, 2026-11-05), so it follows the solving with no extra step; a rebuild took about 0.05 s
on 49 exercises, and a failure there is only a warning. It overwrites one file, not one per run, as a dated
copy per test run would pile up. In a generated batch, an exercise whose `student.py` differs from its skeleton
with no logged run is listed, which shows runs lost outside the course venv; a batch written by hand has a
`student.py` that differs from the start, so there it cannot be seen. No `first_error`, failure detail or rating remark
is copied, as any of them can hold an answer. The results table is public, so it changes only on request.

## Prompt layout for caching

Decided 2026-11-03 (owner): every prompt is designed for a high cache hit rate where conditions
allow. Providers reuse only an identical start of a prompt (OpenAI: from 1024 tokens), so stable
content goes first: the system prompt, rules and shared course material. What varies per item goes
last. The shared part stays the same byte for byte, with no per-item marks, dates or ids. With
parallel calls and a large shared part, one call goes first to fill the cache. `cached_tokens` in
the call log shows the result. Meaning comes first: a short prompt gains nothing and is not
contorted, and reordering a calibrated prompt means calibrating it again.

Measured so far: DeepSeek hits 53-75% on generate and repair. `gpt-6.1-terra` as the judge hits 7%,
its largest OpenAI cost. Its prompt then put each exercise before the slides, and the slides carried
marks for that exercise; moving the slides first was not enough there. In a small test (2026-11-03, week 8's
11 exercises and 2 first drafts with known errors), exercises sharing about 9000 tokens at the start
hit 0%, sent one at a time or in parallel, while the same whole prompt sent again minutes later hit
99.98%. A 90-second wait before the second call, a `prompt_cache_key` and the slides in a message of
their own all hit 0% as well. Why is not known, and the search stopped there, as the rule set for it
said. So the judge's layout stays, and a hit rate is measured before a saving is claimed.

The same test shows what a layout change would risk. The judge run again on its own prompt changed
the letters of 3 of the 13 and whether an exercise is fully fine on 1. The new layout changed 3 of
13 against the first run, no more than that noise, and still flagged both known errors. 13 items
show a direction only, but phase 5 has to report the judge's numbers with this noise in mind.

On Anthropic the prefix is explicit (2026-11-10, "Anthropic provider", cache breakpoints): with
the slides first and the exercise's pages named apart, the judge hit 71% of its input on the
54-item calibration, a 60% saving at Sonnet 5.5's prices; the same layout change that hit 0%
on OpenAI for a reason never found.

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
    data URLs, 1M context; the owner has free tokens for it, and for GLM-5.3-FlashX,
    owner 2026-11-07). How to minimise thinking
    differs by provider and model: LM Studio takes `reasoning_effort: "none"`; GLM takes
    `thinking: {"type": "disabled"}`, but GLM-5.3 models reject it and accept only
    `reasoning_effort` low / high / max, so the client tries `disabled` and falls back to
    `low` on a 400. The switch a model accepted is remembered for the rest of the run, by
    provider, base URL and model (owner, 2026-11-07: before, every GLM-5.3 call was refused
    once first); a new run tries the first switch again. With a cloud model the slide
    images go to that provider; the README states this data flow.
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
    profile. Like `[safety]`, the section is the owner's: generation never writes it.
    A profile's `temperature = "default"` (owner, 2026-11-09) leaves the temperature out of
    its requests: Claude Opus 5.5, reached through Anthropic's OpenAI-compatible address as a
    `custom` profile, answered HTTP 400 ("`temperature` is deprecated for this model") to the
    0 every other call sends. It is the field's one value, as callers choose the temperature
    and the profile only says the model takes none; the Models form offers it under More.
    Such a model answers at its own temperature, so a rerun is not the same answer, and the
    repair rounds' rising temperature (0.2 * N) does not apply to it. Chosen over retrying
    without the field after a refusal: one refused request per run, and less plain. That
    address was for trying Claude out; since 2026-11-10 Claude has its own provider (next
    entry). Apple Intelligence's on-device model is reachable too, through third-party
    bridges that serve it as an OpenAI-compatible local API (apfel and similar; not an
    Apple interface), so the same client can call it with no extra code. It is not a default:
    about 4096 tokens for input and output together (one week's material and the format spec
    do not fit), no image input found, one fixed small model, and rate limits for
    background use. It may serve as one more local arm in the phase 5 model comparison or for
    short text tasks.
  - Anthropic provider (owner, 2026-11-10: "use the native provider"), `provider =
    "anthropic"` in `tutor/llm_anthropic.py`, standard library like the rest of the client.
    Why not the compatible address: Anthropic's own docs call that layer a way "to test and
    compare model capabilities", "not a long-term or production-ready solution", and it has
    no prompt caching at all (`prompt_tokens_details` "always empty"; the 11-10 judge
    calibration with Sonnet 5.5 paid $2.52 for 1.1M input tokens that way); the Messages
    API opts into caching per request. Measured before deciding (one course, 11-10): plan
    (Sonnet 9/9 coverage rows), oracle (Sonnet 54/58 agree with the reference, mutants 91%)
    and the judge (Sonnet as good as gpt-terra on planted defects, 13/18 to 14/18, with no
    owner-approved exercise marked unclear against gpt's 6). The mapping, in the module's
    docstring and here in short: the system prompt is a top-level field; `max_tokens` is
    required (32,000 when a caller gives none, the pipeline's thinking default; thinking
    counts towards it); no `thinking` field (adaptive and on by default on the 5.5 models,
    Opus cannot turn it off) and no temperature (refused), so a call's depth goes through
    `output_config.effort`: `thinking=False` sends the profile's `reasoning_effort` or
    "low", `thinking=True` its `reasoning_effort_on` or nothing (the model's default), the
    same profile fields every provider uses; a JSON schema goes to `output_config.format`
    (structured output), after the constraints Anthropic refuses are dropped from the copy
    sent (`exclusiveMinimum`, `maxItems`, ...: HTTP 400 for the draft and doctor schemas in
    the first probe; the caller's pydantic model still checks them), and a schema with a
    free-form map (the draft's `data_files`, which the API refuses outright) goes into the
    prompt as DeepSeek's JSON mode does, the caller validating as always; the reply's text
    blocks are joined, thinking blocks left out, `max_tokens` becomes the "length" finish
    reason the graph reads; the event stream is put back together like the OpenAI one.
    Usage keeps the client's one meaning: Anthropic's `input_tokens` leaves out the tokens
    read from or written to the cache, so the reply adds the three back (costs are computed
    from the total), with `cached_tokens` and a new `cache_write_tokens` kept apart in the
    call log. The request and error handling are one function (`llm.exchange`) for both
    shapes; only the path, the headers (`x-api-key`, `anthropic-version`) and the reading of
    the reply differ. No SDK: the owner's keys stay in `.env` as for every provider; the
    SDK's credential chain (`ant auth login`, workload identity) is a later option, not a
    reason for a dependency now. Checked with Haiku 5.5 against the real API before the
    tests were final (8 calls, $0.0004); the Models page clicked in headless Chrome on a
    scratch course (New model, Anthropic from the list, Use, Save: `provider = "anthropic"`,
    no base_url written).
  - Cache breakpoints (owner, 2026-11-10, the same day). A caller that gives a message as
    an `llm.Prompt` (its parts: the shared one first, then this call's own) tells the
    provider where the prefix ends: the anthropic module sends one text block per part
    with `cache_control` after every part but the last, and marks the system prompt's last
    block too (at most four marks, the API's limit). To every other reader a Prompt is the
    plain string its parts make (a `str` subclass): the OpenAI-shaped providers, the
    assistant's request files and their ids, the tests' fakes and the prompt pins see the
    bytes a string would give, so DeepSeek's generate and repair requests did not change
    by a byte (the pins prove it), and the pins were not recomputed. Explicit breakpoints,
    not the API's automatic one: that lands on the last block, which in these prompts is
    the part that changes every call. Who gives parts: the generate, repair and retry
    prompts (the course pages first, the example and the item after, so the items of a
    lecture and the rounds of an item share the pages); the judge (below). Not the oracle
    (its spec is about 260 tokens, under the 512 a Claude 5.5 model caches), nor the plan
    (one call) or the hints judge (no slides). The judge's prompt had to change order for
    this: the exercise came first and the slides last, with the exercise's own pages
    marked in their headings, so no two calls shared more than the system prompt (the 7%
    hit rate under "Prompt layout for caching"). Now the batch mode, the lectures taught
    and the unmarked pages come first, then a line naming the exercise's own pages, the
    question, the starting file, Useful functions and the reference; the words did not
    change, the order did, so `PROMPT_VERSION` went to 5 and the calibration was run
    again with Sonnet 5.5, sequentially so that the first call of a lecture fills the
    cache before the next reads it. Measured (54 rated items and 18 planted defects, the
    same set as the morning's run on the compatible address): 71% of the input served from
    the cache (781,749 of 1,101,216 tokens; 53 of 54 calls read, 7 wrote), $0.96 against
    $2.39 for the same calls uncached, a 60% saving; 215 s. The verdicts: planted defects
    12 of 18 (clear 1/5, fits 3/5, taught 3/3, targets 5/5) against 13 of 18 in the
    morning, and the real ratings the same (clear 1 of 5 found, no owner-approved exercise
    marked n), within the rerun noise this section records (3 of 13 letters); one of the
    two fits misses was a verdict with an empty `evidence` and ok=true, which the judge's
    retries do not catch (only a reply that fails the schema is asked again). So the judge
    role moved to `claude-sonnet-5-5` in every course (owner's condition: not worse than
    the morning's run), and the last OpenAI role is gone; the OpenAI profiles stay in the
    files until the key ends. A 1-hour cache lifetime (writes at 2x) was not needed:
    sequential judging stays within the 5 minutes; generation's repair rounds, with
    minutes of validation between calls, are where it may matter, to be read from the
    call log of the next batch before deciding.
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
  Built 2026-10-31 (`tutor/judge/hints.py`), on simulated students since the owner had
  asked `tutor.hint` for nothing yet: `simulate` makes two mutants per exercise of a batch
  play students (their docstring, which names the bug, removed), runs their tests and
  asks the hint model for the level-1 layer 3 hint; `calibrate` has the judge answer
  "leaks?" and "right direction?" (evidence first; it sees the real bug and the
  reference) and scores it against the owner's labels as the teaching judge is scored.
  - Week 7, 22 hints by deepseek-flash: the owner found all 22 fine for learn mode, even
    those naming the change in words, so the leak rule is mode-aware: in learn mode only
    corrected code (or a whole corrected statement) leaks; in review mode naming the
    place or the change does. gpt-terra agreed on all 22.
  - With no bad label, nothing showed it could catch one, so 16 bad hints were planted
    by construction (a hint paired with another exercise's bug; hints naming the change
    judged as review hints; a corrected line pasted in): it found 10/10 leaks and 6/6
    wrong directions, with no false flag on the 38. Trusted on this data; planted bad
    hints are easier than real ones, so real requests (and review-mode batches) should
    be labelled when they come.
- [4] Reranker after hybrid retrieval, as one more arm of the recall@k comparison. Not in the
  first round (2026-11-02): only if hybrid's misses are pages it found but ranked too low.
- [5] Personal workflow cost (Codex review 4): steps and minutes from adding lecture files
  to the first exercise, owner minutes spent fixing each exercise, validation wait, and
  whether a failed run can resume. The first batch is the hand-made baseline, taken from
  this document's batch notes (fixes: page references, stripes, an equivalent mutant,
  too-regular data) and `attempts.jsonl`, with no new logging for the owner. Reviewed 2026-11-05
  (owner): only what the records hold, runs up to the first pass; minutes were never recorded,
  so they are dropped.
- [5] Local model as one more arm of the model comparison: validity, repair rounds and
  cost of a local model (run on the owner's Mac) against API models, judged by the same
  deterministic validator. Moved to the backlog 2026-11-05 (owner), as the full comparison was
  not run.
- [5] Long-context baseline: "without RAG" means the whole week's material in the prompt
  (with caching), not no material, so the comparison shows whether retrieval earns its keep.
  A third arm (2026-10-28): the labelled page slice from `coverage.md`, i.e. perfect
  retrieval, the upper bound that shows how far RAG falls short of it. Done in phase 4 (see
  "RAG"), where the slice proved a reference and not an upper bound on generation.
- [after 3, optional] MCP server exposing `validate` (and later `hint`) so any coding agent
  can check the exercises it writes.
- Not proposed: fine-tuning or RL (too little data; model + validator suffices), multi-agent
  debate, GraphRAG unless the cross-lecture queries call for it (see "RAG"), a hosted vector
  database (a few hundred pages fit in memory),
  an LLM judging validity (already in "Decisions not to do").

## Assistant mode

Decided 2026-10-29 (owner): the pipeline must also run with no API key at all, with a
coding assistant on a subscription (Claude Code with Claude Pro / Max, Codex with ChatGPT
Plus) acting as the model for vision, planning, generation, oracle, repair and hints.

- A profile with `provider = "assistant"` sends nothing over the network
  (`tutor/exchange.py`). Each call becomes `<workspace>/.exchange/<profile>/requests/<id>.md`
  (the messages, what to reply, the JSON Schema; images as PNG files beside it) and the
  caller gets `AwaitingAnswer`. The assistant answers with `python -m tutor.exchange answer
  ID FILE`, which checks the reply's shape first (one JSON object with the required
  fields) and keeps it in `answers/<id>.txt`. The id is a hash of the request (purpose,
  messages, schema), so running again replays every answered call and stops at the first
  unanswered one (owner chose replay over a LangGraph interrupt per call: simpler, and
  parallel items can collect their requests into one round of answers).
- `AwaitingAnswer` is an `LLMError`, so a caller that knows nothing of assistant mode
  (the hint command) falls back as it does for a model that could not answer, and finds
  the answer on the next run.
- An assistant profile's `model` names the assistant (default: the profile name) and its
  queue is its base URL, so two assistants are as independent as two providers for the
  oracle rule.
- Vision: one `--vision` run writes a request for every page to transcribe (the page as
  PNG) and goes on, instead of stopping at the first; the next run stores the answers as
  transcriptions (exit code 3: requests are waiting).
- Batch: an item that stops at a request ends as "waiting" (not an error); the batch's
  `answers` node, after each round of items, pauses once for all of them (an interrupt
  next to the plan approval), and after the answers sends only those items again, which
  replay their answered calls. Validation reports are kept by a hash of the round's files
  while any role is an assistant, so a replay does not run earlier rounds' Spark again.
  A plan waiting for an answer stops the batch, and `resume` replays it. `item` and
  `resume` exit with code 3 while requests wait.
- One assistant for both the exercise and the oracle (owner, 2026-10-29: allowed, with a
  reminder): a second profile of the same assistant (same `model`, its own queue) passes
  the oracle rule, and the CLI prints that its requests must be answered in a fresh
  context that has not seen the answer, being less independent than two providers. One
  profile for both is still refused (the requests would mix).
- Instructions for the assistant: `docs/assistant-mode.md` (setup, the run / answer /
  run-again loop, answering from the request alone, oracle requests in a fresh context,
  plan approval left to the owner).
- Self-test 2026-10-29: Claude Code (this repo's assistant) answered one real week-5 item
  through the files, with no API: the first draft failed the safety check (a spy using a
  variable attribute name), the repair request was answered, and round 1 was VALID (6/6
  mutants killed, oracle and partitions ok) and saved. Two fixes came of it: manifests and
  logs mark an assistant's model "(assistant)", and token counts an assistant cannot
  report are left out instead of printed as 0.
- Codex test 2026-10-29: Codex (desktop app, ChatGPT Plus) answered three vision requests
  through the files and opened each PNG: one code slide matched the hand-checked
  transcription (ratio 0.97), a logo diagram was described with details only the image
  has. So both assistants work with files alone.
- MCP server: deferred (owner, 2026-10-29). The files work for an assistant that can run
  commands and read PNGs (both do); an MCP layer (list / get with the image / submit) is
  built only if permission prompts get in the way, or with the GUI.
- Open (owner chose to wait, 2026-10-29): the vision prompt does not say what to do with
  text an image covers on the slide while the text layer still has it. On one slide a
  screenshot covers the bullets and some code: GLM filled them in from the text
  layer, Codex wrote [?] ("cannot read: do not guess") and lost the code. Fix when pages
  are transcribed again for another reason: a rule to take covered text from the text
  layer and say it was covered, with PROMPT_VERSION raised (which re-transcribes every
  page read with the older prompt, hand-corrected ones excepted).

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
no skipped one; FAILED needs at least one failed assertion in the report; everything else
(import errors, no tests, skips, setup/teardown errors only, a run that exits before writing
its report, which counted as FAILED until an outside review, 2026-10-29) is ERROR. The child starts in its own session and the
whole process group is killed on timeout and after every run, because PySpark starts a JVM
under pytest that would otherwise be orphaned (a test covers this, and fails if only the parent
is killed). This is isolation, not a sandbox: the child can still touch other files and the network.

On Windows (native support decided 2026-10-31, owner: one code base for macOS, Linux and
Windows, platform branches kept to a few functions) there are no process groups, and
walking the parent-pid tree (`taskkill /T`) misses a JVM whose parent pytest has already
exited: measured, the leftover process kept the temp dir open and deleting it failed
(`WinError 32`). So the child is put into a Job Object (`tutor/winjob.py`, ctypes, no new
dependency) right after it starts; its descendants join automatically, `TerminateJobObject`
ends them all, and the runner waits until the job is empty before deleting the temp dir,
since termination is asynchronous. `KILL_ON_JOB_CLOSE` also ends the job if the validator
itself crashes. Accepted gap: a process the child started before being assigned would stay
outside, but assignment follows `Popen` at once, while the child still spends far longer
starting Python and importing pytest. A test covers a process left behind by a run that
passed; both process tests fail on Windows when the child is kept out of the job.
Time limits are 4x on Windows (`execution.timeout_factor`, `TUTOR_TIMEOUT_FACTOR`
overrides it; measured 2026-10-31): Spark cannot fork its Python worker daemon there, so it
starts a new Python process for every task, one after another, about 0.6 s each (a 4-task
RDD job 2.4 s, a JVM-only job 0.09 s). An example's 25-case hypothesis check took 101 s
against a 30 s limit; with 120 s the example was VALID. The factor is applied inside the
runner, so metadata `timeout_s` keeps stating what an exercise needs on macOS or Linux.
Each run also gets a temp dir of its own (`TEMP`, `TMP`, `TMPDIR`, outside the run's
working dir, deleted afterwards), on every platform. Found 2026-11-01: with several Spark
runs at once, one now and then hung until the timeout, waiting in `launch_gateway` for
`spark-submit.cmd`. That script writes its launch command to
`%TEMP%\spark-class-launcher-output-%RANDOM%.txt`, and cmd seeds `%RANDOM%` from the second
it starts, so runs started in the same second shared one file (Spark's own fix, SPARK-28302,
only checks whether the file exists, which two runs can both pass). Measured, three
reference runs at a time: 3 of 30 hung with the shared `%TEMP%`, 0 of 48 with one per run.
Deleting that dir found a second Windows gap: the native library the JVM unpacks there
(`liblz4-java-*.so`) could not be deleted (`WinError 5`) although the job was already
empty, most likely a virus scan of the new file. Removal retries for up to 5 s (measured:
at most 0.2 s over 30 runs) and then leaves what is still locked, as the shared `%TEMP%`
did before (it held 90 such leftovers).
Part of the time the 4x factor covers may have been these hangs; it is to be measured again.

Scope: the runner protects validation, which runs generated code. A learner running
`pytest test_student.py` runs their own code in the terminal, in-process and without a
timeout: Ctrl+C stops an endless loop, and testkit stops a leftover SparkContext after each
program. Accepted for now (raised in the same review); `pytest-timeout` is the fix if hangs
become a problem.

The README states this boundary too ("Running exercise code is not sandboxed", 2026-10-26;
"Where exercise code runs" since the container shipped, 2026-11-06, in `docs/details.md` since
2026-11-07 with a line and a link in the README), since anyone who clones the repo validates
code on their own machine.

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
  stderr / executable`; no `eval`, `exec`, `compile`, `__import__`, `breakpoint`, unless a
  function's own binding (a parameter, a local) hides the builtin: Python fixes those when
  it compiles the function, while a module or class binding may come after the use (one
  file-wide set of bound names let `eval = 0` in one function hide `eval(...)` elsewhere
  until an outside review, 2026-10-29; the API check got Python's scopes then too); no dunder
  attribute outside a short list (`__name__`, `__module__`, `__qualname__`, `__init__`,
  `__class__`, `__doc__`), and `getattr` / `setattr` / `delattr` take a literal name; no
  literal path starting with `/` or `~` or containing `..` in a file-opening call
  (`open`, `Path`, Spark readers), no `home()` / `expanduser()`; no shell commands through
  the frameworks (`RDD.pipe`, MRJob `*_cmd` / `*_pre_filter`). The exercise folder may not
  hold `conftest.py`, `pytest.ini`, `setup.cfg`, `tox.ini` or `pyproject.toml`, which pytest
  runs on its own. Both checks cover every file a run copies in, subfolders included
  (pytest collects tests and conftest.py recursively; checked only at the top until
  2026-10-29, found in an outside review). A symbolic link anywhere in the exercise is
  refused before that, and so is a Windows directory junction (found 2026-10-31 when
  porting to Windows: `is_symlink()` is False for a junction and any account may create
  one, so a junction to a folder outside the exercise passed the check and its files were
  copied into the run; `execution._is_link` checks the junction reparse tag, not every
  reparse point, since OneDrive's files-on-demand are reparse points too): the run copies
  what it points to, which may be outside the
  exercise (`execution.copied_files` lists a run's files the way copytree copies them, for
  this check and the hint cache). A failure makes the exercise INVALID and skips every rule that runs
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
- B, test-run container: first deferred to the generalization phase and the GUI (owner,
  2026-10-28), when courses with riskier libraries and other users arrive; until it ships,
  phase 3 runs with layer A and material the user trusts, and a course whose modules are
  rated medium or high risk (below) is refused rather than run unprotected. Measured and
  decided 2026-11-05 (owner). `container.py` and the runner are written and tested (`pytest -m
  docker` runs the limits below in real containers). Switched on for both private courses on
  2026-11-06, after every saved exercise of both and the repo's examples (65: plain Python, MRJob
  inline and on Hadoop, Spark RDD and DataFrame) validated in containers through `execution.py`
  with the same verdict, rule statuses and mutant counts as their saved host reports: 700 s one
  after another, about 650 container runs, none left behind, no file in a workspace changed.
  - Shape: the host orchestrates (validator logic, reports, attempts, hints, GUI, every LLM
    call) and the container only executes. The one seam is `execution.py`: `checks.py`, hints
    and held-out checks do not change, since results come back as they do now (exit code,
    JUnit XML, output).
  - One fresh `docker run --rm` per test run (owner, 2026-11-05; this replaces the draft's
    long-lived per-course container with `docker exec`):
    `docker run --rm --pull never --init --name tutor-run-<random> --hostname localhost --network
    none --read-only --tmpfs /tmp:rw,exec,size=1g,mode=1777 --user <runner> --cap-drop ALL
    --security-opt no-new-privileges --pids-limit 1024 --memory 2g --memory-swap 2g --cpus 4
    -v <run dir>:/work -w /work -v <tutor package>:/opt/tutor/tutor:ro -e <this run's variables>
    <image> timeout -s KILL <limit + 5> python -m pytest ...`. The run dir is the same throwaway
    copy as today, so the container sees only that copy, with links already refused. Nothing
    of the host's environment goes in, so never `.env` or API keys: the fixed values (`HOME=/tmp`,
    `PYSPARK_PYTHON` and so on) are in the image, and `-e` carries only what a caller set for
    that run.
  - Why each flag that is not obvious. `--hostname localhost`: with `--network none` Docker
    does not list the container's own name in `/etc/hosts`, and Hadoop stopped with
    `UnknownHostException` (measured, reference failed; with the flag VALID). `--init`: without
    it, processes nobody waits for stayed as zombies (seen under a long-lived `sleep`), and
    the container ends with its main process, so a leftover JVM ends with it. `timeout`: the
    container ends even if the validator is killed, when nothing is left to call `docker kill`.
    `--pull never`: an image that went missing stops the run, and nothing is fetched from a
    public registry under that name. `exec` on `/tmp` (2026-11-07): Docker's tmpfs is noexec by
    default, and a C exercise's tests compile into `/tmp` and run the program there (a program
    built there could not start, exit 126); the run folder, a bind mount, always allowed it,
    so nothing can run that could not before. `--cpus` (4) is capped at the VM's CPUs, as Docker refuses
    more. The limits come from one Spark hypothesis run, which peaked at 393 processes and
    threads and 486 MB; 512 left too little room, so 1024.
  - Measured 2026-11-05, Docker Desktop on macOS (arm64, a 12-CPU VM with 7.66 GB). Median of
    5 test runs, seconds, host / `docker exec` / `docker run`: plain 0.58 / 0.65 / 0.73, MRJob
    inline 0.85 / 0.77 / 0.87, Spark 4.69 / 4.73 / 4.71, Spark hypothesis 8.25 / 8.07 / 8.18. An
    empty `docker run` costs 0.14 s, an empty `docker exec` 0.07 s. Four Spark hypothesis runs
    at once: host 11.9 s, `docker run` 10.9 s, against the 30 s limit. A whole example's
    validation, median of 2, host / container: plain 1.4 / 1.8 s, Spark RDD 24.3 / 23.2,
    file-level Spark 34.1 / 33.0, DataFrame 38.5 / 37.9, Hadoop 102.3 / 84.6; every one VALID
    on both sides. With only 2 repeats (Hadoop on the host took 94 and 110 s), this shows no
    real cost, about 0.4 s on the plain one, and not a gain. So the OS-sandbox fallback
    (Seatbelt, bubblewrap) is not needed.
  - Why not one long-lived container. Limits are per container, so runs share them: four
    ordinary Spark runs in one container with a limit of 512 hit it 40 times (the cgroup's
    `pids.events`; no memory kill), 3 failed with `unable to create native thread`, and the
    fourth hung until I killed it at 329 s. A limit of 8192 passed, peaking near 1400, but then
    it no longer stops one run from starving its neighbours, which would make false INVALIDs.
    Killing a `docker exec` client also leaves the code running inside (1 process measured),
    while `docker kill` on a `docker run` left nothing in 0.17 s. Runs would share `/tmp` too.
    And a mount of a folder deleted and made again just before failed with `chdir to cwd` in
    2 of 3 tries, most likely stale file sharing (inferred, not traced).
  - Limits checked in a running container: uid not root, no internet and no DNS, cannot write
    the root file system, its home or the mounted code, cannot see the host's home or `.env`,
    511 processes then an error at a limit of 512, exit 137 over the memory limit, `/tmp`
    writable. The only environment name holding KEY comes from the base image (`GPG_KEY`,
    believed a public signing key).
  - Which courses: `workspace.toml` `[isolation] runner = "container"`, looked up from the files of
    each run, so every caller of the runner is covered. Unlike `[safety]`, every `workspace.toml`
    above every source counts and one asking for the container wins, so a folder holding its own
    file cannot switch it off, and a student file kept elsewhere (held-out candidates, simulated
    hints) still runs with the course's tests in its container. A mistyped value or an unreadable
    file stops the run. No choice made: the host, with one warning per process. `"host"`: a choice,
    so no repeated warning. No `workspace.toml` at all (the repo's examples, CI): the host.
  - No silent fallback (owner, 2026-11-05). A course on the container with no Docker or no
    image raises `ContainerError`, which is no verdict on the code: `tutor.validation` and the
    held-out check exit 2 and write no report, so a stopped Docker can never read as INVALID.
    Generation checks before its first LLM call and again before each item, so that when
    Docker stops in the middle of a batch, the item it hits reports an error like any other
    and the ones after it spend no tokens. Every run asks whether the image exists, which costs
    0.07 s and, importantly, waits while a stopped VM wakes (Resource Saver; measured 2.5 s on the
    first call after idling), so those seconds do not count against the code's time limit.
  - Image: `python -m tutor.container build`, one per course, generated from `[requires]`:
    `python` picks the base image, `java` the JDK, `gcc` Debian's gcc and libc6-dev (in a
    step of its own, so the recipes and image names of courses without C stay as they were;
    the check after a build compares its version with the prefix), `hadoop` the Apache tarball
    with its SHA-512
    fixed in code (`downloads.apache.org`, about 3.3 MB/s, then the slower archive), and any other
    name a pip package, at the host's exact version if it matches, else the prefix. `pytest`
    and `hypothesis` at the host's versions. A plain Python course 184 MB; with Java and Spark
    769 MB; with Hadoop too 1.73 GB (923 MB of that is Hadoop, with its documentation removed),
    about 4-5 minutes from nothing. The tag holds a hash of the recipe without the host's
    versions (two venvs of one course hold different pytest versions, and neither may call the
    image out of date), so a changed `[requires]` says "rebuild"; validation never builds.
    Envcheck then runs inside, against the same `workspace.toml`, and an image that fails is
    removed. The `runner` user gets the host's uid, so the host can delete what a run wrote
    (macOS tested with its uid, native Linux not).
  - The report is read only if it is a regular file: the container can replace it with a link
    or a FIFO, which would make the host read elsewhere or hang.
  - Accepted gaps. A run can write to its own throwaway folder until its time limit, since a bind
    mount has no quota; it is deleted afterwards. What it prints is held in memory on the host, as
    it already is for a run on the host. The heavy-run cap still counts the host's cores, so a VM
    given far fewer needs `TUTOR_MAX_HEAVY_RUNS`. The learner's ordinary `pytest test_student.py`
    stays available on the host (owner, 2026-11-05: debugging in the IDE), which needs Java or
    Hadoop locally for that debugging path. Generated tests that passed in the container can still
    behave differently on the host, since code can tell where it runs; only layer A covers that. The
    command that runs a learner's tests in the container, with attempts written on the host from its
    result, is now `tutor test` (Roadmap 6.3, approved 2026-11-08); the risk tiers below come
    when a course needs them. A container has no Mac GPU, so
    torch exercises run on the CPU. The Docker VM takes memory next to a local LLM. Windows and
    native Linux are not tested, and Windows stays on the host until they are. Podman is not tried.
- Risk tiers for a course's modules (design for the generalization phase): pure
  computation (numpy, networkx) is low and runs on the host; file- or URL-capable libraries
  (pandas `read_csv` takes URLs, numpy `load` unpickles) are medium; system-level ones
  (`subprocess`, `os.fork`, sockets, an operating-systems course) are high and run only in
  the container. Which modules a course needs is hard to list by hand, so an LLM may draft
  `[requires]` and `[safety] allow_imports` at workspace creation, each entry with its
  source (lab, page), and the owner accepts entry by entry, as for the GUI's `[requires]`
  draft. A generated exercise that needs an unlisted module fails `safe` and yields a
  proposal to the owner, never an automatic widening.
- Other languages (generalization phase; C's validation built, below): a `language` field in
  metadata (optional, default `python`); tests stay pytest and run the compiled program as a
  subprocess, as file-level exercises already do; one safety checker per language chosen
  by `check_safety` (C: `validation/csource.py`; for later languages tree-sitter, sqlglot for
  SQL), with per-language allowlists. A few languages built in, others configured by the owner
  in `workspace.toml` (extension, compile and run commands, allowlist); those commands run, so
  generation never writes them. A language without a built-in checker needs the container.
- C (owner, 2026-11-07). Four steps: (1) validation, built; (2) the image and the GUI's
  Environment page, built; (3) generation, built (question.md from student.c came forward
  into it); (4) what the learner touches: the GUI's exercise page and Open in IDE, attempts'
  snapshots and the progress report (4a), hints and the held-out check (4b), built. The first C course's lectures (C99 with `gcc -Wall -std=c99`,
  Makefiles, programs on standard input and output, expected-output files, gdb and valgrind,
  no unit-test framework) show it tests by given input and expected output, so:
  - pytest drives the compiled program (owner chose the recommended option). The rules,
    `execution.py`, the JUnit XML counts, hints, held-out checks and attempts stay as they are;
    only file suffixes, compiling and the static check differ by language. A C unit-test
    framework (Unity, CMocka) would have to be installed in the image and learnt, though the
    course never taught one; that stays for a C++ or Java course that already tests with
    GoogleTest or JUnit.
  - Format: `schema.CMetadata`, picked by `"language": "c"`, with `framework: "c"`, and not new
    fields in `Metadata`: Metadata's JSON Schema goes to the generate and repair models inside
    `ExerciseDraft`, and the calibrated Python prompts keep their exact text
    (`schemas/metadata.schema.json` is unchanged; `metadata-c.schema.json` is new). Code files
    are student.c, reference.c, skeleton.c and mutants/*.c; tests and oracle stay Python, so
    the oracle is written in another language than the reference.
  - Two shapes (owner): a whole program (`interface: "file"`: student.c has main; tests give
    standard input, arguments or files and compare output and exit status) and a function
    (`interface: "function"`: student.c holds functions; a given driver.c and header in the
    exercise folder have the main, and the header lets the compiler check both sides agree).
    Not ctypes: a crash in the student's code would take pytest down with it, a failed test
    would read as an error, and rules 2 and 3 would judge wrongly. Several files with a
    Makefile: later.
  - `tutor.testkit_c`. `build(...)` compiles while pytest imports the test file, so a compile
    error is a collection error and the run an ERROR: a skeleton that does not compile fails
    rule 2 as an import error does, and such a mutant is "broken", not "killed".
    `program.run(...)` raises AssertionError on a non-zero exit, a signal, a sanitizer's report
    or its own time limit (5 s), so each of those fails one test, and a hanging mutant is
    killed in seconds. It imports only the standard library and `tutor.config`, since it runs
    in the container.
  - Sanitizers (owner: ASan and UBSan, not valgrind). Every build adds
    `-fsanitize=address,undefined`, `-fno-sanitize-recover=all` (UBSan otherwise prints and
    carries on), `-g`, and `-Werror=implicit-function-declaration` (C99 otherwise links any
    library function called without its header, which the header check counts on). Why not
    valgrind: 20 to 30 times slower on every run, Linux only, and blind to overflows of arrays
    on the stack, the course's usual arrays. It does catch reads of uninitialised memory, which
    ASan does not; an extra valgrind pass in the container is the way if such mutants are
    wanted. In the examples, a stack array too small and a loop one past a malloc'd array are
    killed only through the sanitizer.
  - Leaks are checked only in a run that asks, `run(..., leaks=True)`. LeakSanitizer's check
    at exit took 0.72 s per run in Docker Desktop's VM (arm64, linuxkit kernel 6.4), the same
    with every container limit removed, with seccomp unconfined, with `SYS_PTRACE` and on one
    CPU, while the run itself took 3 ms; with it always on, rule 4's 100 examples ran past 30 s.
    Not traced further. On macOS there is no LeakSanitizer, so such a run warns that leaks were
    not checked there; it works under the container's limits (`pytest -m docker`).
  - Flags: `workspace.toml` `[languages.c] flags`, which only the owner writes, else
    `-std=c11 -Wall`. The validator passes them to every run (`TUTOR_C_FLAGS`), as a run in
    the container cannot see workspace.toml; a learner's run reads the file. The reference must
    compile without a warning (`TUTOR_C_STRICT`, rule 1 only), as the course asks of its
    students; a skeleton or mutant may warn (`if (x = 0)` is a real mistake that warns).
  - Rule `safe` (owner: header allowlist, and container only). C is validated only in a
    container: `safe` fails while the course's runner is not "container", the repo's examples
    included, whose end-to-end check is `pytest -m docker` (the fast suite runs the rules on
    the host with that check patched out). `csource.py` reads every .c and .h a run compiles,
    on tokens without comments or string contents: `#include <...>` only of the C standard
    headers and a course's `[safety] allow_headers`, `#include "..."` only of the exercise's own
    files by plain name, no other include form (a macro, `#embed`, `#import`), no inline
    assembly or `##`, none of a list of process, shell, network, code-loading and system-call
    names in any use (C lets code declare a library function itself), no literal path outside
    the exercise in `fopen` and the like. It exists for the learner's own runs, which compile
    the generated skeleton and driver on their machine: a filter that stops the obvious, as
    layer A does for Python.
  - Rule `api` reads tokens (`forbidden_calls`, `required_definitions`; a definition is a name,
    its parameters and a body); rule `main` asks a whole program's student.c and reference.c
    for main and does not apply to a function exercise; rule 5 does not apply.
  - The learner's own runs stay on their machine for now, and a command that runs them in the
    container comes later (owner): on macOS `cc` is Apple's clang without LeakSanitizer, and
    envcheck reports `gcc` there as clang. Measured: both examples VALID in a container with
    the course's `-Wall -std=c99` in 3 to 6 s each, and with clang on macOS.
  - No native Windows for C, WSL instead (owner, 2026-11-07, after Windows CI). MinGW's gcc,
    the compiler there, has no AddressSanitizer or UBSan (`cannot find -lasan`), so every C
    test failed to link. The other ways (llvm-mingw, MSVC's ASan without UBSan, Dr. Memory in
    place of valgrind, which has no Windows version) each need an install of their own, report
    in their own words, so the hint rules would be written and tested again for each, and
    differ from the container's gcc that validates. In WSL, `sudo apt install gcc` brings both
    sanitizers, and Docker Desktop on Windows runs on WSL 2 anyway. `testkit_c.build` says so
    on native Windows instead of a linker's error, and a compiler elsewhere without the
    libraries (a minimal Linux install) gets a sentence before the linker's words;
    `testkit_c.host_problem()` gives the same reasons to a caller (the tests' skips).
  - Known issue: AddressSanitizer and 32 bits of address randomisation. Kernels from 6.5 may
    randomise mmap addresses with 32 bits (`vm.mmap_rnd_bits`), and gcc 12's ASan then
    sometimes never finishes, looping on "DEADLYSIGNAL"; a container shares the host's kernel,
    so the course's image is affected on such a Linux host. Seen on GitHub's ubuntu-26.04
    runner (2026-11-07): one C run in each example timed out at 5 s, with no output kept to
    prove the cause; Docker Desktop's VM on the owner's Mac uses 18 bits, and 32 bits there
    (arm64) did not reproduce it in 40 runs. CI prints the value and sets 28 (see "CI"); for a
    user on such a Linux host, `tutor doctor` ("Roadmap" 6) now checks the value in C's
    registered machine check and prints the `sysctl` command (2026-11-09); it never runs it.
    With 28 set, the next CI run (2026-11-08) passed every C test, and
    nothing else about C had changed; as the hang is intermittent, one run is strong evidence,
    not proof.
  - Step 2, a course's language (owner, 2026-11-07; `tutor/languages.py`, the Environment
    page). A course is a C course when workspace.toml has `[languages.c]`, which only the
    owner writes, through the page or by hand; not inferred from `[requires]`, where `java`
    also serves Spark. Python needs nothing declared. A table for another language, or a
    `[requires]` entry naming the toolchain of one (javac, rustc, g++, node, ...), is reported
    as not supported yet, by envcheck and so by the GUI, instead of being taken for a pip
    package that then fails to install.
    - Found in the lectures, confirmed by the owner: the [requires] draft's "gcc" kind (prompt
      version 3), and the compiler flags from the gcc, cc and clang lines the cached lectures
      show, with no model: the set of `-std=`, `-W`, `-pedantic` and `-ansi` flags the most
      lines share, shown with its first line (the first C course: `-Wall -std=c99` on all 19
      lines). The box starts at the course's flags, else these, else `-std=c11 -Wall`.
    - Ticking C writes `[languages.c] flags`, `[safety] allow_headers` (headers beyond the
      standard library's, each a plain `name.h` path) and `gcc = "12"` in [requires] when
      missing (the image's Debian gcc). Checked writes as every other table, the file before
      kept as `.bak`.
    - The page's step 1, laid out for more languages (owner, 2026-11-07): "Find it in the
      lectures" first; then one language list, Python first and not removable (the tests are
      pytest), each row its [requires] toolchain's version (python, gcc), with "Add a
      language" (C, and the languages not supported yet, greyed) and a line when the
      lectures compile C; then each language's own settings, Python's packages and tools
      (the rest of [requires], with "Exercises may import") and C's flags and headers
      (`_language_c.html`, only once C is in the list). A later language adds a row in
      `tutor.languages.LANGUAGES` and a `_language_<key>.html`. Removing C now takes gcc out
      of [requires] too, as it is C's row (it used to stay); accepting gcc from the lectures'
      candidates adds C, with the version typed (an explicit click, so C is still declared,
      not inferred). A pure Python course shows no C settings at all.
    - Doctor's registry (2026-11-09) follows the same language membership/order. Each new
      language also adds a doctor_languages module and DOCTORS entry, a shipped smoke
      example and machine/platform tests. LanguageDoctor keeps machine_checks,
      platform_problem and validation_needs_container separate; doctor.py is unchanged.
    - "On this machine" is greyed for a C course and refused by the server: C runs only in
      the container. A `gcc` here of another make or version (macOS's clang) is shown as
      "differs", not as missing: the learner's own runs compile with it, validation with the
      image's gcc; only no compiler at all is missing.
    - New batch was held back for every C course until step 3 (owner), as the pipeline wrote
      Python exercises only. Since step 3 it is held back only while the course's tests would
      not run in a container, on the page and by the server, and `tutor.graph` stops there
      too, before its first model call: every draft would fail rule `safe`.
  - Step 3, generation (owner, 2026-11-07; `graph/`, its design explained and approved first).
    - One prompt template for every language, not a copy per language (owner: otherwise a new
      version per language). `prompts.SPEC`, `PLAN_TEMPLATE` and `ORACLE_TEMPLATE` hold
      `{slots}` where a language differs (the rules, where the question goes, the tests, the
      reference, the main rule, the misconception shown, test data, how to test the "how",
      the code rules); each language's `LanguagePrompts` fills them (`graph/prompt_parts/`:
      `python.py`, `c.py`; one module per language since 2026-11-08), and a new language adds its
      parts module, a profile (`graph/languages.py`: its plan and draft models, their schemas, where
      the question is) and a synthetic example. This keeps the decision that calibrated
      prompts stay as they are: Python's parts are its old text cut out of it by a script, not
      retyped, and before the change 35 outputs (system prompts with every option, item, repair
      and retry prompts per framework and mode, plan and oracle prompts, the three JSON
      Schemas) were pinned by hash (`tests/test_prompt_pins.py`); they still match, so
      Python's calibration and what providers have cached of it stand.
    - Models of its own for C (`CPlanItem` with framework `c`, `CExerciseDraft` with
      student_c, reference_c, and for a function exercise student_h and driver_c), so the
      Python schemas the models receive keep their text. A C course's batch is all C (owner).
      Both shapes are generated (owner): the model writes a function exercise's driver and
      header, which the student gets as they are.
    - What the C prompts add: the rules as the validator applies them to C (compile without a
      warning under the course's flags, which the prompt names; a skeleton that compiles and
      fails; a mutant that does not compile is broken); the question in student.c's opening
      `/* */` comment, Markdown as is and no `*/` inside; tests through `testkit_c`, whose API
      is read from the module; C misconceptions as mutants, a leak only with a `leaks=True`
      test, never a fault in reading uninitialised memory or unspecified behaviour (no test
      catches those every time); the C code rules, with the course's extra headers.
    - A C mutant names its bug in a `/* Mutant: ... */` line (a docstring would not compile),
      in place of the reference's opening one-line comment, so a diff shows only the bug.
    - Measured so far with stand-in models only: a C item goes from a reply to a saved exercise
      through the real validator on the host and in the course's container. The C prompts
      are not calibrated: the first batches on the C course measure them.
    - The first real batch (2026-11-07, learn mode, 5 exercises; deepseek-v4-flash
      generating, gpt-terra writing the oracle): 5 of 5 VALID, one in round 0 and four after
      one repair round. Round 0 failed three times on rule 1, the reference and its own tests
      reading the question differently (what `scanf` returns at the end of input, a computed
      float, a `printf` format), and once on rule 4, the oracle's test file not loading (a
      Hypothesis test defined twice). Too few to change the prompts on; more batches first.
  - Step 4, what the learner touches (owner, 2026-11-07), in two parts: 4a the solving itself
    (the exercise page, attempts, the progress report), 4b hints and the held-out check.
    - Which file is the learner's code is told by the file in the exercise folder
      (`tutor.languages.student_path`: student.c if there, else student.py), not by
      metadata.json (owner chose the recommended option): metadata sits in `solutions/`, and
      the learner's side (the GUI's pages, the attempts plugin, which loads in every pytest
      run and so imports only light modules) reads the exercise folder alone. Commands that
      load the whole exercise (hints, held-out) use its metadata's language as before.
    - 4a: the exercise page names student.c and says the question is its opening comment when
      there is no question.md; Open in VS Code opens student.c; attempts keeps a failing
      run's student.c as `<hash>.c`, so the held-out check can run it as C; the progress
      report compares student.c with skeleton.c to tell an edited exercise from an untouched
      one.
    - 4b, hints. Layer 1 has C rules of its own (`hint/rules.py` C_RULES), read from what the
      compiler and the sanitizers print as testkit_c passes it on, written from real runs of
      both compilers (clang on the learner's Mac, gcc in the container, `pytest -m docker`):
      a full-width character in the code (not in a comment or string); a library function
      without its header (the #include the compiler's note names) or a function called
      before its prototype; a function not defined (the linker); the compiler's first error;
      a forbidden function (`csource`'s tokens); a program that never finishes; and by the
      sanitizer's report an index outside an array, memory used after free, a double or bad
      free, a NULL or invalid pointer, a stack overflow, a leak, signed overflow, division
      by zero, a bad shift, other undefined behaviour. The place is the first frame in the
      student's own file, so a fault inside scanf names the student's line. Review mode
      names the kind only, as for Python. Python's rules and their order are unchanged.
    - Layer 2 reads a C mutant's bug from its `/* Mutant: ... */` line; signatures leave
      out a sanitizer's process id and the folders before a C file's name, which differ
      between two runs of one mistake. testkit_c now starts a sanitizer's excerpt at its
      line, so UBSan's names the file ("student.c:4:20: runtime error"), without ASan's
      process id.
    - Layer 3 names student.c and its opening comment to the model (Python's text is
      unchanged), and the leak check blanks C comments, so the question's text in the
      reference is not taken for code.
    - Held-out checks the learner's own C code (the passing student.c, the `.c` copies of
      failing runs). A model's held-out solutions and the teaching judge stay Python-only
      (their prompts and calibration are Python's): both refuse a C exercise by name, and a
      model's file in a C exercise would be skipped, never run unchecked. To be designed
      with the first real C batches.
    - With no "## Run" section, a C question's "How to run" compiles with the course's own
      flags, and a function exercise's driver.c (`question.c_run_command`).
    - VS Code: ticking C on the Environment page also writes `.vscode/c_cpp_properties.json`
      with the flags' C standard (`-std=c99` -> cStandard c99) and the flags, so completion
      and red underlines follow the course and not the compiler's default (owner's
      question, 2026-11-07); unticking removes it. A file the course already had is left
      alone, and the page says so. It is read by Microsoft's C/C++ extension.
- Languages in the plan (owner, 2026-11-07), in this order: C, C++ and Java first, as the
  owner has courses in them; then SQL, JavaScript and TypeScript, shell, Rust, R, Go, Haskell,
  assembly, and MATLAB (open, below). Each is designed when a real course with lectures needs
  it, since a design from guesses gets shaped around the wrong course; the order may follow
  the course at hand. What each brings:
  - C and C++: gcc or clang, a compile per run; a static check cannot bound what C does
    (pointers, inline assembly, system calls), so container only; sanitizers (ASan, UBSan)
    turn memory errors into failing tests. C++ reuses C's compiler and harness.
  - Java: OpenJDK (a build such as Eclipse Temurin), not Oracle's JDK, whose licence terms
    differ by version; a JVM start per run is slow (a heavy framework for the executor's caps); courses
    test with JUnit, which bears on whether tests stay pytest.
  - SQL: `sqlite3` from Python's standard library, no compiler; sqlglot can allow only the
    statements an exercise needs; a course on another dialect (PostgreSQL) needs that server
    in the container.
  - JavaScript and TypeScript: Node.js and the TypeScript compiler; npm packages are a supply
    chain risk, so installed from a lock file when the image is built, never per run.
  - Shell: system-level by nature (files, processes), so the high risk tier, container only;
    a static check means little.
  - Rust: compiles take seconds per run; crates fetched when the image is built, runs offline.
  - R: Rscript; packages from CRAN when the image is built.
  - Go: fast compiles. Haskell: GHC is a large toolchain and compiles slowly.
  - Assembly (MIPS, RISC-V): run in a simulator (SPIM, MARS, RARS), not on the machine.
  - MATLAB: a proprietary product; running it needs a MathWorks licence, also in a container.
    GNU Octave (free, GPL) runs most MATLAB code but not every toolbox. Open: Octave for
    MATLAB courses, with exercises kept to what both run.
  - Licences: every other toolchain above is free to install and run. Images are built on the
    user's machine and not published, so the GPL's terms for distributing (bash, gcc, R,
    Octave) do not arise; publishing images would bring them.

## Version conflicts

Doctor step 2A (2026-11-09): `tutor.doctor_advice` now supplies immutable caller-fact,
advice and result records, a strict JSON schema/parser, and a pure permission gate.
It is a library only: no CLI advice flag, model/network call, environment discovery,
installation, execution or file write. Default doctor behavior is unchanged.
Package identity follows PyPA's case/separator normalization. Permissions come only from
declared and installed distributions and `compat.needed` for this course at the proposed
Python; batch order cannot retain companions needed only by the old Python. System tools
and unsupported toolchains are excluded from package changes. Python choices must be
offered, unlocked, different and not ruled out by known conflicts in the current declared
requirements. This conservative check does not resolve joint package/Python changes.
Version constraints accept only numeric releases with ==, !=, >=, <=, >, <, ~= and comma
intersections; ~= needs at least two components. This is a restricted subset, not a full
PEP 440 parser or dependency solver (no wildcards, pre/post/local/epoch syntax, URLs,
markers, extras or pip options). Syntax approval proves neither satisfiability nor that
a matching release exists. Gate results are accepted/rejected/stopped/new-package-review,
always unverified. Rejection or new-package review releases no changes; a new name stays
in its separate review field. Empty changes stop rather than claim success. Disposable
verification and owner-approved application remain later increments.

Doctor step 2B (2026-11-09): explicit --preview-advice/--advise modes require a selected
UTF-8 failure record. The first previews only; the second sends sanitized material and
measured distribution/Python facts to an explicit doctor role/--profile (LLM_* outside
course configuration). Only --env-file reads credentials, through config.load_env's new
optional target mapping; os.environ and subprocess environments are not populated with
those values. Probe the course venv/image, never substitute tool facts for a missing one;
image metadata uses existing container limits, no mount/network/pull/build, and its Python
is locked until 2C. Static probes have captured text, deadlines and owned-container cleanup.
The new prompt marks every external section with graph.prompts.material. llm.chat_payload
is a pure shared preview/request layout; schema_first is opt-in for doctor, leaving old
payloads and calibrated prompts unchanged. llm.py stays whole as one provider-client job.
Thinking is off; retry once for truncation only. Input/request bounds are 64/128 KiB,
never a token estimate. No log or assistant request file is written; exchange profiles
are refused. Every reply passes 2A, all suggestions remain unverified warnings, and unknown
names are withheld pending the independent review path. No fix or original file is changed.
Redaction is best effort; owners must review their selected log before requesting a send.
One assignment keeps its value: a model profile's `api_key_env = "NAME_OF_A_KEY"`, whose
value is a variable's name (upper-case words joined by underscores, named like a
credential); any other value on that line is hidden (owner, 2026-11-09).

Doctor 2C verification (2026-11-09): --verify-advice adds fixed import/dependency-check
evidence, never executes failure text or model-written code. A passing baseline stops
without a model call. A failing baseline is followed by at most three cumulative trials
in fresh disposable venvs/images, with repeated-proposal/missing-module stop rules. Every
reply and cumulative target-Python batch passes the permission gate. Public PyPI wheels
only; inherited pip configuration/credentials are excluded. Host package execution has
user rights; container trials derive an owned image and use tutor.execution's limits,
without host fallback. Temporary resources are cleaned, and unavailable cleanup is
reported. Only the same check failing then passing is verified, not a whole course.
Container Python remains locked. --review-new-package independently asks distinct
provider/endpoint profiles with thinking on, then checks bounded PyPI release-file JSON;
only agreement plus release evidence is displayed, never automatically installed. A
profile label cannot prove the underlying model identity; owners select independent
models. Application/rollback, GUI integration and image records remain separate steps.

Doctor application (2026-11-09): optional [package-bounds] holds the same restricted
numeric comparisons; [requires] retains numeric prefixes. A package identity occurs in
only one table, and repair preview explicitly moves an affected package. Workspace's
effective_requires feeds checks, installs, image identities/recipes, doctor facts and GUI
environment consumers; empty bounds preserve legacy recipes. Known bad release families
are conservatively intersected with bounds; nonnumeric installed versions cannot pass a
numeric bound check. Pip, not this grammar, resolves dependencies. The doctor extra adds
tomlkit only for explicit application/preview, preserving comments/unrelated tables.
--save-repair writes exclusive private structured artifacts with workspace/environment
hashes, no failure text/paths. --preview-repair prints the full diff; --apply-repair with
--confirm re-gates, rejects stale facts and repeats before/after verification. Host
replacement retains the original venv, builds at its final path, checks and rolls back
on failure/interrupt. Container application uses a temporary proposed workspace and
keep_older=True so existing images survive, promoting configuration only after checks.
A per-course lock serializes transactions. Successful backups remain for --undo-repair
or explicit --finish-repair --confirm; edits to saved/current config refuse undo. No
student/lecture/model/runner edit, system install or new-package auto-application occurs.
The Environment integration follows below; image inventories are described under its step 4.

Doctor GUI integration (2026-11-09; the clicks made are listed below): Doctor is the
automatic check in Environment step 2. The initial render defers read-only probes to htmx
fragments; these receive structured, redacted doctor rows without parsing CLI text. Known
problems link to existing install/build, Python switch, runner or host-guide actions on
their own rows. A running course task disables fix controls. Old `/c/<course>/doctor`
bookmarks redirect to Environment step 2; there is no separate Doctor page or bounds editor.

An uncovered Problem offers "Ask a model for a fix", opening a four-screen wizard:
selected data/profile and full sanitized preview, advice, temporary verification result,
then complete diff and application. The failure is pre-filled from the row and latest
failed install/build log tail, at most 64 KiB of UTF-8, and remains editable. An imports
row also supplies its module when recognized. Selected inputs stay in private logs/doctor.
Preview and Send are separate POST actions. Only new-package advice exposes independent
second-provider/PyPI review; a changed destination requires a new preview. Failed
verification offers a return to step 1. A verified proposal still needs a full diff preview
and confirmation before Apply; afterwards Undo restores the original setup and Keep it
explicitly discards its backups. Finish preserves later owner edits; undo refuses edited
configuration. Each action retains the existing CLI service's gates and checks.

Destination/input/config/fact fingerprints bind send/apply to current previews. Model
credentials stay in the worker's private mapping, never the server environment; both
profiles' credential values are removed from shared previews. The wizard displays complete
bounded results, not parsed log tails; oversized previews/diffs are refused rather than
silently cut. Temporary installs, apply, undo and backup discard have explicit consent.
Step 1 has one version cell per package, with prefix/comparison storage described below.
Clicked in headless Chrome on scratch courses, with stand-ins for the model and the
installer (2026-11-09): a missing `.venv` row's Install (the fix buttons disabled while
it ran, then every row OK); the four fix screens (preview, send, test, the diff, Apply,
Undo); an old Doctor bookmark's redirect; a version moved between prefix and comparison
with its comments and the `.bak` kept, and `java >=17` refused; every step's links.
A second round the same day, the container ones with the real Docker: a Docker that
does not answer (its row a Problem, the image row skipped); Build on the image's row
(disabled while it ran, then OK); step 4's record with a package the .venv had moved
away from the image; a .venv on a Python the course no longer declares, switched with
"Make .venv with Python 3.12"; with only an unsuitable Python on the machine (a
stand-in list), "Download one with uv" opened; and a new package's independent review.
The uv download itself was clicked on 2026-11-10, once uv was installed: the page listed
five CPythons with the two the course cannot use marked, the form waited for its tick,
`uv python install` put 3.13 in uv's own folder, and the switch then offered it.
That round found two faults no test had: the page took envcheck's exit status 1 (a
requirement fails) for a check that could not run and dropped its rows, so the switch
did not mark the .venv's own Python nor apply the probe's removed-module rows; and the
plan page showed its date only after a reload. Both are fixed, each with a test.

Doctor's own output (the CLI's lines and the rows the page receives) hides the values of
environment variables named like a credential (KEY, TOKEN, SECRET, PASSWORD and the
like: the advice redactor's name rule) and any text shaped like an API key. It does not
hide every environment value (owner, 2026-11-09): hiding HOME or TMPDIR cut the paths
out of the commands a row advises. What is sent to a model still goes through the
advice redactor, which is stricter.

Decided 2026-11-08 (owner), when Python 3.12 to 3.14 came into CI: a package that installs and
imports cleanly can still fail when a job runs, because a newer Python removed something it
uses (mrjob: `distutils` in 3.12, `pipes` in 3.13) or changed what it relies on (PySpark 3.5
on 3.14: every RDD job fails with a `PicklingError`). The same kind of conflict comes with
every language and framework, so one way of handling them:
- Ask the tool first. What a tool can tell (a compiler about its flags, a JDK about its
  release, pip about what a package declares) is probed, never written down: a probe cannot go
  stale. Languages' own package managers (Maven, Cargo, npm) resolve their libraries; the
  project handles only the runtime's version.
- Then a rule, for what only fails late: `tutor/compat.py`. A companion is a pip package that
  must come with a `[requires]` package from some Python on (mrjob: setuptools from 3.12,
  standard-pipes from 3.13); a conflict is a package version that does not work from some
  Python on (PySpark 3.5 from 3.14). The venv install adds companions with environment
  markers, so pip decides by the venv's own Python; the image adds them only where its Python
  needs them, so a course that needs none keeps its image name and is not rebuilt; the
  `[requires]` check refuses a conflict, and the environment check reports both on the
  Python running it. Each rule is added by hand with its measurement, and CI's Python versions
  test the ones that apply.
- A probe for packages no rule names: the environment check reads each `[requires]`
  package's installed source for imports of standard-library modules the running Python
  removed (`compat.REMOVED_MODULES`, from What's New in 3.12 and 3.13) and names every one at
  once, unless this Python still finds it (a backport is installed) or a companion rule
  brings it back (that rule's check reports it). The image's own check runs it too, so an
  image whose package cannot import refuses to be called ready.
- The fix path in the page (owner, 2026-11-08; "GUI" in AGENTS.md: a gap found in the page
  gets a fix there): when the check reports a conflict with the course's Python or a removed
  import, step 2 lists the Pythons already on this machine (`tutor/pythons.py`: python3.X on
  PATH, and the py launcher's list on Windows; each asked for its version and its base
  interpreter) and marks which suit the course, worked out by code: the conflicts on each
  version, and every version from a removed module's removal on. A button on a suitable one
  runs `python -m tutor.gui.python_switch`: `tutor.workspace init --python ... --replace-venv`
  makes the `.venv` anew (the one before kept as `.venv.old` and put back if anything fails,
  `[requires]` checked against the new Python), and only then sets `[requires] python`
  (`workspace.toml.bak` kept). The server runs only a Python it offers, found again on the
  request, never a path from the form. Additional interpreters now use the explicit uv
  download page reached from Environment's Python recovery (described under "Local GUI").
- Original model-fix design (2026-11-08; implementation recorded above): it reads
  the failure's text, the versions and `[requires]`, and proposes a change as structured data
  (a version, the course's Python, a companion), which the owner confirms before it is applied
  and the checks then verify; a fix that holds becomes a rule. Its prompt is strict: the
  failure text is material, not instructions (AGENTS.md, "Prompts"); a change is another
  Python, or a version constraint on a package that `[requires]` names, that is already
  installed in the environment, or that a rule names (`numpy<2` under a pandas built for
  NumPy 1); never a new package name, which could be a name nobody checked (a look-alike of
  a real package). A model that believes a new package is needed says so in a field of its
  own, which is shown, never applied. A deterministic gate checks the reply's kind, names and
  pip specifier before anything runs. Preferred fixes, in order: another Python for the
  course, another version, a companion. Thinking off (a short structured answer), one retry
  when the reply is cut off, and every proposal verified in a fresh environment before it is
  offered: measured 2026-11-08 on six real conflicts from the web and one with an injected
  instruction (`notes/version-conflicts/`, the calibration set for this prompt), DeepSeek
  V4 Flash and Pro, GLM-5.3-Flash and GPT-6.1-Terra each fixed the five no rule covers and
  ignored the injection, and all four proposed Python 3.12 for mrjob on 3.13, which the
  verification showed still fails (`distutils`): known conflicts belong in rules. GLM with
  thinking on spent 4,000 and then 12,000 tokens without an answer on one case; off, it
  answered in 4 s. Default model: DeepSeek V4 Flash, thinking off (about 1.3 s and 80 output
  tokens a case).
  - A repair loop, at most three rounds: each round's proposal is applied on top of the last
    in a fresh environment, and on failure the model gets what was tried and the new error;
    it stops when the same module fails twice. Measured on mrjob with the Python locked at
    3.13 (two modules missing): every model needed two rounds, one module each. Asking it to
    recall first what the Python version changed (owner's idea) barely helped: a model does not
    know what a package imports, and recalled some removals in the wrong version. Given the
    probe's findings above as facts instead, every model fixed it in one round. So the prompt
    gets facts code can find (the probe, the rules that name the course's packages, the
    failing package's last release), never a request to recall them.
  - The packages a change may name are the course's `[requires]`, what is installed, and the
    companions the rules give for the course's own packages: never every name a rule holds,
    since models reached for an unrelated one (standard-pipes against a missing `audioop`)
    and copied its naming (`standard-audioop`).
  - A new package name is shown with PyPI's facts (whether it exists, its first release, how
    many) and asked of a second model, never applied. Measured: for pydub on 3.13, which
    needs `audioop-lts`, both DeepSeek models named `pyaudioop` (the module in the error
    message) and GLM once `standard-audioop`; neither exists on PyPI, so anyone could
    register them. GPT-6.1-Terra named `audioop-lts` every time.
  - Two tiers (owner, 2026-11-08): the repair rounds run with thinking off, fast and cheap;
    when the model stops and names a new package (rare), it is asked again with thinking on,
    and a second model is asked too. Only a name both give, which PyPI has, is shown to the
    owner, as above. Measured with the probe's facts in the prompt: thinking changed nothing on
    mrjob (one round either way, 4 to 13 times slower on), but DeepSeek V4 Flash and
    GLM-5.3-Flash named `audioop-lts` with thinking on and a wrong name or none with it off;
    DeepSeek V4 Pro, thinking on, still said `pyaudioop`. One run each, so the second model
    is the check, not thinking alone. On a course that runs on the host,
  the GUI and the command line both say plainly that a package was recommended by a model
  before it is installed (owner): in a container it reaches only the image, on the host the
  owner's machine. System software (a JDK, Hadoop, a compiler) is never installed: the model
  only turns a failure into the steps for this platform.
- Each language's profile brings three things for this: its step in the image recipe, its
  probes run in the image after a build (`container._check_inside`; C: compile a small program
  with the course's flags and run it under the sanitizers, added when C is next changed), and
  its part of the rules, which may be empty.
- The container keeps the problem small: an image is one Debian release with a few toolchain
  versions, which CI can test; the host's own versions matter only to a course run on the
  host.

## CI

GitHub Actions, `.github/workflows/ci.yml` (decided 2026-10-26, before going public):
- Node 24 action runtime (owner approved, 2026-11-08): checkout v5, setup-python v6 and
  setup-java v5, the first Node 24 majors in their official release notes
  ([checkout](https://github.com/actions/checkout/releases/tag/v5.0.0),
  [setup-python](https://github.com/actions/setup-python/releases/tag/v6.0.0),
  [setup-java](https://github.com/actions/setup-java/releases/tag/v5.0.0)). All require
  runner >= 2.327.1; workflows use GitHub-hosted latest images. Job names and matrices stay
  unchanged, particularly the required `test (3.11)`. Local tests cannot establish whether
  a GitHub job runs; that is checked on the owner's next push/PR run.
- Runs when work reaches `main` (a push after merging `dev`, or a pull request into `main`)
  and by hand (`workflow_dispatch`), not on every `dev` push: each step is already checked
  locally with `pytest` and `ruff check .`, and the badge then shows the state of `main`,
  the branch visitors see.
- `test`: Python 3.11, 3.12, 3.13 and 3.14, `ruff check .` and the fast `pytest`. The first
  run also had 3.12, which failed on mrjob's `distutils` import (see "Environment"), and the
  owner dropped it (2026-10-26). Brought back with 3.13 and 3.14 (owner, 2026-11-08): the
  `bigdata` extra now brings setuptools (`distutils`) and, from 3.13, `standard-pipes`, and
  `requires-python` lost its `<3.13`. Measured locally first, fast suite and `-m spark`
  (mrjob, PySpark 3.5.9 and Hadoop) on each. 3.15 waits until the dependencies have wheels
  for it. Only `test` runs every version; `other-os`, `docker` and `spark` stay on 3.11.
  `test` and `other-os` install the `gen` extra too, and run `pytest -rs`: without langgraph
  the graph's tests skipped there, 28 skips a job on the first 3.11-3.14 run (2026-11-08),
  among them all of `tests/test_graph_cli.py`.
  No JDK: run in a clean clone with a fake `java` that logs every call, the fast suite
  started no JVM (2026-10-26).
- `other-os` (owner, 2026-11-08, "Roadmap" 6.1): the same fast suite on `macos-latest` and
  `windows-latest`, Python 3.11. A job of its own and not a second matrix axis of `test`, so the
  `test (3.11)` check main's ruleset requires keeps its name (a renamed check never reports and
  blocks every pull request). The macOS runner has clang as `cc`, so the C hint tests run there
  as on the owner's Mac; Windows has no `cc`, and they skip.
- `docker` (owner, 2026-11-08): `pytest -m docker` on `ubuntu-latest`, whose runner has Docker
  with Linux containers: the isolation tests, the examples (C included) validated in the
  course's image, and a generated C item end to end (hence the `gen` extra). It also runs on
  native Linux, where the image's `runner` uid must match the host's (1001 on the runner), which
  had not been tested. These tests skip when Docker cannot run, which would pass quietly in CI,
  so a step reads the JUnit report and fails the job on any skip or on no tests. Locally the 19
  tests take about 40 s with the images built; CI builds them first. Docker is not in CI on
  macOS (the runner has none) or Windows (its runner runs only Windows containers): Docker
  Desktop there is checked by hand, on the owner's machines (see "Roadmap" 6, packaging).
  The job installs the `gui` extra as well: the GUI's test files skip at import without
  Flask, and the check counts every skip. Before the tests it prints `vm.mmap_rnd_bits` and
  sets 28 (see "C", the known issue with AddressSanitizer).
- First run (2026-11-08): macOS green (1267 passed). Windows: 25 failures, 22 of them C with no
  sanitizer libraries (now "C needs WSL", see "C"), a GUI task whose runner died shown as
  running for good, since liveness was not checked there (now `winjob.process_alive`), and two
  tests that expected "/" in paths. Docker: the skips above, a test that took the image's own
  /home/runner for the host's home (GitHub's user is `runner` too), and the C timeouts. After
  the fixes, the second run had all five jobs green.
- Why more systems pay (2026-11-07): on the second-language pull request, gcc on the Ubuntu
  runner, in a UTF-8 locale, quotes with ‘ ’ where clang and the container's gcc (C locale) print ' ',
  and a C hint test failed. CI is where a toolchain or locale the owner's machines do not have
  shows up first.
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
   **Done 2026-10-28** (the first batch's five must exercises solved and rated); the
   `v0.1.0` release below waits for the GUI or the whole backend (owner, 2026-10-31).
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
   - Then release `v0.1.0` (decided 2026-10-26, from a Codex review before going public;
     moved by the owner on 2026-10-31: not at the end of phase 2 or 3, but once the GUI or
     the whole backend is done, with the same contents):
     a `constraints.txt` with the dependency versions CI passed on, a short CHANGELOG, and
     a git tag. Not earlier: before phase 2 is solved, the whole loop has not run once.
     README for the release: one positioning line (private lectures -> runnable exercises
     -> pre-release checks on reference / skeleton / mutants / oracle -> distribution-
     semantics checks -> local practice feedback; AI exercises, IDE practice, autograding
     and no-answer hints alone all have precedents) and a built / planned table, so
     generation, RAG and hints are never read as existing features (Codex review 4).
2b. **Moved to the backlog 2026-11-02 (owner):** the primary goal is the portfolio, and
   phases 4 and 5 are what it still lacks. Tracing and design practice happen in chat for now,
   running every snippet to confirm the answer, which worked well on week 7.
   Code tracing and design exercises (raised from "deferred" on 2026-10-24; scope revised
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
3a. **Done 2026-10-30** (weeks 4, 5 and 7 generated; the week 7 comparison set the roles).
   No solving data needed; runs alongside the owner's solving. First review the [3] items
   of "LLM engineering candidates". In order: isolation layer A (static check); the
   materials reader with the md cache and vision transcription ("Reading files"), with a
   minimal `llm.py`; the LangGraph generate-validate-repair loop with the [3] items adopted
   (model access, structured output, call log, caching, plan approval, preference chat),
   code exercises first; the oracle written in a separate context (see "Independent
   checks"). Isolation layer B moved to the generalization phase (2026-10-28); in the end it
   was built after phase 5, before the GUI (2026-11-05).
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
   Design approved 2026-10-31 (owner), in order, each its own step:
   1. **Done 2026-10-31.** Held-out check with the owner's code, no LLM (see "Independent
      checks").
   2. **Done 2026-10-31.** Model-written held-out candidates, the oracle deciding
      disagreements ("Independent checks", step 2).
   3. **Built 2026-10-31** (as-built below the plan). Teaching-quality judge, in `tutor/judge/`, outside `validation/` so that package
      stays free of LLMs. Input: `question.md` (what the owner reads), skeleton and
      reference (needed for "targets the concept": does the solution need the knowledge
      point?), the item's slides (as generation's `retrieve_context`) and the lectures
      taught so far. Output: y/n per point with one sentence quoting the question, in
      `solutions/<id>/quality.json`; never touches validity. Its model is from another
      family than the generator's (self-preference bias). `--calibrate` compares it with
      `ratings.jsonl` and the blind ratings point by point; a point is trusted when the
      judge finds at least 80% of the owner's n labels and marks at most 20% of y labels
      n, else it is shown as uncalibrated. Items where judge and owner disagree are listed
      for the owner, whose label can be wrong too. The three week 5 ratings noted as format
      problems are left out of "clear".
      As built (2026-10-31): `python -m tutor.judge <batch>` writes `quality.json`;
      `--calibrate [--blind DIR]` judges the 29 rated items (a blind item with the text
      the owner rated, the arm's batch for skeleton and reference via `arms.json`), and a
      rating line's `skip_points` keeps a point out (the three week 5 "clear"). One call per
      exercise; each point's `evidence` comes before its y/n. The lecture context is shared
      with the held-out writer (`materials/slides.py`).
      - glm-full said yyyy to all 29, both before and after "clear" was redefined (see
        "Validity vs teaching quality"): it found none of the owner's five n. gpt-terra
        found 2 of 5 and marked 10 of 21 owner y as n: not trusted by the agreement bar.
      - But most of those "false n" were real defects the owner's y had missed:
        count-min-sketch claimed a never-added item gives 0 (collisions make it
        positive), and boyer-moore-majority left out the slides' starting state (first
        element, count 1), which is what led to the owner's own bug; both fixed. So the judge is a
        reminder of what to look at, not a rating, and is measured a second way: the
        owner reviews each flag (judge n where the owner said y or did not rate),
        answers in `flag-reviews.jsonl`, and the report counts the right ones. First
        review: 15 of 19 right (clear 10/13, fits 4/5, targets 1/1); the wrong ones a
        debugging task's intended gap, a layout problem question.md had already solved,
        and two pedantic points.
      - The `judge` role is gpt-terra (calibration $0.86; all 38 exercises $1.07). On the
        five batches it put an n on 34 of 38, 29 flags: some real (counting-bloom-filter's
        constraints 1 and 2 contradict each other once a never-inserted item collides;
        lossy-counting keeps the eps*n bound though floor(1/eps) breaks it; dgim-query-
        estimate's bucket span implies every position is a 1), some wrong (a Boyer-Moore
        counterexample without a majority), some pedantic. Too many to read each time.
      - Two changes after it (2026-10-31). A flag is now serious or minor and the batch
        report shows the serious ones (`--all` for every one); it did not filter much:
        rerun on all 38, 23 of 25 flags came back serious. The judge also sees the
        "Useful functions" part of `hints.md` (never the hint levels): about ten flags had
        been "the question does not teach the API syntax", which this design puts in the
        hints on purpose; on those seven exercises the rerun gave yyyy. The owner counts
        that part as read with the question (see "clear" above).
      - Fixed from the reruns (question.md only, noted in each manifest):
        counting-bloom-filter's constraint 1 now holds while deletes match inserts;
        lossy-counting's eps*n bound needs a whole 1/eps; dgim-query-estimate calls its
        bucket span a simplification; df-wordcount-explode-split's example said bye,1
        where Bye occurs twice (the tests say bye,2); reverse-graph-secondary-sort's
        example lost its tabs; misra-gries says "more than n/k".
      - Cost: the dollar figures above use the notes' prices ($2 in, $12 out per million
        tokens); the OpenAI balance fell by about half of the estimate, so gpt-terra
        costs roughly half that.
   4. **Done 2026-10-31.** Synthetic defects for calibration: on 2026-10-31 the 29 labels held n only for
      "clear" (5 usable), never for fits, taught or targets, so those three cannot be
      calibrated from real data yet. A few yyyy questions get variants with one defect
      each (an unstated output order; a convention other than the slides'; an API from a
      later week; a task solvable without the knowledge point), labelled by construction
      and spot-checked by the owner; reported apart from real labels, since planted
      defects are easier to spot. Kept in the private workspace (`experiments/`).
      As built: 18 variants of five yyyy questions (two week 2 MRJob, one week 5 Spark
      SQL, two week 7), one defect each, written by hand; a "targets" variant brings a
      reference that takes the workaround. No "taught" variant for week 7, the last
      lecture. `--defects DIR` adds them; only the planted point is labelled, and the
      report scores them apart. gpt-terra found 14 of 18: clear 3/5, fits 4/5, taught
      2/3, targets 5/5. Of the misses, two were weak plants (a worked example still showed
      the removed rule; the example output showed the letters a vague "characters" left
      open) and two were real misses (a lab definition credited to the lecture; a window
      function from a later lecture not seen as untaught). On the real ratings, with the
      hints' Useful functions shown, it found 1 of the owner's 5 "clear" n. So the judge
      is a usable reminder for fits, taught and targets, where real n labels are still
      missing; for "clear" the owner's own rating stays the measure. The owner
      spot-checks the variants (README.md in the folder).
3c. **Done 2026-10-29.**
   Hint command, `python -m tutor.hint <exercise>` (decided 2026-10-24, after 3a since it
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
   SparkContext left running, tuple output, nothing saved to outputPath). Full-width
   punctuation is named only where Python stops at it, not in comments or strings, where it
   is valid and would hide the real mistake (outside review, 2026-10-29). A run's
   signature is which tests fail with which `E` lines (addresses and timings normalised);
   each mutant's signature is computed once and cached in `solutions/<id>/.hints/`, keyed
   by a hash of everything the run copies in (tests, data, helpers, the mutant) and the
   run's timeout and environment (tests and mutant only until an outside review,
   2026-10-29: changed data kept the old signatures). An exact match quotes the mutant's bug ("<the
   bug>, <what goes wrong>"); the same failing tests with other values is offered as a
   guess, the mutant whose assertion lines read most alike first (difflib ratio), since a
   mistake whose output order varies never matches exactly, even against itself. Several
   exact matches are all named ("it is one of: ..."). In a review batch only the "what goes
   wrong" half is shown. Mutants the tests
   cannot tell apart are reported (an exercise lacking a test). Each request is a line in
   `attempts.jsonl` (event "hint", layer, rule or mutant). First request on a Spark
   exercise about a minute (every mutant runs once), later ones the student's run only.
   Layer 3 built 2026-10-29 (`tutor/hint/llm_hint.py`, owner-approved design): only when
   layers 1 and 2 miss, with the workspace's `hint` role (none: the old fallback;
   `--no-llm` skips it). The model gets student.py (question and code), hints.md, the
   constraints and the first error without Java frames, never the reference, mutants or
   oracle. It replies JSON, `diagnosis` first (read by code only, never shown, since it
   may name the fix) and `hint`. Levels count model hints already logged for the same
   student.py hash: 1 concept and direction, 2 where, 3 one corrected line for a
   low-level mistake (learn only; a review batch names the kind, then the place, never
   code). Leak guard, in two parts (owner: state it in the prompt to make leaks rare, and
   check deterministically): the prompt lists the rules and says a checker rejects a hint
   that breaks one; the checker rejects a code block (up to two lines at learn level 3),
   any reference line of 15+ characters (spaces removed) the student has not seen
   (student.py, tests, hints.md, skeleton), and code spans with difflib ratio >= 0.8
   against consecutive reference lines. A rejection is sent back once with its reason;
   a second one falls back to the first error (`llm-blocked` in attempts.jsonl). Shorter
   reference lines are not guarded: `sc.stop()`-sized lines would reject honest hints.
   Evaluation 2026-10-29 (the 32 week-5 mutants as students, docstring and comments
   stripped, layer 2 off, DeepSeek Flash with thinking): 32 hints, none blocked, about
   $0.001 and 4 s each; about 31 of 32 pointed at the real mistake. Most level 1 hints were
   more specific than level 1 asks (they named the function, the operator or the fix, in
   words). Kept as it is (owner, 2026-10-29): a stuck learner gains more from a precise
   hint, and the guard already keeps code out; not a gap to report.
   Evaluation 2026-10-29, each of the 32 mutants of the six week-5 Spark exercises as a
   simulated student: first 29/32 named; the misses were one pair the tests cannot tell
   apart and two no-coalesce mutants whose output order varies. With the two rules above
   (name every exact match, rank guesses by similarity): 30/32 named, the other 2 named
   among the indistinguishable pair, none wrong. Real students' code will differ more from
   a mutant than a mutant does from itself, so this is an upper bound for layer 2.
4. Retrieval, compared by measurement (see "RAG"; plan approved 2026-11-02), one step each:
   1. **Done 2026-11-02.** Page corpus, labels from `coverage.md`, scores and BM25, no LLM.
   2. **Done 2026-11-02.** Vectors, and their fusion with BM25 into hybrid retrieval.
   3. **Done 2026-11-02.** Concept index (tried on one lecture first, as agreed).
   4. **Done 2026-11-02.** Agentic search. It uses the LangGraph library and leaves `graph/` as
      it is.
      **Done 2026-11-02, after a Codex review:** a folder per complete configuration, so a changed
      setting cannot replay old answers; `--run` for true repeats; traces and first-run costs;
      query-by-query comparison; frozen queries; and on dev, thinking, hybrid search for the agent
      and a neighbour-page control.
   5. **Done 2026-11-03, simplified (owner):** the final retrieval table from the test split, run
      once and not pooled, then the cross-lecture split. Hybrid and the agent fall well short on
      it, the condition for trying LightRAG, which is still not built as nothing here needs it.
   6. **Done 2026-11-03, smaller (owner):** the whole lecture, the labelled slice and the three
      real routes on the same 12 items. None beats the whole lecture.
   7. **Done 2026-11-03.** The whole lecture stays; README results.
5. **Done 2026-11-05, light version (owner):** `tutor.eval` reports from the existing records, with
   no model calls, and the README's results; the [5] items of "LLM engineering candidates" are
   reviewed there. The full model comparison was not run.
6. Next, agreed 2026-11-07 (owner, after an outside review of this file asked for easier
   setup, one execution environment for validation and the learner, and measured trust). In
   order, each its own step:
   1. CI: a `docker` job (`pytest -m docker`; GitHub's Ubuntu runners have Docker) and the
      fast suite on macOS and Windows as well. Done when all three are green:
      **done 2026-11-08** (see "CI").
   2. A little refactoring of the files over 500 lines (`gui/app.py`, `graph/nodes.py`,
      `graph/prompts.py`, ...), planned and explained first; the prompt pins must still pass.
      **Done 2026-11-08** (owner's rule since: a file past 300 lines is watched, from 500 a
      split is considered, AGENTS.md). Code moved as it was, by line range, one commit per
      file: `gui/app.py` 1281 -> 155 (routes into `gui/routes/`, one module per page area,
      still registered on the app, so no endpoint name changed); `graph/nodes.py` 1060 -> 421
      (`oracle.py`, `drafts.py`, `batch.py`; the prompt pins still match);
      `gui/environment.py` 663 -> 471 (`languages_page.py`); `gui/generation.py` 618 -> 455
      (`plan_items.py`: each exercise's status and Try again); `gui/lectures.py` 559 -> 432
      (`course_setup.py`). Then the files that later steps would push past 500 (owner): the
      prompts by language, as a third one is coming (`graph/prompt_parts/`, `prompts.py`
      672 -> 396; every Python and C prompt byte for byte as before, 240 outputs compared);
      `execution.py` 440 -> 350 (`processes.py`), with room for `tutor test`; `container.py`
      432 -> 280 (`image_recipe.py`), with room for the build record. `graph/__main__.py`'s
      100-line `_batch` became `_new_batch`, `_resumed_batch`, `_advance` and
      `_owner_decision`, after tests of it were written against the old code
      (`tests/test_graph_cli.py`; it had none). Left whole, each one job:
      `validation/checks.py` (611, the rules in order), `graph/__main__.py` (546, the command
      line), `eval/report.py` (505), `llm.py` (504), `testkit.py` (472: exercises import it by
      name, and saved exercises depend on it).
   3. `tutor test` (built 2026-11-08 after owner approval): the learner's tests in the course's image, as validation runs them, from
      the IDE's terminal: live output, attempts and snapshots written on the host from the
      result, and a 300-second wall-clock time limit (`--timeout SECONDS`, positive and
      finite; an exception to "No timeout on the learner's own `pytest` runs"). It reuses
      `execution.run_pytest`'s container path; the part of
      `attempts.py` that writes a line is shared by both ways. The host's `pytest` stays, for
      debugging in the IDE; `tutor test` is recommended for daily container-course practice.
      V1 requires an explicit container course, accepts an exercise directory, -k and
      verbosity, and never changes the runner or builds an image. With it, a
      container course needs on the host only Docker, the tool and an IDE: the image already
      holds Java, Hadoop and gcc. Done when a C and a Spark exercise give the same outcomes
      both ways and their attempts lines match.
      - Optional, after it: a page for a learner who wants to run tests on the host anyway,
        saying how to install that course's toolchain (Java, Hadoop, a C compiler) on their
        platform, automated where a language allows it safely. Toolchains differ by language,
        so the page grows language by language.
   4. A record of each image build: the pip packages' versions, Java's and gcc's, and the base
      image's digest, shown on the Environment page with the `[requires]` packages whose
      versions differ between the course's `.venv` and the image. Recorded, not pinned: a
      pinned base image misses security updates; pin it once drift is seen.
      Built 2026-11-09: canonical recipe/image names remain unchanged; a FROM-only temporary
      local tag captures the actual base ID, then the build records provenance labels.
      Post-check inventory uses the immutable image ID in a bounded, read-only/network-off
      container, no course mount. Private versioned snapshots are kept per image content,
      with a current recipe pointer and drift/staleness checks. Environment step 4 shows
      build time and actual course-venv/image differences inline, with metadata-only refresh
      and a link to full history; legacy base provenance stays unknown. A build snapshot is
      not a permanent base pin. Browser acceptance is pending.
   Then packaging and release (see "Deferred", "Packaging and release"), with `tutor doctor`
   (checks the tool, that Docker runs, the course's image, then validates one example in the
   course's runner as a smoke test, and says what to do next for each failure) and a fresh
   install timed on macOS, Windows and Linux; Docker on Windows is first tested there.
   - `tutor doctor` step 1 (deterministic checks, no model, no installs): done 2026-11-09.
     Tool checks always run; a nearest-ancestor workspace adds course checks. No .env load,
     course writes or repair actions. Optional --smoke copies a shipped example into a
     disposable course and calls load_exercise/validate/format_report directly, without
     changing validation/ or writing a report. C validation needs a container independently
     of host student-test support. Each check fails independently with an actionable row.
     Per-language parts mirror graph/prompt_parts: doctor_languages.LanguageDoctor holds
     the smoke example, machine_checks, platform_problem and validation_needs_container;
     DOCTORS matches tutor.languages.LANGUAGES. A new language adds a module, its registry
     entry, a shipped example and machine/platform tests; doctor.py does not change.
     Framework dependencies stay in envcheck. The structured advice gate, preview/send,
     disposable verification and explicit application/undo are implemented (2026-11-09;
     "Version conflicts"). Environment integrates the check, host guide and four-screen
     repair wizard; the clicks made are listed in "Version conflicts".
   An optional doctor profile handles failures no rule covers; proposals use the existing
   permission gate and temporary verification. Numeric comparisons are stored in the
   separate `[package-bounds]` table and shown in Environment's one version table. uv can
   download an additional Python with explicit consent when recovery finds none compatible,
   into the user's directory without changing PATH or a course venv. Selecting it for a
   course remains a separate action; default `tutor doctor` installs nothing.
   Later, each when its trigger comes: a JDK instead of a JRE in the image (a course that
   compiles Java in it); a separate execution backend (a third runner such as Podman; until
   then the host or container switch in `execution.py` is enough); VS Code Dev Containers on
   the course's image (debugging inside it); for C, a fixed regression set of plan items rerun
   when its prompts change, and held-out solutions written by a model (after a second C batch
   and the owner's own C solving).

## Backlog (decided, not scheduled)

Order agreed 2026-10-30, around 3b: C1 first (small, and before the owner solves the
week 3 MapReduce batch, so an exercise relying on value order is fixed before it is
solved), then 3b, then the hint-quality judge of [3c] (it reuses 3b's judge and needs
real hint requests from solving week 7), then 2b together with C2 (both ask how good a
design is, and share the Hadoop counters). Since 2026-11-02 2b and C2 wait until after phases
4 and 5, to be weighed again then: as designed, C2's warnings read the reference, not the
learner's code, so reaching the learner would go through `tutor.hint`; and the Hadoop counters
cannot run on native Windows.

- Batch API for whole-batch generation, once a cloud provider is used (see the [3] review in
  "LLM engineering candidates").
- A local model as one more arm, if a full model comparison is ever run (see the [5] items in
  "LLM engineering candidates").

- C1 value-order check: done 2026-10-30, see rule 5 in "Exercise validation".
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
  (Exception: an exercise that teaches something wrong, as the stripes test did, is fixed.
  Exception, owner 2026-10-29: rule `main` was added to the unsolved week 3-5 batches and
  the examples; the solved week 1-2 batch keeps `standalone` unset.)
- No LLM judges whether an exercise is valid; only the deterministic validator does (see
  "Exercise validation"). LLM feedback on the learner's answers (hints, rubric feedback) is
  a different thing and allowed.
- No Hadoop Streaming interface: the course covers streaming for understanding only, and mrjob teaches the same concepts; streaming appears only as plain-Python
  exercises (see "Interface extensions").
- No timeout on the learner's own `pytest` runs: the isolated runner protects validation of
  generated code; in the terminal Ctrl+C stops a loop (see "Code execution safety").
  Exception approved 2026-11-08: `tutor test` uses the course image with a 300-second outer
  limit, configurable per call with --timeout. Ordinary host pytest remains unchanged.
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
  settled in phase 4, long-context baseline, models), which cover most of the ladder; no
  separate study of manual edit time per arm. Instead, user effort shows up as preference / revision
  requests in the generation chat (see the [3] preference-chat candidate), counted per
  batch.
- No license change and no closed generator (decided 2026-10-26, misuse discussion). The
  license stays MIT: commercial reuse is legal anyway, and AGPL would put off some readers
  of a portfolio. The phase 3 generator stays public too: hiding it would remove half of
  the project's point, and an LLM chat can do the misuse it would prevent. Misuse is
  handled by describing the intended use positively (README "Intended use", no
  prohibitions), by MIT's as-is clause, and by protecting users (phase 3 note on malicious
  material).
- A short README (owner, 2026-11-07: it had grown to 415 lines). The README says what the
  repository is, what it does, how to start and its intended use, in about 100 lines; how
  validation and generation work, the results, the RAG comparison, where exercise code
  runs, the layout and the roadmap moved word for word to `docs/details.md`, which it links,
  with design.md behind both.
- No CONTRIBUTING.md or SECURITY.md (decided 2026-10-26, before going public; a Codex review
  suggested both without knowing the repo's intent). This is a personal portfolio and
  revision tool, not a project seeking contributors: setup and checks are in README and
  `docs/setup/`, the synthetic-data rule in AGENTS.md. Vulnerability reports go through
  GitHub's private vulnerability reporting (repo settings, once public), which keeps them
  out of public issues; nobody should post keys or course material in an issue anyway.

- No deterministic check that a debugging skeleton differs from its reference only at the
  bug (considered 2026-10-29 after `df-udf-closure-debug`, whose fix needed three steps the
  skeleton never had). Measured on the three debugging exercises, the changed code lines
  (comments and docstrings ignored) do not separate it: it changes 2 lines and adds 5,
  since its reference chains the steps in one expression, while the good
  `mean-combiner-debug` (-4 +7) and `closure-counter-debug` (-8 +5) change as much or
  more. The rule is in the generation prompt instead, and the teaching-quality judge (3b)
  can rate it.

- No exam mode (owner, 2026-10-30; it was "reconsider later" since 2026-10-24). Review
  mode already gives exam-style practice: fuller tasks, knowledge points combined, help
  only when asked. A real exam mode would need a locked-down exam platform with a built-in
  code editor, a different product from a tutor that leaves solving to the IDE. Practice
  batches stay free of exam pressure (a write-everything-before-the-first-test-run rule
  was turned down, 2026-10-24).
- No RAG in exercise generation (owner, 2026-11-03): generation sends the whole lecture. Same 12
  items, a rule fixed first: the whole lecture had 8 VALID at the first try, 7 exercises the judge
  finds fully fine and 5 serious flags; perfect retrieval 7, 5 and 10; hybrid + neighbours 4, 3 and
  12; concept 8, 3 and 10; the agent 4, 5 and 11. Tokens were 86% to 124% of the whole lecture's: a
  lecture is about 8k of an exercise's 64k, so there is little to save, and one extra repair costs
  more than it does. A plan already names each exercise's pages, so there is little to find too.
  This holds for this course and its size, on a small sample; a lecture too large for the prompt or
  a course with no `coverage.md` reopens it. All the numbers and reasons, and where retrieval could
  still earn its place, are in "RAG".
- No opt-in flag before lecture text goes to a model, and no "untrusted data" wrapper around the
  owner's own inputs to a prompt (owner, 2026-11-06, after GitHub's AI scanning flagged the
  concept index and the plan's `coverage.md`). Sending the lecture to a model is what the tool
  is for (generation, slide transcription, the judge), and the README says what leaves the
  machine; a role in `[llm.roles]` is the opt-in, and it chooses the provider, local or cloud.
  `coverage.md` and the preferences are written by the owner, whom the pipeline only proposes
  changes to, and every plan is approved before anything is generated; whoever can edit them
  can edit `workspace.toml` or the code. Markers in a prompt do not stop injection anyway, and
  the suggested cut to 8,000 characters would drop rows of a real `coverage.md` (about 14,000).
  The same holds for an exercise's sample data that the held-out writer sends: the generator
  wrote it and saw it, and a list of allowed file names, also suggested, kept none of the 17 real
  files (all under `sample_input/`). The untrusted input is lecture material, answered by layer A
  and the test-run container (see "Code execution safety").
  Lecture text itself is marked, since 2026-11-07 (owner, after the same scanning flagged
  `materials/coverage.py` on the GUI's pull request): the coverage and `[requires]` drafts
  send it between `<lecture>` and `</lecture>` (a closing tag inside it broken up), and their
  instructions end by saying it is material to read, not instructions. Only those two
  prompts, which are not calibrated; the generate, plan and vision prompts are, and stay as
  they are until a change can be measured. It marks the boundary for the model and enforces
  nothing: layer A, the container and the owner's review still answer what a lecture can
  make a model write. Coverage's prompt is version 2 (asked again on the next click);
  `[requires]` folded it into its version 2, not used yet.
  New prompts mark what they send from outside (owner, 2026-11-07: keep GitHub's
  prompt-injection scanning as quiet as can be, and record it here). Every prompt written from
  now on, beginning with C's (`graph/prompts.py`, `material()`), sends text it did not write
  itself between tags, a closing tag inside broken up, and its system prompt names the tags as
  material to read, not instructions: the lecture pages (`<lecture>`); in a repair, the
  previous answer and the validator's report, which quotes what generated code printed
  (`<previous_answer>`, `<validator_report>`); for the oracle, the question, the interface and
  the samples another model wrote (`<question>`, `<interface>`, `<samples>`); for the plan,
  the coverage table, the page list, the existing exercises and the previous plan. The owner's
  own preferences and feedback are instructions and stay unmarked. Python's generate, plan and
  oracle prompts stay unmarked as calibrated (pinned, see "C", step 3), as this entry's
  decision says; markers there wait for a measured change. The marks guide a model; layer A,
  the container and the owner's review are still what bound the result.
- No built-in "standard set" of models for a course with none (proposed 2026-11-06). The
  first course adds its models with "Add a model"; later ones copy them from another course.
  A set in the code would need upkeep as providers rename their models, and the owner prefers
  the user to choose. Decided 2026-11-06. Not undone by the models.dev catalog (owner,
  2026-11-07, under the Models page): nothing comes with the code, the list is downloaded
  on the owner's click, and it only fills the New model form; the course gets nothing until
  the owner saves.
- No courses whose work is built and run in a device emulator or an IDE of its own (Android
  Studio and the like; owner, 2026-11-07). Their builds, emulators and UI tests are outside
  what a test run in a container can check, so they are outside this project.

## Deferred

SQLite, CLI, Docker (beyond the planned isolation container, see "Code execution safety"), error taxonomy (derive categories from real attempt data later).
Adaptive learning is listed under the later phases below; exam mode is not done.

Translated `question.md` (candidate, owner 2026-10-30; not built). Why not now: an
assistant in the IDE already explains or translates a question on request, so nothing
needs building yet. Revisit together with how the pipeline produces the `question.md`
format (see "Learning rules").
- Shape: `question.<lang>.md` next to `question.md`. The English file stays the only
  source. A translation call runs after validation; the tests never read the question
  text, so a translation cannot change whether an exercise is valid. Languages are listed
  in `workspace.toml`, English only by default; the repository's examples stay English.
- Deterministic check: inline code, LaTeX, code blocks, commands, slide numbers and links
  must be identical in both files, which catches a changed expression or a dropped rule.
- Points to settle: translated headings change the contents links' anchors (use fixed
  `<a id>` anchors or keep headings in English); technical terms keep the English word on
  first use (exams use it); whether to translate only Concept and keep Task, Input,
  Output, Example and Constraints in English (the rules read as an exam states them, with
  no mistranslated rule); whether `hints.md` and `tutor.hint` follow.

The GUI in other languages (owner, 2026-11-07; not built). Why not now: the GUI's text still
changes with every step (23 templates, the notices, and messages written by the Python
modules: several hundred strings), so a translation now would double each change and its
tests; and the exercises themselves are in the lectures' language, so a translated GUI
around them helps only half. Revisit at packaging and release, when the text has settled, and
together with translated `question.md` above.
- Shape: gettext (Flask-Babel), English as the only source; the language from a setting or the
  browser; untranslated strings fall back to English.

Later phases, after phase 5 (order decided 2026-10-26, numbered 2026-10-30): phase 6 the
second course, phase 7 the local GUI, then packaging and release. Changed by the owner on
2026-11-05 and 2026-11-06: the test-run container first, then the GUI, then the rest of phase 6
(another language or framework), since the second course so far is plain Python. Adaptive
review is a maybe, not a phase (owner, 2026-10-30: batches are started by hand for now); exam
mode is not done (see "Decisions not to do").
- Second course: use the tool for another course (other language or framework) to prove workspace,
  testkit and setup recipes are not shaped around one course. A long-term goal. Brings the module
  risk tiers and other languages (see "Code execution safety"); isolation layer B came first, after
  phase 5 (2026-11-05). A new framework's weight for the executor's caps (heavy: a JVM or similar
  per run) moves from the code's table to the course's `workspace.toml`, decided when the framework
  is set up, by measuring a smoke run (start-up time, peak memory, a `java` child, cores used), and
  confirmed by the owner like a `[requires]` entry (owner, 2026-10-30). A model may propose which
  frameworks the slides use, never how heavy they are; an unmeasured framework counts as heavy.
- Adaptive review (a maybe): use `attempts.jsonl` (failures, run counts, hints used) and `coverage.md`
  to find weak knowledge points and schedule `review`-mode batches with spaced repetition.
- Packaging and release: an install line, one click to start, CI on macOS and Windows
  (WSL) as well, versioned releases after `v0.1.0`.
  Portable-wheel preparation built 2026-11-09: controlled setuptools hooks carry public
  source/build metadata, GUI assets and synthetic examples, excluding private roots,
  caches and links. Installed workspace/repair installers extract the controlled source
  temporarily and install noneditable; source checkouts retain editable installs. No
  unrelated PyPI tutor-name fallback. Archive paths/size are validated, examples work
  outside a checkout, and offline wheel inspection precedes actual fresh-install checks.
  Local launch built 2026-11-09: `tutor gui` shares the module launcher. Installed wheels
  default to per-user courses/config folders (TUTOR_HOME can override both); checkout
  WORKSPACE_DIR behavior stays. Launch state is private and root-specific. A second
  launch verifies a fresh HMAC challenge over loopback before reusing a server; invalid
  state has an explicit reset, and no server is killed. `tutor shortcut` creates a new
  .command/.lnk/.desktop without overwriting or changing startup items. Native desktop
  trust/click and browser acceptance remain separate from platform-format unit tests.
  Optional Python management built 2026-11-09: existing uv-managed stable CPythons join
  interpreter discovery. The separate optional page/`tutor python` command lists uv's
  offline catalog; only a confirmed action downloads a freshly offered interpreter,
  checking known conflicts and measured removed imports. No system installer, PATH alias,
  course switch, runner fallback or default-doctor install. Native install acceptance
  uses a new scratch root and an existing Python, with implicit Python downloads disabled;
  the packaging CI matrix covers installed wheels/fresh synthetic courses on three OSes.
  - One click (the owner's aim, 2026-11-05; installation acceptance still pending): one install
    line gets `uv`, which fetches the Python it needs and installs the tool (`uv tool install`,
    the same job as `pipx`, without needing Python first). `tutor gui` starts the server and
    opens the browser with that launch's token. `tutor shortcut` writes a desktop shortcut on
    this machine (`.command` or a small `.app`, `.lnk`, `.desktop`); made here and not
    downloaded, it should not meet macOS Gatekeeper or need signing, to be tested. A second
    click finds the running server from a small file with its port and token and only opens
    the browser. Not a frozen app (PyInstaller): a build per platform, paid signing on macOS,
    and each course's venv needs a real Python anyway.
  - What a user still installs: that one line, for all; an IDE to solve in; an API key or a
    local model to generate; Java and Hadoop for such courses, since the learner's runs stay
    on the host (the page gives the commands); Docker Desktop to choose the container. A plain
    Python course needs only the line and a key. Once `tutor test` is built ("Roadmap", 6), a
    container course needs that toolchain only to run tests on the host, e.g. to debug.

Local GUI (phase 7, started 2026-11-06 ahead of the rest of phase 6, see above; discussed
2026-10-26). It sits on top of the files and functions the earlier phases build, so it comes
late; the adaptive review screens can be built with it.
- Build order (owner, 2026-11-06), one step at a time, each usable on its own: (1) read-only
  pages: courses, batches, exercises with progress and validation; (2) "Open in IDE" and "Open
  folder"; (3) long tasks: a CLI command as a child process, its log polled (htmx comes here);
  (4) generation: lectures, preferences, approving the plan in the page (it was expected to
  touch `graph/__main__.py`; it did not, see below); (5) uploading lectures and a new
  course; (6) settings: models and keys; (7) the environment page, split (owner, 2026-11-06)
  into 7a, without a model, and 7b, a model drafting `[requires]`. Steps 1 to 7 are built.
- Courses (owner, 2026-11-06): every folder under one root that holds a `workspace.toml`, as
  `tutor.workspace init` lays them out; the root is `--root`, else the folder above
  `WORKSPACE_DIR`. Each is checked like any workspace (refused inside a git repository). A
  command the GUI starts gets that course's `WORKSPACE_DIR` and `LECTURE_DIR`.
- Access (step 1): the server listens on 127.0.0.1 only. A random token per launch is in the
  link the command prints and opens; the first request swaps it for an HttpOnly,
  SameSite=Strict cookie and redirects to the page without it, and every request without it
  gets 403. The Host header must be 127.0.0.1 or localhost with this port, against DNS
  rebinding. Jinja2 escapes every value and the Content-Security-Policy allows scripts from
  the server only, none inline. Names from a URL or a manifest are followed only as plain,
  visible folder names that stay inside their parent once links are resolved. Step 1 has GET
  routes only. From step 2, a route that does something on the machine answers POST only (no
  link or image can trigger it), and its Origin header must be this server's, besides the
  SameSite cookie; it redirects back (303) with a fixed notice code, so the page never
  echoes text from the request. The Referrer-Policy is `same-origin`: under `no-referrer`
  browsers send `Origin: null` on a POST, and the first real click got 403 (the Flask test
  client sets headers by hand, so only a click in Chrome showed it; buttons are now checked
  that way). The GUI reads only `WORKSPACE_DIR` from `.env`, not the
  whole file: its environment passes to what it starts (VS Code, its terminals, the code
  run there), and API keys must not.
- Step 1's pages (owner, 2026-11-06, after the first version looked like a file browser): a
  dashboard per course (progress bar, must exercises passed, the next must exercise not yet
  passed in the newest batch that has one, the last runs with time and result, never a run's
  `first_error`), batches with progress bars, and the question rendered.
- Questions are rendered, not shown as source (owner, 2026-11-06): Markdown by markdown-it-py
  with raw HTML off; `<details>` blocks (17 of 60 questions) by recognising their exact shape
  and writing only fixed tags; LaTeX (31 of 60) as MathML made on the server, so no script and
  no change to the CSP (KaTeX would need inline styles). latex2mathml copies text it does not
  understand into its output (`\text{<script>}` came out as a script tag), so its output is
  kept only if every element and attribute is plain presentation MathML, else the formula is
  shown as escaped source: over the 60 questions, 1131 formulas, all MathML, 9 ms at most.
- Long tasks (step 3, owner, 2026-11-06): a task is a list of existing CLI commands, from a
  table in the code with checked paths, run one after another by `python -m tutor.gui.runner`
  in a session of its own; each runs to the end even if one before failed (a batch goes on past
  an INVALID exercise). Its folder in the course's `logs/gui-tasks/` holds `task.json` (the
  commands, exit codes, state) and `output.log`, so the GUI only reads files and a task goes on
  when the GUI stops; the newest 20 are kept. The commands run in the repo, so each reads `.env`
  itself, with the course's `WORKSPACE_DIR` and `LECTURE_DIR` set. One task per course at a
  time: validation already runs in parallel, and two at once could push runs past their limits.
  The page polls the end of the log every second with htmx and stops when the task ends.
  A task that ends with every command done sends the watching page back to the page it was
  started from, with a notice saying what finished (owner, 2026-11-06: "跑完跳转" for every
  task): lectures read to Lectures, Validate all to its batch, Check to Models, install,
  build and the [requires] draft to Environment, a plan's tasks to its plan, a new course to
  its page. The target is worked out from the task's kind and names through `url_for`, never
  a URL kept in `task.json`. One that failed or was stopped stays on its log, where the
  error is, with a link back; a Validate all with an INVALID exercise is such a one. Only
  the poll redirects, so opening an old task's page shows its log.
  The first task is "Validate all" on a batch, which rewrites each `validation.json` as the
  command line does; generating came in step 4, building the image comes in step 7.
- Docker before a task (owner, 2026-11-06): a task that runs tests or calls a model
  (validation, plan, approve, send back, continue) starts only if the course's tests can run
  now. For a course with `[isolation] runner = "container"` the GUI asks Docker first, as the
  commands do; if `docker` is missing, Docker Desktop is not running, or the image is
  missing, nothing starts and the page says which, with what to do (install Docker Desktop,
  start it, or build the image). A course on the host is not checked. The commands keep
  their own check; this one spares a task that would fail at once.
- Which Python a task runs exercise code with (owner, 2026-11-06, after a course's tests on
  this machine ran in this tool's venv, which lacks the course's packages): validation and
  generation start with the course's own `.venv` when its tests run on this machine, since the
  learner's tests run there and `[requires]` is installed there, and the test runs follow
  `sys.executable`; `tutor.workspace init` installs this tool into it. A course whose tests
  run in a container, or that has no `.venv` yet, uses this tool's interpreter.
- Validating a generated batch (owner, 2026-11-06: "Validate all" did not start by itself):
  Approve and Continue run `python -m tutor.validation.batch <label>` after the graph in the
  same task, which calls the validation command once per exercise the manifest lists, so each
  `validation.json` is written for the files as saved; the graph itself still writes none
  (`validate()` has no side effects). A batch with no manifest yet has nothing to validate,
  and its run's own exit code says how it ended.
- Each exercise's status on the plan page (owner, 2026-11-06: "题目旁边显示状态", after an
  exercise of a real batch failed on a provider error and only Claude could tell, or redo
  it): once a plan is approved, each row says what its exercise came to: valid, invalid or
  saved and not validated yet (manifest and validation.json), discarded with the check its
  last round failed and how many repairs it took (the check's output only when asked, see
  "Try again with a note"), failed with the error, or not generated yet (generation.jsonl's last
  line for it, so a later try counts). One discarded or failed has "Try again": the graph's
  existing `item` command generates it alone, with the plan's item and what the batch was
  planned with (run.json's models and settings), and adds it to the manifest, which only
  ever grows; then it is validated. It costs money, as approving does; only while no task of
  the plan runs, and the page says so while one does (a course runs one task at a time). No
  change to `graph/`. The batch page, which lists only what the manifest has, names the
  plan's exercises that did not come out and links to the plan to try them again (owner).
  With two or more, "Try all N again" runs each one's commands one after another in one
  task.
- Select items on the Plan page (owner approved 2026-11-08): native checkboxes start all
  checked. Approve and generate selected starts only checked rows; an empty selection is
  refused with its ticks preserved, before a task or model call. Full notes and reasons
  remain visible. Unchecked rows say "Left out by you", not failed; Try all includes only
  actual failures. No custom JavaScript or nested forms: checkboxes use HTML's form
  association. The CLI equivalent is `resume --approve --only=alpha,charlie`; omitting
  --only preserves all-items approval, while an explicit empty list is refused.
  - Unlike the earlier status/retry addition, selection changes graph state and dispatch.
    The checkpoint keeps the full approved plan, selected_ids in plan order, the exact
    reviewed file's SHA-256, and generation_complete after collect, even with zero saved.
    plan.json is never filtered. Dispatch and assistant-answer resumes use only selected
    IDs; Continue cannot broaden them. Legacy checkpoints without selected_ids mean all.
    A new plan clears selection metadata; the model-facing schemas and prompts stay intact.
  - Identity must be unambiguous throughout the plan. Schema, unused ID, source and slide
    checks apply only to selected rows; an invalid deferred row need not block a valid one.
    A GUI approval submits IDs and the reviewed digest; both the server and queued CLI
    compare the digest before work. A stale page shows the current full plan for review.
    Selection/approval flags are rejected outside the approval interrupt, before stopped
    work can run or run.json settings can change.
  - The GUI reads the root SQLite checkpoint lazily, with a read-only connection and short
    busy timeout, without loading .env or models. A read failure disables paid controls and
    offers Refresh/repair instructions rather than guessing the authorized subset. Once
    approved, the page and retries use the full checkpoint snapshot, even if plan.json is
    changed externally. Edit plan.json is offered only before approval. Approval before
    the first item log still means Continue; completion without a manifest has no Open
    batch link.
  - After the selected run finishes and the whole course is idle, each deferred row has
    Generate, an explicit new paid item/validation task. It uses the approved notes and
    saved settings/profile overrides, resolved against current course model definitions
    shown on the page. Current schema, source and ID collisions are checked again. A
    blocked row links to the course/existing exercises and New batch as its fix path.
    A later saved/error/discarded result replaces left-out status and follows the ordinary
    retry workflow. No bulk Generate all left out action in this version.
- "Try again with a note" (owner, 2026-11-07, after a real retry failed rule 1 the same way
  twice: the reference and the tests read an ambiguous question differently, and nothing
  about the retry had changed). Under an exercise that did not come out, a folded section
  shows the last round's check output (its first 1500 characters, from generation.jsonl)
  and takes a note. The note is added to that exercise's `notes` in the item file the retry
  writes, after the plan's own notes, for this try only: `plan.json` stays as approved. The
  notes are the generate prompt's last line, so the cached part before them (the lecture's
  pages, the example) is unchanged and a try with a note costs what one without does. The
  note is kept beside the item file, and the next try's box starts from it. A batch made
  with `hide_answers` (hand-calculation exercises) shows no check output, which can quote
  expected values; the note still works there. "Try all" takes no notes.
- "Try again with a 64,000 output cap" (owner, 2026-11-10). A plan's two items came out
  discarded with a rule 1 failure shown, but their first two rounds had been cut off at
  `max_tokens` (32,000, with thinking on, which counts towards it): only the last round's
  failure is shown, so the cut-offs were invisible, and a plain retry would run into the
  same cap. Now an exercise's status reads every round of its generation.jsonl record and
  says which were cut off ("Rounds 0, 1: the reply was cut off at the batch's output cap"),
  and only such an exercise gets a second button, and a ticked box in the note form, for a
  retry with the one larger cap `plan_items.RETRY_CAP` (64,000): both `max_tokens` and
  `max_tokens_no_thinking` as `--set`s after run.json's, since the graph keeps the last
  value of a setting given twice and a repair round may run with thinking off. One step,
  not a free number: the one a cut-off item needs, within the settings' 128,000, and the
  route takes no other value (`cap-bad`) and none for an item whose rounds were not cut
  off. The batch's own cap is unchanged: raising the default would pay for longer thinking
  on every item. "Try all" keeps the batch's cap. Clicked in headless Chrome on a scratch
  course with the stand-in graph (the record rewritten as the real one was): the line, the
  button, the task's command with both caps last. Both week 9 items came out VALID through
  it the same afternoon (first replies of 51,616 and 52,220 output tokens, which the old
  cap had cut; one needed two repairs, the other one).
- Lectures coverage.md has nothing on (owner, 2026-11-10). Week 9's plan of 11-09 had run
  with no coverage rows for its two lectures: the drafted rows were still in
  `coverage-draft.md`, unmerged, so the plan model picked knowledge points from the page
  titles alone, and nothing said so. `generation.coverage_missing` names the lectures the
  file shows no trace of, by three signs, since the file is written two ways: a heading
  equal to the lecture's name (the draft's format), a heading carrying its number ("L8.1
  Finding Similar Items I" in a hand-written file), or a table row whose Slides cell starts
  with its week ("8.3-8.9"; two lectures of one week both number their slides 8.x, so a
  week with rows for one of them warns for neither). The New batch form marks such a
  lecture "(no rows in coverage.md)" with a line pointing to the Coverage page, and a plan
  waiting for approval shows a notice naming them: it can still be approved (a new course
  has no coverage yet, and the planner works without it), but the owner knows what the
  plan was made from. Checked on the real course's New batch form: its two week 9 lectures
  are marked, weeks 2 and 8 (the one heading style and the other) are not; the waiting
  plan's notice on a test plan, as the real week 9 plan is past approval.
- Fixing in the page, not through Claude (owner, 2026-11-06: "这些漏洞下次在网页有补充"):
  - A task that failed or was stopped has "Run again", when it is safe to run twice (reading
    lectures, transcribing, Validate all, Check, install, build, the two drafts). It posts to
    the action that started the task, whose route makes the commands again with its checks;
    the commands in task.json are never run back, as code run on this machine could change
    that file. Generating has Continue and "Try again" instead, and a new course its page.
  - A running task with no new output for 30 s says what it waits for, by its kind (the
    plan model, the generate model, pip, docker build, the test runs) and for how long: a
    model's reply is printed only once it is whole, and a plan call took 12 minutes while
    the page showed nothing new.
- Stopping a task: the test runner starts each pytest or `docker run` in a session of its own,
  out of reach of one process group, so the runner lists every process below it (`ps`), sends
  each Ctrl+C (a `docker run` client passes it into its container), and kills what is left
  after 10 s; a container left without its client ends at its own limit. The runner sets its
  own Ctrl+C handler first: a process started in the background has SIGINT ignored and passes
  that on, and the first real Stop did nothing until it did. Checked by a click in Chrome on a
  copy of two examples, in containers: stopped in 7 s, no container or process left, and the
  interrupted exercise's `validation.json` not rewritten. Windows: Ctrl+Break, not tested.
- Generation (step 4, owner, 2026-11-06): no change to `graph/`. Started without a terminal,
  `python -m tutor.graph plan` already pauses at the plan and exits 0, so the GUI runs the
  CLI's commands as tasks: the new-batch form starts `plan` (lectures read into the cache,
  a label, learn or review, an optional count, preferences, and "hand calculation", which is
  `--set hide_answers=true`); the plan page shows `plan.json` and starts `resume --approve`,
  `resume --feedback=...` or, for a run that stopped, `resume`. Free text goes in as one
  argument in `--name=value` form, so text starting with "-" is not taken for an option, and
  no shell is involved. Editing the plan is "Edit plan.json in VS Code", since approving
  already uses the owner's edited `plan.json`; no editor in the page (owner). The form names
  the model of each role from `[llm.roles]` and says it costs money, with no estimate: tokens
  per exercise vary too much for one to help (owner). Approve and Send back run only on the
  owner's click; tests and Chrome checks use a stand-in for the command
  (`tests/fake_graph.py`), never a model or a real course.
  - Where a batch stands is read from files, since the plan's state proper is in LangGraph's
    checkpoint, which needs the `gen` extra: a GUI task running for its label; a manifest
    (written once every item has finished): generated; a draft folder for a plan id or a
    `generation.jsonl`: stopped midway, Continue; else `plan.json`: waiting for the owner. A
    plan being made again (feedback) that stopped leaves the old `plan.json`, and approving
    would finish the new plan and approve it unseen; when the GUI's own last task for the
    label was a planning one that did not finish, the page offers Continue, not Approve. A
    batch planned in a terminal has no such history. Assistant mode (exit 3) shows as
    waiting for the assistant: its requests are answered from a terminal, then Continue.
  - A label used before (by a batch or a draft) gets the first free -1, -2, ... in the
    form, not a refusal (owner, 2026-11-09); the plan page shows the date of its draft
    folder (DATE_p3-LABEL), as batches show theirs. Only the GUI renames: the CLI's `plan`
    still refuses a taken label, typed by hand. Labels made before stay as they are.
  - The new-batch form is the one page that shows what a request sent: a refused form comes
    back with the values typed, escaped like everything else.
  - How many exercises (owner, 2026-11-06): "let the plan model decide", with its limit in
    view (`--max-count`: `[generation] max_count`, else 40, since some lectures hold far
    more than 6 points; the CLI's default stays 6), or "exactly N" (`--count`).
    The plan model already counts the knowledge points that deserve an exercise, so no
    separate call counts them first: it would read the same pages for a second fee, and the
    two answers could differ. The limit sat out of sight before; a plan that the model
    stopped at the limit now says so on the plan page, since points may have been merged or
    left out. The limit is kept in the batch's state when the plan is made, so a plan sent
    back cannot go past it; more means adding items to `plan.json` or a new batch.
    The bound rose from 20 to 40 (owner, 2026-11-09): only ticked items are generated, so a
    longer plan costs no more, and the plan model's reply bounds it instead. Measured on the
    saved plans, an item is 650 to 1,130 characters (about 300 tokens), so 40 items fit
    `max_tokens_no_thinking` (16k) with room; 100, or a limit of PDF pages / 5 (about 48
    for four 60-page lectures), would be cut off.
- Lectures and new courses (step 5, owner, 2026-11-06). A course's Lectures page lists each
  PDF with what the cache holds (not read, read, image pages waiting). Uploads: PDFs only,
  checked by their first bytes; 100 MB each and 500 MB a request; the name kept as given
  (spaces, brackets, any letters) unless it holds a separator or a character Windows or a
  glob reserves, with ".pdf" in lower case, as the cache and the plan find lectures by that
  name; never over an existing file, since exercises cite its slides (delete the old one
  first). A set of files is saved all or none: hidden part files first, then hard links,
  which fail rather than replace. Each upload starts the text-layer read
  (`python -m tutor.materials`, free) as a task; the vision model reads the waiting image
  pages only from its own button, which names the model, as each page is a paid call.
  - New course (the owner's "one click after uploading the slides", 2026-11-04): a folder
    name and an optional course id; one task runs `python -m tutor.workspace init`; until
    init writes `workspace.toml`, only the task's page is shown. It took optional PDFs too,
    saved first and read after init, until the owner dropped them (2026-11-07): lectures
    are optional there, and the Lectures page takes files or a whole folder to tick from
    once the course exists. Refused inside a git repository, as init refuses it. Its page
    shows the stages (course files, the venv, installing the tool; reading the lectures for
    a course created before that) with a bar, read from the files init has made
    and the commands that ended, not from the log's words; the install has no measure
    inside it, so it shows how long it has run (owner, 2026-11-06: it looked stuck, as init
    printed its steps only at the end; it now prints each as it happens, and a line before
    the install). Once ready, htmx's poll is answered with `HX-Redirect` to the course page;
    a failed one stays on its log. A course page lists what still stands between it and
    generating: lectures not read, no models, where tests run not chosen, no `coverage.md`;
    each line links to the page that fixes it (owner, 2026-11-06).
  - A whole folder (owner, 2026-11-07: the file picker took only lecture files). A second
    input with `webkitdirectory`, no script: the browser sends every file in the folder, so
    the server keeps its PDFs and web pages, leaves out what a browser saves beside a page
    (`Name_files/`) and hidden files, and counts what it left out by kind. They wait in the
    course's `.lecture-uploads/<token>/`, not yet lectures, on a page where the owner
    unticks what is none (exams, solutions, a syllabus), as the GUI does not delete a
    lecture; a file that cannot be saved as it is (its name taken, its content not what its
    extension says) shows why, with no box. A name with no number takes the one its nearest
    subfolder starts with ("10 Matrices/Quiz.htm" -> "10 Quiz.htm"), so its sections are
    numbered like its topic. Tick all, Tick none, Invert, and each subfolder's Tick / Untick
    these (owner, 2026-11-07): with no script of our own, a button sends the ticks as they
    are and the page comes back with them changed; files directly in the folder come first,
    then each subfolder's together. The ticked files then pass the same checks as an
    upload. One folder waits at a time: choosing another, or Cancel, drops it. The whole folder counts
    toward a request's 500 MB, videos too, and at most 5000 files; past either, the 413 page
    says to choose a subfolder.
  - A "discard plan" button stays out for now (owner): it would remove a draft folder, so
    it gets its own step, hiding the folder rather than deleting it.
- htmx 2.0.11, the newest 2.x on npm (2026-09-22, 0BSD), vendored as
  `static/htmx-2.0.11.min.js` after its tarball matched npm's sha512; a test pins the file's
  sha256. Its config turns off eval, script tags in swapped HTML and its inline indicator
  styles, so the CSP did not change. `selfRequestsOnly` keeps its requests on this server.
- Dependencies: the `gui` extra, so the rest of the tool does not carry them: Flask and
  waitress (8 packages), markdown-it-py, its dollar-math plugin, latex2mathml and tomlkit
  (13 in all, all pure Python and MIT).
- Shape: a local web app (a thin Flask backend reusing the CLI functions, which a later MCP
  server could share too, plus a light frontend), served by waitress, bound to `127.0.0.1`
  with a per-launch token or a Host header check so other web pages cannot drive it.
- Stack (owner, 2026-11-05; it was FastAPI, and the frontend a candidate since 2026-10-27),
  chosen for low long-term upkeep: Flask, waitress and Jinja2 are 8 packages in a clean
  Python 3.11 environment, against 17 for FastAPI, uvicorn, Jinja2 and `python-multipart`
  (12 new here, since pydantic is already a dependency), 33 for Streamlit (it brings pandas
  and pyarrow) and 50 for NiceGUI. Werkzeug parses uploads, so no `python-multipart`, which
  had five advisories in 2026 so far, mostly denial of service, little on a local app, but
  each one an upgrade. Flask is synchronous, as the `tutor` functions are. FastAPI would read
  better on a CV and was the trade-off accepted. Long tasks (generation, validation) run the
  existing CLI as a child process writing a log file, and the page polls it: no WebSocket, and
  a GUI crash does not stop the task.
- Frontend without an npm build chain: server-rendered Jinja2 templates, htmx 2.x as one
  vendored JS file with a fixed version, and one small hand-written CSS file (htmx 4 shipped
  in 2026-08 with breaking changes; a vendored file does not move until we move it).
  Reason: most security alerts in web projects come from deep npm dependency trees and
  build tools; without npm, Dependabot only watches the Python side. A desktop shell is no
  better (Electron ships Chromium and still uses npm). The screens are manage-and-browse
  pages that do not need a SPA framework. What still needs care: escaping everything not
  written by us (lecture text, LLM hints, learner code) to prevent XSS.
- Manage, do not edit: drag lectures and lab files into the workspace, pick sources and start
  generation with the preferences box, approving the batch plan there (step 4),
  browse batches with status from `validation.json`, progress from `attempts.jsonl`, mode and
  sources from `manifest.json`, coverage from `coverage.md`.
- Solving stays in the IDE: "Open in VS Code" (step 2) runs `code <course folder> <student.py>`,
  the course folder and not the exercise's, since the course's `.vscode` settings put its venv
  in the terminal; VS Code reuses a window that has the folder open. `code` is looked up on
  PATH, then inside the macOS app (not on PATH here). JetBrains has its own launcher, later.
  The GUI never runs the learner's tests; the IDE terminal's `tutor test` and ordinary host
  `pytest` both use the shared attempts writer.
- An "Open folder" button next to it (owner, 2026-11-05) on course, batch and exercise pages
  shows the folder in Finder or Explorer. Only paths inside the workspace, and never a way
  into `solutions/`: a batch's button opens its `exercises/`, not the batch folder beside
  `solutions/`. Both buttons go through `gui/launch.py`, the one place that can say "not
  available". Only macOS is tested; the Windows and Linux branches are not.
- Shows only `exercises/`; `solutions/` (reference, pseudocode) at most after the exercise is
  solved and on explicit request, matching the tutoring rule.
- Files stay the source of truth; no database (SQLite remains deferred).
- Local only, per "Local-first": no accounts, no hosting. A settings page picks the model
  (API or local) and stores the user's own key, never shown again in full, with the
  data-flow note next to it (built in step 6, below).
- Settings (step 6, owner, 2026-11-06):
  - Keys in the repository's `.env`, the file the commands already read, not the OS
    keychain for now (that would need a dependency and a change to how llm.py reads keys,
    and Linux without a desktop has none). The user adds and removes keys by name, any
    key-like name (`…_KEY`, `…_TOKEN`, `…_SECRET`) or one a profile names; WORKSPACE_DIR
    and the other settings in the file are never listed or touched. A page shows the last
    4 characters of a long key and never sends one back, even in a refused form. The file
    is parsed as text, never loaded into the GUI's environment, kept line for line
    otherwise, replaced in one step and readable by its owner only. A key also set in the
    environment the GUI started from wins (load_env's rule), and the page says so.
    "Used by" lists each course once, with that course's models after it. Adding a key
    (owner, 2026-11-10): choosing a provider fills the name with the variable its key
    goes in, from the models.dev list the Models page keeps (`catalog.py`'s `key_env`);
    the list is only read, by a GET of the page, and a typed name still works, as it must
    when no list is kept yet. A provider whose key is set already says so and the form
    replaces it. The Models page's "not set" links open the form with the name filled.
    The provider is not guessed from the key's shape: a plain `sk-` key fits several.
  - A course's Models page writes its workspace.toml's [llm] with tomlkit (owner), which
    keeps the owner's comments; a changed value loses only its own line's comment. Every
    save is checked before the file is replaced: read back with tomllib, [llm] must be
    exactly what was intended, worked out from the old data and not from tomlkit, and every
    other table unchanged; the file before is kept as `workspace.toml.bak`. A new key in a
    table goes after its last key, not after the comments that end it and belong to the
    next table (tomlkit's `Container._insert_after`, pinned by a test). Profiles are
    checked by llm.py's own rules; roles by generation's (the oracle from another provider
    than generate and repair); a profile in use cannot be removed, and a renamed one takes
    its roles along. "Copy from another course" fills a course that has none. The checked
    write is `gui/toml_edit.py` since step 7a, which writes [requires] and [isolation] the
    same way.
  - Filling New model from models.dev (owner, 2026-11-07: the form was plain, with only
    the providers llm.py knows by name; "custom" meant finding and typing a base URL). The
    public catalog at `https://models.dev/api.json` (MIT, no key) lists providers with
    their base URL and key variable, and their models with release date, thinking and how
    far it can be turned down, image input, context and USD price. Downloaded only on
    "Fetch the list" / "Refresh the list" (beside the provider choice), never by itself;
    the request carries nothing of the courses. Kept in `<root>/.cache/models-dev.json`,
    reduced to what is used (about 1.2 MB, 204 providers of 235): the providers models.dev
    marks as taking OpenAI's requests, plus those it lists with their own SDK whose
    OpenAI-compatible URL is checked in their docs (`catalog.OPENAI_URLS`: Gemini, xAI,
    Mistral, Groq, Together, MiniMax, OpenRouter); the rest are counted on the
    page. A downloaded value is checked before it is kept (https, or http on this machine;
    a key name in capitals; a plain model id) and shown escaped. Above the hand-written
    form, which stays: a provider (common ones first, the key's state in `.env` by name),
    its models newest first without withdrawn ones, and "Use", which fills the form and
    saves nothing. A provider llm.py knows (DeepSeek, Zhipu and z.ai as `glm`, OpenAI,
    Anthropic since 2026-11-10, LM Studio) keeps its own way to turn thinking off; another
    one is `custom` with its URL, and a thinking model gets the lowest `reasoning_effort` it
    takes (none, minimal, low), or a note that each call thinks. No script: choosing a
    provider is a GET form, so the page can be reloaded. A `custom` profile at Anthropic's
    own address is refused with the advice to choose `anthropic` (the rule every known
    provider's address has).
  - One provider's oracle (owner, 2026-11-07: "not everyone buys several APIs; do not
    forbid it, at least warn"). The oracle at the same provider and address as generate or
    repair was refused, by the Models page and by the graph before any call. It now runs,
    with a note at the run's start and a warning on the page: an oracle call still reads
    the question alone, apart from the exercise, while with no oracle role (always
    allowed) the generating model writes oracle.py in the same reply, the least
    independent; refusing the better of the two pushed one-key users to the worse. Still
    refused: one assistant profile for both, whose requests would mix in one queue. Left
    as it was: when rule 1 fails and rule 4 agrees, repair is told oracle.py was written
    by "another model", which with one model is not quite so; that text is part of a
    calibrated prompt, so it stays.
  - One maker's oracle (owner, 2026-11-07: "a whole family gives no warning"). Saving
    roles refused an oracle at the same provider and address as generate or repair, as
    the graph did; the same maker's model through another site (GLM at bigmodel.cn and
    z.ai, DeepSeek directly and through OpenRouter) passed, though it is no independent
    reading either. The maker comes from models.dev's canonical model id ("zhipuai/...";
    for about 85% of its models), else an aggregator's id that starts with a known maker
    ("deepseek/..."); the kept list now has it (format 2: a list kept before is fetched
    again). One model, one provider and one maker are each a warning, said while the
    roles are being chosen: htmx asks a read-only route for the part above Save whenever
    a role changes (hx-get on each select; no script of our own), with "Save even so" to
    tick. Without htmx, a save of changed roles that have a warning comes back held, the
    roles as chosen in the form; once saved, the page keeps saying so. A model the list
    does not know is not compared by maker. A role set to none is
    not compared, and repair set to none is generate. The graph's own check stays as it
    was: it runs without the list. With no script the page cannot warn while a role is
    being chosen, so each model's provider and maker follow its name in the role choices
    and in the table (owner, 2026-11-07). A save that is refused or held back keeps the
    roles as chosen in the form (they were reset to the file's, which looked wiped). The
    GUI never fetches the list by itself; a list kept in an older format is named as such,
    with its date, and the page asks to fetch it again.
  - The form follows its provider (owner, 2026-11-07: an empty base URL under "deepseek"
    read as a contradiction). A provider llm.py knows shows its own base URL in the field,
    editable for another site of the same provider (z.ai for glm), and its way to turn
    thinking off in grey; saving leaves its own URL out of the file, so a change in llm.py
    reaches it. custom requires a URL; assistant greys out the key, the URL and More. With
    no script, Apply beside the provider sends the whole form back by GET, nothing typed
    lost, and replaces a known provider's own URL with the new provider's. A save whose URL
    is another known provider's own (changed without Apply, or a known one as custom) is
    refused, saying which. With htmx the provider's choice swaps the fields at once (the
    page as Apply would send it, cut to those fields with hx-select), so Apply is only the
    way without it. Base URL moved out of More, as it decides where calls go. Enter
    still saves: an off-screen Save is the form's first button. A new model's time limit
    is filled in as 900 s (owner, 2026-11-07; every cloud profile had been given it by
    hand, as plan and generate calls with thinking run past llm.py's 300 s); editing a
    model shows what its file has.
  - "Check" (owner): `python -m tutor.llm check [PROFILE ...]`, as a task, sends each model
    one tiny request (thinking off, 32 output tokens) and says ok or the provider's reason,
    so a wrong key or model name shows before a run that spends more. An assistant profile
    has nothing to check; a profile only the embedding role uses gets an embedding call.
  - Not in this step: the `[generation]` tunables (their defaults are measured values).
- Environment page:
  - Optional host setup guide (2026-11-09): step 2 links to a packaged, read-only page
    with explicit macOS/Linux/Windows/WSL navigation. It reads course requirements and
    the existing language registry; no probes, lecture reads, model requests or installs.
    Python links back to venv install/check/switch; C links to its existing settings.
    Declared Java/Hadoop get manual guidance derived from docs/setup/bigdata.md, with
    official sources and coverage limits for other versions. No new language support,
    system installer. Doctor connects this guide to measured course checks and repair paths.
    Container practice needs no host JVM/compiler.
    Linux/WSL fresh-install testing is still outstanding. Templates are packaged so the
    page does not depend on a source checkout's docs directory.
  - The page is four numbered steps in the order they are done (owner, 2026-11-06, after
    trying it on a new course: by topic it read unclearly): (1) what the course needs, the
    model's draft then the list; (2) install on this machine, the check below its button;
    (3) where tests run; (4) the test image, dimmed and "not needed" on the host. Each says
    whether it is done; steps 2 and 4 say so in their htmx parts.
  - Environment controls (started in step 7a, 2026-11-06; integrated 2026-11-09), one page
    per course, with deterministic checks and explicit optional model repair:
    - Step 2 automatically loads the same deterministic Doctor rows as `tutor doctor`,
      without smoke or model calls. Check / Result / What to do columns use OK, Needs
      attention, Problem and Skipped, with duplicate rows removed. Doctor's host requirement
      probe uses the course's `.venv`; a missing/broken venv produces one failure and skips
      those requirements. Known failures have their existing fix buttons on the row:
      install, build, Python switch, host guide or runner choice. An uncovered Problem
      alone offers the four-screen model-fix wizard described in "Version conflicts".
      Running tasks disable fix actions. The initial page loads probes through htmx;
      read-only checks never start a model, install or worker task. The GUI server never
      loads credentials into its environment.
    - "Exercises may import" (owner, 2026-11-06): each pip row of the list has a tick and the
      module it is imported as; the ticked ones go into `[safety] allow_imports` in the same
      checked write as the version declarations, an unticked one comes out, and modules no
      row names (an OS course's `subprocess`) stay, listed under the table. Unticked by default: widening
      the static check is the owner's call, package by package. The module box starts with
      what the lectures' code imports it as when the draft saw it (scikit-learn is sklearn),
      else the name with `-` as `_`. The generate and repair prompt names them as importable
      next to the core modules (owner, 2026-11-06), or the model would be told it may not use
      what the check lets through. A course with none gets the same prompt text as before
      (checked by its hash), so its prompt cache holds. The oracle's prompt keeps the core
      modules only, to stay independent of the exercise; an exercise whose answer needs the
      library itself may then get no oracle, to decide when one does.
    - Step 1 has one version cell per package, accepting a prefix such as `3.5` or a
      restricted numeric comparison such as `>=1,<2`. Prefixes stay in `[requires]`,
      comparisons in `[package-bounds]`; changing form moves the entry in one checked
      toml_edit write with a `.bak`, preserving unrelated tables/comments. Package identities
      cannot appear twice across spelling variants or declaration tables. Python, java,
      hadoop and gcc accept prefixes only; Python/C versions remain in the language list.
      Language-version and draft-accept saves preserve unrelated bounds. Import consent is
      saved in that same transaction. "+ Add a row" and "−" change the form using htmx;
      Save applies it. Empty rows are ignored, incomplete/invalid rows return as typed.
      The shared envcheck rules still require a JVM for pyspark/hadoop, and container
      courses require a supported Hadoop recipe. Each pip name links to its PyPI page.
    - "Install into .venv" runs `tutor.workspace init` again as a task, which makes the
      `.venv` if missing and installs this package and its declared dependencies (the same
      command a command-line user runs). The page shows first the pip command, where it installs,
      that it needs no admin rights and changes nothing outside the course folder, that it
      downloads from PyPI, and how to undo it.
    - Where tests run: container or host, written to `[isolation] runner` only when chosen;
      host only with a tick saying generated code then runs with the user's rights. A new
      `[isolation]` table goes after init's commented lines, its header saying the page set it.
    - The test image: Docker missing, not running, image missing or out of date, ready with
      its size. "Build image" runs `tutor.container build` with the course's venv, so the
      image takes its package versions; before it, the page shows the recipe
      (`python -m tutor.container recipe`, in that venv), each download and its source, the
      sizes measured so far for scale, and how to remove the image.
    - Step 4 loads build time and course-venv/image version differences in a separate
      read-only fragment; a course tested on this machine has no image, so none is shown.
      Its times are stored in UTC and shown like every other time on the pages: this
      machine's clock, to the second (`tasks.local_time`; owner, 2026-11-10).
      Differences are information; unmeasured versions stay unknown.
      Old records never produce a current comparison. Missing/old/invalid/unavailable records
      offer the existing metadata-only Refresh action; the full record/history page is
      linked here only. An unbuildable recipe keeps a GUI path back to step 1 instead of
      failing the history page. Legacy images keep unknown build/base facts until rebuilt.
    - Python recovery offers installed compatible interpreters through the existing guarded
      switch. Only when none is compatible does it show "No suitable Python here. Download
      one with uv", linking to the existing explicit-consent download page. A compatible
      current interpreter counts; a package-only repair does not imply a Python search.
      The host guide stays inside step 2. No setup links sit above the four steps, whose
      intro states their order only. System-tool installation remains manual.
    - Original step-7a Chrome check (2026-11-06) on a scratch course: a row added, host refused
      without the tick, container saved, the install (2 s) and the build (8 s, cached layers, 0.18
      GB) run as tasks, the check then ok; the three real courses only opened. The clicks
      made on the Environment/Doctor integration, and those still owed, are listed in
      "Version conflicts".
  - Built in step 7b (owner, 2026-11-06): a model drafts `[requires]` from the lectures
    (`python -m tutor.materials.requires`, a task from the page's button, which names the
    model and the input tokens; only on that click, as it may cost money).
    - The model: the new `setup` role's, else `plan`'s, else `generate`'s, so nothing new has
      to be set up. One request per cached lecture, the fixed instructions first (for the
      provider's prompt cache) and the lecture after; thinking off; a JSON schema: kind
      (pip, python, java, hadoop, other), pip name, module imported, version only if the
      page states one, page, and the page's own words that show it.
    - The answer is stored as given, `cache/requires/<lecture>.json`, keyed by the lecture's
      text and the prompt version: a lecture is asked once, and again only when it changes.
      The checks run each time the draft is read, so a better check needs no new call:
      the quote must be on its page (spaces aside) and name the software (the pip name or
      the module, as a whole word: "six" is not in "sixty"; "other" more loosely, as it is
      only shown); the standard library goes (`sys.stdlib_module_names`); versions keep
      their dotted numbers ("3.5.x" is 3.5, Java "1.8" is 8, as envcheck reads it).
    - A pip name its quotes do not show (`import cv2` for opencv-python) is the model's
      knowledge, flagged "name from the model", with its PyPI link: a made-up name can be
      one somebody registered. One piece of software across lectures is one candidate with
      every source; versions stated differently are all shown.
    - The page lists candidates with a tick, a version box, what [requires] holds now (new,
      listed, listed with another version) and the sources; nothing is written until the
      owner ticks entries and adds them, by 7a's rules (a pyspark without java is refused).
      The box starts at the stated version's major.minor (3.5.1 as 3.5; Java its major),
      the slide's own beside it. Software envcheck cannot check (a database, CUDA) is only
      listed. `[safety] allow_imports` is not drafted: widening the static check is a safety
      decision, and the page says a package exercises import must be added there by hand.
    - A course without models gets no button: the GUI does not read `.env`, so it cannot tell
      whether the LLM_* variables would answer.
    - Prompt version 2 (owner, 2026-11-07): only software that builds, runs or tests the
      course's code on the student's own computer, a compiler, build tool and debugger
      named among them. A C course's first lecture had brought in what its lab machines
      are reached with (PuTTY, WinSCP, X2Go, ssh), its editors (vi, emacs) and commands
      every Unix-like system has (tar, nohup); these are now left out by name. Such
      entries were "other", only listed and never installed, so the change cleans the
      list; the new version asks every lecture again on the next click.
    - Prompt version 3 (owner, 2026-11-07, with C): a kind of its own, "gcc", for a C
      compiler (gcc, cc or clang), so the C compiler is a tickable `gcc` row rather than an
      "other" only listed; its quote must name gcc, cc or clang as a word of its own ("cc" is
      in "access"), and its box starts at the major version, as Java's. Every course's stored
      answers are then out of date and the page offers to ask again; [requires] itself is
      untouched.
    - Ask again (owner, 2026-11-07): once lectures are looked at, the page still offers
      "Look at all N lectures again" (`--again`), with the input tokens that sends, after a
      change of model or to compare; it was offered only for lectures not yet asked.
    - Measured on the private deep learning course, with GLM-5.3-Flash (free): 16 lectures,
      157k tokens in and 715 out, 10 candidates, one dropped (a plural, before "other"
      matched loosely). It found torch, numpy and scikit-learn with the versions the slides
      state (scikit-learn from `sklearn`, so flagged), pandas, torchvision and a newer
      Python than the course's [requires], which held only python; matplotlib, which the
      course's venv holds, is in no slide. Nothing was added: that is the owner's click. The
      page was checked by a click in Chrome on a scratch course, its run button with a
      stand-in command.
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
  - Python packages into the course's own venv (one venv per course, owner, 2026-11-05; the
    project venv holds only the tool): one click, after the same review (a broken venv
    is deleted and rebuilt). Everything else, the user runs in their own terminal, then
    clicks "Check again". The GUI never runs system installs, and never runs LLM-written
    commands (arbitrary commands; hallucinated package names can be squatted). A component
    or platform missing from the recipes is reported as needing a new recipe.
  - One click after uploading a new course's slides creates its workspace venv and terminal files
    (owner's wish, 2026-11-04), by calling `python -m tutor.workspace init`.
  - How far this relaxes "Check, don't install" (owner, 2026-11-06): the GUI installs Python
    packages only into a course's own venv (through `tutor.workspace init`) and builds the
    test image (through `tutor.container build`); it never runs a system install, which the
    user runs in their own terminal before "Check again".
  - The test-run container (layer B, "Code execution safety"; owner, 2026-11-05). The GUI
    itself stays on the host, since it opens the IDE and folders and reads keys; only test
    runs go into the container. The page shows, per course: Docker missing, not running,
    image missing or out of date, or ready, with the image's size and "Check again". A "Build
    image" button is allowed, since the recipe comes from our code and not from a model; it
    shows the recipe, download source and expected size first. The user installs and starts
    Docker. The venv click does not build the image, which may be 2 GB: that waits until
    `[requires]` is confirmed. The user chooses host or container for a new course, nothing
    switched silently, with a warning when it matters: choosing the host (generated code runs
    with their rights), and a course set to the container while Docker is not available.
- Coverage page (owner, 2026-11-06): a course's `coverage.md` shown, and a model's draft of
  it (`python -m tutor.materials.coverage`, a task from the page's button, which names the
  model and the input tokens). A course without the file plans from slide titles alone.
  - The model and the storing are the [requires] draft's: the `setup` role's model, else
    `plan`'s, else `generate`'s; one request per cached lecture, instructions first; the
    answer stored as given in `cache/coverage/<lecture>.json`, keyed by the lecture's text
    and the prompt version, so a lecture is asked once and a new lecture costs only itself.
    Each answer lists the lecture's knowledge points, each with its first and last page, a
    status (gap-code, gap-tracing, gap-design, concept, n/a) and, for gap-code, what an
    exercise could check.
  - Checked when read: a point whose pages are not in the lecture, or run backwards, is
    dropped; pages become slide footers ("1.2-1.3"; "p.N" where a page has none), as the
    plan and its slices read them; the pages no point covers are listed per lecture; a `|`
    in a point cannot break the table. `ex:<id>` is never guessed: the plan already lists the
    existing exercises and avoids them, and the owner marks rows as batches are made.
  - `coverage-draft.md` is rewritten by each run, so it is never the file to edit. It never
    writes over `coverage.md`: "Use as coverage.md" creates that file only when there is none
    (opened for exclusive creation), with the header of a kept file. Each lecture's section
    has the lecture's file name as its heading, so a later run can tell which lectures the
    file has.
  - Adding as lectures come (owner, 2026-11-06, "能做到增量添加吗"): the drafted lectures
    `coverage.md` has no section for are offered with a tick; the ticked ones are appended at
    its end, each with a line saying it came from the draft. Nothing already in the file
    changes (the old text must be the start of the new), and the file before is kept as
    `coverage.md.bak`. A hand-made file that names its lectures its own way (no heading is a
    lecture's file name) cannot be matched, so nothing is ticked there and the page says so.
  - Measured on the private test course with GLM-5.3-Flash (free): 20 lectures, 80k tokens
    in and 13.5k out, 276 points (65 gap-code, 46 gap-tracing, 3 gap-design, 145 concept,
    17 n/a), none dropped, a page or two uncovered in 5 lectures. Its PDFs have no slide
    footers, so its ranges are PDF pages; generation sends such a lecture whole anyway.
    Using the draft is the owner's click. Checked by a click in Chrome on a scratch course:
    use, then add a second lecture after a hand-written line, which stayed.
  - coverage as data (owner, 2026-11-09 the idea, 2026-11-10 "do it now, it is the same
    thing" after the no-rows warning; `tutor/materials/coverage_store.py`). `coverage.json`
    is the source of truth and `coverage.md` is rendered from it on every save, in the
    shape the file always had. Why: the Markdown file could not be updated for a changed
    lecture without losing the owner's edits, a batch could only propose its marks, and
    whether a lecture had rows was a guess from headings. Per lecture, keyed by the name a
    plan uses: the hash of the lecture text it was drafted from, the date and model, the
    owner's title and notes, the points and the pages none covers. A point: a stable id
    (sha256 of lecture, slides and text, never its position, so adding a row moves
    nothing), slides, text, one of the five statuses, the check in square brackets, any
    other note, `covered_by` (the `ex:<id> (batch)` marks, a list, the status kept under
    them so a gap's kind survives the exercise), `owner_edited` and `removed`. Reading the
    old file once (`migrate`, the file before kept as `coverage.md.bak`): both shapes the
    files had, a draft's `## <lecture file>` headings and a hand-written file's `## Week N`
    / `### L8.1 Title` headings mapped to lectures by their numbers (the title kept, a
    week's note given to its first lecture); the status cell parsed as the files wrote it,
    escaped `|` included; a row that is not three cells, and any other text, kept as
    notes; a section that is no lecture (a labs table) kept after the lectures. Checked on
    the three courses that have a file: every table row read (102, 220 and 276), and the
    rendered file read again gives the same lectures. Merging a new draft of one lecture:
    by id, a point found again is kept with the owner's fields untouched (its pages filled
    in), a point the owner edited or an exercise practises is kept whatever the draft says,
    any other point the draft no longer has is marked removed and listed under the table
    for the owner to confirm (never deleted by code), a new point is added where the draft
    puts it. A batch marks the one point its saved exercise's lecture and slides match
    exactly (`mark_covered`); with no or several matches it stays a proposal. The plan
    prompt receives the rendered table when the data exists (the same bytes for the same
    data; the prompt's own text is unchanged and its pins hold), the file as written for a
    course not migrated, "(none)" for one with neither, so a new course still plans. Edits
    to the rendered file do not flow back (`rendered_is_stale` tells the page). Command
    line: `python -m tutor.materials.coverage_store migrate | render | redraft [--all]`;
    the drafting itself stays in `tutor.materials.coverage`. The Coverage page (same day)
    is on this data, in three states: nothing yet (the draft, then "Use as coverage" makes
    coverage.json); a hand-kept coverage.md (shown as it is, with what a conversion would
    read from it, lectures, rows and the lines to look at, and one button that converts
    it); the data (each lecture's points with an Edit page per point, Add a point, Title
    and notes, the points a draft no longer found with Remove and Keep, "Draft N lectures"
    for the ones not drafted yet or changed since their text's hash, "Draft every lecture
    again" for the regeneration, and a notice when coverage.md was edited by hand, with a
    button that writes it again). An edit, an addition or a Keep marks the point as the
    owner's, so a draft keeps it; the Remove button is the owner's confirmation, code never
    deletes. A finished batch marks the one point each saved exercise's lecture and slides
    match, and `coverage-proposal.md` lists what it marked apart from what it still
    proposes. The old "add the ticked lectures to coverage.md" is gone: a lecture the data
    lacks is drafted into it. Clicked in headless Chrome on two scratch courses with the
    stand-in launcher: use the draft, edit a point (the rendered coverage.md shows the new
    marks), add a point; the conversion preview and Convert on a hand-written file (the
    week's note on its lecture, `.bak` kept).
  - Drafting a lecture again (owner, 2026-11-10). Ids come from a point's slides and text,
    so a lecture drafted again by another model matches almost none of its old rows by id;
    under the incremental rule every old row would wait for the owner's confirmation (96,
    220 and 276 rows on the three courses). So "Draft every lecture again" (`redraft
    --all`, `merge_draft(replace=True)`) replaces the model's own earlier rows without
    asking: a row the owner edited stays; a practised row's marks move to the one new row
    at the same slides when there is one (the row itself goes), and the row stays when
    there is none; any other old row goes. The incremental draft (new or changed lectures)
    keeps the confirmation rule. A hand-written file's rows (one whose headings are not the
    drafts') are read as the owner's (`owner_edited`), so a whole redraft keeps them all,
    which would put the model's rows beside them: that course is drafted lecture by
    lecture as the owner decides, not whole. Converting also records each covered lecture
    as drafted from its current text, so the next draft asks only for the lectures the
    file lacked. Done 2026-11-10 with Sonnet 5.5 (numbers in the owner's notes): the three
    files converted on the command line (the page's own code), the course with no file
    drafted whole, the two model-drafted courses drafted again whole, the hand-written
    course's three missing lectures taken from their stored answers. The first run found
    that the API wants `additionalProperties: false` on every object of a schema (the
    draft's answer model has no `extra="forbid"`, unlike the pipeline's): the anthropic
    module adds it to the copy it sends.
- Packaging the GUI itself as an image (owner asked, 2026-11-05: the aim is opening it in one click,
  see "Packaging and release", which does not need this, so an image is at most a later option). The
  costs: to start test containers from inside, it needs the Docker socket, which amounts to root on
  the Docker host, so that form must say so plainly; "Open folder" cannot work, and "Open in IDE"
  only as a `vscode://` link the browser opens; the OS keychain is out of reach; the sibling
  containers' mounts need the host's paths, not the paths inside; inside it binds all interfaces
  (the host side stays `-p 127.0.0.1:...`), so the token matters more. To keep that door open at no
  cost: paths come from configuration, starting a run stays in one place (`execution.py`), and the
  open-folder and IDE actions sit behind one small function that can report "not available".

## Open questions

- The embedding model (phase 4). LLMs: local Gemma 4 and bigmodel.cn GLM-5.3-Flash first
  (decided 2026-10-28, see the [3] model access item); others through the same client.

## Pseudocode

The course is examined in writing, so exam answers are written, not run. The course
pseudocode (Lin & Dyer style: `class Mapper / method Map / Emit`) maps one-to-one onto MRJob
`mapper` / `combiner` / `reducer`, so MRJob exercises double as pseudocode practice.
Each MRJob exercise keeps `solutions/<id>/pseudocode.md`: the reference solution written in exam
pseudocode, for comparison after solving.
