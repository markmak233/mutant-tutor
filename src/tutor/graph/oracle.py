"""The independent oracle (write_oracle): a second model, of another family, fills in the
draft's oracle interface from the question alone, never seeing the reference, the tests or
the mutants, so that rule 4 compares two readings of the question. Also the checks that an
interface is one (signatures, docstrings, stub bodies) and that the oracle model filled it.
"""

from __future__ import annotations

import ast
import hashlib
import json

from pydantic import ValidationError

from tutor.graph import prompts
from tutor.graph.nodes import Deps, describe_error, route, stream_args, tokens_spent
from tutor.graph.state import ItemState, OracleDraft
from tutor.validation.safety import CORE_IMPORTS, find_hazards, workspace_allow_imports

ORACLE_SCHEMA = OracleDraft.model_json_schema()


# --- write_oracle -----------------------------------------------------------------------

def write_oracle(state: ItemState, deps: Deps) -> ItemState:
    """Fill in the draft's oracle interface with the oracle model, which sees the question,
    the interface and the sample data, never the reference, tests or mutants. It is written
    again only when one of those changed, so a repair that fixes something else keeps it.
    A problem goes to repair like a validation failure: the question or the interface did
    not tell the oracle model enough, and the generating model can fix either."""
    draft = state.get("draft")
    if deps.oracle_config is None:
        return {}
    if draft is None:  # the reply could not be read; repair asks again
        return {"oracle_status": "skipped"}
    question = deps.profile.question(draft)
    interface = draft.oracle_py
    problems = _interface_problems(interface)
    if problems and all(p.endswith(_HAS_BODY) for p in problems):
        # A whole oracle where an interface was asked for (DeepSeek-V4-Pro did it on every
        # first draft of a batch): keep the signatures and docstrings, drop the bodies. The
        # oracle model never sees the bodies, so its reading stays independent.
        interface, problems = _as_interface(interface), []
    if not question:
        problems.insert(0, "student.c does not start with a /* ... */ comment: the question "
                        "goes there" if deps.language == "c"
                        else "student.py has no module docstring: the question goes there")
    if problems:
        return _oracle_failed(state, "oracle_py must be an interface for another model to "
                              "fill in: " + "; ".join(problems))
    key = hashlib.sha256(json.dumps(
        [question, interface, draft.data_files], sort_keys=True).encode()).hexdigest()
    if key == state.get("oracle_key") and state.get("oracle_py"):
        return {"draft": draft.model_copy(update={"oracle_py": state["oracle_py"]}),
                "oracle_status": "kept"}
    messages = [{"role": "system", "content": prompts.oracle_spec(deps.language)},
                {"role": "user", "content": prompts.oracle_prompt(
                    question, interface, draft.data_files, language=deps.language)}]
    allowed = CORE_IMPORTS | workspace_allow_imports(deps.workspace)
    spent: ItemState = {}
    for _ in range(1 + ORACLE_RETRIES):
        reply = deps.chat(deps.oracle_config, messages, purpose="oracle",
                          log_path=deps.llm_log, max_tokens=deps.max_tokens,
                          response_schema=ORACLE_SCHEMA, thinking=deps.oracle_thinking,
                          **stream_args(state, deps))
        spent = tokens_spent({**state, **spent}, reply)
        try:
            code = OracleDraft.model_validate_json(reply.text).oracle_py
        except ValidationError as e:
            problem = "its reply does not fit the JSON schema: " + "; ".join(
                describe_error(err) for err in e.errors()[:5])
            continue
        problem = _filled_problems(interface, code) or "; ".join(find_hazards(code, allowed))
        if not problem:
            return {"draft": draft.model_copy(update={"oracle_py": code}), **spent,
                    "oracle_key": key, "oracle_py": code, "oracle_status": "written"}
        # The oracle model's own mistake (an import outside the allowlist, a function left
        # unwritten): it fixes it, since the generating model cannot change its code.
        messages = [*messages[:2], {"role": "assistant", "content": reply.text},
                    {"role": "user", "content": f"That oracle.py has problems: {problem}. "
                                                "Return it again with them fixed."}]
    return {**_oracle_failed(state, f"the oracle model could not implement oracle_py: "
                             f"{problem}. Make the interface's docstrings and the question "
                             "say exactly what each function takes and returns."),
            **spent}


def after_oracle(state: ItemState, deps: Deps) -> str:
    """Validate the draft, unless the oracle could not be written: then the round already
    failed, and validating the unfilled interface would only replace that failure."""
    return route(state, deps) if state.get("oracle_status") == "failed" else "validate"


def _oracle_failed(state: ItemState, failure: str) -> ItemState:
    """Not written: shown to repair as a format failure of this round."""
    attempts = [*state.get("attempts", []), {
        "round": state.get("round", 0), "failure": f"format: {failure}",
        "format_error": True, "dropped": [], "unchanged": state.get("unchanged", False),
        "model": state.get("model", "")}]
    return {"failure": f"format: {failure}", "format_error": True, "attempts": attempts,
            "oracle_status": "failed"}


def _functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    """The module's top-level functions by name."""
    return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}


def _is_stub(func: ast.FunctionDef) -> bool:
    """Only `raise NotImplementedError` after an optional docstring: an interface line."""
    body = func.body[1:] if ast.get_docstring(func) is not None else func.body
    return (len(body) == 1 and isinstance(body[0], ast.Raise)
            and "NotImplementedError" in ast.unparse(body[0]))


ORACLE_RETRIES = 1  # the oracle model's second try, after it was told what was wrong
_HAS_BODY = "has a body; give only its docstring and raise NotImplementedError"


def _as_interface(code: str) -> str:
    """`code` with every top-level function body replaced by its docstring and
    `raise NotImplementedError`; imports and constants stay."""
    tree = ast.parse(code)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not _is_stub(node):
            doc = ast.get_docstring(node)
            node.body = ([ast.Expr(ast.Constant(doc))] if doc is not None else []) + [
                ast.parse("raise NotImplementedError").body[0]]
    return ast.unparse(ast.fix_missing_locations(tree)) + "\n"


def _interface_problems(code: str) -> list[str]:
    """Why `code` is not an oracle interface (functions with docstrings and stub bodies)."""
    try:
        functions = _functions(ast.parse(code))
    except SyntaxError as e:
        return [f"it does not parse: {e.msg} (line {e.lineno})"]
    if not functions:
        return ["it defines no top-level function"]
    return [f"{name} {_HAS_BODY}" for name, func in functions.items() if not _is_stub(func)]


def _filled_problems(interface: str, code: str) -> str:
    """Why the oracle model's code does not fill `interface`: a function missing or still
    a stub; "" when every function is written."""
    try:
        written = _functions(ast.parse(code))
    except SyntaxError as e:
        return f"its oracle.py does not parse: {e.msg} (line {e.lineno})"
    wanted = _functions(ast.parse(interface))
    missing = sorted(set(wanted) - set(written))
    if missing:
        return "its oracle.py lacks " + ", ".join(missing)
    stubs = sorted(name for name in wanted if _is_stub(written[name]))
    return f"it left {', '.join(stubs)} unwritten" if stubs else ""
