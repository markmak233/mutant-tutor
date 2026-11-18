# Design: CI

The GitHub Actions jobs. Part of the design record; the index of every part is [README.md](README.md).

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
- Runs when work reaches `main` (a push after merging `dev`, or a pull request into `main`),
  by hand (`workflow_dispatch`), and inside a release run (`release.yml` calls it, see
  "Packaging and release"), not on every `dev` push: each step is already checked
  locally with `pytest` and `ruff check .`, and the badge then shows the state of `main`,
  the branch visitors see.
- Every job runs with a read-only token (`contents: read`), in `ci.yml` and `release.yml`
  alike. One exception since 2026-11-18: `release.yml`'s `publish` job, which runs only when
  the owner ticks that input, has `contents: write` to create the GitHub Release page (see
  "Packaging and release").
- `test`: Python 3.11, 3.12, 3.13 and 3.14, `ruff check .` and the fast `pytest`. The first
  run also had 3.12, which failed on mrjob's `distutils` import (see "Environment"), and the
  owner dropped it (2026-10-26). Brought back with 3.13 and 3.14 (owner, 2026-11-08): the
  `bigdata` extra now brings setuptools (`distutils`) and, from 3.13, `standard-pipes`, and
  `requires-python` lost its `<3.13`. Measured locally first, fast suite and `-m spark`
  (mrjob, PySpark 3.5.9 and Hadoop) on each. 3.15 waits until the dependencies have wheels
  for it. Only `test` runs every version; `other-os`, `docker` and `spark` stay on 3.11.
  `test` and `other-os` stop after 45 minutes (2026-11-16), as the other jobs already had
  limits: pytest takes 4 to 8 minutes there, 27 on a runner five times slower than usual,
  and a stuck test would otherwise hold a runner for GitHub's default 6 hours.
  `test (3.11)` uploads `pip freeze --exclude-editable` as the artifact `constraints-py3.11`
  once its suite has passed (2026-11-16, for v0.1.0): the repository's `constraints.txt`
  is that file, the versions CI passed with, not a local venv's (owner).
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
  on a clean machine. Its first run on GitHub (inside the first release run, 2026-11-16)
  stopped at collection: it installed only `bigdata` and `dev`, and five test files import
  the GUI, so it now installs `gen` and `gui` as every other job does (rehearsed in a venv
  with those extras: 8 passed with JDK 17, the Hadoop ones too, as the repo's `.env` here
  names a HADOOP_HOME; on GitHub those 5 skip).
- Not in CI: the Hadoop tests (Hadoop is a 700 MB download and each job starts a JVM for
  about 7 s). Run `pytest -m spark` locally with `HADOOP_HOME` set before merging changes
  to `run_mrjob`.

