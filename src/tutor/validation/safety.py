"""Static safety check: read exercise code with `ast`, never run it (docs/design/execution.md,
"Code execution safety", isolation layer A).

Exercise code may be written by an LLM from material that carries hidden instructions, and
the validator, and later the learner, runs it with the user's rights. This check is an
allowlist filter that runs before any of that code does:

1. imports: only allowed modules (CORE_IMPORTS, the framework's own modules, and a
   course's extra modules from workspace.toml `[safety] allow_imports`);
2. `os` / `sys`: only the attributes in OS_ATTRS / SYS_ATTRS;
3. no eval, exec, compile, __import__, breakpoint;
4. no dunder attribute outside DUNDER_ATTRS; getattr / setattr / delattr take a literal name;
5. no literal path that starts with "/" or "~" or contains ".." given to a file-opening
   call, and no home-directory lookups;
6. no shell commands through the frameworks (RDD.pipe, MRJob *_cmd / *_pre_filter);
7. `tutor`: only the test kit (`tutor.testkit`, `tutor.testkit_c`), and from it only the
   names it defines itself. The whole package was allowed until 2026-11-18, and any module
   one of its files imports could be taken from it (`from tutor.execution import subprocess`);
8. `pytest`: only the attributes in PYTEST_ATTRS (`pytest.importorskip("subprocess")`
   imports any module without an import statement);
9. no attribute, on any object, named after a module or a function that starts a process or
   imports by name (HANDOFF_NAMES): the standard library's modules import `os` themselves,
   so `pathlib.os.system(...)` needs no forbidden import. The same names may not be
   imported from another module (`from tempfile import _os`);
10. no py4j handle (PY4J_ATTRS): `sc._jvm` reaches every Java class, `Runtime.exec` included.

Rules 1, 7 and 8 are allowlists: what exercises import, and what they use of `tutor` and
`pytest`, is small and was counted (3769 files of two courses and the examples used two kit
modules and four pytest attributes). Rules 9 and 10 can only name what is known to be
dangerous, since an exercise's own attribute names are without limit.

It is a filter, not a sandbox: Python is too dynamic for a static check to prove code safe.
The container of isolation layer B is the real boundary; this layer stops the obvious and
also covers the learner's own `pytest test_student.py`, which runs outside the validator.
"""

from __future__ import annotations

import ast
import tomllib
from collections.abc import Iterable
from functools import cache
from pathlib import Path

from tutor.config import WORKSPACE_FILE
from tutor.validation.api import builtin_uses

# Pure-computation standard library, the test tools, and the exercise's own modules.
CORE_IMPORTS = frozenset({
    "bisect", "collections", "copy", "csv", "dataclasses", "datetime", "decimal", "enum",
    "fractions", "functools", "hashlib", "heapq", "itertools", "json", "math", "operator",
    "os", "pathlib", "random", "re", "statistics", "string", "sys", "tempfile", "textwrap",
    "typing", "unicodedata", "zlib",
    "__future__", "hypothesis", "pytest", "tutor",
    "student", "oracle",
})
# metadata.json `framework` -> the modules that framework needs.
FRAMEWORK_IMPORTS = {
    "python": frozenset(),
    "mrjob": frozenset({"mrjob"}),
    "pyspark-rdd": frozenset({"pyspark"}),
    "pyspark-df": frozenset({"pyspark"}),
}
OS_ATTRS = frozenset({"environ", "path"})
SYS_ATTRS = frozenset({"argv", "exit", "stdin", "stdout", "stderr", "executable"})
FORBIDDEN_BUILTINS = frozenset({"eval", "exec", "compile", "__import__", "breakpoint"})
DUNDER_ATTRS = frozenset({
    "__name__", "__module__", "__qualname__", "__init__", "__class__", "__doc__",
})
DUNDER_NAMES = frozenset({"__name__", "__file__", "__doc__"})
# Calls whose first argument is a file path: open(), pathlib, Spark readers.
PATH_CALLS = frozenset({
    "open", "Path", "PurePath", "PosixPath", "textFile", "wholeTextFiles", "binaryFiles",
    "addFile", "addPyFile", "csv", "text", "json", "parquet", "load",
})
HOME_LOOKUPS = frozenset({"home", "expanduser"})
# MRJob runs these as shell commands instead of Python; RDD.pipe runs one per partition.
MRJOB_SHELL = frozenset({
    "mapper_cmd", "combiner_cmd", "reducer_cmd",
    "mapper_pre_filter", "combiner_pre_filter", "reducer_pre_filter",
})
SHELL_NAMES = MRJOB_SHELL | {"pipe"}
# Rule 7. The only modules of this package an exercise may import: the test kit.
KIT_MODULES = frozenset({"tutor.testkit", "tutor.testkit_c"})
# Rule 8. What tests use of pytest (approx, fixture, raises and mark in every exercise
# counted), and four more that only report or mark a result. Not `importorskip` (imports by
# name), `main` (runs pytest again, with any plugin), `MonkeyPatch` or `Config`.
PYTEST_ATTRS = frozenset({"approx", "fixture", "raises", "mark", "param", "fail", "skip",
                          "warns"})
