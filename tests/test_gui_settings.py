"""Settings in the GUI (step 6): keys in a stand-in `.env`, never the repository's, and a
course's [llm] written with tomlkit, the rest of its workspace.toml kept as written."""

import os
import stat
import tomllib
from pathlib import Path

import pytest

pytest.importorskip("flask")  # the `gui` extra

import tomlkit  # noqa: E402

from tutor.gui import courses, keys, llm_roles, llm_settings, tasks, toml_edit  # noqa: E402
from tutor.gui.app import create_app  # noqa: E402

TOKEN = "test-token"
BASE = "http://127.0.0.1:5000"
SECRET = "sk-test-not-a-real-key-WXYZ"  # made up, and plainly so for the secret scanner
ENV = """# Settings for the commands
WORKSPACE_DIR=/somewhere/course
OPENAI_API_KEY=sk-old-not-a-real-key-1234
ZHIPU_API_KEY='short'
"""
TOML = """course = "demo"   # the course id

[requires]
python = "3.11"

[llm.profiles.cheap]
provider = "glm"
model = "glm-5.3-flash"
api_key_env = "ZHIPU_API_KEY"
reasoning_effort = "low"   # its lowest setting

[llm.roles]
vision = "cheap"       # reads image slides
generate = "cheap"

# A second provider, for the oracle.
[llm.profiles.other]
provider = "openai"
model = "gpt-6.1-terra"
api_key_env = "OPENAI_API_KEY"

[generation]
thinking = true
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def root(tmp_path):
    _write(tmp_path / "courses" / "demo-course" / "workspace.toml", TOML)
    _write(tmp_path / "courses" / "bare" / "workspace.toml", 'course = "bare"\n')
    _write(tmp_path / "repo" / ".env", ENV)
    return tmp_path / "courses"


@pytest.fixture
def env_file(root):
    return root.parent / "repo" / ".env"


@pytest.fixture
def course(root):
    return courses.get_course(root, "demo-course")


def _toml(course):
    return (course.workspace.root / "workspace.toml").read_text(encoding="utf-8")


def _browser(root, env_file):
    client = create_app(root, TOKEN, port=5000, env_file=env_file).test_client()
    client.get(f"/?token={TOKEN}", base_url=BASE)
    return client


def post(client, path, data=None):
    return client.post(path, base_url=BASE, data=data or {}, headers={"Origin": BASE})


# --- keys --------------------------------------------------------------------------------------


def test_keys_are_listed_by_name_with_only_their_tail(env_file, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ZHIPU_API_KEY", "from the shell")
    rows = {r.name: r for r in keys.rows(env_file, {"DEEPSEEK_API_KEY": ["c: ds"]})}

    assert list(rows) == ["DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ZHIPU_API_KEY"]  # no WORKSPACE_DIR
    assert rows["OPENAI_API_KEY"].tail == "…1234"
    assert rows["ZHIPU_API_KEY"].tail == ""  # set, but too short to show any of it
    assert rows["ZHIPU_API_KEY"].shadowed  # the shell's value wins for commands
    assert not rows["DEEPSEEK_API_KEY"].is_set and rows["DEEPSEEK_API_KEY"].used_by == ["c: ds"]


def test_a_key_is_added_or_replaced_and_every_other_line_kept(env_file):
    assert keys.set_key(env_file, "DEEPSEEK_API_KEY", f"  {SECRET}\n") is None
    assert keys.set_key(env_file, "OPENAI_API_KEY", "sk-new-not-a-real-key") is None

    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert lines == ["# Settings for the commands", "WORKSPACE_DIR=/somewhere/course",
                     "OPENAI_API_KEY=sk-new-not-a-real-key", "ZHIPU_API_KEY='short'",
                     f"DEEPSEEK_API_KEY={SECRET}"]
    if os.name == "posix":
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert [p.name for p in env_file.parent.iterdir()] == [".env"]  # no temp file left


def test_a_later_line_of_the_same_name_does_not_stay_to_mislead(env_file):
    env_file.write_text("A_KEY=one\nA_KEY=two\n", encoding="utf-8")

    keys.set_key(env_file, "A_KEY", "three")

    assert env_file.read_text(encoding="utf-8") == "A_KEY=three\n"


@pytest.mark.parametrize(("name", "value", "problem"), [
    ("lower_key", "x", "capital letters"),
    ("A", "x", "capital letters"),
    ("A_KEY", "", "Paste"),
    ("A_KEY", "two words", "does not look like a key"),
    ("A_KEY", "a'b", "does not look like a key"),
])
def test_odd_names_and_values_are_refused(env_file, name, value, problem):
    before = env_file.read_text(encoding="utf-8")

    assert problem in keys.set_key(env_file, name, value)
    assert env_file.read_text(encoding="utf-8") == before


def test_only_keys_can_be_removed(env_file):
    assert keys.delete_key(env_file, "WORKSPACE_DIR", {}) == "Only keys can be removed here."
    assert keys.delete_key(env_file, "OPENAI_API_KEY", {}) is None
    assert "OPENAI_API_KEY" not in env_file.read_text(encoding="utf-8")
    assert "WORKSPACE_DIR=/somewhere/course" in env_file.read_text(encoding="utf-8")
    assert keys.delete_key(env_file, "OPENAI_API_KEY", {}) == "This key is not in .env."


def test_a_key_set_from_the_page_is_never_sent_back(root, env_file, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    client = _browser(root, env_file)

    response = post(client, "/keys/set", {"name": "DEEPSEEK_API_KEY", "value": SECRET})
    page = client.get("/keys/", base_url=BASE).get_data(as_text=True)

    assert response.headers["Location"] == "/keys/?notice=key-saved"
    assert SECRET not in page and "…WXYZ" in page and "sk-old" not in page
    assert f"DEEPSEEK_API_KEY={SECRET}" in env_file.read_text(encoding="utf-8")


def test_a_refused_key_is_not_put_back_in_the_form(root, env_file):
    response = post(_browser(root, env_file), "/keys/set",
                    {"name": "A_KEY", "value": "has a space " + SECRET})

    assert response.status_code == 400 and SECRET not in response.get_data(as_text=True)


def test_the_page_removes_a_key_and_lists_who_uses_one(root, env_file):
    client = _browser(root, env_file)

    page = client.get("/keys/", base_url=BASE).get_data(as_text=True)
    assert "demo-course: other" in page and "WORKSPACE_DIR" not in page

    response = post(client, "/keys/delete", {"name": "OPENAI_API_KEY"})
    assert response.headers["Location"] == "/keys/?notice=key-deleted"
    assert post(client, "/keys/delete", {"name": "WORKSPACE_DIR"}).status_code == 400


def test_the_keys_page_is_post_only_for_changes(root, env_file):
    client = _browser(root, env_file)

    assert client.get("/keys/set", base_url=BASE).status_code == 405
    assert client.post("/keys/set", base_url=BASE, data={"name": "A_KEY", "value": "x"},
                       headers={"Origin": "http://evil.example"}).status_code == 403


# --- a course's models -------------------------------------------------------------------------


def _form(**changes):
    return {"provider": "deepseek", "model": "deepseek-v4-flash",
            "api_key_env": "DEEPSEEK_API_KEY", **changes}


@pytest.mark.parametrize(("name", "changes", "problem"), [
    ("Big", {}, "lowercase"),
    ("ds", {"provider": "acme"}, "Choose a provider"),
    ("ds", {"api_key_env": "deepseek"}, "variable in .env"),
    ("ds", {"model": SECRET}, "holds a key"),  # a key pasted in the wrong box
    ("ds", {"provider": "custom"}, "needs a base_url"),
    ("ds", {"base_url": "ftp://x"}, "http"),
    ("ds", {"timeout_s": "0"}, "1 to 3600"),
    ("ds", {"reasoning_effort": "very low"}, "one word"),
    ("ds", {"temperature": "0.2"}, 'temperature can only be "default"'),
    ("ds", {"parallel_start": "first-event+"}, "Seconds after the first reply begins"),
    ("ds", {"parallel_start": "first-event+", "parallel_start_s": "900"}, "0.5 to 600"),
    ("ds", {"parallel_start": "later"}, "from the list"),
    ("ds", {"max_parallel": "0"}, "1 to 32"),
    ("ds", {"max_parallel": "two"}, "whole number"),
])
def test_a_model_is_checked_by_llm_pys_rules(course, name, changes, problem):
    data, problems = llm_settings.check_profile(course, name, _form(**changes))

    assert data is None and problem in " ".join(problems)


def test_a_new_model_keeps_the_rest_of_the_file_as_written(course):
    before = _toml(course)
    data, _ = llm_settings.check_profile(course, "ds", _form(timeout_s="900"))

    assert llm_settings.save_profile(course, None, "ds", data) == []

    text = _toml(course)
    assert "# A second provider, for the oracle." in text and "# reads image slides" in text
    assert 'course = "demo"   # the course id' in text
    assert tomllib.loads(text)["llm"]["profiles"]["ds"] == {
        "provider": "deepseek", "model": "deepseek-v4-flash",
        "api_key_env": "DEEPSEEK_API_KEY", "timeout_s": 900}
    assert (course.workspace.root / "workspace.toml.bak").read_text(encoding="utf-8") == before


def test_a_new_model_is_set_apart_from_the_table_after_it(root):
    bare = courses.get_course(root, "bare")
    _write(bare.workspace.root / "workspace.toml",
           'course = "bare"\n\n[llm.profiles.a]\nprovider = "glm"\nmodel = "m"\n\n'
           '[llm.roles]\ngenerate = "a"\n')
    data, _ = llm_settings.check_profile(bare, "b", {"provider": "lmstudio", "model": "x"})

    llm_settings.save_profile(bare, None, "b", data)

    assert 'model = "x"\n\n[llm.roles]' in _toml(bare)


def test_a_models_own_temperature_is_saved_and_kept_or_dropped_by_the_form(root, env_file, course):
    """The form's choice writes `temperature = "default"`; saving the model again keeps it,
    and choosing 0 takes the line out (an edit drops what the form does not send)."""
    client = _browser(root, env_file)
    claude = {"name": "claude", "provider": "anthropic", "model": "claude-opus-5-5",
              "api_key_env": "ANTHROPIC_API_KEY"}

    post(client, "/c/demo-course/models/save", {**claude, "temperature": "default"})
    assert tomllib.loads(_toml(course))["llm"]["profiles"]["claude"]["temperature"] == "default"
    page = client.get("/c/demo-course/models/m/claude/", base_url=BASE).get_data(as_text=True)
    assert '<option value="default" selected>' in page

    post(client, "/c/demo-course/models/save",
         {**claude, "original": "claude", "temperature": "default", "timeout_s": "900"})
    saved = tomllib.loads(_toml(course))["llm"]["profiles"]["claude"]
    assert saved["temperature"] == "default" and saved["timeout_s"] == 900

    post(client, "/c/demo-course/models/save", {**claude, "original": "claude", "temperature": ""})
    assert "temperature" not in tomllib.loads(_toml(course))["llm"]["profiles"]["claude"]


def test_how_parallel_calls_start_is_saved_shown_and_left_to_the_provider(
        root, env_file, course):
    """Owner, 2026-11-12: each provider its own way to start a lecture's other calls, the
    seconds adjustable, and a cap. The choice and its seconds are one value in the file
    and split again on the page; the provider's own (empty) writes nothing, so it follows
    the tool; saving without the fields takes them out."""
    client = _browser(root, env_file)
    ds = {"name": "ds", **_form()}

    new = _form_page(client, "/c/demo-course/models/new?provider=deepseek&apply=1")
    assert "own: first-event+10</option>" in new  # deepseek's measured default, shown

    post(client, "/c/demo-course/models/save", {**ds, "parallel_start": "first-event+",
                                               "parallel_start_s": "2.5", "max_parallel": "3"})
    saved = tomllib.loads(_toml(course))["llm"]["profiles"]["ds"]
    assert (saved["parallel_start"], saved["max_parallel"]) == ("first-event+2.5", 3)
    page = _form_page(client, "/c/demo-course/models/m/ds/")
    assert '<option value="first-event+" selected>' in page
    assert 'name="parallel_start_s" value="2.5"' in page and 'name="max_parallel" value="3"' in page

    post(client, "/c/demo-course/models/save", {**ds, "original": "ds",
                                               "parallel_start": "first-event+",
                                               "parallel_start_s": "30"})
    saved = tomllib.loads(_toml(course))["llm"]["profiles"]["ds"]
    assert saved["parallel_start"] == "first-event+30" and "max_parallel" not in saved

    post(client, "/c/demo-course/models/save", {**ds, "original": "ds", "parallel_start": "",
                                               "parallel_start_s": "30"})
    assert "parallel_start" not in tomllib.loads(_toml(course))["llm"]["profiles"]["ds"]


def test_measure_start_runs_the_timing_command_on_one_chat_model(root, env_file, course,
                                                                 monkeypatch):
    """The Models page's "Measure start" (owner, 2026-11-12): a task running
    `python -m tutor.llm_timing NAME` on that model only; offered for chat models, not for
    an embedding-only one or an assistant, and refused for a name not in the file."""
    started = []

    def fake_start(course_root, title, commands, env, meta=None):
        started.append((title, commands, meta))
        return tasks.Task("20260102-100000-0abcde", course_root, title, tasks.RUNNING, "",
                          None, commands, [], 1, meta or {})

    monkeypatch.setattr(tasks, "start", fake_start)
    client = _browser(root, env_file)
    post(client, "/c/demo-course/models/save", {"name": "ds", **_form()})
    page = _form_page(client, "/c/demo-course/models/")
    assert page.count('action="/c/demo-course/models/timing"') == sum(
        llm_settings.measurable(p) for p in llm_settings.profiles(course))
    assert "Measure start" in page

    response = post(client, "/c/demo-course/models/timing", {"profile": "ds"})
    assert response.status_code == 303
    title, commands, meta = started[0]
    assert commands[0][-3:] == ["-m", "tutor.llm_timing", "ds"] and meta == {"kind": "timing"}
    assert post(client, "/c/demo-course/models/timing", {"profile": "nope"}).status_code == 404

    assert not llm_settings.measurable(llm_settings.ProfileView(
        "vec", {"provider": "lmstudio", "model": "nomic"}, ["embedding"]))
    assert not llm_settings.measurable(llm_settings.ProfileView(
        "cc", {"provider": "assistant"}, []))


def test_renaming_a_model_moves_its_roles_and_keeps_other_comments(course):
    data = {"provider": "glm", "model": "glm-5.3-flash", "api_key_env": "ZHIPU_API_KEY",
            "reasoning_effort": "low"}

    assert llm_settings.save_profile(course, "cheap", "glm-flash", data) == []

    parsed = tomllib.loads(_toml(course))["llm"]
    assert "cheap" not in parsed["profiles"] and parsed["profiles"]["glm-flash"] == data
    assert parsed["roles"] == {"vision": "glm-flash", "generate": "glm-flash"}
    assert "# its lowest setting" in _toml(course)  # an unchanged line keeps its comment


def test_a_model_in_use_cannot_be_removed(course):
    assert "used by generate, vision" in llm_settings.delete_profile(course, "cheap")[0]
    assert llm_settings.delete_profile(course, "other") == []
    assert "other" not in tomllib.loads(_toml(course))["llm"]["profiles"]


def test_roles_are_saved_and_one_set_to_none_is_removed(course):
    assert llm_roles.save_roles(course, {"oracle": "other", "vision": ""}) == []

    roles = tomllib.loads(_toml(course))["llm"]["roles"]
    assert roles == {"generate": "cheap", "oracle": "other"}  # generate was not offered
    assert "# A second provider, for the oracle." in _toml(course)


def test_an_oracle_as_close_as_one_model_is_warned_about_not_refused(course):
    """Owner, 2026-11-07: not everyone pays for two providers; the page warns instead."""
    assert llm_roles.save_roles(course, {"oracle": "cheap"}, dry_run=True) == []
    roles = llm_roles.merged_roles(course, {"oracle": "cheap"})
    warnings = llm_roles.role_warnings(course, roles, None)
    assert len(warnings) == 1 and "one model (cheap)" in warnings[0]


def test_a_role_cannot_name_a_missing_model(course):
    assert llm_roles.save_roles(course, {"oracle": "nowhere"}) == [
        "oracle: no model named nowhere."]


def test_models_are_copied_into_a_course_that_has_none(root, course):
    bare = courses.get_course(root, "bare")

    assert llm_roles.copy_models(bare, course) == []
    assert tomllib.loads(_toml(bare))["llm"] == tomllib.loads(_toml(course))["llm"]
    assert "has models already" in llm_roles.copy_models(course, bare)[0]


def test_a_change_that_would_touch_another_table_is_not_written(course, monkeypatch):
    before = _toml(course)
    real = llm_settings._tables

    def wrong(doc):  # as if tomlkit had changed more than [llm]
        doc["generation"]["thinking"] = False
        return real(doc)

    monkeypatch.setattr(llm_settings, "_tables", wrong)
    problems = llm_roles.save_roles(course, {"oracle": "other"})

    assert "left as it was" in problems[0] and _toml(course) == before


def test_a_new_key_goes_after_the_tables_last_key_not_its_trailing_comment():
    """Pins tomlkit's Container._insert_after, which toml_edit.put relies on."""
    doc = tomlkit.parse('[llm.roles]\nvision = "a"  # x\n\n# about the next table\n[next]\n')

    toml_edit.put(doc["llm"]["roles"], "oracle", "b")

    assert tomlkit.dumps(doc) == ('[llm.roles]\nvision = "a"  # x\noracle = "b"\n\n'
                                  '# about the next table\n[next]\n')


