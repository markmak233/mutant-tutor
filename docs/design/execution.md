# Design: Code execution

Where exercise code runs, the container, C, and version conflicts. Part of the design record; the index of every part is [README.md](README.md).

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

Credentials stay out of a host run's environment (2026-11-18, found in a review of what a
lecture carrying instructions could reach). The validator and the generation pipeline load
`.env`, the models' keys included, and until then a host run inherited all of it, while layer
A lets exercise code read `os.environ`. The child now gets the environment without every
variable named like a credential (KEY, TOKEN, SECRET, PASSWORD, CREDENTIAL or APIKEY as a
whole part of the name; `runtime_env.without_credentials`) and without those a profile or a
provider card of the run's course names in `api_key_env`. A denylist, not the allowlist the
doctor's probes use: Spark, Hadoop and a C toolchain read variables no fixed list covers
(JAVA_HOME, HADOOP_HOME, SPARK_*, CC), and a missing one would turn a valid exercise into an
ERROR. The learner's own pytest is covered the same way: the attempts plugin and the Hadoop
runner in testkit load `.env` without its credentials (`load_env(credentials=False)`). What
this does not cover: a key the learner exported in the shell before running pytest, and the
`.env` file itself, which code on the host can still open by its path. The container, which
gets nothing of the host's environment or files, remains the boundary.

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
  - Four more rules (2026-11-18, from a review that tried the check against a lecture
    carrying instructions; each line below passed until then, measured with
    `find_hazards`). Of `tutor`, only the test kit (`tutor.testkit`, `tutor.testkit_c`) and
    the names it defines itself, read from its source with `ast`: the whole package was on
    the allowlist, so `from tutor.execution import subprocess` handed over any module one
    of its files imports, and `from tutor import config, llm` the way to the keys. Of
    `pytest`, only `approx`, `fixture`, `raises`, `mark`, `param`, `fail`, `skip` and
    `warns`: `pytest.importorskip("subprocess")` imports any module without an import
    statement. No attribute on any object, and no name imported from another module, called
    `os`, `_os`, `sys`, `_sys`, `subprocess`, `importlib`, `builtins`, `modules`, `system`,
    `popen`, `Popen` or `import_module`: the standard library's modules import `os`
    themselves, so `pathlib.os.system(...)`, `os.path.os`, `from tempfile import _os` and
    `from pathlib import os` needed no forbidden import. No py4j handle (`_jvm`, `_jsc`,
    `_gateway`, `_jsparkSession`, `_jdf`, `_jrdd`, `_jconf`): `sc._jvm` reaches every Java
    class, `Runtime.exec` included. The first two are allowlists, as what exercises use of
    both is small; the last two name what is known to be dangerous, since an exercise's own
    attribute names are without limit. Counted before the change: of 3769 `.py` files in two
    courses' batches and the examples, none uses anything these rules refuse (the kit
    modules and `approx`, `fixture`, `raises`, `mark` are all they use). The generation
    prompt does not state them; a draft that breaks one is INVALID and goes to repair.
    Still open, and not closable by reading code: a path built when the code runs
    (`Path(os.environ["HOME"]) / ".ssh"`), and any name the lists do not hold.
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
    (arm64) did not reproduce it in 40 runs. Docker Desktop's WSL 2 VM on the owner's Windows
    PC (x64, kernel 6.6) uses 32 bits and did reproduce it (2026-11-16): `pytest -m docker`
    had six C tests time out now and then, a looped ASan run printed "DEADLYSIGNAL" without
    end, and a rerun failed different ones. CI prints the value and sets 28 (see "CI"); for a
    user on such a Linux host, `tutor doctor` ("Roadmap" 6) now checks the value in C's
    registered machine check and prints the `sysctl` command (2026-11-09); it never runs it.
    With 28 set, the next CI run (2026-11-08) passed every C test, and
    nothing else about C had changed; as the hang is intermittent, one run is strong evidence,
    not proof.
    - Measured and fixed (owner, 2026-11-16). On the Windows PC, a do-nothing program built
      with the tests' flags and run 300 times under the 5 s limit: gcc 12 (bookworm) at 32
      bits hung 67 times (68 in a second sample), each hang printing only
      "AddressSanitizer:DEADLYSIGNAL"; gcc 12 at 28 bits, 0; gcc 14.2 (Debian trixie) at
      32 bits, 0. Running the program with randomisation off (`setarch -R`) is no way round
      it: Docker 29.6.1's default seccomp profile refuses that `personality()` call (Docker
      24.0.6 on the Mac allowed it), and widening seccomp for every run is not worth it. A
      sysctl in Docker Desktop's VM is lost when Docker restarts, so it is no fix for a
      learner either. So gcc's major picks the image's Debian release
      (`image_recipe.DEBIAN_BY_GCC`): `gcc = "14"` builds on trixie, `"12"` stays on
      bookworm, and a course without C keeps its recipe and image name. Trixie packages only
      JDK 21, so gcc 14 with java 17 (a Spark course) is refused before a build. Adding C
      now writes gcc 14. gcc 14 also makes some old warnings errors (incompatible pointer
      types, implicit int, int-conversion): such code no longer compiles, which the owner
      accepted (2026-11-16). For a course still on gcc 12, a timed-out run whose output has
      DEADLYSIGNAL and no sanitizer report says the sanitizer hung, not the learner's loop
      (`testkit_c.ASAN_HUNG`), and `tutor doctor` reads the value where the tests run: in
      the course's built image (as root, the file is root-only; no network, nothing pulled)
      for the container, which also covers Docker Desktop's VM on macOS and Windows, or in
      /proc on a Linux host, saying `sudo cat` when it is root-only there. It warns above 28
      with gcc 14 as the fix. A gcc 14 course gets no row. Checked on the Windows PC at 32
      bits (2026-11-16, 93a0ace): `pytest -m docker` passed twice in a row (32 passed each;
      before the fix, up to six C tests timed out in a run), and `tests/test_c_real.py`
      with the gen extra passed twice too (5 passed each), with no C run timing out.
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
      standard library's, each a plain `name.h` path) and `gcc = "14"` in [requires] when
      missing (the image's Debian gcc: trixie's, see the AddressSanitizer issue above; 12,
      bookworm's, until 2026-11-16). Checked writes as every other table, the file before
      kept as `.bak`.
    - What the lectures' code uses beyond the core modules (owner, 2026-11-10; the plan
      had a model drafting `[safety] allow_imports` and `[languages.*]` at a course's
      creation, each entry with its source, accepted one by one). The [requires] draft
      already proposes the pip packages, and ticking one allows its module; `[languages.c]
      flags` already come from the lectures' gcc lines. What was left out: a module that
      is no pip package (`subprocess`, `socket`, `threading`, `struct`: the standard
      library, which the draft leaves out on purpose) was never proposed, so a course that
      teaches it got exercises the static check refused; likewise a header a course ships.
      Import and include lines are exact text, so `materials/imports_found.py` reads them
      with a regular expression, no model (as the C flags): every module the cached
      lectures' code imports beyond the core set, the frameworks' and `allow_imports`, and
      every header beyond the standard ones and `allow_headers`, each with the lecture,
      page and line it was seen on, most-cited first. Step 1 lists them with a tick each
      and "Allow the ticked", which adds to `[safety] allow_imports` / `allow_headers`
      through the checked write (`.bak` kept); a name that starts processes, reaches the
      network, loads code or touches the interpreter is marked risky, with what allowing
      it means. Never ticked by code. A prose line that starts with "import" shows up too
      (one did), with its line, and the owner skips it. On the real courses it found one
      prose line on the Spark course and the two headers the C course ships.
      `[languages.*]` beyond C waits for a second language. Clicked in headless Chrome on
      a scratch course: the three rows, two ticked and allowed, the risky one left.
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

