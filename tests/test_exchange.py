"""Assistant mode: a coding assistant answers request files instead of an API."""

import json

import pytest

from tutor import exchange
from tutor.exchange import AwaitingAnswer, Queue, store_answer
from tutor.llm import LLMError, chat, config_for, image_message

SCHEMA = {"type": "object", "properties": {"hint": {"type": "string"}}, "required": ["hint"]}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    (tmp_path / "workspace.toml").write_text(
        'course = "demo"\n[llm.profiles.claude-code]\nprovider = "assistant"\n'
        '[llm.roles]\nhint = "claude-code"\n', encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    return tmp_path


def ask(workspace, messages, schema=SCHEMA):
    config = config_for("hint", workspace=workspace)
    return chat(config, messages, purpose="hint", log_path=workspace / "calls.jsonl",
                response_schema=schema)


def test_an_assistant_profile_needs_no_model_key_or_url(workspace):
    config = config_for("hint", workspace=workspace)
    assert (config.provider, config.model, config.base_url) == (
        "assistant", "claude-code", "assistant:claude-code")
    assert config.queue == workspace / ".exchange" / "claude-code"


def test_a_call_writes_a_request_and_waits_then_returns_the_answer(workspace):
    messages = [{"role": "system", "content": "Be brief."},
                {"role": "user", "content": "Why does my loop fail?"}]
    with pytest.raises(AwaitingAnswer) as waiting:
        ask(workspace, messages)
    request = waiting.value.request
    text = request.read_text(encoding="utf-8")
    assert "## system\nBe brief." in text and "Why does my loop fail?" in text
    assert "JSON Schema of the reply" in text and '"hint"' in text
    with pytest.raises(AwaitingAnswer) as again:  # asked again: the same request
        ask(workspace, messages)
    assert again.value.request_id == waiting.value.request_id
    queue = Queue(workspace / ".exchange" / "claude-code")
    store_answer(queue, request.stem, '```json\n{"hint": "Look at the loop bound."}\n```')
    reply = ask(workspace, messages)
    assert json.loads(reply.text) == {"hint": "Look at the loop bound."}
    log = json.loads((workspace / "calls.jsonl").read_text(encoding="utf-8"))
    assert (log["outcome"], log["profile"], log["input_tokens"]) == ("ok", "claude-code", None)
    assert queue.waiting() == []


def test_an_image_is_saved_beside_the_request_and_named_in_it(workspace):
    png = b"\x89PNG\r\n\x1a\nfake"
    with pytest.raises(AwaitingAnswer) as waiting:
        ask(workspace, [image_message("Copy this slide.", png)], schema=None)
    text = waiting.value.request.read_text(encoding="utf-8")
    image = waiting.value.request.with_name(f"{waiting.value.request_id}-1.png")
    assert f"[image: open {image}]" in text and image.read_bytes() == png
    assert "reply with: plain text" in text


@pytest.mark.parametrize(("reply", "expected"), [
    ("not json", "must be one JSON object"),
    ('{"diagnosis": "x"}', "lacks required fields: hint"),
])
def test_a_reply_in_the_wrong_shape_is_refused_before_the_pipeline_sees_it(
        workspace, reply, expected):
    with pytest.raises(AwaitingAnswer) as waiting:
        ask(workspace, [{"role": "user", "content": "hint?"}])
    queue = Queue(workspace / ".exchange" / "claude-code")
    with pytest.raises(ValueError, match=expected):
        store_answer(queue, waiting.value.request_id, reply)
    assert queue.waiting() == [waiting.value.request]


def test_a_caller_unaware_of_assistant_mode_sees_a_model_that_could_not_answer(workspace):
    with pytest.raises(LLMError, match="waiting for an answer"):
        ask(workspace, [{"role": "user", "content": "hint?"}])


def test_the_command_lists_shows_and_takes_answers(workspace, tmp_path, capsys):
    with pytest.raises(AwaitingAnswer) as waiting:
        ask(workspace, [{"role": "user", "content": "hint please"}])
    rid = waiting.value.request_id
    assert exchange.main(["list"]) == 0
    assert rid in capsys.readouterr().out
    assert exchange.main(["show", rid]) == 0
    assert "hint please" in capsys.readouterr().out
    reply = tmp_path / "reply.json"
    reply.write_text('{"hint": "Check the loop."}', encoding="utf-8")
    assert exchange.main(["answer", rid, str(reply)]) == 0
    assert exchange.main(["answer", "0000000000000000", str(reply)]) == 1
    assert json.loads(ask(workspace, [{"role": "user", "content": "hint please"}]).text) == {
        "hint": "Check the loop."}
