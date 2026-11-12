"""The Providers page (gui/providers.py, routes/providers.py): the shared llm.toml's provider
cards, each with its key and models; the keys no card names below. Owner, 2026-11-12: the
models span every course, each provider one card; roles stay each course's."""

import tomllib

import pytest

pytest.importorskip("flask")  # the `gui` extra

from tutor.gui import providers  # noqa: E402
from tutor.gui.app import create_app  # noqa: E402

TOKEN = "t" * 32
BASE = "http://127.0.0.1:5000"
SHARED = """# The models every course uses.
[providers.deepseek]
api_key_env = "DEEPSEEK_API_KEY"   # the key's name
max_parallel = 4

[providers.zai]
kind = "glm"
base_url = "https://api.z.ai/api/paas/v4"
api_key_env = "ZAI_API_KEY"

[profiles.ds-flash]
provider = "deepseek"
model = "deepseek-v4-flash"

[profiles.glm-flash]
provider = "zai"
model = "glm-5.3-flash"
parallel_start = "first-done"

[profiles.by-hand]
provider = "lmstudio"
model = "gemma"
"""


@pytest.fixture
def root(tmp_path):
    courses = tmp_path / "courses"
    (courses / "a-course").mkdir(parents=True)
    (courses / "a-course" / "workspace.toml").write_text(
        'course = "A"\n\n[llm.roles]\ngenerate = "ds-flash"\noracle = "glm-flash"\n',
        encoding="utf-8")
    (courses / "llm.toml").write_text(SHARED, encoding="utf-8")
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / ".env").write_text("DEEPSEEK_API_KEY=sk-test-not-a-real-value-1234\n",
                                            encoding="utf-8")
    return courses


def _browser(root):
    env_file = root.parent / "repo" / ".env"
    client = create_app(root, TOKEN, port=5000, env_file=env_file).test_client()
    client.get(f"/?token={TOKEN}", base_url=BASE)
    return client


def post(client, path, data=None):
    return client.post(path, base_url=BASE, data=data or {}, headers={"Origin": BASE})


def _shared(root):
    return (root / "llm.toml").read_text(encoding="utf-8")


def test_cards_show_what_their_kind_brings_and_their_models(root):
    ds, zai = providers.cards(root, {"ds-flash": ["A: generate"]})
    assert (ds.kind, ds.base_url, ds.own_url, ds.api_key_env) == (
        "deepseek", "https://api.deepseek.com", False, "DEEPSEEK_API_KEY")
    assert (ds.parallel_start, ds.own_start, ds.max_parallel) == ("first-event+10", False, 4)
    assert [(m.name, m.model, m.used_by) for m in ds.models] == [
        ("ds-flash", "deepseek-v4-flash", ["A: generate"])]
    assert (zai.kind, zai.base_url, zai.own_url, zai.parallel_start) == (
        "glm", "https://api.z.ai/api/paas/v4", True, "first-done")
    assert [m.name for m in providers.unassigned_models(root)] == ["by-hand"]
    assert providers.key_users(root) == {"DEEPSEEK_API_KEY": ["provider: deepseek"],
                                         "ZAI_API_KEY": ["provider: zai"]}


@pytest.mark.parametrize(("name", "form", "problem"), [
    ("Big", {"kind": "deepseek"}, "lowercase"),
    ("acme", {}, "Choose which provider"),  # a name that is no kind needs one
    ("acme", {"kind": "assistant"}, "Choose which provider"),  # no card for an assistant
    ("mine", {"kind": "custom"}, "give the base URL"),
    ("mine", {"kind": "custom", "base_url": "ftp://x"}, "http"),
    ("deepseek", {"api_key_env": "deepseek"}, "variable in .env"),
    ("deepseek", {"parallel_start": "first-event+"}, "Seconds after"),
    ("deepseek", {"max_parallel": "99"}, "1 to 32"),
])
def test_a_card_is_checked(name, form, problem):
    data, problems = providers.check_card(name, form)
    assert data is None and problem in " ".join(problems)


def test_a_cards_own_name_as_kind_and_its_kinds_address_are_left_out():
    data, problems = providers.check_card("deepseek", {
        "kind": "deepseek", "base_url": "https://api.deepseek.com/",
        "api_key_env": "DEEPSEEK_API_KEY", "parallel_start": "first-event+",
        "parallel_start_s": "30", "max_parallel": "2"})
    assert problems == [] and data == {"api_key_env": "DEEPSEEK_API_KEY",
                                       "parallel_start": "first-event+30", "max_parallel": 2}
    data, problems = providers.check_card("zai", {"kind": "glm",
                                                  "base_url": "https://api.z.ai/api/paas/v4"})
    assert problems == [] and data == {"kind": "glm", "base_url": "https://api.z.ai/api/paas/v4"}


