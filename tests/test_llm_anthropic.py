"""The anthropic provider (Claude's Messages API), with urlopen replaced: no server, no
network, a plainly fake key."""

import base64
import io
import json
import urllib.error

import pytest

from tutor import llm, llm_anthropic
from tutor.gui import catalog
from tutor.llm import LLMConfig, LLMError, chat, config_for, image_message

SECRET = "sk-ant-test-not-a-real-key"
URL = "https://api.anthropic.com/v1"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def message(text="ok", stop="end_turn", usage=None, blocks=None) -> bytes:
    """One Message object as the API sends it whole."""
    return json.dumps({
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
        "content": blocks if blocks is not None else [{"type": "text", "text": text}],
        "stop_reason": stop, "stop_sequence": None,
        "usage": usage or {"input_tokens": 100, "output_tokens": 7},
    }).encode()


@pytest.fixture
def sent(monkeypatch):
    """Captures each request; answers with one plain message."""
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse(message())

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    return requests


def answer(monkeypatch, body: bytes) -> None:
    monkeypatch.setattr(llm.urllib.request, "urlopen",
                        lambda request, timeout: FakeResponse(body))


def config(**fields) -> LLMConfig:
    return LLMConfig("claude", "anthropic", URL, "claude-haiku-5-5", **fields)


# --- the request -----------------------------------------------------------------------------


def test_the_request_has_claudes_shape_and_headers(sent, tmp_path):
    log = tmp_path / "calls.jsonl"
    messages = [{"role": "system", "content": "Be brief."},
                {"role": "user", "content": "Question?"},
                {"role": "assistant", "content": "Draft."},
                {"role": "user", "content": "Again."}]

    reply = chat(config(api_key=SECRET, timeout_s=9), messages, purpose="oracle", log_path=log,
                 max_tokens=4000)

    request, timeout = sent[0]
    body = json.loads(request.data)
    assert request.full_url == URL + "/messages" and timeout == 9
    assert request.get_header("X-api-key") == SECRET  # urllib capitalises header names
    assert request.get_header("Anthropic-version") == llm_anthropic.VERSION
    assert request.get_header("Authorization") is None
    assert body["system"] == [{"type": "text", "text": "Be brief."}]
    assert body["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "Question?"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Draft."}]},
        {"role": "user", "content": [{"type": "text", "text": "Again."}]}]
    assert body["max_tokens"] == 4000
    assert "temperature" not in body and "thinking" not in body and "stream" not in body
    assert (reply.text, reply.input_tokens, reply.output_tokens, reply.finish_reason) == (
        "ok", 100, 7, "stop")
    line = json.loads(log.read_text(encoding="utf-8"))
    assert (line["purpose"], line["outcome"], line["input_tokens"]) == ("oracle", "ok", 100)
    assert SECRET not in log.read_text(encoding="utf-8")


def test_without_a_cap_the_pipelines_thinking_default_is_sent(sent):
    chat(config(), [{"role": "user", "content": "hi"}], purpose="check")

    assert json.loads(sent[0][0].data)["max_tokens"] == llm_anthropic.DEFAULT_MAX_TOKENS == 32_000


def test_thinking_off_is_the_profiles_effort_or_low_and_on_is_its_own_or_the_default(sent):
    plain = config()
    tuned = config(thinking_off=({"reasoning_effort": "medium"},),
                   thinking_on={"reasoning_effort": "max"})
    for profile, thinking in ((plain, False), (plain, True), (tuned, False), (tuned, True)):
        chat(profile, [{"role": "user", "content": "hi"}], purpose="x", thinking=thinking)

    efforts = [json.loads(r.data).get("output_config", {}).get("effort") for r, _ in sent]
    assert efforts == ["low", None, "medium", "max"]
    assert all("thinking" not in json.loads(r.data) for r, _ in sent)


