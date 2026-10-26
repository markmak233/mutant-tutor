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

## Git

- Never run `git push`.
- Check `git status` and the diff before every commit.
- Remember `.gitignore` does not remove files already in git history.

## Student work

- Never overwrite or write the owner's answers (`student.py` attempts).
- Do not reveal `reference.py` as a hint; hints come from `hints.md`.

## Code execution

- Run generated or untrusted code only through the isolated runner
  (subprocess + timeout + temporary working directory).
