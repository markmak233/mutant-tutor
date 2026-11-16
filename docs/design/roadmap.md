# Design: Roadmap

The phases in order, the backlog and what is deferred. Part of the design record; the index of every part is [README.md](README.md).

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
      - The exercise page's "Run my tests in the container" (owner, 2026-11-10: the
        terminal command as a button). It starts `python -m tutor test <exercise>` as a
        course task, so it queues like any other, shows live output on its task page and
        records the attempt on the host as the terminal would. Shown only for a container
        course (a host course's notice says to run pytest in the course terminal); Docker
        or the image missing stops it before it starts, as for validation. A run whose
        tests fail is a failed task, which stays on its log, where the failures are, with
        Run again; one that passes goes back to the exercise with a notice. Clicked in
        headless Chrome on a scratch container course with no image: the button, the task,
        `tutor test`'s own "image is missing" message, Back and Run again. The exercise
        page then lists its own runs (owner, the same day: "the record is in Tasks, but
        that means leaving the exercise"): every logged attempt, newest first, with its
        counts, duration and snapshot name, never its first error (as the dashboard's
        runs: a hand-calculation answer can sit in it; the task's log has it), and the
        test tasks started from the page, linked to their logs.
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

The GUI in other languages: built 2026-11-10 (see "The GUI" below, "The pages in another
language"); the owner's decision that day: the repository ships English only, and a model
writes any other language on the Language page. Translated `question.md` above stays deferred.

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
