# Changelog

## Unreleased

- **A release can have its own page.** The manual Release workflow takes a "publish" box:
  ticked, the zip it has just tested becomes a GitHub Release for the version's tag, with
  this file's section for that version as its text. Off by default; still never PyPI.
- **Lectures for demo courses.** `examples/lectures/` holds two short synthetic workshops
  (plain Python, five weeks; C, three weeks) to try the tool on without a course of your
  own. Every piece of code in them is run by a test against the output it shows. The README
  and `docs/details.md` now show the GUI on these courses, and the pipeline as a diagram.
- **Checks after generation in the GUI.** A batch's page starts the judge of teaching
  quality, the writing of held-out solutions and the held-out check as tasks, and shows each
  exercise's verdict: the judge's four letters, once you have rated the exercise yourself, and
  how many held-out checks were flagged. The reasons stay in each task's log.
- **Hints in the GUI.** An exercise's page has a Hints tab: its `hints.md`, each hint closed
  until you open it, and "Check my code", which runs your tests in a temporary copy and names
  a known mistake, as `python -m tutor.hint` does. The page says whether your code goes to a
  model.
- **Keys stay out of test runs on the host.** Exercise code that runs on this machine (a
  course without the container, and your own `pytest`) no longer finds the models' keys in
  its environment. A container run never had them.
- **A stricter static safety check.** Exercise code may use only the test kit of `tutor`,
  only a few attributes of `pytest`, and no module reached through another (`pathlib.os`)
  or through PySpark's JVM handles (`sc._jvm`). The C check reads a name split over two
  lines as one and refuses trigraphs and the digraph for `#`. Existing exercises are
  unaffected.

## 0.1.0 (2026-11-17)

The first release: the whole loop, from a course's lectures to exercises solved in an IDE,
runs end to end. The dependency versions CI passed with are in `constraints.txt`.

Built:

- **Validation before you see an exercise.** Deterministic rules: the metadata contract,
  safety, the reference passes, the skeleton fails, every mutant is caught, the reference
  agrees with an oracle written from the question alone, the distribution checks, and a
  static API check. No LLM judges whether an exercise is valid.
- **Generation.** A LangGraph pipeline plans a batch from the lectures and a coverage file,
  waits for your approval, then generates, validates and repairs each exercise; Python
  (plain, PySpark, MRJob) and C (with sanitizers, in a container).
- **Practice.** Exercises as folders opened in VS Code; every test run recorded on the host;
  hints that name a known mistake or the mutant your code fails like, never the answer.
- **Checks after generation.** Held-out solutions written by another model, and a judge of
  teaching quality calibrated against your ratings (command line).
- **Isolation.** Each test run in a fresh container with no network, or a subprocess with a
  time limit on the host.
- **Models.** Any OpenAI-compatible provider or Anthropic's own API, one model per role,
  shared by all courses; prompts laid out for the providers' prompt caches.
- **A local GUI** on 127.0.0.1 with a launch token: courses, lectures, coverage, models,
  the environment (requirements, runner, test image, `tutor doctor`), plans, batches and
  exercises; tasks with a queue and a Stop button.
- **Install and run.** A wheel installed with uv, `tutor gui`, `tutor shortcut`,
  `tutor doctor`, `tutor test`; install texts for macOS, Linux and Windows, walked through
  on fresh machines; a manual release workflow that runs CI and builds the package.
- **Measurements** behind the choices: the retrieval comparison (no RAG needed), model
  comparisons for each role, and an evaluation report.

Not yet: C++ and Java, the held-out check and judge as GUI buttons, hints in the GUI,
Windows on ARM (planned through x64 emulation). See README, "Status".
