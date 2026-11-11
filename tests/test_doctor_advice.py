"""Structured advice gates use supplied synthetic facts, never models or installations."""

import builtins
import copy
import json
import socket
import subprocess
import urllib.request
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from tutor import compat, config, llm
from tutor.doctor_advice.gate import review_reply
from tutor.doctor_advice.models import (
    MAX_REPLY_CHARS,
    Advice,
    AdviceContext,
    AdviceError,
    Change,
    reply_schema,
)


def context(**overrides):
    """The caller, not advice text, supplies the permission boundary and Python choices."""
    fields = {"requires": {"demo-lib": "1.0"}, "installed": frozenset({"transitive-lib"}),
              "current_python": "3.11", "available_pythons": frozenset({"3.11", "3.12", "3.13"})}
    return AdviceContext(**(fields | overrides))


def change(kind="version", *, python=None, package="demo-lib", spec=">=1.1,<2"):
    return {"kind": kind, "python": python, "package": package, "spec": spec}


def reply(changes=None, **overrides):
    """Build strict wire objects; every nullable field remains present."""
    return {"cause": "A synthetic dependency mismatch", "version_changes": ["Use a newer release"],
            "changes": [change()] if changes is None else changes,
            "needs_new_package": None} | overrides


def review(data=None, facts=None):
    return review_reply(json.dumps(reply() if data is None else data), facts or context())


def test_permitted_advice_is_unverified_and_does_not_apply_changes():
    result = review()
    assert result.status == "accepted" and result.verified is False
    assert result.changes == (Change("version", package="demo-lib", spec=">=1.1,<2"),)
    assert result.needs_new_package is None and "not installed or verified" in result.reason


@pytest.mark.parametrize("spec", ["==1.2.3", "!=1.2", ">=1", "<=2", ">0.9", "<2", "~=1.2",
                                 "~=1.2.3", " >= 1.2 , < 2 "])
def test_supported_numeric_constraint_subset(spec):
    result = review(reply([change(spec=spec)]))
    assert result.status == "accepted" and not result.verified
    assert " " not in result.changes[0].spec


@pytest.mark.parametrize("spec", [
    "1.2", "~=1", "===1.2", "==1.*", "==1.2rc1", "==1!2", "==1.0+local", "==1.0.post1",
    ">=1; python_version > '3'", "demo-lib>=1", "demo-lib[extra]>=1", "-e demo-lib",
    "https://example.invalid/lib.whl", "demo-lib @ https://example.invalid/lib.whl",
    "git+https://example.invalid/lib", ">=1 --index-url https://example.invalid/",
    ">=1; echo unsafe", ">=1\necho unsafe", ">=1\r", ">=1,$(echo unsafe)", ">=1,`echo unsafe`",
    ">=1,", ",<2", ">=１", "\u00a0>=1", "(>=1)",
])
def test_broader_or_executable_constraint_syntax_is_refused(spec):
    result = review(reply([change(spec=spec)]))
    assert result.status == "rejected" and result.changes == () and not result.verified


@pytest.mark.parametrize("package", ["demo-lib", "DEMO_LIB", "Demo...Lib", "demo-._-lib"])
def test_distribution_aliases_share_only_the_same_permission(package):
    result = review(reply([change(package=package)]))
    assert result.status == "accepted" and result.changes[0].package == "demo-lib"


def test_installed_transitive_distribution_is_allowed_but_nearby_spelling_is_not():
    assert review(reply([change(package="TRANSITIVE_LIB")])).status == "accepted"
    assert review(reply([change(package="transitive-libs")])).status == "rejected"


@pytest.mark.parametrize("name", ["lookalike-fixer", "demo-lib; echo unsafe", "demo-lib\n",
                                 "-demo-lib", "demo-lib-", "demo-lib[extra]", "../demo-lib",
                                 "https://example.invalid/", "dеmo-lib"])
def test_unknown_or_injected_distribution_names_cannot_enter_changes(name):
    assert review(reply([change(package=name)])).status == "rejected"


@pytest.mark.parametrize("name", ["python", "java", "hadoop", "gcc", "javac", "rustc"])
def test_system_tools_are_not_pip_repairs_even_when_present_in_supplied_facts(name):
    facts = context(requires={name: "1"}, installed=frozenset({name}))
    assert review(reply([change(package=name)]), facts).status == "rejected"


def test_compatible_python_is_offered_and_another_version():
    result = review(reply([change("python", python="3.12", package=None, spec=None)]))
    assert result.status == "accepted" and result.changes[0].python == "3.12"


