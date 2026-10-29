"""Static API constraints: read the source with `ast`, never run it (docs/design.md).

metadata.json "constraints" may contain:
    forbidden_calls       names that must not be used as a method or function,
                          e.g. "groupByKey", "collect"
    forbidden_imports     modules that must not be imported, submodules included,
                          e.g. "pyspark.sql" (no DataFrames)
    required_definitions  functions or methods the code must define,
                          e.g. "mapper_init", "mapper_final" (in-mapper combining),
                          or "Class.method" for a method of that class
    required_any          groups of names; at least one group must be fully defined,
                          e.g. [["combiner"], ["mapper_init", "mapper_final"]]
    count_uses            names whose uses are counted and reported, never limited,
                          e.g. "MRStep" (see count_uses())

forbidden_calls: any attribute access counts, called or not (`f = rdd.groupByKey; f()`),
and so does any use of a bare name the code does not bind in that scope (`f = sorted;
f(x)`); a variable that happens to share the name (`count = 0`) is not a use where it is
visible, and a parameter of one function does not hide the builtin in another.

A name counts as defined if a function has that name, or an MRStep(...) call passes it as a
keyword with a real value: multi-step jobs write `MRStep(combiner=self.combine_counts)`
instead of a `combiner` method (`dict(combiner=None)` does not count).

The `if __name__ == "__main__":` block is left out of every check: it is the demo that
`python student.py` runs on the question's example (validation rule "main"), not the
solution, and may print with an action such as collect() that the solution must not use.
The safety check still reads it.

This is a static aid, not a proof: dynamic access (getattr with a built string, exec,
importing a name under another module) can still get round it. Tests and mutants carry
the real weight; the check catches the honest mistakes and the obvious shortcuts.
"""

from __future__ import annotations

import ast
from collections.abc import Collection, Mapping

KNOWN_KEYS = {
    "forbidden_calls", "forbidden_imports", "required_definitions", "required_any",
    "count_uses",
}


def find_violations(source: str, constraints: Mapping[str, list]) -> list[str]:
    """One message per violation, e.g. "line 12: uses groupByKey". Empty list = all good."""
    unknown = set(constraints) - KNOWN_KEYS
    if unknown:
        # A typo such as "forbiden_calls" would otherwise silently check nothing.
        return [f"unknown constraint key(s): {', '.join(sorted(unknown))}"]
    groups = constraints.get("required_any", [])
    if not all(isinstance(g, list) and g and all(isinstance(n, str) for n in g) for g in groups):
        # A flat list such as ["combiner", "mapper_init"] would mean something else.
        return ['required_any must be a list of non-empty name lists, e.g. [["combiner"]]']
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f"line {e.lineno}: cannot parse ({e.msg})"]
    tree = without_main_block(tree)

    forbidden_calls = set(constraints.get("forbidden_calls", []))
    forbidden_imports = constraints.get("forbidden_imports", [])
    violations: list[str] = []
    defined: set[str] = set()

    unbound = {id(n) for n in builtin_uses(tree, forbidden_calls, static_only=False)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in forbidden_calls:
            violations.append(f"line {node.lineno}: uses {node.attr}")
        elif id(node) in unbound:
            violations.append(f"line {node.lineno}: uses {node.id}")
        elif isinstance(node, ast.Import | ast.ImportFrom):
            for module in _imported_modules(node):
                if any(_is_within(module, banned) for banned in forbidden_imports):
                    violations.append(f"line {node.lineno}: imports {module}")
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            defined.add(node.name)
        elif isinstance(node, ast.ClassDef):
            # "AuctionStats.run", as metadata entry_point names a file-level program's
            # method; a model copied the entry point into required_definitions.
            defined.update(f"{node.name}.{item.name}" for item in node.body
                           if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef))
        if isinstance(node, ast.Call) and _called_name(node) == "MRStep":
            defined.update(
                k.arg for k in node.keywords
                if k.arg and not (isinstance(k.value, ast.Constant) and k.value.value is None)
            )

    for name in constraints.get("required_definitions", []):
        if name not in defined:
            violations.append(f"must define {name} (a name the code defines, not a call)")
    if groups and not any(all(name in defined for name in group) for group in groups):
        options = " | ".join(" + ".join(group) for group in groups)
        violations.append(f"must define one of: {options} (names the code defines, not calls)")
    return violations


def is_main_block(node: ast.AST) -> bool:
    """`if __name__ == "__main__":` (either side of the ==)."""
    if not (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)):
        return False
    sides = [node.test.left, *node.test.comparators]
    names = {side.id for side in sides if isinstance(side, ast.Name)}
    texts = {side.value for side in sides if isinstance(side, ast.Constant)}
    return names == {"__name__"} and texts == {"__main__"}


