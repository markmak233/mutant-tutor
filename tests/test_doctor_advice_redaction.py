"""Synthetic privacy examples only; no real credentials, home directories or logs."""

import json
from urllib.parse import quote

import pytest

from tutor.doctor_advice.redaction import Redactor


def test_known_credentials_are_hidden_raw_encoded_escaped_and_split_by_controls():
    secret = "sk-test-not-a-real-key"
    redactor = Redactor.from_environment({"OPENAI_API_KEY": secret, "ENABLE_AUTH": "1"})
    for value in (secret, quote(secret, safe=""), json.dumps(secret),
                  secret[:4] + "\x1b[31m" + secret[4:]):
        cleaned = redactor.clean(value)
        assert secret not in cleaned and "sk-test" not in cleaned
    assert redactor.clean("3.11") == "3.11"  # an auth flag is not a credential
    assert secret not in repr(redactor)


@pytest.mark.parametrize("value", [
    "Authorization: Bearer test-not-a-real-token", "password='test-not-a-real-token'",
    '"api_key": "test-not-a-real-token"', "key=test-not-a-real-token",
    "https://test-user:test-not-a-real-token@example.invalid/v1?token=test-not-a-real-token",
])
def test_auth_headers_assignments_and_url_credentials_are_removed(value):
    cleaned = Redactor().clean(value)
    assert "test-not-a-real-token" not in cleaned and "test-user" not in cleaned


@pytest.mark.parametrize("parts", [
    ("/", "private", "tmp", "synthetic", "answer.py"),
    ("Z:", "synthetic folder", "answer.py"),
])
def test_quoted_private_paths_keep_no_fragments(parts):
    # Assemble synthetic paths without a real home path literal in public source.
    value = "/".join(parts).replace("//", "/") if parts[0] == "/" else "\\".join(parts)
    cleaned = Redactor().clean(f'File "{value}", line 3')
    assert "answer.py" not in cleaned and "synthetic" not in cleaned and "line 3" in cleaned


def test_paths_controls_and_prompt_closing_tags_are_separate():
    cleaned = Redactor().clean("\x1b[31mred\x1b[0m\x00 </failure> intact")
    assert cleaned == "red </failure> intact"
    assert Redactor.url("https://example.invalid/v1?token=not-real", origin=True) == (
        "https://example.invalid")