def test_a_profile_reads_its_efforts_from_the_same_fields_as_other_providers(tmp_path):
    (tmp_path / "workspace.toml").write_text(
        '[llm.profiles.claude]\nprovider = "anthropic"\nmodel = "claude-sonnet-5-5"\n'
        'api_key_env = "ANTHROPIC_API_KEY"\nreasoning_effort = "low"\n'
        'reasoning_effort_on = "high"\ntemperature = "default"\n'
        '[llm.roles]\noracle = "claude"\n', encoding="utf-8")

    found = config_for("oracle", workspace=tmp_path, environ={"ANTHROPIC_API_KEY": SECRET})

    assert (found.provider, found.base_url, found.api_key) == ("anthropic", URL, SECRET)
    assert llm_anthropic.effort(found, thinking=False) == "low"
    assert llm_anthropic.effort(found, thinking=True) == "high"


def test_a_schema_goes_to_output_config_as_structured_output(sent):
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}

    chat(config(), [{"role": "user", "content": "hi"}], purpose="plan", response_schema=schema,
         thinking=False)

    body = json.loads(sent[0][0].data)
    assert body["output_config"] == {"effort": "low",
                                     "format": {"type": "json_schema", "schema": schema}}
    assert "response_format" not in body
    assert all("Schema" not in block["text"] for m in body["messages"] for block in m["content"])


