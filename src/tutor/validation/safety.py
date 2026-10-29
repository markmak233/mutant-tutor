"""Static safety check: read exercise code with `ast`, never run it (docs/design.md,
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
6. no shell commands through the frameworks (RDD.pipe, MRJob *_cmd / *_pre_filter).

It is a filter, not a sandbox: Python is too dynamic for a static check to prove code safe.
The container of isolation layer B is the real boundary; this layer stops the obvious and
also covers the learner's own `pytest test_student.py`, which runs outside the validator.
"""

from __future__ import annotations

import ast
import tomllib
from collections.abc import Iterable
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
# pytest reads these on its own, so they could run code without being a test.
CONFIG_FILES = frozenset({"conftest.py", "pytest.ini", "setup.cfg", "tox.ini", "pyproject.toml"})


def allowed_imports(framework: str, extra: Iterable[str] = ()) -> frozenset[str]:
    return CORE_IMPORTS | FRAMEWORK_IMPORTS.get(framework, frozenset()) | frozenset(extra)


def workspace_allow_imports(start: Path) -> frozenset[str]:
    """The course's extra modules: `[safety] allow_imports` of the nearest workspace.toml
    above `start`. Empty outside a workspace (the repo's examples). Only the owner edits
    workspace.toml; generation never writes it, so generated code cannot widen its own
    allowlist."""
    for folder in [start, *start.parents]:
        path = folder / WORKSPACE_FILE
        if path.is_file():
            with path.open("rb") as f:
                names = tomllib.load(f).get("safety", {}).get("allow_imports", [])
            if not (isinstance(names, list) and all(isinstance(n, str) and n for n in names)):
                raise ValueError(f"{path}: [safety] allow_imports must be a list of module names")
            return frozenset(names)
    return frozenset()


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
    """Local names bound to the os / sys modules: `import os as o` -> {"o": "os"}."""
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in ("os", "sys"):
                    # `import os.path` binds the name `os` too
                    aliases[alias.asname or root] = root
    return aliases


def _node_hazards(node: ast.AST, allowed, module_aliases) -> list[str]:
    if isinstance(node, ast.Import):
        return [f"import of '{a.name}' is not allowed" for a in node.names
                if a.name.split(".")[0] not in allowed]
    if isinstance(node, ast.ImportFrom):
        if node.level:
            return ["relative imports are not allowed"]
        root = (node.module or "").split(".")[0]
        if root not in allowed:
            return [f"import of '{node.module}' is not allowed"]
        limits = {"os": OS_ATTRS, "sys": SYS_ATTRS}.get(node.module)
        if limits is not None:
            return [f"{node.module}.{a.name} is not allowed" for a in node.names
                    if a.name not in limits]
        return []
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


def _attribute_hazards(node: ast.Attribute, module_aliases) -> list[str]:
    found = []
    if isinstance(node.value, ast.Name) and node.value.id in module_aliases:
        module = module_aliases[node.value.id]
        if node.attr not in (OS_ATTRS if module == "os" else SYS_ATTRS):
            found.append(f"{module}.{node.attr} is not allowed")
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
    if name in PATH_CALLS and node.args:
        first = node.args[0]
        literal = isinstance(first, ast.Constant) and isinstance(first.value, str)
        if literal and _outside(first.value):
            return [f"{name}('{first.value}') reaches outside the exercise"]
    return []


def _outside(path: str) -> bool:
    return path.startswith(("/", "~", "\\")) or ".." in path.replace("\\", "/").split("/")
