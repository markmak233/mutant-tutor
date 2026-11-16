# Changelog

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
