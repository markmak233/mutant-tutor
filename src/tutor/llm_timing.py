"""When can a provider's prompt cache be read by the next call? A measurement for one profile,
to choose its `parallel_start` (llm.PARALLEL_START; owner, 2026-11-12: "a button that tests
it, then I adjust"). Paid: about sixteen short calls, so it runs only on a click.

    python -m tutor.llm_timing PROFILE      (in a course, or with WORKSPACE_DIR set)

Four rounds, one per way a lecture's other calls can start: after the first call's first
streamed event, 3 s after it, 10 s after it, and after the first call has ended. Each round
sends one long call (it must still be streaming at +10 s) with a prefix nobody has cached
(a new id at its start), releases three short calls with the same prefix at that round's
moment, and records for every call its input tokens, the cached ones, its time to the first
text and the round's wall time. The prefix is this package's own source text, so no course
material is sent, and it is there in an installed tool too (docs/ is not).

A follower read the cache when the provider says so (cached share >= HIT_SHARE), or, for a
server that reports no cached tokens (LM Studio, Ollama), when its first text came at most
FAST_RATIO of the first call's (a prefix cached is not computed again). The suggestion:
  - a provider that reports cached tokens: the earliest round whose three followers all
    read it (money: an earlier start that misses pays for the prefix again);
  - one that reports none: the round that finished soonest (2026-11-12, LM Studio: the
    followers read the prefix from the first event, but four replies decoding together
    slowed the first, and waiting for its end finished sooner);
  - no follower read it in any round: "together", as waiting gains nothing.
Only suggested: the owner sets it on the model form. Thinking is off on every call: the
cache does not depend on it and it would only cost more.
"""

from __future__ import annotations

import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from tutor import llm

# The rounds: a name as parallel_start writes it, and the seconds after the first event the
# followers wait (None: until the first call has ended).
ROUNDS: tuple[tuple[str, float | None], ...] = (
    ("first-event", 0.0), ("first-event+3", 3.0), ("first-event+10", 10.0), ("first-done", None))
FOLLOWERS = 3
PREFIX_CHARS = 40_000  # about 10k tokens: past every provider's cache minimum, cheap to send
LONG_TOKENS = 2_000  # the first call's reply: long enough to be streaming at +10 s
SHORT_TOKENS = 40  # a follower's reply: only its first text matters
HIT_SHARE = 0.8  # a follower read the cache: at least this share of its input was cached
FAST_RATIO = 0.5  # a server with no counts: first text at most this share of the first's
FIRST_EVENT_WAIT_S = 300  # the first event of the long call, at most (then the round fails)
LONG_QUESTION = ("Describe, section by section and at length, what this source code does and "
                 "why. Keep writing until you reach the limit.")
SHORT_QUESTION = "In one short sentence: what is this source code for?"


@dataclass
class Call:
    """One call of a round, as measured: seconds from the round's start."""

    first: bool  # the round's long call
    started: float = 0.0
    seconds: float = 0.0  # the whole call
    first_text_s: float | None = None  # from its own start to its first text
    input_tokens: int | None = None
    cached: int | None = None
    error: str = ""

    def share(self) -> float | None:
        """The share of its input served from the cache; None when the server gives none."""
        if self.cached is None or not self.input_tokens:
            return None
        return self.cached / self.input_tokens


@dataclass
class Round:
    name: str
    calls: list[Call] = field(default_factory=list)
    wall_s: float = 0.0  # from the first call's start to the last call's end
    note: str = ""  # e.g. the first call ended before the release, so this was first-done

    @property
    def followers(self) -> list[Call]:
        return [c for c in self.calls if not c.first]

    def read_cache(self, counted: bool) -> bool:
        """Did every follower read the prefix from the cache (see the module docstring)?"""
        first = self.calls[0]
        if any(c.error for c in self.calls) or not self.followers:
            return False
        if counted:
            return all((c.share() or 0) >= HIT_SHARE for c in self.followers)
        if first.first_text_s is None:
            return False
        return all(c.first_text_s is not None and c.first_text_s <= FAST_RATIO * first.first_text_s
                   for c in self.followers)


def shared_text(chars: int = PREFIX_CHARS) -> str:
    """This package's own source, in a fixed order, cut to `chars`: the same prefix wherever
    the tool is installed, and nothing of a course."""
    here = Path(__file__).resolve().parent
    parts = []
    for path in sorted(here.rglob("*.py")):
        parts.append(f"# {path.relative_to(here).as_posix()}\n"
                     + path.read_text(encoding="utf-8", errors="replace"))
        if sum(map(len, parts)) >= chars:
            break
    return "".join(parts)[:chars]


def _messages(nonce: str, text: str, question: str) -> list[dict]:
    """The system prompt starts with the round's id, so its prefix is cached by nobody yet;
    llm.Prompt marks the shared part for a provider that takes cache breakpoints."""
    system = llm.Prompt(f"Material {nonce}. Read it, then answer the question after it.\n\n"
                        + text, "\n\n(end of the material)")
    return [{"role": "system", "content": system}, {"role": "user", "content": question}]