# Rule 9. Names under which a module hands on another module, or that start a process or
# import by name. `_os` and `_sys` are how tempfile, random and collections keep theirs.
HANDOFF_NAMES = frozenset({
    "os", "_os", "sys", "_sys", "subprocess", "importlib", "builtins", "modules",
    "system", "popen", "Popen", "import_module", "importorskip",
})
# Rule 10. PySpark's handles into the JVM (SparkContext, SparkSession, DataFrame, RDD).
PY4J_ATTRS = frozenset({
    "_jvm", "_jsc", "_gateway", "_jsparkSession", "_jdf", "_jrdd", "_jconf",
})
# pytest reads these on its own, so they could run code without being a test.
CONFIG_FILES = frozenset({"conftest.py", "pytest.ini", "setup.cfg", "tox.ini", "pyproject.toml"})


def allowed_imports(framework: str, extra: Iterable[str] = ()) -> frozenset[str]:
    return CORE_IMPORTS | FRAMEWORK_IMPORTS.get(framework, frozenset()) | frozenset(extra)


def workspace_allow_imports(start: Path) -> frozenset[str]:
    """The course's extra modules: `[safety] allow_imports` of the nearest workspace.toml
    above `start`. Empty outside a workspace (the repo's examples). Only the owner edits
    workspace.toml; generation never writes it, so generated code cannot widen its own
    allowlist."""
    return _workspace_names(start, "allow_imports", "module names")


def workspace_allow_headers(start: Path) -> frozenset[str]:
    """The course's extra C headers, e.g. "unistd.h": `[safety] allow_headers`, found and
    owned as allow_imports is (csource.find_hazards)."""
    return _workspace_names(start, "allow_headers", "header names")


def _workspace_names(start: Path, key: str, what: str) -> frozenset[str]:
    """A list of names under [safety] in the nearest workspace.toml above `start`."""
    for folder in [start, *start.parents]:
        path = folder / WORKSPACE_FILE
        if path.is_file():
            with path.open("rb") as f:
                names = tomllib.load(f).get("safety", {}).get(key, [])
            if not (isinstance(names, list) and all(isinstance(n, str) and n for n in names)):
                raise ValueError(f"{path}: [safety] {key} must be a list of {what}")
            return frozenset(names)
    return frozenset()


@cache
def kit_names(module: str) -> frozenset[str]:
    """What a kit module ("tutor.testkit") defines itself: its top-level functions, classes
    and constants whose names do not start with "_". Read from its source with `ast`, so a
    function added to the kit needs no list kept here, and so the modules the kit imports
    (os, subprocess, tutor.config's load_env) are not among them."""
    path = Path(__file__).resolve().parents[1] / f"{module.rpartition('.')[2]}.py"
    names: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return frozenset(n for n in names if not n.startswith("_"))