# --- the model form follows its provider (owner, 2026-11-07) -----------------------------------


def test_a_providers_own_url_is_left_out_and_anothers_refused(course):
    data, problems = llm_settings.check_profile(course, "ds", _form(
        base_url="https://api.deepseek.com/"))
    assert problems == [] and "base_url" not in data  # its own: llm.py's default

    data, _ = llm_settings.check_profile(course, "z", _form(
        provider="glm", model="glm-5.3-flash", base_url="https://api.z.ai/api/paas/v4"))
    assert data["base_url"] == "https://api.z.ai/api/paas/v4"  # another site of glm: kept

    for provider in ("glm", "custom"):  # changed without Apply; a known one as custom
        _, problems = llm_settings.check_profile(course, "x", _form(
            provider=provider, base_url="https://api.deepseek.com"))
        assert "deepseek's own address" in problems[0] and "Apply" in problems[0]


def _form_page(client, path):
    return client.get(path, base_url=BASE).get_data(as_text=True)


def test_the_form_shows_what_the_provider_brings(root, env_file):
    client = _browser(root, env_file)

    text = _form_page(client, "/c/demo-course/models/new")
    assert 'option value="" selected>choose one' in text and 'class="enter-saves"' in text
    assert 'name="timeout_s" value="900"' in text  # a new model starts at 900 s
    # Apply: every field comes back, and the provider's own address and thinking switch.
    text = _form_page(client, "/c/demo-course/models/new?apply=1&name=ds&provider=deepseek"
                              "&model=deepseek-v4-flash&api_key_env=DEEPSEEK_API_KEY")
    assert 'name="base_url" value="https://api.deepseek.com"' in text
    assert 'placeholder="built in: thinking {&#34;type&#34;: &#34;disabled&#34;}' in text
    assert 'name="model" value="deepseek-v4-flash"' in text and 'name="name" value="ds"' in text
    # Changed to glm with deepseek's address still shown: glm's own replaces it; one typed
    # in stays.
    text = _form_page(client, "/c/demo-course/models/new?apply=1&provider=glm"
                              "&base_url=https://api.deepseek.com")
    assert 'name="base_url" value="https://open.bigmodel.cn/api/paas/v4"' in text
    text = _form_page(client, "/c/demo-course/models/new?apply=1&provider=glm"
                              "&base_url=https://api.z.ai/api/paas/v4")
    assert 'name="base_url" value="https://api.z.ai/api/paas/v4"' in text
    text = _form_page(client, "/c/demo-course/models/new?apply=1&provider=custom"
                              "&base_url=https://api.deepseek.com")
    assert 'value="" placeholder="https://api.example.com/v1" required' in text
    text = _form_page(client, "/c/demo-course/models/new?apply=1&provider=assistant")
    assert 'name="base_url" value="" disabled' in text
    assert 'name="api_key_env" value="" disabled' in text
    # Editing a model with no base URL in the file shows the one it uses; its time limit
    # stays as the file has it (none: 300 s), and Apply keeps one cleared by hand.
    text = _form_page(client, "/c/demo-course/models/m/cheap/")
    assert 'name="base_url" value="https://open.bigmodel.cn/api/paas/v4"' in text
    assert 'name="timeout_s" value=""' in text
    text = _form_page(client, "/c/demo-course/models/new?apply=1&provider=glm&timeout_s=")
    assert 'name="timeout_s" value=""' in text


