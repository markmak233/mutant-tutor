# Setup: big data course (Spark, MRJob, Hadoop)

Everything needed to run the tests, validate the examples and solve the big data course exercises on a
fresh machine. Verified on macOS (Apple silicon, Homebrew). The Linux / WSL commands are
the standard equivalents and have not been tested yet.

## 1. What to install

| Tool | Version | Needed for | Required? |
|---|---|---|---|
| Python | 3.11 (3.12: see below) | everything | yes. Not 3.13: mrjob imports the removed `pipes` module |
| Java (JDK) | 17 | Spark, and Hadoop if installed; one JDK serves both | yes |
| Hadoop | 3.3.x, standalone | MRJob exercises with a custom partitioner or sort order (e.g. `examples/station-condition-share`) | no. Without it those tests are skipped |
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

### Windows, untested

Use WSL2 with Ubuntu and follow the Linux steps inside it. Spark and Hadoop on native
Windows need extra binaries (`winutils.exe`) and are not supported here.

## 3. Python environment

```bash
git clone https://github.com/markmak233/mutant-tutor.git && cd mutant-tutor
python3.11 -m venv .venv && source .venv/bin/activate   # name the version: plain python3 may be 3.13
pip install -e ".[bigdata,dev]"
cp .env.example .env
```

Python 3.12 works too, with one extra step: run `pip install setuptools` in the venv.
mrjob 0.7.4 imports `distutils`, which 3.12 removed, and 3.12 venvs no longer come with
setuptools, whose `distutils` shim fills the gap. Without it the mrjob tests fail with
`No module named 'distutils'`. CI tests 3.11 only.

Edit `.env`. Set only what you have, and **comment out the lines you do not use**: a variable
left at its `/path/to/...` placeholder counts as set, so the checks that need it run and fail
instead of being skipped.

| Variable | Set when | Value |
|---|---|---|
| `JAVA_HOME` | always | the JDK 17 directory from step 2 |
| `HADOOP_HOME` | Hadoop is installed | e.g. `~/opt/hadoop-3.3.6`, written as a full path |
| `WORKSPACE_DIR` | you have a course workspace (step 5) | the workspace folder |

Optional, before your first commit: `pre-commit install` (needs gitleaks).

## 4. Check it works

```bash
pytest                 # fast unit tests, about 30 s; all pass (test_env skips without WORKSPACE_DIR)
ruff check .           # All checks passed!
pytest -m spark        # validates every example end to end: about 8 min with Hadoop;
                       # without HADOOP_HOME about 3 min, and the 5 Hadoop tests show as skipped
```

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

## 6. Common problems

| Symptom | Cause | Fix |
|---|---|---|
| `No module named 'pipes'` | the venv or Hadoop task runs Python 3.13 | recreate the venv with `python3.11` (or 3.12) |
| `No module named 'distutils'` | Python 3.12 venv without setuptools | `pip install setuptools` |
| `Java gateway process exited` / `JAVA_HOME is not set` | Spark cannot find the JDK | set `JAVA_HOME` in `.env` to a JDK 17 |
| `test_env` fails with `/path/to/...` | a placeholder left in `.env` | set the real path or comment the line out |
| Hadoop tests fail although Hadoop is not installed | `HADOOP_HOME` placeholder left in `.env` | comment the line out |
| `yarn` runs Hadoop instead of JavaScript yarn, or the reverse | `brew install hadoop` | uninstall it; use the tarball |
| An MRJob job on Hadoop writes no output | the job file lacks `if __name__ == "__main__": MyJob.run()` | add it (the test helpers report this) |
