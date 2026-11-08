# Setup: big data course (Spark, MRJob, Hadoop)

Everything needed to run the tests, validate the examples and solve the big data course exercises on a
fresh machine. Verified on macOS (Apple silicon, Homebrew) and on native Windows 11. The
Linux / WSL commands are the standard equivalents and have not been tested yet.

## 1. What to install

| Tool | Version | Needed for | Required? |
|---|---|---|---|
| Python | 3.11 to 3.13 (see below) | everything | yes. Not 3.14: PySpark 3.5 does not run on it |
| Java (JDK) | 17 | Spark, and Hadoop if installed; one JDK serves both | yes |
| Hadoop | 3.3.x, standalone | MRJob exercises with a custom partitioner or sort order (e.g. `examples/station-condition-share`) | no. Without it those tests are skipped. On Windows they are always skipped (below) |
| winutils | for Hadoop 3.3.x | Windows only: Spark reading and writing files | yes, on Windows |
| Docker | Docker Desktop (tested with 4.25 on macOS) | running exercise code in a container (step 5) | no, but recommended before generating exercises. Not tested on Linux or Windows yet |
| gitleaks | any | the pre-commit secret scan | only if you commit to the repo |

Spark itself needs no separate install: `pip install pyspark` ships it.
Hadoop runs in standalone (local) mode: no HDFS, no YARN, nothing to configure after unpacking.

## 2. System packages

### macOS (Homebrew)

```bash
brew install python@3.11 openjdk@17
brew --prefix openjdk@17       # JAVA_HOME is this + /libexec/openjdk.jdk/Contents/Home
```

Hadoop (optional): download the Apache tarball. Do **not** `brew install hadoop`: its `yarn`
command clashes with the JavaScript `yarn`.

```bash
mkdir -p ~/opt && cd ~/opt
curl -LO https://archive.apache.org/dist/hadoop/common/hadoop-3.3.6/hadoop-3.3.6.tar.gz
tar xzf hadoop-3.3.6.tar.gz    # about 700 MB unpacked; HADOOP_HOME is ~/opt/hadoop-3.3.6
JAVA_HOME="$(brew --prefix openjdk@17)/libexec/openjdk.jdk/Contents/Home" \
  ~/opt/hadoop-3.3.6/bin/hadoop version     # should print Hadoop 3.3.6
```

### Linux (Ubuntu / Debian), untested

```bash
sudo apt install openjdk-17-jdk
sudo apt install python3.11 python3.11-venv   # Ubuntu 22.04; on 24.04 use the system 3.12 + setuptools
ls /usr/lib/jvm/                              # JAVA_HOME is e.g. /usr/lib/jvm/java-17-openjdk-amd64
```

Hadoop: the same tarball commands as macOS, with your `JAVA_HOME`.

### Windows (native)

In PowerShell:

```powershell
winget install Python.Python.3.11
winget install Microsoft.OpenJDK.17
py -3.11 --version     # plain `python` may be the Microsoft Store 3.13, which mrjob cannot use
```

`JAVA_HOME` is the JDK folder, e.g. `C:\Program Files\Microsoft\jdk-17.0.x-hotspot`.

