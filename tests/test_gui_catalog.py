"""The models.dev catalog on the New model page (gui/catalog.py): what is kept from the
downloaded file, how a model fills the form, and the page around it. No test uses the
network: the download is a stand-in. Providers and models here are made up in models.dev's
shape; the ids of the providers this tool knows by name are real, as the mapping needs them."""

import io
import json
import tomllib
import urllib.error
from pathlib import Path

import pytest

pytest.importorskip("flask")  # the `gui` extra

from tutor.gui import catalog  # noqa: E402
from tutor.gui.app import create_app  # noqa: E402

TOKEN = "test-token"
BASE = "http://127.0.0.1:5000"


def _model(model_id, released="2026-01-01", **extra):
    return {"id": model_id, "name": model_id.title(), "release_date": released,
            "modalities": {"input": ["text"], "output": ["text"]},
            "limit": {"context": 131072, "output": 8192},
            "cost": {"input": 0.5, "output": 1.5}, **extra}


MODELS_DEV = {
    # Known by name: thinking is turned off its own way, base URL by its name.
    "deepseek": {"id": "deepseek", "name": "DeepSeek", "npm": "@ai-sdk/openai-compatible",
                 "api": "https://api.deepseek.com", "env": ["DEEPSEEK_API_KEY"],
                 "doc": "https://example.org/deepseek-docs",
                 "models": {"ds-old": _model("ds-old", "2025-01-01"),
                            "ds-new": _model("ds-new", "2026-09-01", reasoning=True,
                                             canonical_model_id="deepseek/ds-new-0901"),
                            "ds-gone": _model("ds-gone", "2026-10-01", status="deprecated")}},
    # One maker's model at two sites: Zhipu's own and z.ai. The list's URL for a known
    # provider is not used (tampered here: this tool's own is kept).
    "zhipuai": {"id": "zhipuai", "name": "Zhipu AI", "npm": "@ai-sdk/openai-compatible",
                "api": "https://open.bigmodel.cn.evil.example/api/paas/v4",
                "env": ["ZHIPU_API_KEY"],
                "models": {"glm-x": _model("glm-x", canonical_model_id="zhipuai/glm-x")}},
    "zai": {"id": "zai", "name": "Z.AI", "npm": "@ai-sdk/openai-compatible",
            "api": "https://api.z.ai/api/paas/v4", "env": ["ZHIPU_API_KEY"],
            "models": {"glm-x": _model("glm-x", canonical_model_id="zhipuai/glm-x")}},
    # A server on this machine: its models come from the server, so the entry is skipped.
    "lmstudio": {"id": "lmstudio", "name": "LMStudio", "npm": "@ai-sdk/openai-compatible",
                 "api": "http://127.0.0.1:1234/v1", "env": ["LMSTUDIO_API_KEY"],
                 "models": {"local-x": _model("vendor/Local-X 7B".replace(" ", "-"))}},
    # Any other OpenAI-compatible one: custom, with its URL and the lowest effort it takes.
    "acme-cn": {"id": "acme-cn", "name": "Acme (China)", "npm": "@ai-sdk/openai-compatible",
                "api": "https://api.acme.example/v1/", "env": ["ACME_API_KEY"],
                "models": {
                    "can-off": _model("acme-1", "2026-08-01", reasoning=True,
                                      reasoning_options=[{"type": "effort",
                                                          "values": ["none", "high"]}]),
                    "lowest": _model("acme-2", "2026-07-01", reasoning=True,
                                     reasoning_options=[{"type": "effort",
                                                         "values": ["low", "high"]}]),
                    "always": _model("acme-3", "2026-06-01", reasoning=True),
                    "eyes": _model("acme-v", "2026-05-01", modalities={
                        "input": ["text", "image"], "output": ["text"]}),
                    "painter": _model("acme-paint", modalities={"input": ["text"],
                                                                "output": ["image"]}),
                    "odd-id": _model("<b>acme</b>")}},
    # Its own SDK; llm.py speaks its Messages API (provider "anthropic").
    "anthropic": {"id": "anthropic", "name": "Anthropic", "npm": "@ai-sdk/anthropic",
                  "env": ["ANTHROPIC_API_KEY"], "models": {"c": _model("claude-test-1")}},
    "openrouter": {"id": "openrouter", "name": "OpenRouter", "npm": "@openrouter/ai-sdk-provider",
                   "api": "https://openrouter.ai/api/v1", "env": ["OPENROUTER_API_KEY"],
                   "models": {"r": _model("vendor/routed-1"),
                              "d": _model("deepseek/ds-routed")}},
    # OpenAI's own request format at its own URL.
    "openai-shaped": {"id": "openai-shaped", "name": "Shaped", "npm": "@ai-sdk/openai",
                      "api": "https://shaped.example/v1", "env": ["SHAPED_API_KEY"],
                      "models": {"s": _model("shaped-1")}},
    # Left out: its own SDK with no known URL; a URL that is not https; a key name that is
    # not one (the provider stays, with no key name).
    "vendor-sdk": {"id": "vendor-sdk", "name": "Vendor", "npm": "@ai-sdk/vendor",
                   "env": ["VENDOR_KEY"], "models": {}},
    "plain-http": {"id": "plain-http", "name": "Plain", "npm": "@ai-sdk/openai-compatible",
                   "api": "http://api.plain.example/v1", "env": ["PLAIN_KEY"], "models": {}},
    "odd-key": {"id": "odd-key", "name": "<i>Odd</i>", "npm": "@ai-sdk/openai-compatible",
                "api": "https://odd.example/v1", "env": ["lower-case key"],
                "doc": "javascript:alert(1)", "models": {"m": _model("odd-1")}},
}


