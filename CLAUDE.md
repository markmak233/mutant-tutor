@AGENTS.md

## Claude Code workflow

- Before planning any new feature, read `docs/design.md`. If a change contradicts a decision there,
  point it out and ask; if the owner approves a new decision, update `docs/design.md` in the same change.
- For any task touching more than one file, propose a plan first and wait for approval before editing.
- Work in small steps: one feature or fix per change, then run `pytest` and `ruff check .`.
- After each step, summarise what changed and why in 3-5 lines, so the owner can review the diff.
- For core modules (`src/tutor/validation/`, `src/tutor/graph/`), explain the design before writing code;
  the owner needs to be able to explain this code in interviews.
- If a test fails twice for the same reason, stop and explain the problem instead of trying more fixes.

## Tutoring (when the owner asks about an exercise they are solving)

- Before answering, run the exercise's tests in its folder and read `student.py`; do not guess.
- Start from the first real error: what causes it and the concept behind it
  (e.g. why an RDD cannot be looped over on the driver).
- Default to direction, not code. Give a concrete line-level fix only when the owner asks how to fix it.
- Never edit `student.py`. Never read or quote `solution/` (reference, mutants) while tutoring.
- Afterwards, list the typical mistakes the owner hit; they are material for debugging
  exercises and mutants.
