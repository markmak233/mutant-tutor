"""Moving a course's own models into the shared llm.toml (gui/move_models.py, the Models
page's "Move to shared"; owner, 2026-11-12). Both files are written through the checked
write; the course's roles never change."""

import tomllib

import pytest

pytest.importorskip("flask")  # the `gui` extra

from tutor.gui import courses, llm_settings, move_models  # noqa: E402
from tutor.gui.app import create_app  # noqa: E402

TOKEN = "t" * 32
BASE = "http://127.0.0.1:5000"
COURSE = """course = "demo"   # the course id

[llm.profiles.deepseek]
provider = "deepseek"
model = "deepseek-v4-flash"
api_key_env = "DEEPSEEK_API_KEY"   # the flash model
timeout_s = 900

[llm.profiles.deepseek-pro]
provider = "deepseek"
model = "deepseek-v4-pro"
api_key_env = "DEEPSEEK_API_KEY"

[llm.profiles.zai-flash]
provider = "glm"
model = "glm-5.3-flash"
base_url = "https://api.z.ai/api/paas/v4"
api_key_env = "ZAI_API_KEY"

[llm.profiles.local]
provider = "lmstudio"
model = "gemma"

[llm.profiles.claude-code]
provider = "assistant"

[llm.roles]
generate = "deepseek"
oracle = "zai-flash"
vision = "local"

[generation]
parallel = 4   # kept as written
"""


@pytest.fixture
def root(tmp_path):
    courses_dir = tmp_path / "courses"
    (courses_dir / "demo-course").mkdir(parents=True)
    (courses_dir / "demo-course" / "workspace.toml").write_text(COURSE, encoding="utf-8")
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / ".env").write_text("", encoding="utf-8")
    return courses_dir


def _course(root):
    return courses.get_course(root, "demo-course")


def _own(root):
    return (root / "demo-course" / "workspace.toml").read_text(encoding="utf-8")


def _shared(root):
    return tomllib.loads((root / "llm.toml").read_text(encoding="utf-8"))


def test_the_plan_groups_profiles_into_cards_by_kind_and_address(root):
    plan = move_models.plan(root, _course(root))
    assert plan.cards == {
        "deepseek": {"api_key_env": "DEEPSEEK_API_KEY"},
        "glm": {"base_url": "https://api.z.ai/api/paas/v4", "api_key_env": "ZAI_API_KEY"},
        "lmstudio": {}}
    assert plan.models == {
        "deepseek": {"provider": "deepseek", "model": "deepseek-v4-flash", "timeout_s": 900},
        "deepseek-pro": {"provider": "deepseek", "model": "deepseek-v4-pro"},
        "zai-flash": {"provider": "glm", "model": "glm-5.3-flash"},
        "local": {"provider": "lmstudio", "model": "gemma"},
        "claude-code": {"provider": "assistant"}}  # no card: no endpoint, no key
    assert plan.merged == [] and plan.stays == {}
    assert plan.moved == ["claude-code", "deepseek", "deepseek-pro", "local", "zai-flash"]


def test_the_move_writes_the_shared_file_then_drops_the_profiles_keeping_the_rest(root):
    before = _own(root)
    assert move_models.apply(root, _course(root)) == []
    shared = _shared(root)
    assert set(shared["providers"]) == {"deepseek", "glm", "lmstudio"}
    assert shared["profiles"]["deepseek"] == {"provider": "deepseek", "model": "deepseek-v4-flash",
                                              "timeout_s": 900}
    own = _own(root)
    assert "[llm.profiles" not in own and "# the course id" in own and "# kept as written" in own
    assert tomllib.loads(own)["llm"] == {"roles": {"generate": "deepseek", "oracle": "zai-flash",
                                                   "vision": "local"}}
    assert (root / "demo-course" / "workspace.toml.bak").read_text(encoding="utf-8") == before
    # The course still resolves every role, now from the shared file.
    merged = llm_settings.section(_course(root))["profiles"]
    assert merged["zai-flash"]["base_url"] == "https://api.z.ai/api/paas/v4"
    assert merged["zai-flash"]["api_key_env"] == "ZAI_API_KEY"
    assert "Nothing to move" in move_models.apply(root, _course(root))[0]


