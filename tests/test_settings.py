"""Generation settings: defaults, then workspace.toml [generation], then the command line."""

import argparse
import json

import pytest

from tutor.graph.nodes import Deps
from tutor.graph.settings import (
    DEFAULTS,
    DEPS_FIELDS,
    GenerationSettings,
    parse_set,
    resolve,
    settings_schema,
)
from tutor.llm import LLMConfig


def workspace(tmp_path, generation=""):
    (tmp_path / "workspace.toml").write_text('course = "demo"\n' + generation, encoding="utf-8")
    return tmp_path


def test_the_defaults_are_the_values_deps_starts_with(tmp_path):
    """One source of defaults: a Deps built by hand (tests, scripts) runs like the CLI."""
    d = Deps(workspace=tmp_path, batch_dir=tmp_path, config=LLMConfig("p", "glm", "u", "m"))
    assert {name: getattr(d, name) for name in DEPS_FIELDS} == {
        name: getattr(DEFAULTS, name) for name in DEPS_FIELDS}


def test_the_command_line_wins_over_the_workspace_which_wins_over_the_defaults(tmp_path):
    ws = workspace(tmp_path, "[generation]\nmax_rounds = 2\nstronger_repair_from = 1\n")
    settings = resolve(ws, parse_set(["max_rounds=4", "thinking=on"]))
    assert (settings.max_rounds, settings.stronger_repair_from, settings.thinking) == (4, 1, True)
    assert settings.max_tokens == DEFAULTS.max_tokens  # set nowhere


@pytest.mark.parametrize(("pairs", "expected"), [
    (["max_round=2"], "max_round: Extra inputs are not permitted"),  # a typo
    (["max_rounds=9"], "max_rounds: Input should be less than or equal to 5"),
    (["mode=exam"], "mode: Input should be 'learn' or 'review'"),
])
def test_a_wrong_name_or_value_is_named(tmp_path, pairs, expected):
    with pytest.raises(ValueError, match=expected):
        resolve(workspace(tmp_path), parse_set(pairs))


def test_a_set_without_a_value_says_how_to_write_it():
    with pytest.raises(ValueError, match="write name=value"):
        parse_set(["max_rounds"])


def test_a_resumed_batch_keeps_its_settings_and_takes_changes(tmp_path):
    from tutor.graph.__main__ import _read_run, _write_run

    ws = workspace(tmp_path, "[generation]\nmax_rounds = 2\n")
    run = tmp_path / "run.json"
    _write_run(run, {"oracle_profile": "gpt"}, resolve(ws, {}))
    (ws / "workspace.toml").write_text('course = "demo"\n[generation]\nmax_rounds = 5\n',
                                       encoding="utf-8")
    profiles, settings = _read_run(run)
    assert (profiles, settings.max_rounds) == ({"oracle_profile": "gpt"}, 2)  # as planned
    assert resolve(ws, {"max_rounds": "3"}, base=settings).max_rounds == 3


def test_a_run_file_from_before_the_settings_still_resumes(tmp_path):
    from tutor.graph.__main__ import _read_run

    run = tmp_path / "run.json"
    run.write_text(json.dumps({"profile": None, "oracle_profile": "gpt", "thinking": "low",
                               "repair_thinking": "on", "mode": "review", "parallel": 3}),
                   encoding="utf-8")
    profiles, settings = _read_run(run)
    assert profiles == {"oracle_profile": "gpt"}
    assert (settings.thinking, settings.repair_thinking, settings.mode, settings.parallel) == (
        False, True, "review", 3)


def test_named_flags_still_work_and_set_wins_over_them():
    from tutor.graph.__main__ import _given

    args = argparse.Namespace(thinking="on", repair_thinking=None, mode=None, parallel=None,
                              plan_thinking="low", max_count=None,
                              set=["thinking=off"])
    assert _given(args) == {"thinking": "off", "plan_thinking": False}


def test_the_schema_gives_a_form_its_descriptions_and_bounds():
    fields = settings_schema()["properties"]
    assert set(fields) == set(GenerationSettings.model_fields)
    assert fields["max_rounds"]["maximum"] == 5 and fields["max_rounds"]["description"]
    assert all(field.get("description") for field in fields.values())


def test_the_resolved_settings_reach_the_nodes(tmp_path):
    from tutor.graph.__main__ import _deps

    ws = workspace(tmp_path, '[llm.profiles.f]\nprovider = "glm"\nmodel = "glm-5.3-flash"\n'
                             '[llm.roles]\ngenerate = "f"\n[generation]\nmax_rounds = 1\n')
    d = _deps(ws, ws / "batches" / "b", {}, resolve(ws, {"stronger_repair_from": "1"}))
    assert (d.max_rounds, d.stronger_repair_from, d.config.name) == (1, 1, "f")