Spark needs two Hadoop binaries on Windows to read and write files, even without Hadoop
jobs: `winutils.exe` and `hadoop.dll`. Unpack the Hadoop tarball (the same file as macOS;
`tar` ships with Windows) and put both binaries in its `bin`. They come from the
`hadoop-3.3.x` folder of a winutils build such as
[cdarlint/winutils](https://github.com/cdarlint/winutils).

```powershell
mkdir $HOME\opt; cd $HOME\opt
curl.exe -LO https://archive.apache.org/dist/hadoop/common/hadoop-3.3.6/hadoop-3.3.6.tar.gz
tar xzf hadoop-3.3.6.tar.gz
# copy winutils.exe and hadoop.dll into $HOME\opt\hadoop-3.3.6\bin, then:
[Environment]::SetEnvironmentVariable("HADOOP_HOME", "$HOME\opt\hadoop-3.3.6", "User")
[Environment]::SetEnvironmentVariable("Path",
  [Environment]::GetEnvironmentVariable("Path", "User") + ";$HOME\opt\hadoop-3.3.6\bin", "User")
```

Open a new shell (and restart VS Code) so the new `PATH` is seen. `HADOOP_HOME` in `.env`
is not enough: Windows looks up `hadoop.dll` on `PATH`. `python -m tutor.envcheck` (step 5)
checks both and prints a `winutils` line.

What does not work natively:

- **mrjob's Hadoop runner.** `hadoop.cmd` splits `-files` at commas, so the jobs cannot be
  submitted. Tests that need it are skipped, and the validator reports an exercise with
  `runner = "hadoop"` as SKIPPED, which counts as INVALID. Validate and solve those
  exercises in WSL2 (Ubuntu, the Linux steps above). Spark and the other MRJob exercises
  (inline and local runners) work natively.
- **Speed.** Starting Spark is slower than on macOS, so every test and validator timeout is
  multiplied by 4 on Windows. Set `TUTOR_TIMEOUT_FACTOR` (e.g. `6`) if runs still time out
  on a busy machine.

For the gitleaks pre-commit scan: `winget install Gitleaks.Gitleaks`, then open a new shell.

## 3. Python environment

```bash
git clone https://github.com/markmak233/mutant-tutor.git && cd mutant-tutor
python3.11 -m venv .venv && source .venv/bin/activate   # name the version: plain python3 may be 3.13
pip install -e ".[bigdata,dev]"
cp .env.example .env
```

On Windows (PowerShell): `py -3.11 -m venv .venv`, then `.venv\Scripts\Activate.ps1`
(the other commands are the same; use `copy` for `cp`).

Python 3.12 and 3.13 work too. mrjob 0.7.4 imports `distutils`, which 3.12 removed, and
`pipes`, which 3.13 removed; the `bigdata` extra installs setuptools (whose shim provides
`distutils`) and standard-pipes (the removed module as it was) on the Pythons that need them,
and so does a course's `.venv` and test image when `[requires]` has mrjob. CI tests 3.11 to
3.14; Python 3.14 is refused for a course with PySpark 3.5, which fails there.

To run a PySpark program directly (`python student.py <input> <output>`), also tell Spark
which Python its workers use; otherwise they start the first `python3` on PATH, which may
be another version than the venv, and the job fails with `PYTHON_VERSION_MISMATCH`. The
tests and the validator set this themselves.

```bash
export PYSPARK_PYTHON="$(which python)"   # after activating the venv; add it to your shell profile or venv activate script
```

```powershell
$env:PYSPARK_PYTHON = (Get-Command python).Source   # Windows PowerShell, after activating
```

Edit `.env`. Set only what you have, and **comment out the lines you do not use**: a variable
left at its `/path/to/...` placeholder counts as set, so the checks that need it run and fail
instead of being skipped.

| Variable | Set when | Value |
|---|---|---|
| `JAVA_HOME` | always | the JDK 17 directory from step 2 |
| `HADOOP_HOME` | Hadoop is installed | e.g. `~/opt/hadoop-3.3.6`, written as a full path |
| `WORKSPACE_DIR` | you have a course workspace (step 5) | the workspace folder |

Optional, before your first commit: `pre-commit install` (needs gitleaks). On Windows,
GitHub Desktop finds gitleaks only after a restart that follows its install.

## 4. Check it works

```bash
pytest                 # fast unit tests, about 30 s; all pass (test_env skips without WORKSPACE_DIR)
ruff check .           # All checks passed!
pytest -m spark        # validates every example end to end: about 8 min with Hadoop;
                       # without HADOOP_HOME about 3 min, and the 5 Hadoop tests show as skipped
```

On Windows the fast tests take several minutes (each starts processes, which is slower
there), and the Hadoop tests in `pytest -m spark` are always skipped.

Validate one example and solve one yourself:

```bash
python -m tutor.validation examples/station-average-rdd/exercise examples/station-average-rdd/solution
# ... VALID

cd examples/station-average-rdd/exercise
pytest test_student.py                  # 7 failed: the skeleton is empty
# write average_by_station in student.py, then:
pytest test_student.py                  # 7 passed
TUTOR_PARTITIONS=4 pytest test_student.py
```

## 5. Your own course workspace (optional)

Lectures, generated exercises and attempt logs live in a workspace **outside any git
repository** (the code refuses one inside a repo, so course material is never committed).

```bash
mkdir -p ~/smartcodereview-workspace/bigdata/lectures   # put the lecture PDFs here
```

Create `~/smartcodereview-workspace/bigdata/workspace.toml`:

```toml
course = "bigdata"

[requires]
python = "3.11"     # write the version you installed, e.g. "3.12"
java = "17"
pyspark = "3.5"
mrjob = "0.7"
hadoop = "3.3"      # delete this line if you skipped Hadoop
```

Set `WORKSPACE_DIR` in `.env` to that folder, then:

```bash
python -m tutor.envcheck     # one line per [requires] entry, with a hint for each failure
```

Exercises are generated into `batches/` inside the workspace. Until phase 3 (LLM generation),
they are drafted with Claude Code from the lectures and checked with `python -m tutor.validation`.

### A venv and VS Code terminal for this workspace

Give each workspace its own venv, as a package one course needs can change another's results (numpy
changes PySpark's `StatCounter`). With this repository's venv active:

```bash
python -m tutor.workspace init ~/smartcodereview-workspace/bigdata --extras bigdata,gen,dev
```

It creates what is missing and keeps every existing file, so the `workspace.toml` above stays: the
`lectures/` folder, a minimal `workspace.toml`, `.venv` with this package and the extras named, and the
`.vscode` files below. Run it again to finish an install that failed or to add extras. It changes nothing
outside the folder, and installs no system software.

Tests run outside this venv still pass, but nothing records them: the attempts plugin is installed only
here, and it gives no warning when it is missing. VS Code's own activation did not reach our terminals on
macOS, and the `conda` base environment stayed first on `PATH`. So `.vscode/settings.json` points `ZDOTDIR`
at `.vscode/zsh`, whose `.zshrc` loads your `~/.zshrc` and then activates the venv. `~/.zshrc` is not
changed. The settings also set `WORKSPACE_DIR` and `LECTURE_DIR`, which win over `.env`: it can name only
one workspace, and the plugin skips tests in any other, again without a warning.

Open the workspace folder itself in VS Code, open a new terminal and check:

```bash
which pytest     # must be this workspace's .venv/bin/pytest
```

This covers VS Code terminals on macOS only. Anywhere else, run `source .venv/bin/activate` first.

### Running exercise code in a container (recommended)

Validation runs the exercise's code, and generated code is written by a model, so let every run
happen in a fresh Docker container (README, "Where exercise code runs"). Install and start Docker
Desktop yourself: the tool only calls the `docker` command and never installs it. Then, in this
workspace's terminal (the image takes this venv's package versions):