def run_round(config: llm.LLMConfig, name: str, wait_s: float | None, *,
              chat: Callable = llm.chat, log_path: Path | None = None,
              text: str | None = None) -> Round:
    """One round: the long call, then the followers at the round's moment, all joined."""
    text = shared_text() if text is None else text
    nonce = uuid.uuid4().hex
    result = Round(name, [Call(True)] + [Call(False) for _ in range(FOLLOWERS)])
    began = threading.Event()  # the long call's first streamed event (on_progress(0))
    t0 = time.monotonic()

    def one(call: Call, question: str, max_tokens: int, signal: threading.Event | None) -> None:
        call.started = time.monotonic() - t0

        def progress(chars: int) -> None:
            if signal is not None:
                signal.set()
            if chars and call.first_text_s is None:
                call.first_text_s = time.monotonic() - t0 - call.started
        try:
            reply = chat(config, _messages(nonce, text, question), purpose="timing",
                         log_path=log_path, max_tokens=max_tokens, thinking=False, stream=True,
                         on_progress=progress)
            call.input_tokens, call.cached = reply.input_tokens, reply.cached_tokens
        except llm.LLMError as e:
            call.error = str(e)[:300]
        finally:
            call.seconds = time.monotonic() - t0 - call.started
            if signal is not None:
                signal.set()  # a call that failed or never streamed frees the round

    lead = threading.Thread(target=one, args=(result.calls[0], LONG_QUESTION, LONG_TOKENS, began))
    lead.start()
    if wait_s is None:
        lead.join()
    else:
        began.wait(FIRST_EVENT_WAIT_S)
        time.sleep(wait_s)
        if not lead.is_alive():
            result.note = "the first call had ended before the release, so this was first-done"
    others = [threading.Thread(target=one, args=(c, SHORT_QUESTION, SHORT_TOKENS, None))
              for c in result.followers]
    for t in others:
        t.start()
    for t in [lead, *others]:
        t.join()
    result.wall_s = max(c.started + c.seconds for c in result.calls)
    return result


def suggest(rounds: list[Round]) -> tuple[str, str]:
    """(parallel_start, why) from the rounds, by the rules in the module docstring."""
    counted = any(c.cached is not None for r in rounds for c in r.calls)
    usable = [r for r in rounds if not r.note]  # a round that was really another is left out
    reading = [r for r in usable if r.read_cache(counted)]
    if not reading:
        return "together", ("no follower read the cache in any round, so waiting for the "
                            "first call gains nothing")
    if counted:
        best = reading[0]
        return best.name, (f"the earliest moment at which all {FOLLOWERS} followers read the "
                           f"cache (the provider counts cached tokens)")
    best = min(reading, key=lambda r: r.wall_s)
    return best.name, ("the server counts no cached tokens; of the rounds whose followers "
                       f"answered fast (the prefix not computed again), this one finished "
                       f"soonest, in {best.wall_s:.0f} s")


def report(rounds: list[Round]) -> list[str]:
    """The lines a person reads: one per round, then each call."""
    lines = []
    for r in rounds:
        lines.append(f"{r.name}: all done in {r.wall_s:.1f} s" + (f" ({r.note})" if r.note else ""))
        for i, c in enumerate(r.calls):
            who = "first call" if c.first else f"follower {i}"
            if c.error:
                lines.append(f"  {who}: failed: {c.error}")
                continue
            share = c.share()
            cached = (f"{c.cached:,} of {c.input_tokens:,} input tokens cached ({share:.0%})"
                      if share is not None else "no cached-token count from the server")
            first = f"{c.first_text_s:.1f} s" if c.first_text_s is not None else "none"
            lines.append(f"  {who}: started at {c.started:.1f} s, first text after {first}, "
                         f"{c.seconds:.1f} s in all; {cached}")
    return lines


def main(argv: list[str] | None = None) -> int:
    """python -m tutor.llm_timing PROFILE: measure, print every call, suggest a setting."""
    from tutor.config import WorkspaceError, load_env, load_workspace

    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("Usage: python -m tutor.llm_timing PROFILE", file=sys.stderr)
        return 2
    load_env()
    try:
        workspace = load_workspace().root
        config = llm.config_for("timing", args[0], workspace)
    except (WorkspaceError, llm.LLMError) as e:
        print(f"setup: {e}", file=sys.stderr)
        return 1
    if config.provider == "assistant":
        print("nothing to measure: a coding assistant answers its requests", file=sys.stderr)
        return 1
    print(f"{args[0]} ({config.provider}: {config.model}), now parallel_start = "
          f"{config.parallel_start!r}. {len(ROUNDS)} rounds of 1 + {FOLLOWERS} calls, a "
          f"{PREFIX_CHARS:,}-character prefix (this tool's own source) new in each round.",
          flush=True)
    text, rounds, log = shared_text(), [], workspace / "logs" / "llm_calls.jsonl"
    for name, wait_s in ROUNDS:
        print(f"round {name} ...", flush=True)
        rounds.append(run_round(config, name, wait_s, log_path=log, text=text))
    print("\n".join(report(rounds)))
    if all(c.error for r in rounds for c in r.calls):
        print("every call failed: nothing to suggest", file=sys.stderr)
        return 1
    choice, why = suggest(rounds)
    print(f"\nSuggested: parallel_start = \"{choice}\": {why}. Set it on the model's form "
          f"(More); this command changes nothing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