def test_a_card_is_added_changed_renamed_and_removed_keeping_the_rest(root):
    before = _shared(root)
    data, _ = providers.check_card("openai", {"kind": "openai", "api_key_env": "OPENAI_API_KEY"})
    assert providers.save_card(root, None, "openai", data) == []
    text = _shared(root)
    assert "# The models every course uses." in text and "# the key's name" in text
    assert tomllib.loads(text)["providers"]["openai"] == {"api_key_env": "OPENAI_API_KEY"}
    assert (root / "llm.toml.bak").read_text(encoding="utf-8") == before

    # Renamed: its models follow it; a changed field; a dropped one goes.
    data, _ = providers.check_card("z", {"kind": "glm", "base_url": "https://api.z.ai/api/paas/v4",
                                         "max_parallel": "3"})
    assert providers.save_card(root, "zai", "z", data) == []
    loaded = tomllib.loads(_shared(root))
    assert loaded["providers"]["z"] == {"kind": "glm", "base_url": "https://api.z.ai/api/paas/v4",
                                        "max_parallel": 3}
    assert "zai" not in loaded["providers"] and loaded["profiles"]["glm-flash"]["provider"] == "z"

    assert "is the provider of glm-flash" in providers.delete_card(root, "z")[0]
    assert providers.delete_card(root, "openai") == []
    assert "openai" not in tomllib.loads(_shared(root))["providers"]
    assert "exists already" in providers.save_card(root, None, "deepseek", {})[0]
    assert "no longer in llm.toml" in providers.save_card(root, "gone", "gone", {})[0]


def test_a_first_card_makes_the_file(tmp_path):
    data, _ = providers.check_card("deepseek", {"kind": "deepseek"})
    assert providers.save_card(tmp_path, None, "deepseek", data) == []
    assert tomllib.loads(_shared(tmp_path)) == {"providers": {"deepseek": {}}}
    assert not (tmp_path / "llm.toml.bak").exists()


def test_the_page_lists_cards_with_keys_models_and_the_other_keys(root):
    client = _browser(root)
    page = client.get("/providers/", base_url=BASE).get_data(as_text=True)
    assert 'id="provider-deepseek"' in page and 'id="provider-zai"' in page
    assert "set …1234" in page  # deepseek's key, by its tail
    assert page.index("ZAI_API_KEY") < page.index('id="keys"')  # on its card
    assert "ds-flash" in page and "A: generate" in page and "A: oracle" in page
    assert "Models with no card" in page and "by-hand" in page
    assert "sk-test-not-a-real-value-1234" not in page
    assert '<a class="header-link" href="/providers/">Providers</a>' in page


def test_the_form_saves_a_card_and_a_refusal_comes_back(root):
    client = _browser(root)
    page = client.get("/providers/new?kind=openai&apply=1", base_url=BASE).get_data(as_text=True)
    assert 'value="https://api.openai.com/v1"' in page  # the kind's own address, shown
    ok = post(client, "/providers/save", {"name": "openai", "kind": "openai",
                                          "base_url": "https://api.openai.com/v1",
                                          "api_key_env": "OPENAI_API_KEY",
                                          "parallel_start": "first-done"})
    assert ok.headers["Location"] == "/providers/?notice=provider-saved"
    saved = tomllib.loads(_shared(root))["providers"]["openai"]
    assert saved == {"api_key_env": "OPENAI_API_KEY", "parallel_start": "first-done"}
    page = client.get("/providers/p/openai/", base_url=BASE).get_data(as_text=True)
    assert '<option value="first-done" selected>' in page
    bad = post(client, "/providers/save", {"name": "openai", "original": "openai",
                                           "kind": "openai", "max_parallel": "0"})
    assert bad.status_code == 400 and "1 to 32" in bad.get_data(as_text=True)
    assert client.get("/providers/p/nobody/", base_url=BASE).status_code == 404
    gone = post(client, "/providers/p/openai/delete")
    assert gone.headers["Location"] == "/providers/?notice=provider-removed"