```bash
python -m tutor.container build    # the image from [requires]; with Hadoop about 1.7 GB, 4-5 minutes
python -m tutor.container status   # "ready" once the image matches [requires]
```

and add to `workspace.toml`:

```toml
[isolation]
runner = "container"
```

From then on validation, generation and hints stop with an error when Docker is not running, rather
than run the code on this machine. A changed `[requires]` needs a new build, and `status` says so.
Your own `pytest test_student.py` still runs in this venv. Docker Desktop's Resource Saver stops its
VM when idle; the first run after that waits a few seconds for it, outside the test's time limit.

### Reading the lectures into text (optional)

```bash
python -m tutor.materials "$WORKSPACE_DIR/lectures"            # text layer, about a second
python -m tutor.materials --vision "$WORKSPACE_DIR/lectures"   # also slides that are images
```

The text goes to `$WORKSPACE_DIR/cache/lectures/<file>.pdf.md`, one section per page; a page
is read again only when it changes. Slides that are images (code screenshots, pseudocode,
diagrams) stay marked `pending` until a run with `--vision` transcribes them with a vision
model through an OpenAI-compatible API. A local model keeps the slides on your machine:

1. Install [LM Studio](https://lmstudio.ai), download a vision model (tested: Gemma 4
   26B-A4B, about 15 GB; needs about 32 GB of memory), and start the server on its
   Developer page (or `lms server start`).
2. Name the model in `workspace.toml` and give it the `vision` role:
   ```toml
   [llm.profiles.gemma-local]
   provider = "lmstudio"                    # base URL http://localhost:1234/v1
   model = "google/gemma-4-26b-a4b-qat"     # the id LM Studio shows

   [llm.roles]
   vision = "gemma-local"
   ```

A cloud model is one more profile. Its key goes in `.env` only, and the profile names the
variable (a key written into `workspace.toml` is refused):

```toml
[llm.profiles.glm-flash]
provider = "glm"                            # Zhipu bigmodel.cn
model = "glm-5.3-flash"
api_key_env = "ZHIPU_API_KEY"               # .env: ZHIPU_API_KEY=...
```

Use it for one run with `--vision --profile glm-flash`, or make it the `vision` role. The
slide images are then sent to that provider. Other OpenAI-compatible servers use
`provider = "custom"` with a `base_url`. About 7 s per image page with the local model on an
M2 Pro; every call is logged in `$WORKSPACE_DIR/logs/llm_calls.jsonl` (never the key).

### Generating exercises (optional, phase 3)

Needs the lectures read into the cache (above), a model for the `generate` and `plan`
roles, and the `gen` extra:

```bash
pip install -e ".[bigdata,dev,gen]"
```

```toml
[llm.roles]
generate = "glm-flash"
plan = "glm-flash"
```

