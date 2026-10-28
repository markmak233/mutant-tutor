"""The OpenAI-compatible client, with urlopen replaced: no server, no network."""

import base64
import io
import json
import re
import urllib.error

import pytest

from tutor import llm
from tutor.llm import LLMConfig, LLMError, chat, config_for, image_message

SECRET = "sk-test-not-a-real-key"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def sent(monkeypatch):
    """Captures each request; answers with a fixed completion."""
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse(json.dumps({
            "choices": [{"message": {"content": "## Mean v1"}}],
            "usage": {"prompt_tokens": 1200, "completion_tokens": 80},
        }).encode())

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    return requests


WORKSPACE_LLM = """
[llm.profiles.gemma-local]
provider = "lmstudio"
model = "google/gemma-4-26b-a4b-qat"

[llm.profiles.glm-flash]
provider = "glm"
model = "glm-5.3-flash"
api_key_env = "ZHIPU_API_KEY"

[llm.roles]
vision = "gemma-local"
generate = "glm-flash"
"""


def workspace(tmp_path, llm=WORKSPACE_LLM):
    (tmp_path / "workspace.toml").write_text('course = "demo"\n' + llm)
    return tmp_path


def test_roles_pick_profiles_and_providers_fill_in_the_rest(tmp_path):
    ws = workspace(tmp_path)
    env = {"ZHIPU_API_KEY": SECRET}

    vision = config_for("vision", workspace=ws, environ=env)
    assert (vision.name, vision.base_url, vision.api_key) == (
        "gemma-local", "http://localhost:1234/v1", None)
    assert vision.thinking_off == ({"reasoning_effort": "none"},)

    generate = config_for("generate", workspace=ws, environ=env)
    assert (generate.base_url, generate.model, generate.api_key) == (
        "https://open.bigmodel.cn/api/paas/v4", "glm-5.3-flash", SECRET)
    assert SECRET not in repr(generate)

    assert config_for("vision", "glm-flash", ws, env).name == "glm-flash"  # --profile wins
    assert llm.roles(ws) == {"vision": "gemma-local", "generate": "glm-flash"}
    (tmp_path / "bare").mkdir()
    assert llm.roles(workspace(tmp_path / "bare", llm="")) == {}


@pytest.mark.parametrize(
    ("role", "profile", "llm", "env", "expected"),
    [
        ("hint", None, WORKSPACE_LLM, {}, "no model for 'hint'"),
        ("vision", "gpt", WORKSPACE_LLM, {}, "no [llm.profiles.gpt]"),
        ("generate", None, WORKSPACE_LLM, {}, "needs ZHIPU_API_KEY set in .env"),
        ("vision", None, '[llm.profiles.p]\nprovider = "glm"\nmodel = "m"\napi_key = "x"\n'
         '[llm.roles]\nvision = "p"\n', {}, "holds a key"),
        ("vision", None, '[llm.profiles.p]\nprovider = "glm"\nmodel = "sk-abcdefghijklmnopqrstu"\n'
         '[llm.roles]\nvision = "p"\n', {}, "holds a key"),
        ("vision", None, '[llm.profiles.p]\nprovider = "lmstudio"\nmodel = "m"\nmodle = "x"\n'
         '[llm.roles]\nvision = "p"\n', {}, "unknown fields: modle"),
        ("vision", None, '[llm.profiles.p]\nprovider = "custom"\nmodel = "m"\n'
         '[llm.roles]\nvision = "p"\n', {}, "needs a base_url"),
    ],
)
def test_configuration_errors_say_what_to_fix(tmp_path, role, profile, llm, env, expected):
    with pytest.raises(LLMError, match=re.escape(expected)):
        config_for(role, profile, workspace(tmp_path, llm), env)


def test_without_an_llm_section_the_environment_is_the_profile(tmp_path):
    ws = workspace(tmp_path, "")
    with pytest.raises(LLMError, match="no model configured"):
        config_for("vision", workspace=ws, environ={})
    config = config_for("vision", workspace=ws,
                        environ={"LLM_MODEL": "gemma", "LLM_API_KEY": SECRET})
    assert (config.name, config.base_url, config.api_key) == (
        "env", "http://localhost:1234/v1", SECRET)


