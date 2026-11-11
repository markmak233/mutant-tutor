# Install and launch

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) using its official
instructions for your system. Tutor never installs uv or changes shell profiles itself.
From this repository's checkout, with Python 3.11 already installed:

```console
uv tool install --no-python-downloads --python 3.11 ".[gen,gui,doctor]"
tutor gui
```

This installs a standalone tool from this source, including the generator, GUI and repair
editor. Do not use `uv tool install tutor`: that name on PyPI may belong to another project.
`uv tool update-shell` is an explicit optional step if uv's tool bin directory is not on PATH;
restart your terminal afterward. Source developers can retain editable pip installs instead.

If you choose to let uv download its managed CPython, omit `--no-python-downloads` from
the install command. That is a separate choice; its distributions come from Astral's
[python-build-standalone](https://docs.astral.sh/uv/concepts/python-versions/#managed-python-distributions).
The existing-Python command above refuses a download. It needs no admin rights.

An installed wheel defaults to a per-user Tutor courses folder and a separate config
folder. `tutor gui --root DIR` selects an existing folder holding your courses. `TUTOR_HOME`
overrides the app's default courses/config base. A checkout retains WORKSPACE_DIR behavior.
Neither the tool install nor first launch creates a model profile or copies course content.
Add keys through Keys and choose profiles through Models; the GUI process never loads keys
from `.env`. Commands load only the credentials they explicitly need.

The server binds 127.0.0.1, swaps a launch token for an HttpOnly cookie, and checks request
origin. A second `tutor gui` verifies the existing server and opens it again. Ctrl+C in
its original terminal stops it. If startup state is stale/corrupt, check that terminal
before using `tutor gui --reset-state`; reset removes launch metadata, not a running process.

```console
tutor shortcut --root DIR
```

This creates a new Tutor desktop launcher: .command on macOS, .lnk on Windows, .desktop
on Linux. Existing files are refused, and no login/startup item changes. `--directory DIR`
chooses another existing destination. Native desktop trust prompts vary; on Linux your
desktop may require “Allow Launching”. The Python/tool environment must stay installed.
For a different desktop folder, including redirected Windows desktops, specify its path.

## What else is needed

- Plain Python: an IDE/editor; a model API key or a local endpoint to generate exercises.
  Creating a course downloads its Python packages into its own venv.
- Container courses, including C validation: Docker with Linux containers running and
  the course image built through Environment. No host fallback if Docker is stopped.
- Host Spark/Hadoop: Java/Hadoop and their configuration; use
  [the big-data guide](bigdata.md) or Environment → host setup. C on native Windows uses WSL.
- `tutor doctor COURSE --smoke` checks a synthetic shipped example on a temporary copy.
  It needs the course's libraries; default doctor neither installs nor calls a model.

Optional Python downloads are under Environment → Optional Python, or:

```console
tutor python list COURSE
tutor python install COURSE IDENTIFIER --confirm
```

Listing is offline/read-only. The explicit download accepts only a fresh stable CPython
catalog entry compatible with known course constraints and measured removed imports.
It adds an interpreter to uv's user directory without PATH aliases; it does not switch
the course. Environment → Check again → Make .venv is the separate guarded switch.
`uv python uninstall IDENTIFIER` removes an unused interpreter; first check no venv uses it.
uv is optional: manually installed Pythons remain discoverable.

## Clean-install acceptance

Build a wheel, then run the explicit script with a new scratch directory outside any
repository. It downloads dependencies and creates tool/course environments, never calls
a model, never uses a real course and never downloads Python implicitly:

```console
python tools/package_acceptance.py --uv UV_EXECUTABLE --python PYTHON_EXECUTABLE --wheel WHEEL --work NEW_SCRATCH_DIR
```

Private scratch logs include the launch URL; share only result.json and sanitized errors.
The script checks installed resources, a fresh course, deterministic smoke, shortcut file
creation, real loopback authentication and second-process server reuse. It stops its own
server with SIGTERM. It does not claim browser clicks or desktop trust acceptance.
Delete only that new scratch directory to undo. CI runs this on Linux, macOS and Windows;
interactive browser/desktop and Docker Desktop acceptance still need native manual checks.