OpenAI is `provider = "openai"` with `api_key_env = "OPENAI_API_KEY"` (the key in `.env`).
An `oracle` role (e.g. `oracle = "gpt-terra"`, another provider than `generate`) has that
model write each exercise's oracle from the question alone, so validation rule 4 compares
two independent readings; without it the generating model writes the oracle too.
DeepSeek is `provider = "deepseek"` (e.g. `model = "deepseek-v4-flash"`,
`api_key_env = "DEEPSEEK_API_KEY"`); its API has only JSON mode, so the schema goes into the
prompt and the validator still checks the reply.

```bash
python -m tutor.graph plan --lectures Lecture_4.1-Spark_I Lecture_4.2-Spark_II --batch w4 \
    --prefs "one or two debugging exercises"
# shows the plan and pauses; edit batches/.drafts/<batch>/plan.json if you like, then
python -m tutor.graph resume --batch w4 --approve      # or --feedback "..."
```

The plan takes the knowledge points marked `gap-code` in the workspace's `coverage.md`
(one exercise each, at most `--max-count`, or exactly `--count`) and follows the course
facts in `conventions.md`. Approved exercises are generated a few at a time, each checked
by the validator and repaired up to three times; only VALID ones reach the batch. A run
that stopped midway continues with `resume`; `resume --profile NAME` finishes it with
another model. `coverage-proposal.md` in the batch lists the coverage.md changes to make.

Tunable values (repair rounds, thinking per role, token limits, ...) have defaults; to
change them for every batch, add a `[generation]` table (the names, meanings and bounds are
in `src/tutor/graph/settings.py` and `schemas/generation-settings.schema.json`), or change
one run with `--set`:

```toml
[generation]
max_rounds = 3
stronger_repair_from = 1    # the repair model from the first repair on
```

```bash
python -m tutor.graph plan ... --set max_rounds=2
python -m tutor.graph resume --batch w4 --set max_rounds=4   # for the rest of the batch
```

No API key: a profile with `provider = "assistant"` lets a coding assistant (Claude Code,
Codex) answer every call as request files; `docs/assistant-mode.md` has the setup and the
loop it follows.

A `hint` role (e.g. `hint = "deepseek"`) adds a model's hint to `python -m tutor.hint`
when no rule and no known mistake fits. The model gets your `student.py`, `hints.md`, the
constraints and the first error, never the solution; a hint that shows solution code is
rejected and asked for again. Asking again without changing your code gives a more
specific hint (three levels; a review batch never gets code). `--no-llm` skips the model.

## 6. Common problems

| Symptom | Cause | Fix |
|---|---|---|
| `No module named 'pipes'` | a Python 3.13 venv made before the `bigdata` extra brought standard-pipes, or a Hadoop task running another Python | run `pip install -e ".[bigdata,dev]"` again (a course: `python -m tutor.workspace init <course>`); for Hadoop, `--python-bin` |
| `No module named 'distutils'` | a Python 3.12 venv made before the `bigdata` extra brought setuptools | the same |
| `PicklingError: Could not serialize object: RecursionError` | PySpark 3.5 on Python 3.14 | recreate the venv with Python 3.13 or older |
| `Java gateway process exited` / `JAVA_HOME is not set` | Spark cannot find the JDK | set `JAVA_HOME` in `.env` to a JDK 17 |
| `PYTHON_VERSION_MISMATCH` when running a PySpark program directly | Spark workers start another `python3` than the venv's | `export PYSPARK_PYTHON="$(which python)"` (step 3) |
| `test_env` fails with `/path/to/...` | a placeholder left in `.env` | set the real path or comment the line out |
| Hadoop tests fail although Hadoop is not installed | `HADOOP_HOME` placeholder left in `.env` | comment the line out |
| `yarn` runs Hadoop instead of JavaScript yarn, or the reverse | `brew install hadoop` | uninstall it; use the tarball |
| Windows: `HADOOP_HOME and hadoop.home.dir are unset`, or `UnsatisfiedLinkError ... NativeIO$Windows.access0` | Spark cannot find `winutils.exe` or `hadoop.dll` | put both in `%HADOOP_HOME%\bin`, add that folder to `PATH`, open a new shell; `python -m tutor.envcheck` says which is missing |
| Windows: `No module named 'pipes'` although Python 3.11 is installed | the venv was made with the Store `python` (3.13) | recreate it with `py -3.11 -m venv .venv` |
| Windows: Hadoop exercise reported SKIPPED / INVALID | mrjob's Hadoop runner does not work on native Windows | validate it in WSL2 |
| An MRJob job on Hadoop writes no output | the job file lacks `if __name__ == "__main__": MyJob.run()` | add it (the test helpers report this) |
