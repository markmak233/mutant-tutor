"""Guide integration on synthetic courses; GET must not run checks or install anything."""

import subprocess

import pytest

pytest.importorskip("flask")

from tutor.gui import environment, tasks  # noqa: E402
from tutor.gui.app import create_app  # noqa: E402

BASE = "http://127.0.0.1:5000"
GUIDE = "/c/demo/environment/host-setup"


@pytest.fixture
def guide(tmp_path, monkeypatch):
    """Only workspace.toml is needed; reject any accidental process/task/check call."""
    folder = tmp_path / "demo"
    folder.mkdir()
    config = folder / "workspace.toml"

    def forbidden(*args, **kwargs):
        pytest.fail("The instruction page must not start a process or task")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(tasks, "start", forbidden)
    monkeypatch.setattr(environment, "check", forbidden)
    client = create_app(tmp_path, "test-token", port=5000).test_client()
    client.get("/?token=test-token", base_url=BASE)

    def get(toml, platform="macos"):
        config.write_text('course = "demo"\n' + toml, encoding="utf-8")
        before = config.read_bytes()
        response = client.get(GUIDE, query_string={"platform": platform}, base_url=BASE)
        assert config.read_bytes() == before
        assert list(folder.iterdir()) == [config]
        return response

    return get, client


def test_plain_course_has_only_applicable_guidance_and_environment_entry(guide):
    """Real endpoint links connect to existing actions without inventing another installer."""
    get, client = guide
    response = get('[requires]\npython = "3.11"\n')
    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "<h2>Java" not in text and "<h2>Hadoop" not in text
    assert "C: existing setup" not in text
    assert "/c/demo/environment/#host-install" in text
    page = client.get("/c/demo/environment/", base_url=BASE).get_data(as_text=True)
    assert f'href="{GUIDE}"' in page
    assert 'id="host-install"' in page and 'id="test-image"' in page


@pytest.mark.parametrize("platform", ["macos", "linux", "windows", "wsl"])
def test_declared_dependencies_platform_navigation_and_container_context(guide, platform):
    """Platform-specific instructions preserve Windows runner limits and optional host use."""
    get, _ = guide
    text = get('[requires]\njava = "17"\nhadoop = "3.3"\npyspark = "3.5"\n'
               '[isolation]\nrunner = "container"\n', platform).get_data(as_text=True)
    assert "<h2>Java" in text and "<h2>Hadoop" in text
    assert "Local compilers" in text
    assert f'platform={platform}" aria-current="page"' in text
    assert ("winget install" in text) == (platform == "windows")
    assert ("brew install" in text) == (platform == "macos")
    assert ("Spark on native Windows" in text) == (platform == "windows")
    if platform == "windows":
        assert "unsupported on native Windows" in text


def test_uncovered_versions_are_data_not_commands_and_c_reuses_settings(guide):
    """Unknown version strings remain escaped; they never become shell command arguments."""
    get, _ = guide
    text = get('[requires]\njava = "<script>alert(1)</script>"\nhadoop = "9"\n'
               '[languages.c]\nflags = []\n').get_data(as_text=True)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "<script>alert" not in text
    assert "No reviewed installation recipe" in text
    assert "No reviewed archive recipe" in text
    assert "brew install" not in text
    assert 'href="/c/demo/environment/#course-needs"' in text
    assert "C: existing setup" in text


def test_unknown_platform_and_missing_course_are_rejected(guide):
    """An arbitrary query never selects a file, and the normal course boundary applies."""
    get, client = guide
    assert get("", "../../anything").status_code == 400
    assert client.get("/c/missing/environment/host-setup", base_url=BASE).status_code == 404
