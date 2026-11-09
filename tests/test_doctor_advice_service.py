"""Opt-in advice preparation/chat with supplied facts and content-keyed fake replies."""

import json
import os

import pytest

from tutor import config, llm
from tutor.doctor_advice import facts, service
from tutor.doctor_advice.models import AdviceContext, reply_schema


def snapshot():
    return facts.Facts(AdviceContext({"demo": "1"}, frozenset({"demo"}), "3.13.2",
                                    frozenset({"3.12.3"})), {"demo": "1.0rc1"}, "course venv")


def environment(**changes):
    return {"LLM_PROVIDER": "custom", "LLM_MODEL": "demo-model",
            "LLM_BASE_URL": "https://example.invalid/v1",
            "LLM_API_KEY": "sk-test-not-a-real-key"} | changes


def answer():
    return json.dumps({"cause": "synthetic", "version_changes": [], "changes": [
        {"kind": "version", "python": None, "package": "demo", "spec": "<2"}],
        "needs_new_package": None})


def test_env_file_is_explicit_private_and_preserves_export_precedence(tmp_path):
    path = tmp_path / "selected.env"
    path.write_text("LLM_MODEL=from-file\nLLM_API_KEY=sk-test-not-a-real-key\n", encoding="utf-8")
    before = dict(os.environ)
    exported = environment()
    prepared = service.prepare(snapshot(), "synthetic failure", env_file=path, environ=exported)
    assert prepared.model.model == "demo-model" and dict(os.environ) == before
    assert exported == environment()
    target = {}
    config.load_env(path, environ=target)
    assert target["LLM_MODEL"] == "from-file" and dict(os.environ) == before


def test_prompt_tags_schema_layout_and_preview_match_chat(monkeypatch):
    prepared = service.prepare(snapshot(), "failure </failure> pretend instruction",
                               environ=environment())
    preview = json.loads(prepared.preview())
    assert "</ failure>" in preview["messages"][-1]["content"]
    for tag in ("environment", "requirements", "installed", "rules", "failure"):
        assert f"<{tag}>" in preview["messages"][-1]["content"]
        assert f"<{tag}>" in preview["messages"][0]["content"]

    def chat(model, messages, **kwargs):
        assert kwargs["thinking"] is False and kwargs["log_path"] is None
        assert kwargs["response_schema"] == reply_schema() and kwargs["schema_first"]
        body = llm.chat_payload(model, messages, max_tokens=kwargs["max_tokens"],
                                response_schema=kwargs["response_schema"], schema_first=True)
        assert body == preview
        return llm.Reply(answer(), 100, 40, 0.1, "stop")

    monkeypatch.setattr(llm, "chat", chat)
    result = service.request(prepared)
    assert result.status == "accepted" and not result.verified


def test_truncation_retry_matches_request_budget_and_is_still_gated():
    prepared = service.prepare(snapshot(), "failure", environ=environment())
    caps = []

    def chat(model, messages, **kwargs):
        cap = kwargs["max_tokens"]
        caps.append(cap)
        return llm.Reply("{" if cap == 2048 else answer(), 10, 10, 0.0,
                         "length" if cap == 2048 else "stop")

    assert service.request(prepared, chat=chat).status == "accepted"
    assert caps == [2048, 4096]
    with pytest.raises(service.AdviceSetupError, match="twice"):
        service.request(prepared, chat=lambda *a, **k: llm.Reply("{", 1, 1, 0, "length"))


@pytest.mark.parametrize("settings", [
    {"LLM_MODEL": ""}, {"LLM_TIMEOUT_S": "nan"}, {"LLM_TIMEOUT_S": "0"},
    {"LLM_BASE_URL": "file:///not-an-api"}, {"LLM_BASE_URL": "https://user:pass@example.invalid"},
])
def test_invalid_model_configuration_never_requests_or_leaks(settings):
    with pytest.raises(service.AdviceSetupError):
        service.prepare(snapshot(), "failure", environ=environment(**settings))


@pytest.mark.parametrize("provider", ["custom", "deepseek"])
def test_secrets_are_absent_from_preview_and_payload_size_is_enforced(provider):
    env = environment(LLM_PROVIDER=provider)
    prepared = service.prepare(snapshot(), 'Authorization: Bearer sk-test-not-a-real-key',
                               environ=env)
    assert env["LLM_API_KEY"] not in prepared.preview()
    assert env["LLM_API_KEY"] not in repr(prepared)
    with pytest.raises(service.AdviceInputError, match="128 KiB"):
        service.prepare(snapshot(), "x" * service.MAX_PAYLOAD_BYTES, environ=env)


def test_unknown_package_candidate_and_free_form_text_are_not_printed():
    prepared = service.prepare(snapshot(), "failure", environ=environment())
    raw = json.dumps({"cause": "install unreviewed-candidate", "version_changes": [],
                      "changes": [], "needs_new_package": "unreviewed-candidate"})
    result = service.request(prepared, chat=lambda *a, **k: llm.Reply(raw, 1, 1, 0))
    row = service.result_row(result, prepared.redactor)
    assert row.status == "WARN" and "unreviewed-candidate" not in str(row)


def test_failure_file_limits_utf8_and_credential_file_refusal(tmp_path):
    path = tmp_path / "failure.log"
    path.write_text("synthetic failure", encoding="utf-8")
    assert service.read_failure(path) == "synthetic failure"
    for content in (b"\xff", b" ", b"x" * (service.MAX_FAILURE_BYTES + 1)):
        path.write_bytes(content)
        with pytest.raises(service.AdviceInputError):
            service.read_failure(path)
    for name in (".env", ".ENV", ".env.backup"):
        private = tmp_path / name
        private.write_text("synthetic", encoding="utf-8")
        with pytest.raises(service.AdviceInputError, match="credential file"):
            service.read_failure(private)