def _kept(data=None):
    return catalog.compact(data or MODELS_DEV, "2026-11-07")


def _catalog(tmp_path, data=None):
    path = tmp_path / catalog.CACHE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_kept(data)), encoding="utf-8")
    return catalog.load(tmp_path)


# --- what is kept ------------------------------------------------------------------------------


def test_only_providers_this_tool_can_call_are_kept_each_value_checked(tmp_path):
    found = _catalog(tmp_path)
    by_id = {p.id: p for p in found.providers}

    assert [p.id for p in found.providers] == [
        "deepseek", "zhipuai", "zai", "anthropic", "openrouter",  # common first
        "odd-key", "acme-cn", "openai-shaped"]  # then by name; lmstudio: from the server
    assert found.left_out == 2  # vendor-sdk (its own SDK), plain-http (http elsewhere)
    assert by_id["anthropic"].base_url == "https://api.anthropic.com/v1"
    assert by_id["acme-cn"].base_url == "https://api.acme.example/v1"  # no trailing slash
    # A known provider's URL is this tool's own, whatever the list says; another provider's
    # is the list's, marked so for the page.
    assert by_id["zhipuai"].base_url == "https://open.bigmodel.cn/api/paas/v4"
    assert [p.own_url for p in found.providers] == [True] * 5 + [False] * 3
    assert by_id["odd-key"].key_env == "" and by_id["odd-key"].doc == ""
    assert by_id["odd-key"].name == "<i>Odd</i>"  # kept as text; the page escapes it


def test_models_are_newest_first_without_the_withdrawn_or_odd_ones(tmp_path):
    acme = _catalog(tmp_path).provider("acme-cn")
    assert [m.id for m in acme.models] == ["acme-1", "acme-2", "acme-3", "acme-v"]
    assert [m.id for m in _catalog(tmp_path).provider("deepseek").models] == ["ds-new",
                                                                               "ds-old"]
    first = acme.models[0]
    assert (first.thinking, acme.models[1].thinking, acme.models[2].thinking) == (
        "can be off", "lowest low", "always")
    assert acme.models[3].image and not first.image
    assert (first.context_text, first.price_text) == ("128k", "0.5 · 1.5")


def test_a_price_reads_in_dollars_and_cents():
    model = catalog.Model("m", "m", "", False, (), False, 0, 3.8874, 20.3342)
    assert model.price_text == "3.89 · 20.33"
    assert catalog.Model("m", "m", "", False, (), False, 0, 0.003, 1.0).price_text == "0.003 · 1"


@pytest.mark.parametrize(("tokens", "text"), [(1048576, "1M"), (1_000_000, "1M"),
                                              (131072, "128k"), (200_000, "200k"),
                                              (2_500_000, "2.5M"), (0, "")])
def test_a_context_reads_as_written(tokens, text):
    assert catalog.Model("m", "m", "", False, (), False, tokens, None, None).context_text == text