def config_files(names: Iterable[str]) -> list[str]:
    """The pytest configuration files among `names` (paths inside the run directory): pytest
    reads a conftest.py in a subfolder as well as at the top."""
    return sorted(n for n in names if n.rsplit("/", 1)[-1] in CONFIG_FILES)


def find_hazards(source: str, allowed: frozenset[str]) -> list[str]:
    """One message per finding, e.g. "line 3: import of 'socket' is not allowed"."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Python cannot run it either, so it is harmless here; the rules that run code
        # report it (a skeleton or mutant that errors before any test runs).
        return []
    module_aliases = _module_aliases(tree)
    found = [f"line {n.lineno}: {n.id} is not allowed"
             for n in builtin_uses(tree, FORBIDDEN_BUILTINS, static_only=True)]
    for node in ast.walk(tree):
        found += [f"line {node.lineno}: {m}" for m in _node_hazards(node, allowed,
                                                                     module_aliases)]
    return sorted(set(found), key=lambda m: (int(m.split(":")[0].split()[1]), m))


def _module_aliases(tree: ast.AST) -> dict[str, str]:
    """Local names bound to a module whose attributes are limited: os, sys, pytest and the
    kit modules. `import os as o` -> {"o": "os"}; `from tutor import testkit as t` ->
    {"t": "tutor.testkit"}."""
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in ("os", "sys", "pytest"):
                    # `import os.path` binds the name `os` too
                    aliases[alias.asname or root] = root
                elif alias.name in KIT_MODULES and alias.asname:
                    aliases[alias.asname] = alias.name  # import tutor.testkit as t
        elif isinstance(node, ast.ImportFrom) and node.module == "tutor" and not node.level:
            for alias in node.names:
                if f"tutor.{alias.name}" in KIT_MODULES:
                    aliases[alias.asname or alias.name] = f"tutor.{alias.name}"
    return aliases


def _tutor_import(name: str) -> list[str]:
    """Rule 7 for `import tutor...`: a kit module, by its full name."""
    if name in KIT_MODULES:
        return []
    return [f"import of '{name}' is not allowed (of tutor, only "
            f"{' and '.join(sorted(KIT_MODULES))})"]


def _from_import(node: ast.ImportFrom) -> list[str]:
    """Rules 2, 7, 8 and 9 for `from <allowed module> import <names>`."""
    module = node.module or ""
    names = [a.name for a in node.names]
    limits = {"os": OS_ATTRS, "sys": SYS_ATTRS}.get(module)
    if limits is not None:
        return [f"{module}.{n} is not allowed" for n in names if n not in limits]
    if module == "pytest":
        return [f"pytest.{n} is not allowed" for n in names if n not in PYTEST_ATTRS]
    if module == "tutor":  # from tutor import testkit
        return [m for n in names for m in _tutor_import(f"tutor.{n}")]
    if module.split(".")[0] == "tutor":
        if module not in KIT_MODULES:
            return _tutor_import(module)
        # Only what the kit defines: not `*`, and not a module the kit itself imports.
        return [f"{module}.{n} is not allowed (not a name the test kit defines)"
                for n in names if n not in kit_names(module)]
    # Any other allowed module: it may not hand on a module it imported itself.
    return [f"import of {n} from '{module}' is not allowed" for n in names
            if n in HANDOFF_NAMES]


def _node_hazards(node: ast.AST, allowed, module_aliases) -> list[str]:
    if isinstance(node, ast.Import):
        found = [f"import of '{a.name}' is not allowed" for a in node.names
                 if a.name.split(".")[0] not in allowed]
        # `import tutor` alone would bind the whole package: only a kit module, in full.
        return found + [m for a in node.names if a.name.split(".")[0] == "tutor"
                        and "tutor" in allowed for m in _tutor_import(a.name)]
    if isinstance(node, ast.ImportFrom):
        if node.level:
            return ["relative imports are not allowed"]
        root = (node.module or "").split(".")[0]
        if root not in allowed:
            return [f"import of '{node.module}' is not allowed"]
        return _from_import(node)
    if isinstance(node, ast.Attribute):
        return _attribute_hazards(node, module_aliases)
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        if node.id.startswith("__") and node.id.endswith("__") and node.id not in DUNDER_NAMES:
            return [f"{node.id} is not allowed"]
        return []
    if isinstance(node, ast.FunctionDef) and node.name in MRJOB_SHELL:
        return [f"defining {node.name} is not allowed (it runs a shell command)"]
    if isinstance(node, ast.keyword) and node.arg in MRJOB_SHELL:
        return [f"{node.arg}= is not allowed (it runs a shell command)"]
    if isinstance(node, ast.Call):
        return _call_hazards(node)
    return []


def _kit_module(node: ast.expr, module_aliases) -> str | None:
    """The kit module an expression names: an alias of one, or `tutor.testkit` written out
    after `import tutor.testkit`."""
    if isinstance(node, ast.Name):
        module = module_aliases.get(node.id)
        return module if module in KIT_MODULES else None
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "tutor" and f"tutor.{node.attr}" in KIT_MODULES):
        return f"tutor.{node.attr}"
    return None


def _attribute_hazards(node: ast.Attribute, module_aliases) -> list[str]:
    found = []
    receiver = node.value.id if isinstance(node.value, ast.Name) else None
    module = module_aliases.get(receiver) if receiver else None
    if module in ("os", "sys", "pytest"):
        # Rules 2 and 8: these modules' attributes are allowlists.
        limits = {"os": OS_ATTRS, "sys": SYS_ATTRS, "pytest": PYTEST_ATTRS}[module]
        if node.attr not in limits:
            found.append(f"{module}.{node.attr} is not allowed")
    elif kit := _kit_module(node.value, module_aliases):
        # Rule 7: `testkit.subprocess` is the kit's own import, not something it defines.
        if node.attr not in kit_names(kit):
            found.append(f"{kit}.{node.attr} is not allowed (not a name the test kit "
                         "defines)")
    elif receiver == "tutor" and f"tutor.{node.attr}" not in KIT_MODULES:
        found.append(f"tutor.{node.attr} is not allowed")
    elif node.attr in HANDOFF_NAMES:
        # Rule 9, on any other object: pathlib.os, tempfile._os, anything.system.
        found.append(f"attribute {node.attr} is not allowed (it reaches a module or starts "
                     "a process)")
    if node.attr in PY4J_ATTRS:
        found.append(f"attribute {node.attr} is not allowed (it reaches the JVM)")
    if node.attr.startswith("__") and node.attr.endswith("__") and node.attr not in DUNDER_ATTRS:
        found.append(f"attribute {node.attr} is not allowed")
    if node.attr in HOME_LOOKUPS:
        found.append(f"{node.attr}() is not allowed (home directory)")
    if node.attr in SHELL_NAMES:
        found.append(f"{node.attr} is not allowed (it runs a shell command)")
    return found


def _call_hazards(node: ast.Call) -> list[str]:
    name = node.func.id if isinstance(node.func, ast.Name) else (
        node.func.attr if isinstance(node.func, ast.Attribute) else None)
    if name in ("getattr", "setattr", "delattr") and len(node.args) >= 2:
        attr = node.args[1]
        if not (isinstance(attr, ast.Constant) and isinstance(attr.value, str)):
            return [f"{name} needs a literal attribute name"]
        if (attr.value.startswith("__") and attr.value.endswith("__")
                and attr.value not in DUNDER_ATTRS):
            return [f"{name}(..., '{attr.value}') is not allowed"]
        # Rules 9 and 10 by name: getattr(pathlib, "os"), getattr(sc, "_jvm").
        if attr.value in HANDOFF_NAMES | PY4J_ATTRS:
            return [f"{name}(..., '{attr.value}') is not allowed"]
    if name in PATH_CALLS and node.args:
        first = node.args[0]
        literal = isinstance(first, ast.Constant) and isinstance(first.value, str)
        if literal and _outside(first.value):
            return [f"{name}('{first.value}') reaches outside the exercise"]
    return []


def _outside(path: str) -> bool:
    return path.startswith(("/", "~", "\\")) or ".." in path.replace("\\", "/").split("/")