def test_saving_the_shown_own_url_writes_none(root, env_file, course):
    response = post(_browser(root, env_file), "/c/demo-course/models/save",
                    {"name": "ds", **_form(base_url="https://api.deepseek.com")})
    assert response.status_code == 303
    assert "base_url" not in tomllib.loads(_toml(course))["llm"]["profiles"]["ds"]


def test_a_key_name_used_at_another_host_in_any_course_is_refused(root, env_file, course):
    """A changed URL (a moved provider, or a tampered list) must not send a key elsewhere:
    one key name goes to one host, across the courses. The same host passes, as does
    saving a profile again, and a key name at a local server with no key is not one."""
    client = _browser(root, env_file)
    _write(root / "bare" / "workspace.toml", 'course = "bare"\n\n[llm.profiles.o]\n'
           'provider = "custom"\nmodel = "m"\napi_key_env = "ACME_API_KEY"\n'
           'base_url = "https://api.acme.example/v1"\n')

    # OPENAI_API_KEY goes to api.openai.com from `other`: a custom server with it is refused.
    bad = post(client, "/c/demo-course/models/save",
               {"name": "x", **_form(provider="custom", base_url="https://evil.example/v1",
                                     api_key_env="OPENAI_API_KEY")})
    assert bad.status_code == 400
    text = bad.get_data(as_text=True)
    assert "OPENAI_API_KEY is the key other (demo) sends to api.openai.com" in text
    assert "would send it to evil.example" in text
    assert "x" not in tomllib.loads(_toml(course))["llm"]["profiles"]
    # Across courses too: bare's ACME_API_KEY goes to api.acme.example.
    bad = post(client, "/c/demo-course/models/save",
               {"name": "y", **_form(provider="custom", base_url="https://api.other.example/v1",
                                     api_key_env="ACME_API_KEY")})
    assert "ACME_API_KEY is the key o (bare) sends to api.acme.example" in bad.get_data(
        as_text=True)
    # The same host (another path), a profile saved again, and no key: all pass.
    ok = post(client, "/c/demo-course/models/save",
              {"name": "z", **_form(provider="custom", base_url="https://api.openai.com/v2",
                                    api_key_env="OPENAI_API_KEY")})
    assert ok.status_code == 303
    again = post(client, "/c/demo-course/models/save",
                 {"name": "other", "original": "other", **_form(provider="openai",
                                                                model="gpt-6.1-terra",
                                                                api_key_env="OPENAI_API_KEY")})
    assert again.status_code == 303
    local = post(client, "/c/demo-course/models/save",
                 {"name": "q", **_form(provider="ollama", model="qwen3:0.6b", api_key_env="")})
    assert local.status_code == 303


