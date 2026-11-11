"""Provider payload layout: old callers unchanged, doctor schema before variable data."""

import pytest

from tutor import llm
from tutor.doctor_advice.models import reply_schema


@pytest.mark.parametrize("first", [False, True])
def test_deepseek_old_and_new_layout_preserve_callers_input(monkeypatch, first):
    config = llm.LLMConfig("demo", "deepseek", "https://example.invalid/v1", "demo-model")
    messages = [{"role": "system", "content": "shared"}, {"role": "user", "content": "facts"}]
    posted = []

    def post(config, body, *args):
        posted.append(body)
        return llm.Reply("{}", 1, 1, 0)

    monkeypatch.setattr(llm, "_post", post)
    llm.chat(config, messages, purpose="synthetic", response_schema=reply_schema(),
             schema_first=first)
    actual = posted[0]["messages"]
    assert actual[0] == messages[0]
    assert actual[-1 if first else 1] == messages[1]
    assert actual[1 if first else -1]["role"] == ("system" if first else "user")
    assert len(messages) == 2 and posted[0]["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize("provider", ["openai", "custom", "glm"])
def test_native_schema_payloads_do_not_change_with_schema_ordering(provider):
    config = llm.LLMConfig("demo", provider, "https://example.invalid/v1", "demo-model")
    messages = [{"role": "user", "content": "synthetic"}]
    assert llm.chat_payload(config, messages, response_schema=reply_schema()) == (
        llm.chat_payload(config, messages, response_schema=reply_schema(), schema_first=True))