def test_an_existing_card_is_reused_and_a_second_address_gets_a_second_card(root):
    (root / "llm.toml").write_text(
        '[providers.deepseek]\napi_key_env = "DEEPSEEK_API_KEY"\n\n'
        '[providers.glm]\napi_key_env = "ZHIPU_API_KEY"\n\n'  # Zhipu's own site
        '[profiles.ds]\nprovider = "deepseek"\nmodel = "deepseek-v4-flash"\n',
        encoding="utf-8")
    plan = move_models.plan(root, _course(root))
    assert "deepseek" not in plan.cards  # reused
    assert plan.cards["glm-2"] == {"kind": "glm", "base_url": "https://api.z.ai/api/paas/v4",
                                   "api_key_env": "ZAI_API_KEY"}
    assert plan.models["zai-flash"]["provider"] == "glm-2"


def test_equal_models_merge_and_different_ones_or_other_keys_stay(root):
    (root / "llm.toml").write_text(
        '[providers.deepseek]\napi_key_env = "OTHER_KEY_NAME"\n\n'
        '[providers.lmstudio]\n\n'
        '[profiles.local]\nprovider = "lmstudio"\nmodel = "gemma"\n\n'
        '[profiles.claude-code]\nprovider = "assistant"\nmodel = "other"\n',
        encoding="utf-8")
    plan = move_models.plan(root, _course(root))
    assert plan.merged == ["local"]  # the same card and fields: nothing to write
    assert plan.stays == {"deepseek": "key", "deepseek-pro": "key", "claude-code": "differs"}
    assert set(plan.models) == {"zai-flash"}
    assert move_models.apply(root, _course(root)) == []
    own = tomllib.loads(_own(root))["llm"]["profiles"]
    assert set(own) == {"deepseek", "deepseek-pro", "claude-code"}  # the stayers
    assert set(_shared(root)["profiles"]) == {"local", "claude-code", "zai-flash"}


def test_a_shared_file_that_cannot_be_written_leaves_the_course_untouched(root, monkeypatch):
    from tutor.gui import toml_edit

    before = _own(root)
    real = toml_edit.edit_path

    def refuse(path, change, expected):
        if path.name == "llm.toml":
            return ["llm.toml could not be changed safely"]
        return real(path, change, expected)

    monkeypatch.setattr(toml_edit, "edit_path", refuse)
    assert "could not be changed" in move_models.apply(root, _course(root))[0]
    assert _own(root) == before and not (root / "llm.toml").exists()


def test_the_page_offers_the_move_on_a_ticked_box_and_lists_shared_models_for_the_roles(root):
    env_file = root.parent / "repo" / ".env"
    client = create_app(root, TOKEN, port=5000, env_file=env_file).test_client()
    client.get(f"/?token={TOKEN}", base_url=BASE)
    page = client.get("/c/demo-course/models/", base_url=BASE).get_data(as_text=True)
    assert 'id="move-to-shared"' in page and "5 models are still in this course" in page
    assert "this course&#39;s own)</option>" in page and "Copy them from" not in page

    untick = client.post("/c/demo-course/models/move", base_url=BASE, data={},
                         headers={"Origin": BASE})
    assert untick.status_code == 400 and "Tick the box" in untick.get_data(as_text=True)
    moved = client.post("/c/demo-course/models/move", base_url=BASE, data={"confirm": "1"},
                        headers={"Origin": BASE})
    assert moved.headers["Location"] == "/c/demo-course/models/?notice=models-moved"
    page = client.get("/c/demo-course/models/", base_url=BASE).get_data(as_text=True)
    assert 'id="move-to-shared"' not in page and "This course&#39;s own models" not in page
    assert '<option value="deepseek" selected>deepseek (deepseek)</option>' in page
    assert "Add a model (on the Providers page)" in page