@pytest.mark.parametrize("python,overrides", [
    ("3.15", {}), ("3.11", {}), ("3.12", {"python_locked": True}),
    ("3.10", {"available_pythons": frozenset({"3.10"})}),
    ("3.11", {"current_python": "3.11.9"}),
    ("3.14", {"requires": {"pyspark": "3.5"}, "available_pythons": frozenset({"3.14"})}),
    ("3.12; echo unsafe", {}),
])
def test_unoffered_locked_same_unsupported_or_conflicting_python_is_refused(python, overrides):
    proposed = reply([change("python", python=python, package=None, spec=None)])
    result = review(proposed, context(**overrides))
    assert result.status == "rejected" and result.changes == ()


@pytest.mark.parametrize("python", ["3.12", "3.13"])
def test_companions_come_from_existing_rules_for_this_course_and_python(python):
    facts = context(requires={"MRJOB": "0.7.4"}, current_python=python)
    result = review(reply([change("add", package="SETUPTOOLS", spec=None)]), facts)
    assert result.status == "accepted" and result.changes[0].package == "setuptools"
    pipes = review(reply([change("add", package="Standard_Pipes", spec="<2")]), facts)
    assert (pipes.status == "accepted") == (python == "3.13")


def test_unrelated_companion_rules_never_expand_the_allowlist():
    assert review(reply([change("add", package="standard-pipes", spec=None)])).status == "rejected"


def test_rule_selection_reuses_compat_instead_of_copying_its_package_list(monkeypatch):
    rule = compat.Companion("demo-lib", "3.11", "demo-companion", "", "synthetic rule")
    monkeypatch.setattr(compat, "COMPANIONS", (rule,))
    result = review(reply([change("add", package="demo-companion", spec=None)]))
    assert result.status == "accepted"


@pytest.mark.parametrize("reverse", [False, True])
def test_target_python_companions_are_order_independent(reverse):
    changes = [change("add", package="standard-pipes", spec=None),
               change("python", python="3.13", package=None, spec=None)]
    facts = context(requires={"mrjob": "0.7.4"}, installed=frozenset())
    result = review(reply(list(reversed(changes)) if reverse else changes), facts)
    assert result.status == "accepted" and len(result.changes) == 2


def test_moving_to_an_older_python_does_not_keep_old_companion_permissions():
    facts = context(requires={"mrjob": "0.7.4"}, current_python="3.13", installed=frozenset())
    result = review(reply([change("add", package="standard-pipes", spec=None),
                           change("python", python="3.11", package=None, spec=None)]), facts)
    assert result.status == "rejected" and not result.changes


@pytest.mark.parametrize("changes", [
    [change(), change(package="DEMO_LIB", spec="<1")],
    [change(), change("add", spec=None)],
    [change("python", python="3.12", package=None, spec=None),
     change("python", python="3.13", package=None, spec=None)],
    [change(), change(package="unapproved-name")],
])
def test_duplicate_conflicting_or_partly_invalid_batches_release_nothing(changes):
    result = review(reply(changes))
    assert result.status == "rejected" and result.changes == ()


def test_new_package_is_separate_with_all_changes_withheld():
    result = review(reply(needs_new_package="Unreviewed_Companion"))
    assert result.status == "new-package-review" and not result.verified
    assert result.needs_new_package == "unreviewed-companion" and result.changes == ()
    assert result.cause == "A synthetic dependency mismatch"


@pytest.mark.parametrize("name", ["DEMO_LIB", "gcc", "lookalike; echo unsafe", "name\n"])
def test_new_package_field_cannot_bypass_validation_or_rename_a_known_package(name):
    assert review(reply(needs_new_package=name)).status == "rejected"


def test_new_package_field_does_not_authorize_it_as_a_change():
    result = review(reply([change(package="new-lib")], needs_new_package="new-lib"))
    assert result.status == "rejected" and result.needs_new_package is None


def test_empty_changes_are_stop_or_new_review_never_fixed():
    assert review(reply([], cause="Everything is FIXED")).status == "stopped"
    result = review(reply([], needs_new_package="new-lib"))
    assert result.status == "new-package-review" and not result.verified


@pytest.mark.parametrize("raw", ["not JSON", "```json\n{}\n```", "{} trailing prose", "[]",
                                '{"cause": "one", "cause": "two"}',
                                '{"cause": NaN}', '{"cause": Infinity}',
                                "[" * 2000 + "]" * 2000, " " * (MAX_REPLY_CHARS + 1)])
def test_malformed_ambiguous_or_unbounded_json_is_refused(raw):
    result = review_reply(raw, context())
    assert result.status == "rejected" and result.changes == ()


