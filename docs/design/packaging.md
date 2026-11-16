# Design: Packaging and release

Installing the tool, the release package and its workflow. Part of the design record; the index of every part is [README.md](README.md).

- Packaging and release: an install line, one click to start, CI on macOS and Windows
  (WSL) as well, versioned releases after `v0.1.0`.
  Portable-wheel preparation built 2026-11-09: controlled setuptools hooks carry public
  source/build metadata, GUI assets and synthetic examples, excluding private roots,
  caches and links. Installed workspace/repair installers extract the controlled source
  temporarily and install noneditable; source checkouts retain editable installs. No
  unrelated PyPI tutor-name fallback. Archive paths/size are validated, examples work
  outside a checkout, and offline wheel inspection precedes actual fresh-install checks.
  Local launch built 2026-11-09: `tutor gui` shares the module launcher. Installed wheels
  default to per-user courses/config folders (TUTOR_HOME can override both); checkout
  WORKSPACE_DIR behavior stays. Launch state is private and root-specific. A second
  launch verifies a fresh HMAC challenge over loopback before reusing a server; invalid
  state has an explicit reset, and no server is killed. Stale state is the usual case, not
  a rare one: closing the server's window (Windows' way to stop it), SIGTERM or SIGHUP
  never run its cleanup (SIGTERM measured 2026-11-16). It is removed when the old pid is
  dead or, since 2026-11-16, when its port refuses connections (a server binds its port
  before saving state and keeps it until it stops), so a pid Windows has handed to another
  program no longer stops a launcher click at `--reset-state`.
  Under WSL (2026-11-16, a joint test of the owner's PC and its WSL 2, default NAT
  networking): Windows reaches a server in WSL that listens on 127.0.0.1, by
  `127.0.0.1` or `localhost` (curl: 403 without the token, 302 and the cookie with it;
  the owner's browser showed the courses page). WSL itself has no browser: Python's
  `webbrowser` fell through to `gio`, which printed "Operation not supported" and nothing
  opened. So under WSL `tutor gui` hands the link to Windows' http handler,
  `rundll32.exe url.dll,FileProtocolHandler URL`, which opened it in the owner's default
  browser (`explorer.exe URL`, the first try, opened the Documents folder instead;
  dde96b1 checked through `tutor gui` itself in WSL: the first launch and a reuse each
  opened the courses page in Windows' browser);
  without Windows interop it tries the usual way, and a
  `webbrowser` that finds no browser at all prints a line to open the link by hand.
  Mirrored networking (`.wslconfig`) is untested. `tutor shortcut` creates a new
  .command/.lnk/.desktop without overwriting or changing startup items. Native desktop
  trust/click and browser acceptance remain separate from platform-format unit tests.
  The server runs in the console window the launcher opens; closing it stops the server.
  On Windows the .lnk opens that window minimised, with a tooltip saying so (owner,
  2026-11-12, after the first real double-click: "a terminal stays there the whole
  time"; no trust prompt appeared). No window at all (`pythonw.exe`, no terminal on macOS
  and Linux) waits for a Stop-server button in the pages.
  Optional Python management built 2026-11-09: existing uv-managed stable CPythons join
  interpreter discovery. The separate optional page/`tutor python` command lists uv's
  offline catalog; only a confirmed action downloads a freshly offered interpreter,
  checking known conflicts and measured removed imports. No system installer, PATH alias,
  course switch, runner fallback or default-doctor install. Native install acceptance
  uses a new scratch root and an existing Python, with implicit Python downloads disabled;
  the packaging CI matrix covers installed wheels/fresh synthetic courses on three OSes.
  - Release package (owner, 2026-11-15): one zip handed over directly, with no hosting (no
    PyPI, no GitHub Release for now) and still no install script: the wheel plus
    `packaging/INSTALL-<OS>.txt` for macOS, Linux and Windows, plain ASCII, which the user
    follows by hand (uv's official installer, `uv tool install` of the wheel, then `tutor
    gui`, `shortcut` and `doctor`; Docker only pointed to). One file per system (owner: a
    mixed file made each reader skip two thirds of it). `tests/test_install_instructions.py`
    checks each names this version, existing extras and the courses folder `locations`
    gives. Walked through by hand on fresh machines (2026-11-15/16): macOS 15 in a UTM VM,
    Ubuntu in VMware Fusion (Docker Engine there needed the docker group and a new login),
    Windows 11 x64 in Windows Sandbox; their fixes are in the texts (no Notepad in Sandbox,
    OneDrive or translated desktops, `$env:` in PowerShell, a VM clock months behind
    failing TLS, a new terminal after installing Docker). Since 2026-11-16 `tutor shortcut`
    finds the desktop itself (Windows' Desktop known folder; `xdg-user-dir DESKTOP` on
    Linux, where no desktop folder is an error naming `--directory`, owner: not a silent
    fallback to the applications menu), so the texts no longer give a workaround.
    Checked the same evening in fresh clones: Windows 11 x64 (the Desktop known folder
    gave `OneDrive\桌面`; a `.lnk` made in a non-ASCII folder points at the right python
    and arguments), WSL 2 (no desktop folder: exit 2 naming `--directory`) and an Ubuntu
    26.04 aarch64 VM with KDE (`~/Desktop`; a double-click asks how to open the file,
    "Launch" among editors such as Kate, and Launch opened Konsole and the pages; the
    Linux INSTALL text now says so beside GNOME's "Allow Launching"). The aarch64
    install of `.[dev,gui,gen]` built nothing from source. A launcher outlives the
    install it points at: the VM's launcher from the 11-15 walk-through said "could not
    find the program" once that tool was uninstalled, as the INSTALL texts' removal
    section warns (delete it there; a reinstall with uv should put the python back at
    the same path, not tested). Windows on ARM is not supported: sqlite-vec, which
    langgraph-checkpoint-sqlite 3 requires (only its vector store imports it; the
    SqliteSaver used here does not), has no win_arm64 wheel, and PyYAML has none for
    Python 3.11. Resolving for it with Python 3.12 and sqlite-vec excluded (`uv tool install
    --excludes`) works, but a course's `.venv` installs the same extras, so support waits
    for a change of its own. Decided (owner, 2026-11-16): not a native arm64 build, as
    few people use Windows on ARM, but x64 Python under Windows' own x64 emulation
    (Prism), installed with uv (`cpython-3.11-windows-x86_64`), so every win_amd64 wheel
    applies as on an x64 PC and the code may need no change; only INSTALL-Windows.txt
    gets an ARM paragraph. Containers stay Linux arm64 under Docker Desktop. To check in
    a Windows 11 ARM VM (UTM on the owner's Mac) before it is written down as supported:
    the install, the suite, a course, and whether Docker Desktop runs there. Docker on Windows x64 (2026-11-16, `pytest -m docker` on the
    owner's PC): every container-limit test and a container course's `doctor --smoke` pass;
    C runs sometimes hang (see "C", the AddressSanitizer issue; fixed with gcc 14 the same
    day).
  - Making it (owner, 2026-11-16): a manual workflow, `release.yml` (`workflow_dispatch`
    only), not a release on every tag or push, since a release is handed over by hand
    anyway. It replaces making the package by hand, as the 11-15 walk-throughs did
    (`uv build`, then the wheel and the INSTALL texts zipped in a `tutor-VERSION`
    folder): the owner asked for it that day ("commit, then the release workflow"; the
    schedule had it as "later") and approved the plan and the artifact's name. What stays
    by hand: starting the run, the tag and its push, and any GitHub Release page; the
    by-hand steps stay in docs/setup/install.md, "Release package", for use without
    GitHub Actions. It calls `ci.yml` whole (`workflow_call`), so a package CI would fail is never
    made; builds one wheel with `uv build`, the command for a release by hand, and checks
    its name against pyproject's version; runs the clean-install acceptance on that same
    wheel on three systems (CI's own `packaging` job tests a wheel it builds itself); then
    uploads the `tutor-VERSION` folder as the run's artifact, named with the commit's first
    7 characters so two builds of one version stay apart, kept 90 days (GitHub's maximum
    by default) and downloadable by logged-in users. An artifact is not hosting in the
    sense decided above: it lives on the run's page, is not listed as a release and
    expires. Not runnable locally: checked with actionlint 1.7.7 and its build and bundle
    commands rehearsed (that caught uv writing a `.gitignore` into `dist`); its first real
    run, after it reaches the default branch, is the check that it works.
    v0.1.0 (2026-11-17): tag `v0.1.0` on main's 1891f31, pushed by the owner after a
    manual CI run there (spark included, all green), as a push to main skips spark and a
    merge had passed its PR and failed on main before. Release run 43876271241: 17 jobs
    green, the zip `tutor-0.1.0-1891f31` holding `tutor-0.1.0/` with the wheel (0.1.0,
    315 files) and the three INSTALL texts. `constraints.txt` is CI's 3.11 freeze.
  - One click (the owner's aim, 2026-11-05; installation acceptance still pending): one install
    line gets `uv`, which fetches the Python it needs and installs the tool (`uv tool install`,
    the same job as `pipx`, without needing Python first). `tutor gui` starts the server and
    opens the browser with that launch's token. `tutor shortcut` writes a desktop shortcut on
    this machine (`.command` or a small `.app`, `.lnk`, `.desktop`); made here and not
    downloaded, it should not meet macOS Gatekeeper or need signing, to be tested. A second
    click finds the running server from a small file with its port and token and only opens
    the browser. Not a frozen app (PyInstaller): a build per platform, paid signing on macOS,
    and each course's venv needs a real Python anyway.
  - What a user still installs: that one line, for all; an IDE to solve in; an API key or a
    local model to generate; Java and Hadoop for such courses, since the learner's runs stay
    on the host (the page gives the commands); Docker Desktop to choose the container. A plain
    Python course needs only the line and a key. Once `tutor test` is built ("Roadmap", 6), a
    container course needs that toolchain only to run tests on the host, e.g. to debug.

- Packaging the GUI itself as an image (owner asked, 2026-11-05: the aim is opening it in one click,
  see "Packaging and release", which does not need this, so an image is at most a later option). The
  costs: to start test containers from inside, it needs the Docker socket, which amounts to root on
  the Docker host, so that form must say so plainly; "Open folder" cannot work, and "Open in IDE"
  only as a `vscode://` link the browser opens; the OS keychain is out of reach; the sibling
  containers' mounts need the host's paths, not the paths inside; inside it binds all interfaces
  (the host side stays `-p 127.0.0.1:...`), so the token matters more. To keep that door open at no
  cost: paths come from configuration, starting a run stays in one place (`execution.py`), and the
  open-folder and IDE actions sit behind one small function that can report "not available".

