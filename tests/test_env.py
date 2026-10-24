"""This machine matches the current workspace's [requires]. Needs a local .env."""

import os

import pytest

from tutor.config import load_env, load_workspace
from tutor.envcheck import check_workspace


def test_environment_matches_workspace():
    load_env()
    if not os.environ.get("WORKSPACE_DIR"):
        pytest.skip("WORKSPACE_DIR not set (no .env); nothing to check on this machine")

    ws = load_workspace()  # a set-but-broken workspace should fail, not skip
    failures = [r for r in check_workspace(ws) if not r.ok]

    assert not failures, "\n".join(
        f"{r.name}: required {r.required}, found {r.found or '-'} -> {r.hint}" for r in failures
    )
