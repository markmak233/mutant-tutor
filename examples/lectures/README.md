# Synthetic lectures for demo courses

Two short workshops, written for this repository by an AI model and reviewed by the owner.
They copy no course's material and are under the repository's MIT licence, so the docs can
show a whole course in public: lectures, a plan, generated exercises.

| Course | File | Topic |
|---|---|---|
| `python-workshop` | `week1.html` | values and types, strings, lists, loops, conditions |
| | `week2.html` | tuples, dictionaries, functions, comprehensions, sorting |
| | `week3.html` | reading and writing files, exceptions, a small program |
| | `week4.html` | sets, nested data, text processing, formatting |
| | `week5.html` | classes, data classes, modules |
| `c-workshop` | `week1.html` | a first program, types, conditions, loops, functions, input |
| | `week2.html` | arrays, strings, pointers |
| | `week3.html` | structs, `malloc` and `free`, reading lines, a small program |

Each lecture has eight sections, one per `<h2>`, which the tool reads as slides N.1 to N.8
(`materials/html.py`). Every piece of code is followed by the output it prints, or by the
last line of the error it raises; `tests/test_example_lectures.py` runs each Python block
and compiles and runs each C program, and compares.

## Using them

A course's lectures must sit outside any git repository, so make a new course for each
workshop and upload its files on the GUI's Lectures page (or copy the folder out and point
`LECTURE_DIR` at the copy).

- `python-workshop` needs no framework and no `[languages]` table: its exercises are plain
  Python.
- `c-workshop` is a C course: declare `[languages.c]` in its `workspace.toml` (the GUI's
  Environment page proposes the flags the lectures compile with, `-Wall -std=c11`) and give
  it the container, since C is validated only in one.

Exercises generated from these lectures stay in your workspace, like any course's.