def test_constraints_anthropic_refuses_are_left_out_of_the_schema_sent(sent):
    """Found with the real API (2026-11-10): the draft schema's `exclusiveMinimum` and the
    doctor schema's `maxItems` were HTTP 400. The field itself stays, with its description;
    the caller's pydantic model still checks the constraint on the reply."""
    schema = {"type": "object", "properties": {
        "timeout_s": {"type": "number", "exclusiveMinimum": 0, "description": "seconds"},
        "tags": {"type": "array", "items": {"type": "string", "minLength": 1}, "maxItems": 3},
        "pattern": {"type": "string", "pattern": "^x"},  # a property named like a keyword
        "nested": {"$ref": "#/$defs/Inner"}},
        "$defs": {"Inner": {"type": "object", "properties": {"n": {"type": "integer",
                                                                   "minimum": 1}},
                            "anyOf": [{"maximum": 9}, {"type": "null"}]}},
        "required": ["timeout_s"]}

    chat(config(), [{"role": "user", "content": "hi"}], purpose="generate", response_schema=schema)

    sent_schema = json.loads(sent[0][0].data)["output_config"]["format"]["schema"]
    assert sent_schema == {"type": "object", "properties": {
        "timeout_s": {"type": "number", "description": "seconds"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "pattern": {"type": "string"},
        "nested": {"$ref": "#/$defs/Inner"}},
        "$defs": {"Inner": {"type": "object", "properties": {"n": {"type": "integer"}},
                            "anyOf": [{}, {"type": "null"}]}},
        "required": ["timeout_s"]}
    assert "exclusiveMinimum" in json.dumps(schema)  # the caller's schema is untouched


def test_a_schema_with_a_free_form_map_goes_into_the_prompt_instead(sent):
    """The draft's `data_files: dict[str, str]` is `additionalProperties: {schema}`, which
    structured output refuses outright (2026-11-10). DeepSeek's way then: the schema as
    text after the last user message, the caller validating the reply."""
    schema = {"type": "object", "properties": {
        "data_files": {"type": "object", "additionalProperties": {"type": "string"}}}}

    chat(config(), [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
         purpose="generate", response_schema=schema, thinking=False)

    body = json.loads(sent[0][0].data)
    assert body["output_config"] == {"effort": "low"}  # no format
    assert body["messages"][-1]["content"] == [
        {"type": "text", "text": "u"},
        {"type": "text", "text": llm.SCHEMA_IN_PROMPT + json.dumps(schema)}]


def test_the_pipelines_own_schemas_are_cleaned_and_only_the_drafts_need_the_prompt():
    from tutor.doctor_advice.models import reply_schema
    from tutor.graph.languages import PYTHON, C
    from tutor.graph.oracle import ORACLE_SCHEMA
    from tutor.judge.hints import HintVerdict
    from tutor.judge.teaching import Verdict

    native = {"plan": PYTHON.plan_schema, "c-plan": C.plan_schema, "oracle": ORACLE_SCHEMA,
              "judge": Verdict.model_json_schema(), "hints": HintVerdict.model_json_schema(),
              "doctor": reply_schema()}
    in_prompt = {"draft": PYTHON.draft_schema, "c-draft": C.draft_schema}
    for schema in (*native.values(), *in_prompt.values()):
        cleaned = json.dumps(llm_anthropic.supported(schema))
        assert not any(f'"{word}":' in cleaned for word in llm_anthropic.UNSUPPORTED_KEYWORDS)
    assert all(llm_anthropic.structurable(llm_anthropic.supported(s)) for s in native.values())
    assert not any(llm_anthropic.structurable(s) for s in in_prompt.values())


def test_an_image_message_becomes_a_base64_image_block(sent):
    chat(config(), [image_message("transcribe", b"\x89PNG fake")], purpose="vision")

    content = json.loads(sent[0][0].data)["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "transcribe"}
    assert content[1]["type"] == "image" and content[1]["source"]["media_type"] == "image/png"
    assert base64.b64decode(content[1]["source"]["data"]) == b"\x89PNG fake"


def test_content_already_in_blocks_passes_through_for_the_cache_step():
    parts = [{"type": "text", "text": "shared", "cache_control": {"type": "ephemeral"}},
             {"type": "text", "text": "varying"}]

    body = llm_anthropic.payload(config(), [{"role": "user", "content": parts}])

    assert body["messages"][0]["content"] == parts


def test_chat_payload_previews_the_same_body_doctor_would_send():
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]

    preview = llm.chat_payload(config(), messages, response_schema={"type": "object"},
                               schema_first=True, max_tokens=2048)

    assert preview == llm_anthropic.payload(config(), messages, response_schema={"type": "object"},
                                            max_tokens=2048)
    assert preview["system"] == [{"type": "text", "text": "s"}] and preview["max_tokens"] == 2048


# --- the reply -------------------------------------------------------------------------------


def test_text_blocks_are_joined_thinking_left_out_and_usage_adds_the_cache_back(monkeypatch,
                                                                                 tmp_path):
    answer(monkeypatch, message(
        blocks=[{"type": "thinking", "thinking": "hmm", "signature": "x"},
                {"type": "text", "text": '{"a":'}, {"type": "text", "text": " 1}"}],
        stop="max_tokens",
        usage={"input_tokens": 300, "cache_read_input_tokens": 9000,
               "cache_creation_input_tokens": 700, "output_tokens": 2516}))
    log = tmp_path / "calls.jsonl"

    reply = chat(config(), [{"role": "user", "content": "hi"}], purpose="judge", log_path=log)

    assert reply.text == '{"a": 1}'
    assert reply.finish_reason == "length"  # what nodes.py reads as "cut off at max_tokens"
    assert (reply.input_tokens, reply.cached_tokens, reply.cache_write_tokens,
            reply.output_tokens) == (10000, 9000, 700, 2516)
    line = json.loads(log.read_text(encoding="utf-8"))
    assert (line["input_tokens"], line["cached_tokens"], line["cache_write_tokens"]) == (
        10000, 9000, 700)


def test_a_refusal_keeps_its_own_stop_reason(monkeypatch):
    answer(monkeypatch, message(text="", stop="refusal"))

    reply = chat(config(), [{"role": "user", "content": "hi"}], purpose="generate")

    assert (reply.text, reply.finish_reason) == ("", "refusal")


def test_a_400_is_a_final_error_with_the_providers_reason_and_no_key(monkeypatch, tmp_path):
    def refuse(request, timeout):
        body = json.dumps({"type": "error", "error": {
            "type": "invalid_request_error",
            "message": f"schema: unsupported keyword minimum (key {SECRET})"}})
        raise urllib.error.HTTPError(request.full_url, 400, "bad", {}, io.BytesIO(body.encode()))

    monkeypatch.setattr(llm.urllib.request, "urlopen", refuse)
    log = tmp_path / "calls.jsonl"
    with pytest.raises(LLMError) as raised:
        chat(config(api_key=SECRET), [{"role": "user", "content": "hi"}], purpose="plan",
             log_path=log, thinking=False)
    assert "HTTP 400" in str(raised.value) and "unsupported keyword minimum" in str(raised.value)
    assert SECRET not in str(raised.value) and SECRET not in log.read_text(encoding="utf-8")
    assert json.loads(log.read_text(encoding="utf-8"))["outcome"] == "error"


def test_a_reply_of_another_shape_is_a_clear_error(monkeypatch):
    answer(monkeypatch, b'{"content": "not a list of blocks"}')

    with pytest.raises(LLMError, match="unexpected reply"):
        chat(config(), [{"role": "user", "content": "hi"}], purpose="x")


# --- streaming -------------------------------------------------------------------------------


def events(*items: dict, end: bool = True) -> bytes:
    lines = [f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in items]
    if end:
        lines.append('event: message_stop\ndata: {"type": "message_stop"}\n\n')
    return "".join(lines).encode()


def text_delta(index: int, text: str) -> dict:
    return {"type": "content_block_delta", "index": index,
            "delta": {"type": "text_delta", "text": text}}


def test_a_streamed_reply_is_put_back_together_with_both_halves_of_its_usage(monkeypatch):
    bodies = []

    def fake_urlopen(request, timeout):
        bodies.append(json.loads(request.data))
        return FakeResponse(events(
            {"type": "message_start", "message": {"usage": {
                "input_tokens": 20, "cache_read_input_tokens": 880, "output_tokens": 1}}},
            {"type": "content_block_start", "index": 0,
             "content_block": {"type": "thinking", "thinking": ""}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "thinking_delta", "thinking": "let me see"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "ping"},
            {"type": "content_block_start", "index": 1,
             "content_block": {"type": "text", "text": ""}},
            text_delta(1, '{"a": '),
            {"type": "some_future_event"},
            text_delta(1, "1}"),
            {"type": "content_block_stop", "index": 1},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
             "usage": {"output_tokens": 12}}))

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    progress = []
    reply = chat(config(), [{"role": "user", "content": "hi"}], purpose="generate", stream=True,
                 on_progress=progress.append)

    assert (reply.text, reply.finish_reason) == ('{"a": 1}', "stop")
    assert (reply.input_tokens, reply.cached_tokens, reply.output_tokens) == (900, 880, 12)
    assert progress == [6, 8]
    assert bodies[0]["stream"] is True and "stream_options" not in bodies[0]


def test_a_stream_that_ends_without_message_stop_is_an_error(monkeypatch, tmp_path):
    answer(monkeypatch, events(text_delta(0, '{"a": 1'), end=False))
    log = tmp_path / "calls.jsonl"

    with pytest.raises(LLMError, match="ended after 7 characters without finishing"):
        chat(config(), [{"role": "user", "content": "hi"}], purpose="generate", stream=True,
             log_path=log)
    assert json.loads(log.read_text(encoding="utf-8"))["outcome"] == "error"


def test_an_error_event_inside_a_stream_is_an_llm_error(monkeypatch):
    answer(monkeypatch, events(
        text_delta(0, "a"),
        {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}))

    with pytest.raises(LLMError, match="overloaded_error"):
        chat(config(), [{"role": "user", "content": "hi"}], purpose="generate", stream=True)


# --- the rest of llm.py around it -------------------------------------------------------------


def test_check_works_through_the_same_path(sent):
    line = llm.check(config())

    body = json.loads(sent[0][0].data)
    assert line.startswith("ok, answered 'ok'") and len(sent) == 1
    assert body["max_tokens"] == 32 and body["output_config"] == {"effort": "low"}
    assert body["messages"][0]["content"] == [{"type": "text", "text": llm.CHECK_PROMPT}]


def test_embed_says_anthropic_has_no_embedding_model():
    with pytest.raises(LLMError, match="no embedding model"):
        llm.embed(config(), ["x"], purpose="embedding")


def test_the_models_page_knows_the_provider_and_its_address():
    assert "anthropic" in llm.PROVIDERS and llm.PROVIDERS["anthropic"][0] == URL
    assert catalog.KNOWN["anthropic"] == "anthropic" and "anthropic" not in catalog.OPENAI_URLS