def test_a_broken_shared_file_is_named_on_the_page(root):
    (root / "llm.toml").write_text("[providers.x\n", encoding="utf-8")
    page = _browser(root).get("/providers/", base_url=BASE).get_data(as_text=True)
    assert "does not parse" in page


# --- a card's models ---------------------------------------------------------------------------


def test_a_shared_model_is_checked_with_its_cards_fields(root):
    data, problems = providers.check_model(root, "deepseek", "ds-pro", {
        "model": "deepseek-v4-pro", "timeout_s": "900", "parallel_start": "first-event+",
        "parallel_start_s": "30"})
    assert problems == [] and data == {"provider": "deepseek", "model": "deepseek-v4-pro",
                                       "timeout_s": 900, "parallel_start": "first-event+30"}
    for form, problem in [({}, "Give the provider's name"),
                          ({"model": "m", "reasoning_effort": "very low"}, "one word"),
                          ({"model": "m", "temperature": "0.3"}, 'temperature can only be')]:
        data, problems = providers.check_model(root, "deepseek", "x", form)
        assert data is None and problem in " ".join(problems)
    assert "no longer in llm.toml" in providers.check_model(root, "gone", "x", {"model": "m"})[1][0]
    assert "lowercase" in providers.check_model(root, "deepseek", "Big", {"model": "m"})[1][0]


def test_a_shared_model_is_added_changed_renamed_and_removed(root):
    used = {"ds-flash": ["A: generate"]}
    data, _ = providers.check_model(root, "deepseek", "ds-pro", {"model": "deepseek-v4-pro"})
    assert providers.save_model(root, None, "ds-pro", data, used) == []
    loaded = tomllib.loads(_shared(root))
    assert loaded["profiles"]["ds-pro"] == {"provider": "deepseek", "model": "deepseek-v4-pro"}
    assert "# the key's name" in _shared(root)

    data, _ = providers.check_model(root, "deepseek", "pro", {"model": "deepseek-v4-pro",
                                                              "timeout_s": "600"})
    assert providers.save_model(root, "ds-pro", "pro", data, used) == []
    loaded = tomllib.loads(_shared(root))
    assert "ds-pro" not in loaded["profiles"] and loaded["profiles"]["pro"]["timeout_s"] == 600

    # A model a role names: no rename, no removal; the role is in the course's own file.
    data, _ = providers.check_model(root, "deepseek", "flash", {"model": "deepseek-v4-flash"})
    assert "named by A: generate" in providers.save_model(root, "ds-flash", "flash", data, used)[0]
    assert "named by A: generate" in providers.delete_model(root, "ds-flash", used)[0]
    assert providers.delete_model(root, "pro", used) == []
    assert "pro" not in tomllib.loads(_shared(root))["profiles"]
    assert "exists already" in providers.save_model(root, None, "ds-flash", data, used)[0]


def test_the_model_form_lists_the_cards_models_from_the_kept_list_and_saves(root, monkeypatch):
    import test_gui_catalog as catalog_support

    catalog_support._catalog(root)  # the kept models.dev list
    client = _browser(root)
    page = client.get("/providers/p/deepseek/models/new", base_url=BASE).get_data(as_text=True)
    assert "Fill from the provider" in page
    assert 'href="/providers/p/deepseek/models/new?model=' in page
    assert 'name="provider"' not in page  # the card fixes it
    # The card has deepseek-v4-flash already; a list entry of that id is not offered again.
    assert "?model=ds-old" in page
    ok = post(client, "/providers/models/save", {"card": "deepseek", "name": "old",
                                                 "model": "ds-old"})
    assert ok.status_code == 303
    again = client.get("/providers/p/deepseek/models/new", base_url=BASE).get_data(as_text=True)
    assert "?model=ds-old" not in again and "?model=ds-new" in again
    filled = client.get("/providers/p/deepseek/models/new?model=ds-new",
                        base_url=BASE).get_data(as_text=True)
    assert 'name="model" value="ds-new"' in filled and "Filled with" in filled
    assert 'name="name" value="ds-new"' in filled  # a free name from the model's id
    # The z.ai card (kind glm at z.ai's address) lists the entry at that address.
    zai = client.get("/providers/p/zai/models/new", base_url=BASE).get_data(as_text=True)
    assert 'href="/providers/p/zai/models/new?model=glm-x' in zai

    ok = post(client, "/providers/models/save", {"card": "deepseek", "name": "ds-pro",
                                                 "model": "deepseek-v4-pro", "timeout_s": "900"})
    assert ok.headers["Location"] == "/providers/?notice=provider-saved"
    assert tomllib.loads(_shared(root))["profiles"]["ds-pro"]["timeout_s"] == 900
    edit = client.get("/providers/m/ds-pro/", base_url=BASE).get_data(as_text=True)
    assert 'name="model" value="deepseek-v4-pro"' in edit and "Fill from" not in edit
    bad = post(client, "/providers/models/save", {"card": "deepseek", "name": "ds-pro",
                                                  "original": "ds-pro", "model": ""})
    assert bad.status_code == 400 and "Give the provider" in bad.get_data(as_text=True)
    assert client.get("/providers/m/nobody/", base_url=BASE).status_code == 404
    assert post(client, "/providers/models/save", {"card": "gone", "name": "x", "model": "m"}
                ).status_code == 404

    listing = client.get("/providers/", base_url=BASE).get_data(as_text=True)
    assert 'href="/providers/m/ds-pro/"' in listing and "/providers/m/ds-pro/delete" in listing
    assert "/providers/m/ds-flash/delete" not in listing  # a role names it
    gone = post(client, "/providers/m/ds-pro/delete")
    assert gone.headers["Location"] == "/providers/?notice=provider-removed"
    kept = post(client, "/providers/m/ds-flash/delete")
    assert kept.status_code == 400 and "named by A: generate" in kept.get_data(as_text=True)


