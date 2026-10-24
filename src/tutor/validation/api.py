"""Static API constraints: read the source with `ast`, never run it (docs/design.md).

metadata.json "constraints" may contain:
    forbidden_calls       names that must not be used as a method or function,
                          e.g. "groupByKey", "collect"
    forbidden_imports     modules that must not be imported, submodules included,
                          e.g. "pyspark.sql" (no DataFrames)
    required_definitions  functions or methods the code must define,
                          e.g. "mapper_init", "mapper_final" (in-mapper combining)

Any attribute access counts, called or not, so `f = rdd.groupByKey; f()` is still found.
Only fully dynamic access such as getattr(rdd, "group" + "ByKey") escapes; that is accepted.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping

KNOWN_KEYS = {"forbidden_calls", "forbidden_imports", "required_definitions"}


def find_violations(source: str, constraints: Mapping[str, list[str]]) -> list[str]:
    """One message per violation, e.g. "line 12: uses groupByKey". Empty list = all good."""
    unknown = set(constraints) - KNOWN_KEYS
    if unknown:
        # A typo such as "forbiden_calls" would otherwise silently check nothing.
        return [f"unknown constraint key(s): {', '.join(sorted(unknown))}"]
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f"line {e.lineno}: cannot parse ({e.msg})"]

    forbidden_calls = set(constraints.get("forbidden_calls", []))
    forbidden_imports = constraints.get("forbidden_imports", [])
    violations: list[str] = []
    defined: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in forbidden_calls:
            violations.append(f"line {node.lineno}: uses {node.attr}")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in forbidden_calls
        ):
            violations.append(f"line {node.lineno}: calls {node.func.id}")
        elif isinstance(node, ast.Import | ast.ImportFrom):
            for module in _imported_modules(node):
                if any(_is_within(module, banned) for banned in forbidden_imports):
                    violations.append(f"line {node.lineno}: imports {module}")
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            defined.add(node.name)

    for name in constraints.get("required_definitions", []):
        if name not in defined:
            violations.append(f"must define {name}")
    return violations


def _imported_modules(node: ast.Import | ast.ImportFrom) -> list[str]:
    """Full module names an import statement brings in.
    `from pyspark import sql` counts as pyspark.sql, the same as `import pyspark.sql`."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    base = node.module or ""
    return [base, *(f"{base}.{alias.name}" for alias in node.names)]


def _is_within(module: str, banned: str) -> bool:
    return module == banned or module.startswith(banned + ".")