def test_each_model_knows_its_maker_where_the_list_says(tmp_path):
    found = _catalog(tmp_path)
    routed = {m.id: m.maker for m in found.provider("openrouter").models}
    assert found.provider("deepseek").models[0].maker == "deepseek"  # its canonical id
    assert routed == {"deepseek/ds-routed": "deepseek",  # an aggregator's id, a known maker
                      "vendor/routed-1": ""}  # "vendor" makes nothing models.dev names
    assert found.maker("https://api.z.ai/api/paas/v4/", "glm-x") == "zhipuai"
    assert found.maker("https://elsewhere.example/v1", "glm-x") == "zhipuai"  # by its id
    assert found.maker("https://api.z.ai/api/paas/v4", "nothing-like-it") == ""


def test_a_list_kept_in_an_older_format_is_offered_again(tmp_path):
    path = tmp_path / catalog.CACHE
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({**_kept(), "format": 1}), encoding="utf-8")
    assert catalog.load(tmp_path) is None


# --- how a model fills the form ----------------------------------------------------------------


def test_a_known_provider_keeps_its_own_way_and_url(tmp_path):
    found = _catalog(tmp_path)
    deepseek = found.provider("deepseek")
    values, notes = catalog.fill(deepseek, deepseek.models[0], {"ds-new"})
    assert values == {"name": "ds-new-2", "provider": "deepseek", "model": "ds-new",
                      "api_key_env": "DEEPSEEK_API_KEY",
                      "base_url": "https://api.deepseek.com",  # shown; left out on saving
                      "reasoning_effort": "", "reasoning_effort_on": "", "timeout_s": "",
                      "temperature": ""}  # empty: the usual 0 is sent
    assert notes == []

    zai = found.provider("zai")
    values, _ = catalog.fill(zai, zai.models[0], set())
    assert (values["provider"], values["base_url"]) == ("glm", "https://api.z.ai/api/paas/v4")

    assert found.provider("lmstudio") is None  # a server on this machine: from the server


def test_another_provider_is_custom_with_the_lowest_thinking_it_takes(tmp_path):
    acme = _catalog(tmp_path).provider("acme-cn")
    efforts = [catalog.fill(acme, m, set()) for m in acme.models[:3]]

    assert [v["provider"] for v, _ in efforts] == ["custom"] * 3
    assert efforts[0][0]["base_url"] == "https://api.acme.example/v1"
    assert [v["reasoning_effort"] for v, _ in efforts] == ["none", "low", ""]
    assert "cannot be turned down" in efforts[2][1][0]


def test_anthropic_is_its_own_provider_not_the_compatible_layer(tmp_path):
    anthropic = _catalog(tmp_path).provider("anthropic")
    values, notes = catalog.fill(anthropic, anthropic.models[0], set())

    assert (values["provider"], values["base_url"]) == ("anthropic", "https://api.anthropic.com/v1")
    assert values["api_key_env"] == "ANTHROPIC_API_KEY"
    assert notes == []


# --- downloading -------------------------------------------------------------------------------


class _Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_a_download_is_kept_and_a_failure_keeps_the_copy_before(tmp_path):
    asked = []

    def opener(request, timeout):
        asked.append((request.full_url, timeout))
        return _Reply(json.dumps(MODELS_DEV).encode())

    assert catalog.refresh(tmp_path, opener) is None
    assert asked == [(catalog.SOURCE, catalog.TIMEOUT_S)]
    assert catalog.load(tmp_path).provider("acme-cn") is not None

    def offline(request, timeout):
        raise urllib.error.URLError("no network")

    assert "could not be reached (no network)" in catalog.refresh(tmp_path, offline)
    assert "not its catalog" in catalog.refresh(tmp_path, lambda r, timeout: _Reply(b"<html>"))
    assert catalog.load(tmp_path).provider("acme-cn") is not None  # the copy before


def test_a_fetch_keeps_which_providers_moved_since_the_fetch_before(tmp_path):
    """A provider's URL that differs from the copy before is listed for the page: a move,
    or a tampered list. A known provider's URL cannot move, as the list's is not used."""
    assert catalog.refresh(tmp_path, lambda r, timeout: _Reply(json.dumps(MODELS_DEV).encode())
                           ) is None
    assert catalog.load(tmp_path).url_changes == ()  # nothing before

    moved = json.loads(json.dumps(MODELS_DEV))
    moved["acme-cn"]["api"] = "https://api.acme.evil.example/v1"
    moved["deepseek"]["api"] = "https://api.deepseek.evil.example"
    moved["openai-shaped"]["api"] = "https://shaped.example/v1/"  # the same, written otherwise
    assert catalog.refresh(tmp_path, lambda r, timeout: _Reply(json.dumps(moved).encode())
                           ) is None
    assert catalog.load(tmp_path).url_changes == (
        ("acme-cn", "https://api.acme.example/v1", "https://api.acme.evil.example/v1"),)
    assert catalog.load(tmp_path).provider("deepseek").base_url == "https://api.deepseek.com"


