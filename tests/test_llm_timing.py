"""The parallel_start measurement (tutor.llm_timing) against stand-in servers that serve a
prefix from their cache at different moments, as the real ones did on 2026-11-11/12. No real
model is called: the chat function is a fake that keys its cache on the request's text."""

import threading
import time

import pytest

from tutor import llm, llm_timing
from tutor.llm import LLMConfig, Reply

CONFIG = LLMConfig("p", "custom", "http://x/v1", "m")
LONG_S = 0.25  # the stand-in's long call
# Small rounds for the tests: the same names, waits scaled down from 0 / 3 / 10 s.
ROUNDS = (("first-event", 0.0), ("first-event+3", 0.03), ("first-event+10", 0.1),
          ("first-done", None))


class Server:
    """A stand-in provider. `ready` says when a prefix may be read once its first call's
    stream has begun: a number of seconds after that, or "end" (only once that call has
    ended); `counts`: whether replies carry cached-token counts (a local server's do not)."""

    def __init__(self, ready, counts=True, decode_together_s=0.0):
        self.ready, self.counts = ready, counts
        self.decode_together_s = decode_together_s  # a busy local server: slower when shared
        self.lock = threading.Lock()
        self.begun: dict[str, float] = {}  # prefix -> its first call's first event
        self.ended: set[str] = set()
        self.busy = 0
        self.started = 0  # calls started so far: another one during a call means company
        self.long_active = 0  # long calls under way: what a short call decodes beside

    def __call__(self, config, messages, *, purpose, on_progress, max_tokens, **kw):
        prefix = messages[0]["content"]
        long = max_tokens == llm_timing.LONG_TOKENS
        with self.lock:
            first = prefix not in self.begun
            if first:
                self.begun[prefix] = time.monotonic()
            since = time.monotonic() - self.begun[prefix]
            hit = not first and (prefix in self.ended if self.ready == "end"
                                 else since >= self.ready)
            self.busy += 1
            self.started += 1
            self.long_active += long
            company = not long and self.long_active > 0
            started_before = self.started
        on_progress(0)  # the stream has begun
        time.sleep(0.005 if hit else 0.06)  # a cached prefix is not computed again
        on_progress(5)  # the first text
        time.sleep(LONG_S if long else 0.01)
        with self.lock:
            # The long reply slows when others start beside it, and they beside it.
            company = company or (long and self.started > started_before)
        if company:  # decoding beside the long reply at some point: slower (one GPU)
            time.sleep(self.decode_together_s)
        with self.lock:
            self.busy -= 1
            self.long_active -= long
            if first:
                self.ended.add(prefix)
        cached = (1000 if hit else 0) if self.counts else None
        return Reply("ok", 1000, 10, 0.1, "stop", cached)


def measure(server):
    return [llm_timing.run_round(CONFIG, name, wait, chat=server, text="x" * 2000)
            for name, wait in ROUNDS]


@pytest.mark.parametrize(("server", "expected"), [
    # Anthropic: readable at the first streamed event (99.9% at +1 s).
    (Server(ready=0.0), "first-event"),
    # DeepSeek: 0% at the first event, 99% from 3 s (here: from 0.02 s, between rounds).
    (Server(ready=0.02), "first-event+3"),
    # GLM: only once the first call had ended.
    (Server(ready="end"), "first-done"),
])
def test_a_counting_provider_gets_the_earliest_moment_its_cache_is_read(server, expected):
    rounds = measure(server)
    choice, why = llm_timing.suggest(rounds)
    assert choice == expected and "earliest" in why
    # The first call of a round never reads a cache: its prefix is new each round.
    assert all(r.calls[0].cached == 0 for r in rounds)


def test_a_server_with_no_counts_gets_the_round_that_finished_soonest():
    """LM Studio, 2026-11-12: from the first event the followers reused the prefix, but
    decoding together slowed every reply, so waiting for the first's end finished sooner."""
    rounds = measure(Server(ready=0.0, counts=False, decode_together_s=0.4))
    assert all(r.read_cache(counted=False) for r in rounds)  # all fast to their first text
    choice, why = llm_timing.suggest(rounds)
    assert choice == "first-done" and "finished soonest" in why


def test_no_cache_at_all_suggests_starting_together():
    rounds = measure(Server(ready=99.0))
    assert llm_timing.suggest(rounds)[0] == "together"


def test_a_round_whose_first_call_ended_before_the_release_is_not_taken_for_its_name():
    server = Server(ready="end")
    late = llm_timing.run_round(CONFIG, "first-event+10", LONG_S + 0.2, chat=server, text="y")
    assert "had ended before the release" in late.note
    # It read the cache, but only as a first-done round would: not a reason for +10.
    assert llm_timing.suggest([late])[0] == "together"


def test_failed_calls_are_reported_and_read_no_cache():
    def failing(config, messages, **kw):
        raise llm.LLMError("HTTP 401: wrong key")
    rounds = [llm_timing.run_round(CONFIG, "first-event", 0.0, chat=failing, text="z")]
    lines = llm_timing.report(rounds)
    assert "failed: HTTP 401: wrong key" in "\n".join(lines)
    assert llm_timing.suggest(rounds)[0] == "together"


def test_the_prefix_is_this_tools_own_source_the_same_every_time():
    text = llm_timing.shared_text(5000)
    assert len(text) == 5000 and text == llm_timing.shared_text(5000)
    assert text.startswith("# ") and ".py\n" in text.splitlines()[0] + "\n"


def test_the_report_names_each_call_with_its_cached_share():
    rounds = measure(Server(ready=0.0))
    lines = llm_timing.report(rounds)
    assert lines[0].startswith("first-event: all done in")
    assert any("first call" in line and "(0%)" in line for line in lines)
    assert any("follower 1" in line and "(100%)" in line for line in lines)