def test_the_models_page_shows_each_key_set_or_not(root, env_file, monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env_file.write_text("OPENAI_API_KEY=sk-x-not-a-real-key\n", encoding="utf-8")

    page = _browser(root, env_file).get("/c/demo-course/models/", base_url=BASE)
    text = page.get_data(as_text=True)

    assert page.status_code == 200 and "glm-5.3-flash" in text
    assert "chip-not-set" in text  # ZHIPU_API_KEY
    assert "sk-x-" not in text


def test_the_form_saves_a_model_and_a_refusal_comes_back_escaped(root, env_file, course):
    client = _browser(root, env_file)

    ok = post(client, "/c/demo-course/models/save", {"name": "ds", **_form()})
    bad = post(client, "/c/demo-course/models/save",
               {"name": "x", **_form(model="<script>alert(1)</script>", provider="acme")})

    assert ok.headers["Location"] == "/c/demo-course/models/?notice=model-saved"
    assert "ds" in tomllib.loads(_toml(course))["llm"]["profiles"]
    text = bad.get_data(as_text=True)
    assert bad.status_code == 400 and "<script>alert(1)" not in text


def test_check_starts_one_task_for_the_models_named(root, env_file, monkeypatch):
    started = []

    def fake_start(course_root, title, commands, env, meta=None):
        started.append((title, commands, meta))
        return tasks.Task("20260102-100000-0abcde", course_root, title, tasks.RUNNING, "",
                          None, commands, [], 1, meta or {})

    monkeypatch.setattr(tasks, "start", fake_start)
    client = _browser(root, env_file)

    post(client, "/c/demo-course/models/check")
    post(client, "/c/demo-course/models/check", {"profile": "other"})

    assert started[0][1][0][1:] == ["-m", "tutor.llm", "check", "cheap", "other"]
    assert started[1][1][0][-1] == "other" and started[1][2] == {"kind": "check"}
    assert post(client, "/c/demo-course/models/check", {"profile": "-x"}).status_code == 404


# --- keys: one line per course, and the provider that fills the name --------------------------


def test_a_key_lists_each_course_once_with_its_models_after_it():
    row = keys.KeyRow("A_KEY", None, ["first: fast", "first: slow", "second: fast"])
    assert row.by_course == [("first", ["fast", "slow"]), ("second", ["fast"])]


def test_choosing_a_provider_fills_the_name_and_says_when_its_key_is_set(root, env_file):
    import test_gui_catalog as catalog_support

    catalog_support._catalog(root)  # the kept models.dev list, as the Models page leaves it
    client = _browser(root, env_file)

    page = client.get("/keys/?provider=deepseek", base_url=BASE).get_data(as_text=True)
    assert 'name="name" value="DEEPSEEK_API_KEY"' in page and "for DeepSeek" in page
    assert '<option value="deepseek" selected>' in page and "is set already" not in page

    # Its key is in .env already: the form replaces it, and still shows no key.
    page = client.get("/keys/?provider=zhipuai", base_url=BASE).get_data(as_text=True)
    assert 'name="name" value="ZHIPU_API_KEY"' in page and "is set already" in page
    form = page.split('id="add-key"')[1]
    assert 'class="primary">Replace</button>' in form and 'value="short"' not in page

    # An id that is not in the list, and a name the form would refuse, fill nothing.
    for query in ("?provider=nobody", "?name=lower%22%3E", "?provider=lmstudio"):
        page = client.get("/keys/" + query, base_url=BASE).get_data(as_text=True)
        assert 'name="name" value=""' in page
    page = client.get("/keys/?name=ACME_API_KEY", base_url=BASE).get_data(as_text=True)
    assert 'name="name" value="ACME_API_KEY"' in page
    assert env_file.read_text(encoding="utf-8") == ENV  # looking changes nothing


def test_without_the_provider_list_the_form_takes_a_typed_name(root, env_file):
    page = _browser(root, env_file).get("/keys/", base_url=BASE).get_data(as_text=True)
    assert 'name="provider"' not in page and "No provider list yet" in page
    assert "/c/bare/models/new" in page or "/c/demo-course/models/new" in page