# --- servers on this machine -------------------------------------------------------------------


def test_a_local_server_is_asked_for_its_own_models():
    """GET /v1/models at the server, no key; Ollama answers "data": null with no model."""
    asked = []

    def opener(request, timeout):
        asked.append((request.full_url, request.headers.get("Authorization"), timeout))
        return _Reply(json.dumps({"object": "list", "data": [
            {"id": "qwen3:0.6b", "object": "model"}, {"id": "gemma-4"},
            {"id": "qwen3:0.6b"}, {"id": "<odd>"}, "junk"]}).encode())

    models, problem = catalog.installed("http://localhost:11434/v1", opener)
    assert asked == [("http://localhost:11434/v1/models", None, catalog.LOCAL_TIMEOUT_S)]
    assert problem == "" and [m.id for m in models] == ["gemma-4", "qwen3:0.6b"]
    assert models[0].thinking == "not said" and models[0].price_text == ""

    none = catalog.installed("http://localhost:11434/v1",
                             lambda r, timeout: _Reply(b'{"object":"list","data":null}'))
    assert none[0] == () and "lists no model" in none[1]
    odd = catalog.installed("http://localhost:11434/v1", lambda r, timeout: _Reply(b"[1]"))
    assert "not a list of models" in odd[1]

    def down(request, timeout):
        raise urllib.error.URLError("connection refused")

    assert "is the server running" in catalog.installed("http://localhost:8080/v1", down)[1]
    # Never a host elsewhere: the GUI's server does not call out from this page.
    assert "not on this machine" in catalog.installed("https://api.example/v1", opener)[1]


def test_a_local_provider_fills_the_form_with_no_key_and_a_note():
    provider = catalog.local_provider("ollama")
    assert provider.base_url == "http://localhost:11434/v1" and provider.own_url
    model = catalog.installed("http://localhost:11434/v1", lambda r, timeout: _Reply(
        b'{"data":[{"id":"qwen3:0.6b"}]}'))[0][0]
    values, notes = catalog.fill(provider, model, set())
    assert (values["provider"], values["model"], values["api_key_env"],
            values["base_url"], values["reasoning_effort"]) == (
        "ollama", "qwen3:0.6b", "", "http://localhost:11434/v1", "")
    assert values["name"] == "qwen3-0.6b"
    assert "Check on the Models page tells" in notes[0]
    assert catalog.local_provider("deepseek") is None


# --- the page ----------------------------------------------------------------------------------


@pytest.fixture
def root(tmp_path):
    course = tmp_path / "courses" / "demo-course"
    course.mkdir(parents=True)
    (course / "workspace.toml").write_text('course = "demo"\n', encoding="utf-8")
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / ".env").write_text("ACME_API_KEY=not-a-real-key\n", encoding="utf-8")
    return tmp_path / "courses"


def _browser(root):
    client = create_app(root, TOKEN, port=5000, env_file=root.parent / "repo" / ".env"
                        ).test_client()
    client.get(f"/?token={TOKEN}", base_url=BASE)
    return client


def _page(client, path):
    return client.get(path, base_url=BASE).get_data(as_text=True)


def test_with_no_list_yet_the_page_offers_to_fetch_it_and_the_form_by_hand(root):
    text = _page(_browser(root), "/c/demo-course/models/new")
    assert "Fetch the list" in text and "Or by hand" in text and 'id="model-form"' in text
    assert 'label="On this machine"' in text and "Ollama (ollama)" in text  # without the list