def test_chat_sends_an_openai_request_and_logs_without_the_key(sent, tmp_path):
    config = LLMConfig("p", "lmstudio", "http://localhost:1234/v1", "gemma", api_key=SECRET,
                       timeout_s=9)
    log = tmp_path / "logs" / "calls.jsonl"
    message = image_message("transcribe", b"\x89PNG fake")

    reply = chat(config, [message], purpose="vision", log_path=log)

    request, timeout = sent[0]
    body = json.loads(request.data)
    assert request.full_url == "http://localhost:1234/v1/chat/completions"
    assert request.get_header("Authorization") == f"Bearer {SECRET}"
    assert (body["model"], body["temperature"], timeout) == ("gemma", 0.0, 9)
    url = body["messages"][0]["content"][1]["image_url"]["url"]
    assert base64.b64decode(url.removeprefix("data:image/png;base64,")) == b"\x89PNG fake"
    assert (reply.text, reply.input_tokens, reply.output_tokens) == ("## Mean v1", 1200, 80)
    line = json.loads(log.read_text())
    assert (line["purpose"], line["outcome"], line["output_tokens"]) == ("vision", "ok", 80)
    assert SECRET not in log.read_text()


def test_no_authorization_header_without_a_key(sent):
    chat(LLMConfig("p", "custom", "http://x/v1", "gemma"), [], purpose="test")
    assert sent[0][0].get_header("Authorization") is None


def test_an_unreachable_server_is_a_clear_error_and_is_logged(monkeypatch, tmp_path):
    def refuse(request, timeout):
        raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))

    monkeypatch.setattr(llm.urllib.request, "urlopen", refuse)
    log = tmp_path / "calls.jsonl"
    with pytest.raises(LLMError, match="is the server running"):
        chat(LLMConfig("p", "lmstudio", "http://localhost:1234/v1", "gemma"), [], purpose="vision",
             log_path=log)
    assert json.loads(log.read_text())["outcome"] == "error"


def test_cached_input_tokens_are_read_and_logged(monkeypatch, tmp_path):
    def cached(request, timeout):
        return FakeResponse(json.dumps({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 17405, "completion_tokens": 2516,
                      "prompt_tokens_details": {"cached_tokens": 5504}},
        }).encode())

    monkeypatch.setattr(llm.urllib.request, "urlopen", cached)
    log = tmp_path / "calls.jsonl"
    reply = chat(LLMConfig("p", "openai", "https://api.openai.com/v1", "gpt-6.1-terra"), [],
                 purpose="repair", log_path=log)
    assert reply.cached_tokens == 5504
    assert json.loads(log.read_text())["cached_tokens"] == 5504


def test_a_byte_that_is_not_utf8_does_not_crash_the_call(monkeypatch):
    body = json.dumps({"choices": [{"message": {"content": "abXcd"}}]}).encode()
    monkeypatch.setattr(llm.urllib.request, "urlopen",
                        lambda request, timeout: FakeResponse(body.replace(b"X", b"\x80")))
    reply = chat(LLMConfig("p", "glm", "http://x/v1", "glm-5.3-flash"), [], purpose="generate")
    assert reply.text == "ab�cd"


def test_openai_gets_its_own_parameter_names(sent):
    schema = {"type": "object", "properties": {"id": {"type": "string"}}}
    chat(LLMConfig("p", "openai", "https://api.openai.com/v1", "gpt-6.1-terra"), [],
         purpose="generate", max_tokens=1000, response_schema=schema)
    body = json.loads(sent[0][0].data)
    assert body["max_completion_tokens"] == 1000 and "max_tokens" not in body
    assert "temperature" not in body
    assert body["response_format"]["json_schema"]["strict"] is False


def test_a_slow_reply_says_timeout_not_unreachable(monkeypatch):
    def slow(request, timeout):
        raise TimeoutError("The read operation timed out")

    monkeypatch.setattr(llm.urllib.request, "urlopen", slow)
    with pytest.raises(LLMError, match=r"within 300 s; raise timeout_s in \[llm.profiles.p\]"):
        chat(LLMConfig("p", "glm", "http://x/v1", "glm-5.3-flash"), [], purpose="generate")


def test_thinking_off_tries_each_switch_until_the_model_accepts_one(monkeypatch, tmp_path):
    """GLM-5.3 refuses thinking: disabled with HTTP 400 and accepts reasoning_effort low."""
    bodies = []

    def fake_urlopen(request, timeout):
        body = json.loads(request.data)
        bodies.append(body)
        if "thinking" in body:
            raise urllib.error.HTTPError(request.full_url, 400, "bad", {},
                                         io.BytesIO(b'{"error": "thinking cannot be disabled"}'))
        return FakeResponse(json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode())

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    config = config_for("vision", "glm-flash", workspace(tmp_path), {"ZHIPU_API_KEY": SECRET})

    assert chat(config, [], purpose="vision", thinking=False).text == "ok"
    assert [b.get("thinking") or b.get("reasoning_effort") for b in bodies] == [
        {"type": "disabled"}, "low"]
    bodies.clear()
    chat(config, [], purpose="generate")  # thinking left on: no switch sent
    assert "thinking" not in bodies[0] and "reasoning_effort" not in bodies[0]


