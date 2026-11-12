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