@pytest.mark.parametrize("field,value", [
    ("cause", None), ("cause", True), ("cause", " "),
    ("version_changes", "text"), ("version_changes", [1]),
    ("changes", {}), ("changes", [None]), ("changes", [change()] * 4),
    ("needs_new_package", []), ("needs_new_package", ""),
])
def test_reply_fields_have_strict_types(field, value):
    assert review(reply(**{field: value})).status == "rejected"


@pytest.mark.parametrize("fields", [
    {"kind": "shell"}, {"kind": ["version"]}, {"python": "3.12"}, {"package": None},
    {"spec": True}, {"spec": None}, {"kind": "python", "python": "3.12"},
    {"kind": "python", "python": 3.12, "package": None, "spec": None},
])
def test_change_fields_cannot_be_coerced_or_mixed_between_kinds(fields):
    assert review(reply([change() | fields])).status == "rejected"


def test_unknown_missing_and_duplicate_fields_are_rejected_at_both_levels():
    for data in (reply(command="echo unsafe"), reply([change() | {"command": "echo unsafe"}])):
        assert review(data).status == "rejected"
    data = reply()
    data.pop("cause")
    assert review(data).status == "rejected"
    data = reply([change()])
    data["changes"][0].pop("spec")
    assert review(data).status == "rejected"
    raw = json.dumps(reply()).replace('"kind": "version"', '"kind": "add", "kind": "version"')
    assert review_reply(raw, context()).status == "rejected"


def test_schema_is_fresh_and_agrees_with_the_strict_wire_fields():
    schema = reply_schema()
    assert set(schema["required"]) == set(reply())
    assert set(schema["properties"]["changes"]["items"]["required"]) == set(change())
    schema["required"].clear()
    schema["properties"]["changes"]["items"]["required"].clear()
    assert review().status == "accepted" and len(reply_schema()["required"]) == 4


def test_context_and_parsed_advice_are_snapshots_and_results_stay_unverified():
    requires, installed, offered = {"Demo_Lib": "1.0"}, ["transitive-lib"], ["3.12"]
    facts = context(requires=requires, installed=installed, available_pythons=offered)
    requires["injected-name"] = "1"
    installed.append("injected-name")
    offered.append("3.15")
    assert "injected-name" not in facts.requires and "injected-name" not in facts.installed
    assert "3.15" not in facts.available_pythons
    with pytest.raises(TypeError):
        facts.requires["new"] = "1"
    result = review(facts=facts)
    for obj, name, value in ((facts, "python_locked", True), (result, "verified", True),
                             (result.changes[0], "package", "new")):
        with pytest.raises(FrozenInstanceError):
            setattr(obj, name, value)
    changes, explanations = [result.changes[0]], ["synthetic explanation"]
    parsed = Advice("synthetic cause", explanations, changes)
    changes.clear()
    explanations.clear()
    assert len(parsed.changes) == len(parsed.version_changes) == 1


@pytest.mark.parametrize("overrides", [
    {"requires": []}, {"requires": {"demo-lib": 1}},
    {"requires": {"demo-lib": "1", "DEMO_LIB": "2"}},
    {"requires": {"invalid name": "1"}}, {"installed": "demo-lib"},
    {"installed": ["demo-lib\n"]}, {"available_pythons": ["3"]},
    {"current_python": "3.11; echo unsafe"}, {"python_locked": 0},
])
def test_invalid_caller_facts_are_refused_before_review(overrides):
    with pytest.raises(AdviceError):
        context(**overrides)


def test_rejection_reasons_do_not_echo_untrusted_values():
    secret = "sk-test-not-a-real-key"
    for data in (reply(command=secret), reply([change(package=secret)]),
                 reply([change(spec=secret)])):
        result = review(data)
        assert result.status == "rejected" and secret not in result.reason


def test_review_is_pure_and_ignores_process_environment(tmp_path, monkeypatch):
    """Deny external effects while constructing context, parsing and reviewing a reply."""
    raw, fields = json.dumps(reply()), {"demo-lib": "1"}
    unchanged = copy.deepcopy(fields)
    marker = tmp_path / "untouched.txt"
    marker.write_text("synthetic", encoding="utf-8")

    def forbidden(*args, **kwargs):
        raise AssertionError("advice parsing/gating attempted an external effect")

    with monkeypatch.context() as patch:
        patch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
        for target, name in ((builtins, "open"), (Path, "read_text"), (Path, "write_text"),
                             (socket, "socket"), (subprocess, "Popen"),
                             (urllib.request, "urlopen"), (config, "load_env"), (llm, "chat")):
            patch.setattr(target, name, forbidden)
        result = review_reply(raw, context(requires=fields))
    assert result.status == "accepted" and fields == unchanged
    assert marker.read_text(encoding="utf-8") == "synthetic"
    assert list(tmp_path.iterdir()) == [marker]
