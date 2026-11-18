# Design: Environment and layout

The machine, the repository and workspace layouts, and the course materials. Part of the design record; the index of every part is [README.md](README.md).

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
├── docs/design/                         # this record, one file per part; README.md is the index
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
│   ├── station-drop-low-c/              # C, a function and a given driver.c: an array changed in place
│   └── lectures/                        # synthetic lectures for demo courses, as HTML:
│       ├── python-workshop/             #   five weeks of plain Python
│       └── c-workshop/                  #   three weeks of C
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

