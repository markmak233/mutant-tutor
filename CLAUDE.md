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