def test_a_response_schema_asks_for_structured_output(sent):
    schema = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}
    chat(LLMConfig("p", "glm", "http://x/v1", "glm-5.3-flash"), [], purpose="generate",
         response_schema=schema)
    assert json.loads(sent[0][0].data)["response_format"] == {
        "type": "json_schema", "json_schema": {"name": "output", "schema": schema, "strict": True}}
    chat(LLMConfig("p", "glm", "http://x/v1", "glm-5.3-flash"), [], purpose="generate")
    assert "response_format" not in json.loads(sent[1][0].data)


def test_deepseek_gets_json_mode_with_the_schema_in_the_prompt(sent):
    schema = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}
    question = {"role": "user", "content": "write it"}
    chat(LLMConfig("p", "deepseek", "https://api.deepseek.com", "deepseek-v4-flash",
                   thinking_off=llm.PROVIDERS["deepseek"][1]), [question],
         purpose="generate", response_schema=schema, thinking=False)
    body = json.loads(sent[0][0].data)
    assert sent[0][0].full_url == "https://api.deepseek.com/chat/completions"
    assert body["response_format"] == {"type": "json_object"}
    assert body["thinking"] == {"type": "disabled"}
    assert body["messages"][0] == question
    assert json.dumps(schema) in body["messages"][-1]["content"]


def test_lm_studio_turns_thinking_off_with_reasoning_effort_none(sent):
    chat(LLMConfig("p", "lmstudio", "http://x/v1", "gemma",
                   thinking_off=({"reasoning_effort": "none"},)), [], purpose="vision",
         thinking=False)
    assert json.loads(sent[0][0].data)["reasoning_effort"] == "none"


def stream_of(*pieces: dict) -> bytes:
    lines = [f"data: {json.dumps(p)}\n\n" for p in pieces] + ["data: [DONE]\n\n"]
    return (": keep-alive\n\n" + "".join(lines)).encode()


def test_a_streamed_reply_is_put_back_together_with_its_usage(monkeypatch):
    bodies = []

    def fake_urlopen(request, timeout):
        bodies.append(json.loads(request.data))
        return FakeResponse(stream_of(
            {"choices": [{"delta": {"role": "assistant", "content": ""}}]},
            {"choices": [{"delta": {"content": '{"a": '}}]},
            {"choices": [{"delta": {"content": "1}"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 900, "completion_tokens": 12,
                                      "prompt_tokens_details": {"cached_tokens": 640}}}))

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    progress = []
    reply = chat(LLMConfig("p", "deepseek", "https://api.deepseek.com", "deepseek-v4-flash"),
                 [], purpose="generate", stream=True, on_progress=progress.append)

    assert (reply.text, reply.finish_reason) == ('{"a": 1}', "stop")
    assert (reply.input_tokens, reply.cached_tokens, reply.output_tokens) == (900, 640, 12)
    assert progress == [6, 8]
    assert bodies[0]["stream"] is True
    assert bodies[0]["stream_options"] == {"include_usage": True}


def test_only_providers_known_to_accept_it_are_asked_for_usage_in_a_stream(monkeypatch):
    bodies = []

    def fake_urlopen(request, timeout):
        bodies.append(json.loads(request.data))
        return FakeResponse(stream_of({"choices": [{"delta": {"content": "ok"}}]}))

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    reply = chat(LLMConfig("p", "glm", "http://x/v1", "glm-5.3-flash"), [], purpose="plan",
                 stream=True)
    assert reply.text == "ok" and reply.input_tokens is None
    assert bodies[0]["stream"] is True and "stream_options" not in bodies[0]


def test_an_error_inside_a_stream_is_an_llm_error(monkeypatch, tmp_path):
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda request, timeout: FakeResponse(
        stream_of({"error": {"message": "overloaded"}})))
    with pytest.raises(LLMError, match="error in the stream.*overloaded"):
        chat(LLMConfig("p", "openai", "http://x/v1", "gpt"), [], purpose="generate",
             stream=True, log_path=tmp_path / "calls.jsonl")
    assert json.loads((tmp_path / "calls.jsonl").read_text())["outcome"] == "error"
