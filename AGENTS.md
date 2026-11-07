# Agent Rules

Rules for any AI coding agent working in this repository. Design decisions live in `docs/design.md`.
When reviewing, read its "Decisions not to do (and why)" section first: those omissions are
deliberate, not gaps to report.

## Private data and secrets

- Never commit lecture PDFs, text extracted from them, generated exercises from lectures, or API keys.
- Do not copy course content into public directories. Only hand-written synthetic examples go in `examples/`.
- Never hardcode API keys; read them from environment variables or a local `.env` (never committed).
  Do not print credentials in logs, test output or error reports.
- Do not write real absolute paths into source, README or public JSON; use `LECTURE_DIR` / `WORKSPACE_DIR`.
- Before a commit, search the staged diff for course details: course codes, but also lecture and
  slide numbers, titles, quotes, and version numbers or package names taken from the slides.

## Git

- Never run `git push`.
- Check `git status` and the diff before every commit.
- Remember `.gitignore` does not remove files already in git history.

## Student work

- Never overwrite or write the owner's answers (`student.py` / `student.c` attempts).
- Do not reveal `reference.py` as a hint; hints come from `hints.md`.

## Code execution

- Run generated or untrusted code only through the isolated runner (`tutor.execution`): a fresh
  container when the course's `workspace.toml` says `[isolation] runner = "container"`, else
  subprocess + timeout + temporary working directory. Never call the code directly to get around it.
- Do not switch a course to `runner = "host"`, or add a fallback to the host, to get past a
  stopped Docker or a missing image: report it to the owner.
- C is validated only in a container. C does not run natively on Windows; WSL instead
  (design.md, "C").

## Code

- A source file past 300 lines is watched; from 500, propose a split along its sections, or say
  why it stays whole (one cohesive job). "Consider" is not "must".
- When a change touches a file, comment that whole file: what each block does and why (regexes,
  checks, loops, prompt sections). New code is written well commented. No repository-wide
  comment-only change.
- Read and write every text file with an explicit `encoding="utf-8"` (`tests/test_encoding.py`
  checks it): Windows' default code page garbles anything outside ASCII.
- Code runs on macOS, Linux and native Windows; tests must not assume `/` in paths.
- Standard library first. A new dependency goes into the extra that needs it (`pyproject.toml`).
  The GUI is Flask, Jinja2 and a vendored htmx: no npm, and no script of our own unless needed.

## Prompts

- Calibrated prompts stay byte for byte: the pipeline's Python prompts (generate, repair, plan,
  oracle), pinned by hash in `tests/test_prompt_pins.py`, and the judges (teaching, hints),
  calibrated against labels. Change one only with a measurement; if a pinned prompt's input
  changes, compute the new pins with the code from before the change.
- A new prompt puts text it did not write (lectures, an earlier answer, a validator's report)
  between tags (`graph/prompts.material()`), and its system prompt names them as material, not
  instructions (design.md, "Decisions not to do", the untrusted-data entry).
- Lay a prompt out for the provider's prompt cache: what every call shares first and byte for byte
  the same, what varies per call last. Check `cached_tokens` in the call log before claiming a saving.
- No LLM judges whether an exercise is valid; only the deterministic validator does.

## Tests

- After each change: `pytest` and `ruff check .`. After a change to execution, validation, a
  container or C: also `pytest -m docker` (Docker running). After a change to `testkit` or the
  validator's Spark rules: also `pytest -m spark`.
- Tests never call a real model or touch a real course: use stand-ins (`tests/fake_graph.py`, fake
  chat functions). Key a fake's replies by request content, not by call order: callers run in
  threads.
- No made-up secrets with high entropy, and no "key" in test ids: gitleaks takes them for real
  keys. Use plainly fake strings (`sk-test-not-a-real-key`). Never `--no-verify`.
- A skip is not a pass: report what was skipped and why.
- A new language or framework runs one generated item through the whole path (generate, validate,
  save), not only unit tests.

## GUI

- Check every GUI action with a real click in a browser, on a scratch course: the Flask test
  client sets headers by hand, so a passing test does not prove the page works. The server caches
  templates: restart it after editing one.
- A gap found while using the GUI (a step that does not start, a setting with no button, an item
  that failed) gets a fix path in the page, not a fix made by hand outside it.
- The server listens on 127.0.0.1 only, with a token per launch, and never loads `.env` into its
  environment: VS Code and its terminals, started from it, would inherit the keys.