def test_a_local_server_lists_its_models_on_the_page_or_says_it_is_down(root, monkeypatch):
    client = _browser(root)
    monkeypatch.setattr(catalog, "installed", lambda url: (
        (catalog.Model("qwen3:0.6b", "qwen3:0.6b", "", None, (), False, 0, None, None),),
        ""))
    text = _page(client, "/c/demo-course/models/new?catalog=ollama")
    assert "<strong>Ollama</strong>" in text and "no key: a server on this machine" in text
    assert "qwen3:0.6b" in text and "not said" in text and "its docs" in text
    assert "WSL 2 forwards it" in text
    text = _page(client, "/c/demo-course/models/new?catalog=ollama&model=qwen3:0.6b")
    assert "Filled from the server&#39;s list" in text and 'value="qwen3:0.6b"' in text
    assert 'value="qwen3-0.6b"' in text and "Check on the Models page tells" in text

    monkeypatch.setattr(catalog, "installed", lambda url: ((), f"Nothing answers at {url}"))
    text = _page(client, "/c/demo-course/models/new?catalog=llamacpp")
    assert "Nothing answers at http://localhost:8080/v1" in text and "<table" not in text
    assert 'name="at" value="http://localhost:8080/v1"' in text  # the address, to change


def test_a_local_servers_address_can_be_changed_and_listed_again(root, monkeypatch):
    """Another port (owner, 2026-11-10): typed beside the choice, asked at that address,
    carried by Use into the form."""
    asked = []

    def installed(url):
        asked.append(url)
        return (catalog.Model("m-1", "m-1", "", None, (), False, 0, None, None),), ""

    monkeypatch.setattr(catalog, "installed", installed)
    client = _browser(root)
    text = _page(client, "/c/demo-course/models/new?catalog=ollama&at=http://localhost:11435/v1/")
    assert asked == ["http://localhost:11435/v1"]
    assert 'name="at" value="http://localhost:11435/v1"' in text
    assert "model=m-1&amp;at=http://localhost:11435/v1" in text  # Use keeps it
    text = _page(client, "/c/demo-course/models/new?catalog=ollama&model=m-1"
                         "&at=http://localhost:11435/v1")
    assert 'name="base_url" value="http://localhost:11435/v1"' in text
    text = _page(client, "/c/demo-course/models/new?catalog=ollama&at=localhost:11435")
    assert "The address starts with http://" in text and len(asked) == 2
    # The choice changed after the address was shown (no htmx): that address is not sent
    # to the other server; the box shows the new provider's own.
    text = _page(client, "/c/demo-course/models/new?catalog=lmstudio"
                         "&at=http://localhost:11435/v1&at_for=ollama")
    assert asked[-1] == "http://localhost:1234/v1"
    assert 'name="at" value="http://localhost:1234/v1"' in text
    assert 'name="at_for" value="lmstudio"' in text


def test_the_address_box_follows_the_choice(root):
    """htmx swaps the box when the choice changes: a local provider's default address, or
    nothing for a cloud one."""
    client = _browser(root)
    text = _page(client, "/c/demo-course/models/new/at?catalog=vllm")
    assert 'name="at" value="http://localhost:8000/v1"' in text and 'value="vllm"' in text
    text = _page(client, "/c/demo-course/models/new/at?catalog=deepseek")
    assert text.strip() == '<span id="local-at"></span>'
    assert 'hx-get="/c/demo-course/models/new/at"' in _page(client, "/c/demo-course/models/new")


def test_a_providers_address_from_the_list_is_marked_and_a_moved_one_shown(root):
    _catalog(root)
    client = _browser(root)
    assert "check the domain before saving" in _page(client,
                                                     "/c/demo-course/models/new?catalog=acme-cn")
    assert "check the domain" not in _page(client, "/c/demo-course/models/new?catalog=deepseek")

    path = root / catalog.CACHE
    data = json.loads(path.read_text(encoding="utf-8"))
    data["url_changes"] = [["acme-cn", "https://api.acme.example/v1",
                            "https://api.acme.evil.example/v1"]]
    path.write_text(json.dumps(data), encoding="utf-8")
    text = _page(client, "/c/demo-course/models/new")
    assert "the list gives another address for" in text and "acme.evil.example" in text


def test_fetching_comes_back_on_the_provider_chosen(root, monkeypatch):
    def fake_refresh(where):
        _catalog(where)

    monkeypatch.setattr(catalog, "refresh", fake_refresh)
    response = _browser(root).post("/c/demo-course/models/catalog", base_url=BASE,
                                   data={"catalog": "acme-cn"}, headers={"Origin": BASE})
    assert response.status_code == 303
    assert response.headers["Location"] == ("/c/demo-course/models/new?notice=catalog-fetched"
                                            "&catalog=acme-cn")