def test_check_and_measure_run_as_a_task_of_the_course_naming_the_model(root, monkeypatch):
    from tutor.gui import tasks

    started = []

    def fake_start(course_root, title, commands, env, meta=None):
        started.append((course_root.name, title, commands[0][-3:], meta))
        return tasks.Task("20260102-100000-0abcde", course_root, title, tasks.RUNNING, "",
                          None, commands, [], 1, meta or {})

    monkeypatch.setattr(tasks, "start", fake_start)
    client = _browser(root)
    assert post(client, "/providers/m/ds-flash/check").status_code == 303
    assert post(client, "/providers/m/glm-flash/timing").status_code == 303
    assert started == [
        ("a-course", "Check ds-flash", ["tutor.llm", "check", "ds-flash"],
         {"kind": "check", "from": "providers"}),
        ("a-course", "Measure how glm-flash's cache starts",
         ["-m", "tutor.llm_timing", "glm-flash"], {"kind": "timing", "from": "providers"})]
    assert post(client, "/providers/m/nobody/check").status_code == 404


def test_a_new_card_is_filled_from_the_list_or_a_local_server(root):
    """Owner, 2026-11-12: New provider offers the models.dev list, as New model did: an
    entry fills the name, the kind llm.py knows for it (else custom), the address (a
    known kind's own, else the list's) and the key's variable; a local server its own."""
    import test_gui_catalog as catalog_support

    client = _browser(root)
    bare = client.get("/providers/new", base_url=BASE).get_data(as_text=True)
    assert "Fetch the list" in bare and 'optgroup label="On this machine"' in bare
    catalog_support._catalog(root)
    page = client.get("/providers/new", base_url=BASE).get_data(as_text=True)
    assert 'optgroup label="Common"' in page and "(deepseek) · has a card" in page

    filled = client.get("/providers/new?catalog=openrouter", base_url=BASE).get_data(as_text=True)
    assert 'name="name" value="openrouter"' in filled
    assert '<option value="custom" selected>' in filled
    assert 'name="api_key_env" value="OPENROUTER_API_KEY"' in filled
    assert "check the domain" not in filled  # its address is this tool's own (OPENAI_URLS)
    listed = client.get("/providers/new?catalog=acme-cn", base_url=BASE).get_data(as_text=True)
    assert 'value="https://api.acme.example/v1"' in listed  # the list's address
    assert "from models.dev: check the domain" in listed
    known = client.get("/providers/new?catalog=anthropic", base_url=BASE).get_data(as_text=True)
    assert '<option value="anthropic" selected>' in known
    assert 'value="https://api.anthropic.com/v1"' in known and "check the domain" not in known
    local = client.get("/providers/new?catalog=ollama", base_url=BASE).get_data(as_text=True)
    assert '<option value="ollama" selected>' in local and 'name="api_key_env" value=""' in local
    nobody = client.get("/providers/new?catalog=nobody", base_url=BASE).get_data(as_text=True)
    assert 'name="name" value=""' in nobody
