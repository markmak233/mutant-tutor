# Design: Local GUI

The pages, access, tasks, generation, lectures and coverage. Part of the design record; the index of every part is [README.md](README.md).

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
  Without `--root`, the course is looked for in this order (2026-11-16, after a course
  `.venv`'s `tutor gui` would not start without `--root`): `WORKSPACE_DIR` in the
  environment; the course the command runs in (a `workspace.toml` at or above the current
  folder: a course's VS Code terminal sets `WORKSPACE_DIR` on macOS only); `WORKSPACE_DIR`
  in the current folder's `.env`; then in the checkout's own `.env`, from any folder, as
  the attempts plugin already read it. Each `.env` is read for that one name. An installed
  tool, which has no checkout, falls back to its per-user courses folder.
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
  off. The batch's own cap was left at 32,000 then (raising it would pay for longer thinking
  on every item); on 2026-11-11 the owner raised the default to 64,000 and this cap to
  128,000 (graph/settings.py says why: DeepSeek Flash's cut-offs on the deep-learning course,
  6 of 22 and 6 of 15 first drafts, and one of Sonnet's on a long lecture; a cap is not a
  cost). "Try all" keeps the batch's cap. Clicked in headless Chrome on a scratch
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
- Task queue (owner, 2026-11-09 the wish, 2026-11-10 built). A course runs one task at a
  time, for the reasons `gui/tasks.py` states (validation's own parallelism, two batches
  pushing runs past their limits), and before this a second click was refused with "a
  task is running" and every button of the course hidden until the task ended: the daily
  friction while a long task ran. Now `tasks.start` writes the task as `queued` and
  `kick` starts the oldest queued task when nothing runs; the runner kicks the queue at its
  end, so the next task starts with no GUI around (a task already went on if the GUI
  stopped), and a page reading the course kicks it too, for a queue a dead runner left.
  Two processes can notice the queue at once, so a task starts only for the one that
  creates its `claimed` file first (`O_EXCL`). Its `started` time is when it really
  started. The course page lists the queue, next first, each with Remove (a queued task is
  deleted whole: its log is empty); a queued task's page says what it starts after, polls
  like a running one and has the same Remove; the notice after a click says it was queued.
  A failed task does not hold the queue. Decided with the owner: still one at a time (the
  queue removes the friction; parallel kinds can come later); a paid task queued runs when
  its turn comes without a second click ("加入队列他已经点过一次了": the click that queued
  it is the consent), so the queue shows it and Remove is there until it starts. What a
  queue cannot do: a task's preconditions are checked at the click, not at the start (a
  retry's item file is written at the click; the graph command refuses a plan that moved).
  The plan page keeps its own rule: a task of this plan, running or queued, takes its
  buttons away (one exercise is not generated twice), another course task no longer does;
  the plan's state is "queued" and its table stays visible. Pages that disable buttons
  while a task runs for their own reasons (the Environment page's install and build, the
  doctor's steps) keep doing so. Clicked in headless Chrome on a scratch course with the
  stand-in graph slowed down: a second plan queued behind the first plan's generation, the
  course page's list and the task page's place, Remove, a third plan starting by itself
  when the generation ended.
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
  interrupted exercise's `validation.json` not rewritten. Windows (2026-11-11, found and
  then verified with a Remote Control session on the owner's PC; Stop had never worked
  there): no signal does it. A venv's python.exe is a launcher that runs the interpreter as
  its child, so the runner's own pid is not the process group's, and CTRL_BREAK_EVENT to it
  went to the whole console (it killed pytest; in the GUI `/stop` was a 500, WinError 87);
  and a Python blocked in `wait()` does not wake for SIGBREAK. So there `tasks.stop` writes a
  `stop` file in the task's folder, the runner polls the command every 0.25 s and takes the
  file as Ctrl+C, each command runs in a process group of its own, Ctrl+Break goes to that
  group alone, and `taskkill /T /F` ends what is left after the grace time. Verified on the
  PC: `test_gui_tasks.py` whole (21 passed, the 3 ps-based tests skipped), `/stop` 303 and
  the task `stopped` within 0.5 s, the server alive, the queue moving on. Not exercised
  there: the taskkill path (no command ignored Ctrl+Break).
  A Stop inside `Popen` (POSIX, 2026-11-16, read in CPython's subprocess, not seen): Popen
  has forked and waits a few ms for the exec when Ctrl+C raises out of it, so the command
  runs and the runner holds no Popen for it; the runner stopped nothing and the command
  outlived it. Now, when the runner has no running Popen but processes below it, it stops
  them the same way and reaps its own child with `waitpid`. The `ps` it runs is left out
  of the list: it had listed itself (measured), sending signals to a freed pid.
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
  PDF with what the cache holds (not read, read, image pages waiting) and, since
  2026-11-18, what its pages are cited by: its own slide numbers, or PDF pages when it
  prints none (layout.md, "Slide numbers or PDF pages"). That column changed the page, so
  `docs/images/lectures.png` is to be retaken on the demo courses. Checked by fetching the
  pages from a running server on a scratch copy of a course (the Lectures table and a
  waiting plan's row, "PDF pages 7-9"); it is a column and a label, no new action, and no
  click was made. Uploads: PDFs only,
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
  - Deleting a plan (owner, 2026-11-11, replacing 11-07's "hide the folder rather than
    delete it": "just delete it"; built 2026-11-16). Found when a `w3` planned on 11-10
    was never approved: its draft kept the label, so the next New batch became `w3-1`,
    and the plan page had no way to drop it. The plan page offers "Delete plan" only for a
    plan nothing of which reached `batches/` (no batch folder: no exercise, so no student
    work) and with no task of it running or queued; the box, then the button, as the
    GUI's other removals. It removes `batches/.drafts/<date>_p3-<label>/` whole (plan, the
    checkpoint, item drafts of a generation that saved nothing), never through a link;
    the task logs stay. The course page then says the name is free again. Checked by the
    owner's click in a browser on a scratch course (2026-11-16): the plan and its draft
    folder went.
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
- Checks after generation, on the batch page (owner, 2026-11-17; commands only until then):
  "Judge teaching quality" (`python -m tutor.judge <batch>`), "Write held-out solutions"
  (`tutor.heldout_writer`) and "Run held-out check" (`tutor.validation.heldout`), each a task
  on the whole batch (`gui/after_checks.py`, `routes/checks.py`). The held-out check is two
  buttons as it is two steps: writing spends tokens and runs nothing, checking runs tests and
  calls no model, and the owner's own student files are held-out material without the first.
  - A button that calls a model says which one. A course that names no such role gets a link
    to its Models page and no button (the rule under "Models": the GUI does not read `.env`,
    so it cannot tell whether a fallback exists). The judge is not offered for a C course: its
    prompt and calibration are Python's.
  - The table gains two columns, verdicts only: the judge's four letters and how many checks
    the held-out run flagged. The reasons (the judge's evidence, a check's output) quote the
    question, the reference and test output, so they stay in the task's log; of `solutions/`
    a page still reads verdicts and nothing else.
  - The judge's letters show only for an exercise the owner has rated: the ratings are what
    the judge is calibrated against, and a rating given after reading its verdict would only
    agree with it. Before that the cell says "judged; shown once you rate it". The judge
    task's log does show the verdicts, so the page says to rate first.
  - What a run did is said beside its button (owner, 2026-11-17: the first click's effect was
    not plain to see): how many exercises are judged and how many of those verdicts show, how
    many the held-out check ran on and flagged, and a link to the last run's log. A cell with
    an n names the points answered no ("no on: clear"), which is still a verdict. A held-out
    flag is something to read, not an invalid exercise, and the line says so.
  - A finished task goes back to the batch with a notice. The held-out check exits 1 when it
    flags something, so that task stays on its log, as a failed validation does. All three
    can be run again from a failed task: the two that call a model keep what is unchanged.
- Hints on the exercise page (owner, 2026-11-18; a command only until then). The page has
  two tabs, Question and Hints, each a page of its own (`/e/<id>/` and `/e/<id>/hints/`):
  links, so no script is needed and a finished task can land on the Hints tab
  (`gui/hints_page.py`).
  - The exercise's `hints.md`: it sits in the exercise folder beside the question, so it is
    the learner's side and the page may show it. What is not a numbered hint (the useful
    functions) is shown; each "Hint N" is closed until opened, in learn and review alike, and
    a review batch adds a line asking to try first. Rendered as the question is, raw HTML off.
  - "Check my code" runs `python -m tutor.hint <exercise folder>` as a task: the learner's
    tests in a temporary copy, not recorded as a run, the student file untouched; then a
    rule, a known mistake, or the course's `hint` model. The page says where the code goes: to
    no provider when the course names no `hint` model, else to the model it names, with a box
    "without the model" (`--no-llm`). The mode's rule (a review batch is told only what goes
    wrong) is the command's own.
  - The latest hint is shown as the command printed it, as plain text; earlier ones are links
    to their logs. Nothing of `solutions/` is read by the page: the command reads the mutants,
    as it does from a terminal.
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
    footers, so its ranges are PDF pages; generation sent such a lecture whole (until
    2026-11-18: generation.md, the "p.12-p.13" paragraph).
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