def test_a_failed_fetch_says_why(root, monkeypatch):
    monkeypatch.setattr(catalog, "refresh", lambda where: "models.dev could not be reached.")
    response = _browser(root).post("/c/demo-course/models/catalog", base_url=BASE,
                                   headers={"Origin": BASE})
    assert response.status_code == 502
    assert "could not be reached" in response.get_data(as_text=True)


def test_a_provider_lists_its_models_and_whether_its_key_is_set(root, monkeypatch):
    monkeypatch.delenv("ACME_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    _catalog(root)
    client = _browser(root)

    text = _page(client, "/c/demo-course/models/new?catalog=acme-cn")
    assert text.index("acme-1") < text.index("acme-2")  # the newest first
    assert "ACME_API_KEY" in text and 'chip-ok">in .env' in text
    assert "not-a-real-key" not in text  # the key's name only, never its value
    assert "&lt;i&gt;Odd&lt;/i&gt;" in text and "<i>Odd" not in text  # escaped
    text = _page(client, "/c/demo-course/models/new?catalog=deepseek")
    assert 'chip-missing">not in .env' in text and "add it on the Keys page" in text


def test_use_fills_the_form_which_saves_as_any_other(root, monkeypatch):
    monkeypatch.setattr(catalog, "SHOWN", 1)
    _catalog(root)
    client = _browser(root)

    text = _page(client, "/c/demo-course/models/new?catalog=acme-cn&model=acme-2")
    assert 'name="model" value="acme-2"' in text and 'name="name" value="acme-2"' in text
    assert 'value="https://api.acme.example/v1"' in text and 'value="low"' in text
    assert 'name="timeout_s" value="900"' in text
    assert "Filled from models.dev" in text and "Show all 4 models" in text
    assert _page(client, "/c/demo-course/models/new?catalog=acme-cn&all=1").count(
        ">Use</a>") == 4

    response = client.post("/c/demo-course/models/save", base_url=BASE, headers={"Origin": BASE},
                           data={"name": "acme-2", "provider": "custom", "model": "acme-2",
                                 "api_key_env": "ACME_API_KEY",
                                 "base_url": "https://api.acme.example/v1",
                                 "reasoning_effort": "low"})
    assert response.status_code == 303
    saved = tomllib.loads(Path(root / "demo-course" / "workspace.toml").read_text(encoding="utf-8"))
    assert saved["llm"]["profiles"]["acme-2"]["base_url"] == "https://api.acme.example/v1"


def test_an_unknown_provider_or_model_fills_nothing(root):
    _catalog(root)
    text = _page(_browser(root), "/c/demo-course/models/new?catalog=nope&model=x")
    assert "Filled from models.dev" not in text and 'name="name" value=""' in text


# --- the oracle and the writers by one maker (owner, 2026-11-07) -------------------------------

ROLES_TOML = """course = "demo"

[llm.profiles.gen]
provider = "glm"
model = "glm-x"
api_key_env = "ZHIPU_API_KEY"

[llm.profiles.zai-oracle]
provider = "glm"
model = "glm-x"
api_key_env = "ZHIPU_API_KEY"
base_url = "https://api.z.ai/api/paas/v4"

[llm.profiles.other]
provider = "deepseek"
model = "ds-new"
api_key_env = "DEEPSEEK_API_KEY"
"""


def _roles(root):
    path = root / "demo-course" / "workspace.toml"
    return tomllib.loads(path.read_text(encoding="utf-8")).get("llm", {}).get("roles", {})


def _post_roles(client, **roles):
    return client.post("/c/demo-course/models/roles", base_url=BASE, headers={"Origin": BASE},
                       data=roles)


def test_one_makers_oracle_and_writer_are_asked_about_before_saving(root):
    (root / "demo-course" / "workspace.toml").write_text(ROLES_TOML, encoding="utf-8")
    _catalog(root)
    client = _browser(root)

    response = _post_roles(client, generate="gen", oracle="zai-oracle", repair="")
    text = response.get_data(as_text=True)
    assert response.status_code == 409 and _roles(root) == {}  # nothing written yet
    assert "Not saved yet" in text and "made by zhipuai" in text and "Save even so" in text
    assert text.count("are both models made by") == 1  # repair is none: generate repairs
    assert (_selected(text, "generate"), _selected(text, "oracle")) == ("gen", "zai-oracle")

    response = _post_roles(client, generate="gen", oracle="zai-oracle", repair="",
                           confirm="1")  # "Save even so" ticked
    assert response.status_code == 303 and _roles(root) == {"generate": "gen",
                                                            "oracle": "zai-oracle"}
    text = client.get("/c/demo-course/models/", base_url=BASE).get_data(as_text=True)
    assert "The roles are saved, but:" in text and "Save even so" not in text
    # Saving the same roles again is not held back.
    assert _post_roles(client, generate="gen", oracle="zai-oracle").status_code == 303


def _selected(text, role):
    """The option chosen in a role's select on the Models page."""
    select = text[text.index(f'<select name="{role}"'):]
    select = select[:select.index("</select>")]
    chosen = [part.split('"')[1] for part in select.split("<option value=")[1:]
              if '" selected>' in part.split(">")[0] + ">"]
    return chosen[0] if chosen else ""


def test_one_provider_is_warned_about_and_one_assistant_profile_refused(root):
    (root / "demo-course" / "workspace.toml").write_text(
        ROLES_TOML + '\n[llm.profiles.cc]\nprovider = "assistant"\n', encoding="utf-8")
    client = _browser(root)

    response = _post_roles(client, generate="gen", oracle="gen", plan="other")
    assert response.status_code == 409 and "one model (gen)" in response.get_data(
        as_text=True)
    response = _post_roles(client, generate="cc", oracle="cc", plan="other")
    text = response.get_data(as_text=True)
    assert response.status_code == 400 and "requests would mix" in text
    # The roles as chosen stay in the form, not the file's (none).
    assert (_selected(text, "generate"), _selected(text, "oracle"),
            _selected(text, "plan")) == ("cc", "cc", "other")
    assert _roles(root) == {}
    assert _post_roles(client, generate="gen", oracle="other").status_code == 303


def test_the_part_above_save_follows_the_roles_being_chosen(root):
    (root / "demo-course" / "workspace.toml").write_text(ROLES_TOML, encoding="utf-8")
    _catalog(root)
    client = _browser(root)
    check = "/c/demo-course/models/roles/check"

    text = client.get(f"{check}?generate=gen&oracle=zai-oracle&repair=",
                      base_url=BASE).get_data(as_text=True)
    assert '<div id="role-check">' in text and "<html" not in text  # the part alone
    assert "made by zhipuai" in text and 'name="confirm"' in text
    text = client.get(f"{check}?generate=gen&oracle=other", base_url=BASE).get_data(
        as_text=True)
    assert "notice" not in text  # another maker: nothing to say
    assert _roles(root) == {}  # only read
    page = client.get("/c/demo-course/models/", base_url=BASE).get_data(as_text=True)
    assert f'hx-get="{check}" hx-trigger="change"' in page and 'id="role-check"' in page


def test_the_provider_choice_swaps_the_fields_that_follow_it(root):
    text = _browser(root).get("/c/demo-course/models/new", base_url=BASE).get_data(as_text=True)
    assert ('hx-get="/c/demo-course/models/new" hx-trigger="change" '
            'hx-target="#provider-fields" hx-select="#provider-fields"') in text
    assert '<div id="provider-fields">' in text and "hx-vals='{\"apply\": \"1\"}'" in text


def test_each_models_maker_shows_where_roles_are_chosen(root):
    (root / "demo-course" / "workspace.toml").write_text(ROLES_TOML, encoding="utf-8")
    _catalog(root)
    text = _browser(root).get("/c/demo-course/models/", base_url=BASE).get_data(as_text=True)
    # Still in the course's own file (not moved to the shared one): said so in the option.
    assert ">zai-oracle (glm, by zhipuai; this course&#39;s own)</option>" in text
    assert ">other (deepseek, by deepseek; this course&#39;s own)</option>" in text
    assert 'muted">by zhipuai</span>' in text  # in the table too


def test_a_list_kept_by_an_older_version_is_named_as_such(root):
    path = root / catalog.CACHE
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({**_kept(), "format": 1}), encoding="utf-8")
    text = _browser(root).get("/c/demo-course/models/new", base_url=BASE).get_data(as_text=True)
    assert "The list kept (2026-11-07) came from an older version" in text
    assert "Fetch the list" in text


def test_without_the_list_nothing_is_compared(root):
    (root / "demo-course" / "workspace.toml").write_text(ROLES_TOML, encoding="utf-8")
    response = _post_roles(_browser(root), generate="gen", oracle="zai-oracle")
    assert response.status_code == 303  # no list: the maker is not known