def without_main_block(tree: ast.Module) -> ast.Module:
    """The module without its top-level main blocks (the demo, not the solution)."""
    return ast.Module(body=[n for n in tree.body if not is_main_block(n)],
                      type_ignores=tree.type_ignores)


def count_uses(source: str, names: list[str]) -> dict[str, int]:
    """How often each name is used as a name or attribute: `MRStep(...)` and
    `mrjob.step.MRStep(...)` both count, the import line does not. For reports only."""
    counts = dict.fromkeys(names, 0)
    for node in ast.walk(without_main_block(ast.parse(source))):
        if isinstance(node, ast.Name) and node.id in counts:
            counts[node.id] += 1
        elif isinstance(node, ast.Attribute) and node.attr in counts:
            counts[node.attr] += 1
    return counts


_FUNCTION_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ListComp,
                    ast.SetComp, ast.DictComp, ast.GeneratorExp)


def builtin_uses(tree: ast.Module, names: Collection[str], static_only: bool) -> list[ast.Name]:
    """Uses of `names` as bare names that the code does not bind in that scope, so they
    reach the builtin (or a module the code never imported).

    Python's scopes: a function sees its own bindings (parameters, assignments, loop
    targets, nested defs, imports), its enclosing functions' and the module's; a class body
    sees its own, which its methods do not. `static_only` (the safety check) counts only
    function bindings, which Python fixes when it compiles the function: a module or class
    binding may come after the use (`eval(s)` above `eval = 0`, or a function called
    before it), so under one a use still counts as the builtin."""
    found: list[ast.Name] = []

    def visit(node: ast.AST, inherited: frozenset[str], here: frozenset[str]) -> None:
        if isinstance(node, _FUNCTION_SCOPES):
            outer, inner, own = _function_parts(node)
            for child in outer:  # decorators, defaults, annotations: the enclosing scope
                visit(child, inherited, here)
            scope = inherited | own
            for child in inner:
                visit(child, scope, scope)
            return
        if isinstance(node, ast.ClassDef):
            for child in [*node.decorator_list, *node.bases, *node.keywords]:
                visit(child, inherited, here)
            body = here if static_only else here | _bindings(node.body)
            for child in node.body:  # a method inherits `inherited`, not the class body
                visit(child, inherited, body)
            return
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and (
                node.id in names and node.id not in here):
            found.append(node)
        for child in ast.iter_child_nodes(node):
            visit(child, inherited, here)

    module = frozenset() if static_only else _bindings(tree.body)
    for child in tree.body:
        visit(child, module, module)
    return found


def _function_parts(node: ast.AST) -> tuple[list[ast.AST], list[ast.AST], frozenset[str]]:
    """(what the enclosing scope evaluates, the scope's own code, the names it binds)."""
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
        a = node.args
        args = [*a.posonlyargs, *a.args, *a.kwonlyargs, *filter(None, [a.vararg, a.kwarg])]
        outer = [*a.defaults, *filter(None, a.kw_defaults)]
        if isinstance(node, ast.Lambda):
            return outer, [node.body], frozenset(x.arg for x in args) | _bindings([node.body])
        outer += [*node.decorator_list, *filter(None, [x.annotation for x in args]),
                  *filter(None, [node.returns])]
        return outer, node.body, frozenset(x.arg for x in args) | _bindings(node.body)
    first, *rest = node.generators
    results = [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
    inner = [first.target, *first.ifs, *rest, *results]
    return [first.iter], inner, _bindings([g.target for g in node.generators])


def _bindings(nodes: list[ast.AST]) -> frozenset[str]:
    """Names bound by `nodes` in their own scope, not in scopes nested in them; names
    declared global bind in the module instead."""
    bound: set[str] = set()
    declared: set[str] = set()
    stack = list(nodes)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
            bound.add(node.id)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            bound.add(node.name)
            continue
        elif isinstance(node, _FUNCTION_SCOPES):
            continue
        elif isinstance(node, ast.Import | ast.ImportFrom):
            bound.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            bound.add(node.rest)
        elif isinstance(node, ast.Global | ast.Nonlocal):
            declared.update(node.names)
        stack.extend(ast.iter_child_nodes(node))
    return frozenset(bound - declared)


def _called_name(call: ast.Call) -> str | None:
    """"MRStep" for both MRStep(...) and mrjob.step.MRStep(...)."""
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _imported_modules(node: ast.Import | ast.ImportFrom) -> list[str]:
    """Full module names an import statement brings in.
    `from pyspark import sql` counts as pyspark.sql, the same as `import pyspark.sql`."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    base = node.module or ""
    return [base, *(f"{base}.{alias.name}" for alias in node.names)]


def _is_within(module: str, banned: str) -> bool:
    return module == banned or module.startswith(banned + ".")
