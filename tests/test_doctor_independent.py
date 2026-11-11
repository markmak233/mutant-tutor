"""Blind independent replies and fake PyPI JSON; never call providers or a registry."""

import io
import json
from dataclasses import replace

import pytest
import test_doctor_advice_service as support

from tutor import llm
from tutor.doctor_advice import independent, service


def prepared(provider="deepseek"):
    env = support.environment(LLM_PROVIDER=provider, LLM_BASE_URL=f"https://{provider}.invalid/v1")
    return service.prepare(support.snapshot(), "synthetic import failure", environ=env)


def reply(name="extra-demo"):
    return llm.Reply(json.dumps({"cause": "synthetic", "version_changes": [], "changes": [],
                                 "needs_new_package": name}), 1, 1, 0)


def test_models_receive_identical_blind_material_and_matching_name_needs_facts():
    seen = []

    def chat(model, messages, **kwargs):
        assert "extra-demo" not in str(messages)
        assert kwargs["thinking"] and kwargs["log_path"] is None
        seen.append(messages)
        return reply()

    result = independent.review(prepared(), prepared("glm"), chat=chat,
                                fetch=lambda n: independent.PackageFacts(n, 2, "2020-01-01"))
    assert result.name == "extra-demo" and len(seen) == 2 and seen[0] == seen[1]


def test_same_provider_or_endpoint_refused_before_any_request():
    def forbidden(*a, **kw):
        pytest.fail("not independent")
    for second in (prepared(), replace(prepared("glm"), model=replace(
            prepared("glm").model, base_url=prepared().model.base_url))):
        with pytest.raises(service.AdviceSetupError, match="independent"):
            independent.review(prepared(), second, chat=forbidden, fetch=forbidden)


def test_disagreement_never_queries_pypi_or_offers_a_candidate():
    result = independent.review(prepared(), prepared("glm"),
                                chat=lambda m, *a, **kw: reply("one-demo" if m.provider == "glm"
                                                              else "other-demo"),
                                fetch=lambda n: pytest.fail("disagreement"))
    assert result is None


def test_all_selected_credentials_are_removed_from_both_requests():
    first, second = prepared(), prepared("glm")
    second = replace(second, model=replace(second.model, api_key="test-not-a-real-token"),
                     redactor=replace(second.redactor, secrets=("test-not-a-real-token",)))
    first = replace(first, parts=(*first.parts, ("user", "test-not-a-real-token")))

    def chat(model, messages, **kwargs):
        assert "test-not-a-real-token" not in str(messages)
        return reply()

    assert independent.review(first, second, chat=chat, fetch=lambda n:
                              independent.PackageFacts(n, 1, "2020-01-01"))


def test_two_destination_previews_share_actual_selected_credential_redaction():
    first, second = prepared(), prepared("glm")
    token = "test-not-a-real-token"
    second = replace(second, redactor=replace(second.redactor, secrets=(token,)))
    first = replace(first, parts=(*first.parts, ("user", token)))
    shared = service.shared_redaction(first, second)
    assert all(token not in item.preview() for item in shared)
    assert token in first.preview()  # immutable caller data was not silently changed


def test_truncated_independent_reply_is_not_a_partial_candidate():
    with pytest.raises(service.AdviceSetupError, match="truncated"):
        independent.review(prepared(), prepared("glm"),
                           chat=lambda *a, **kw: llm.Reply("{", 1, 1, 0, "length"))


def test_pypi_reads_only_bounded_project_json_and_counts_releases_with_files():
    data = {"info": {"name": "extra_demo"}, "releases": {
        "1": [{"upload_time_iso_8601": "2020-02-01T00:00:00Z"}],
        "2": [{"upload_time_iso_8601": "2021-01-01T00:00:00Z"}], "3": []}}

    def open_url(request, timeout):
        assert request.full_url == "https://pypi.org/pypi/extra-demo/json" and timeout == 10
        return io.BytesIO(json.dumps(data).encode("utf-8"))

    facts = independent.pypi_facts("extra-demo", open_url=open_url)
    assert facts.releases == 2 and facts.first_upload.startswith("2020-02-01")


@pytest.mark.parametrize("data", [{}, {"info": {"name": "other-demo"}, "releases": {}},
                                 {"info": {"name": "extra-demo"}, "releases": {"1": []}}])
def test_bad_or_empty_pypi_evidence_never_becomes_a_recommendation(data):
    with pytest.raises(service.AdviceSetupError, match="no new package"):
        independent.pypi_facts("extra-demo", open_url=lambda *a, **kw:
                               io.BytesIO(json.dumps(data).encode("utf-8")))
