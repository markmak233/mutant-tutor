# Design Decisions

Living record of project decisions. Update it when a decision changes; note the reason.

## Where each part is

This file holds the goals, the rules for public and private material, what is deliberately
not done, and this index. Each other part is one file, small enough to read whole; read
the ones a change touches. (Until 2026-11-16 this was one file, `docs/design.md`, of 4,700
lines; the text moved word for word, and references of the form `"Section"` still name the
section or entry inside the file below.)

| File | What it holds |
|---|---|
| [layout.md](layout.md) | Environment (Python, OS); repository and workspace layouts; course coding materials, reading files |
| [validation.md](validation.md) | Exercise validation (rules 0 to 5, the API check, distribution checks, two modes, question and `question.md` format, independent checks, validity vs teaching quality); learning rules; exam pseudocode |
| [generation.md](generation.md) | The LangGraph pipeline (plan, generate, repair, generation settings, week 7 with five arms); hand-calculation exercises; prompt layout for caching; assistant mode |
| [rag.md](rag.md) | The retrieval comparison (RAG, agent, concept index against the whole lecture); evaluation metrics |
| [models.md](models.md) | Model access and providers, the Anthropic provider, per-role model comparisons, LLM engineering candidates |
| [execution.md](execution.md) | Code execution safety (subprocess, the container, image, `tutor test`, C with sanitizers, other languages, languages in the plan); version conflicts |
| [ci.md](ci.md) | The GitHub Actions jobs and why each exists |
| [roadmap.md](roadmap.md) | The phases in order (batch format contract, held-out and judge steps), the backlog, deferred work and later phases |
| [packaging.md](packaging.md) | Packaging and release: the install, the release package and workflow, desktop shortcut, Windows on ARM, the GUI as an image |
| [gui.md](gui.md) | The local GUI: build order, courses, access and security, pages, tasks and Stop, generation and plans, lectures, the stack, coverage |
| [gui-settings.md](gui-settings.md) | GUI settings (models, providers, keys, languages of the pages) and the Environment page |

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
  - `.env` names one workspace, and the plugin skipped tests outside it without a word, so these
    terminals also set `WORKSPACE_DIR` and `LECTURE_DIR` to their folder.
  - Since 2026-11-16 the plugin no longer depends on that: a test that is not one of
    `WORKSPACE_DIR`'s exercises belongs to the nearest folder above it with a
    `workspace.toml`, when it is one of that course's exercises (`attempts.workspace_of`).
    Found reading the code for the release package: on Linux and Windows a course's VS Code
    terminal sets no `WORKSPACE_DIR` and an installed tool has no `.env`, so every host run
    there went unrecorded. Temp copies (the validator's, a tutoring check's) have no
    `workspace.toml` above them and still leave no trace.
  - `python -m tutor.workspace init` writes all of this and keeps any file that exists (owner, 2026-11-05).
    Checked end to end in a scratch folder, with a real venv and install, about 11 s; `docs/setup/bigdata.md`
    has the step.
- `config.py` refuses a `WORKSPACE_DIR` that has no `workspace.toml` (wrong directory) or that sits
  inside any git repository (same check for `LECTURE_DIR`). This is stricter than relying on `.gitignore`.
- `.env` is read from the current directory by a ~10-line stdlib loader (no `python-dotenv`);
  variables already exported in the shell win. Run commands from the repo root.
- Public demo corpus for RAG: Apache Spark documentation (Apache 2.0). Same code, different data source.
- Exercises generated from lectures are private by default; only hand-written synthetic examples go in `examples/`.
  `examples/lectures/` holds synthetic lectures (owner, 2026-11-18): two short workshops
  (plain Python, five weeks; C, three weeks) written for the repository by a model and read
  by the owner, no course's material, under the repository's licence, so that screenshots
  and a public walk-through have a course to show. A test runs every piece of code in them
  against the output they show (C on this machine's compiler, with the sanitizers).
  Exercises generated from them stay in the workspace all the same; a screenshot of one
  may be published. Screenshots live in `docs/images/` and are taken only on these demo
  courses, in a courses folder of their own (`tutor gui --root`), so that no page lists a
  real course and no path names the user (owner, 2026-11-18).
- Safety net: pre-commit + gitleaks, `.claude/settings.json` deny rules, project-only API key with usage limit.
  Remember `.gitignore` does not remove files already in git history.
- gitleaks runs as a pre-commit `local` / `system` hook on staged changes, using the brew-installed
  binary (no Go build). `.gitleaks.toml` extends the default rules with `home-directory-path`
  (`/Users/<name>/`, `/home/<name>/`), since default rules only know secrets. History was scanned
  clean on 2026-10-24; rescan with `gitleaks git .` after any history-sensitive change.
- History is not rewritten (owner, 2026-10-26; main's ruleset blocks force pushes): course
  details that early commits held and later ones removed stay there, as "someone reading
  the history is acceptable, as long as it is not on a search engine's first page". Only
  the current files are kept clean. The whole-history search before v0.1.0 (2026-11-16)
  found one course's code and name, its setup guide's name and three tool versions in
  seven commits of 10-23 to 10-25, all removed on 10-26; no lecture text, slide, exercise
  or answer was ever committed.
  `.env.example` is marked `skip-worktree` locally so filled-in values never get staged.

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
- No Batch API for generating a batch (owner, 2026-11-18; a backlog item since 2026-10-28,
  "once a cloud provider is used"). A provider's batch endpoint takes requests that nobody
  waits for at about half price and answers within hours. Not worth building here: one
  course's batches cost little to begin with; the prompts are already laid out for the
  prompt cache (55 to 70% of the input read from it in the 2026-11-17 runs); assistant
  mode costs nothing; and an exercise is a chain (draft, oracle, validate, repair, validate again),
  of which only the first drafts and the oracles are independent, so each repair round
  would wait for a batch of its own and a batch of exercises would take hours instead of
  minutes. To look at again only for work that is large and that nobody waits for, such as
  drafting coverage again for every lecture or a judge calibration.

## Open questions

- The embedding model (phase 4). LLMs: local Gemma 4 and bigmodel.cn GLM-5.3-Flash first
  (decided 2026-10-28, see the [3] model access item); others through the same client.

