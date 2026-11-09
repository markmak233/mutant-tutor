"""Explicit independent new-name review: two blind replies and bounded public PyPI facts.

Agreement/existence is evidence for owner review, never permission to install. Fetch no
artifact or URL from model output, and never send one model the other's proposed name.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from tutor import llm
from tutor.doctor_advice import service
from tutor.doctor_advice.gate import review_reply
from tutor.doctor_advice.models import package_name, reply_schema
from tutor.doctor_advice.redaction import Redactor


@dataclass(frozen=True)
class PackageFacts:
    """Release-file evidence from PyPI; not a popularity or security score."""

    name: str
    releases: int
    first_upload: str


def pypi_facts(name: str, *, open_url=None) -> PackageFacts:
    """Fixed public JSON endpoint, no proxy credentials, redirect or artifact download."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    name = package_name(name)
    if open_url is None:
        open_url = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open
    request = urllib.request.Request(f"https://pypi.org/pypi/{name}/json",
                                     headers={"Accept": "application/json"})
    try:
        with open_url(request, timeout=10) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError
        data = json.loads(raw.decode("utf-8"))
        if package_name(data["info"]["name"]) != name:
            raise ValueError
        releases, times = 0, []
        for files in data["releases"].values():
            if files:
                releases += 1
            for item in files:
                # All dates must parse as timezone-aware ISO values before display/order.
                stamp = item["upload_time_iso_8601"].replace("Z", "+00:00")
                uploaded = datetime.fromisoformat(stamp)
                if uploaded.tzinfo is None:
                    raise ValueError
                times.append(uploaded)
        if not releases or not times:
            raise ValueError
        return PackageFacts(name, releases, min(times).isoformat())
    except (OSError, urllib.error.URLError, ValueError, KeyError, TypeError, AttributeError):
        raise service.AdviceSetupError("PyPI did not provide valid release-file facts; "
                                       "no new package is recommended") from None


def review(first, second, *, chat=None, fetch=None) -> PackageFacts | None:
    """Require distinct configured providers/endpoints before spending either request.

    Users must select genuinely independent models; proxy labels cannot establish the
    underlying identity. Conservatively refuse a same-provider or same-endpoint pair.
    """
    a, b = first.model, second.model
    if (a.provider == b.provider or urlsplit(a.base_url).hostname == urlsplit(b.base_url).hostname
            or a.provider == "assistant" or b.provider == "assistant"):
        raise service.AdviceSetupError("choose independent models on distinct provider endpoints "
                                       "with --profile and --second-profile")
    chat = llm.chat if chat is None else chat
    fetch = pypi_facts if fetch is None else fetch
    # Both actual API values must be removed even if a profile uses an unconventional
    # environment-variable name that the generic credential-name heuristic cannot spot.
    redactor = Redactor(tuple(set(first.redactor.secrets + second.redactor.secrets)),
                        tuple(set(first.redactor.paths + second.redactor.paths)))
    messages = [{"role": m["role"], "content": redactor.clean(m["content"])}
                for m in first.messages]
    messages[0]["content"] += ("\nIndependently review whether a NEW package is necessary. "
                                "Use needs_new_package only, with no changes. "
                                "No other model's suggestion is provided.")
    for model in (a, b):
        service.check_messages(model, messages, redactor, thinking=True)
    names = []
    for model in (a, b):
        for cap in (2048, 4096):
            reply = chat(model, messages, purpose="doctor-independent", thinking=True,
                         response_schema=reply_schema(), schema_first=True, max_tokens=cap,
                         log_path=None)
            if reply.finish_reason != "length":
                break
        else:
            raise service.AdviceSetupError("independent review truncated twice")
        gated = review_reply(reply.text, first.facts.context)
        if gated.status != "new-package-review":
            return None
        names.append(gated.needs_new_package)
    if names[0] != names[1]:
        return None
    result = fetch(names[0])
    if result.name != names[0] or redactor.contains_secret(result.name):
        raise service.AdviceSetupError("independent package facts could not be safely displayed")
    return result
