"""Generate exercises (docs/design.md, "Generation pipeline"). Needs the `gen` extra:
pip install -e ".[gen]".

    python -m tutor.graph plan --lectures L4.1 L4.2 --batch w4 [--prefs "..."] [--count N]
    python -m tutor.graph resume --batch w4 [--approve | --feedback "..."]
    python -m tutor.graph item ITEM.json --batch w4-exp      (one item, for experiments)

`plan` asks the `plan` role's model ([llm.roles] in workspace.toml; the `generate` role if
there is none) for a batch plan from coverage.md's gaps, shows it, and waits for the owner:
approve it (after editing batches/.drafts/<batch>/plan.json if wished), or send it back with
feedback. The state is saved, so `resume` continues later, in another terminal. Approved
items are generated in parallel (--parallel, default from the CPU count) into
`batches/<today>_p3-<LABEL>/`; each round's files and report stay in
`batches/.drafts/<batch>/<id>/round-N/`; outcomes go to the batch's generation.jsonl, and
coverage-proposal.md lists the coverage.md changes for the owner to apply.

With an `oracle` role (or --oracle-profile), that model writes each exercise's oracle.py
from the question alone; best another provider than the models that write the exercise
(one provider is allowed, with a note: less independent). With a `repair` role (or
--repair-profile), a larger model takes over from the second repair.

Tunable values (repair rounds, thinking, token limits, ...; see graph/settings.py) come
from the defaults, then workspace.toml's [generation] table, then this run's flags:
`--set max_rounds=2 --set stronger_repair_from=1`. A batch keeps the values it was
planned with; `resume --set ...` changes them for the rest of it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from tutor.config import WORKSPACE_FILE, WorkspaceError, load_env, load_workspace
from tutor.container import ContainerError, check_course
from tutor.exchange import AwaitingAnswer
from tutor.graph.batch import latest_results, write_manifest
from tutor.graph.languages import course_language, profile_of
from tutor.graph.nodes import Deps, check_round, reusing_reports
from tutor.graph.plan_review import _owner_decision, approval_decision
from tutor.graph.settings import (
    DEFAULTS,
    DEPS_FIELDS,
    GenerationSettings,
    parse_set,
    resolve,
)
from tutor.llm import LLMError, config_for, roles

WAITING = 3  # exit code: assistant requests wait for an answer (assistant mode)
# Flags that choose a model for a role: kept with the batch, outside the settings.
_PROFILE_FLAGS = ("profile", "plan_profile", "oracle_profile", "repair_profile")
# The older named flags that set a settings field (same name); "on" / "low" mean True / False.
_SETTING_FLAGS = ("thinking", "repair_thinking", "plan_thinking", "mode", "parallel",
                  "max_count")


def default_parallel() -> int:
    """Items generated at once: each validation runs Spark with every core, so about one
    per four cores (3 on a 12-core M2 Pro), between 1 and 4."""
    return max(1, min(4, (os.cpu_count() or 4) // 4))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tutor.graph")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="plan a batch, then generate it once approved")
    plan.add_argument("--lectures", nargs="+", required=True,
                      help="lecture file names without .pdf (or .html), as in lectures/")
    plan.add_argument("--prefs", default="", help="your preferences, in plain words")
    plan.add_argument("--count", type=int,
                      help="exactly this many (default: one per knowledge point)")
    plan.add_argument("--max-count", type=int,
                      help="at most this many when --count is not given (default: 6)")
    resume = sub.add_parser("resume", help="continue a planned batch")
    resume.add_argument("--approve", action="store_true")
    resume.add_argument("--feedback")
    resume.add_argument("--only", help="comma-separated plan IDs; requires --approve")
    resume.add_argument("--plan-digest", help="fingerprint of reviewed plan; requires --approve")
    resume.add_argument("--profile", help="generate the remaining items with this profile")
    resume.add_argument("--oracle-profile", help="write their oracles with this profile")
    resume.add_argument("--repair-profile", help="repair them with this profile")
    resume.add_argument("--set", action="append", metavar="NAME=VALUE",
                        help="change a setting for the rest of the batch")
    item = sub.add_parser("item", help="generate one item from a JSON file")
    item.add_argument("item_file", type=Path)
    for p in (plan, resume, item):
        p.add_argument("--batch", required=True, help="batch label, e.g. w4-spark")
    for p in (plan, item):
        # Defaults of None: a flag left out must not hide the workspace's [generation].
        p.add_argument("--set", action="append", metavar="NAME=VALUE",
                       help="a generation setting for this run, e.g. max_rounds=2 "
                            "(names in graph/settings.py)")
        p.add_argument("--profile", help="profile for generating (default: generate role)")
        p.add_argument("--oracle-profile",
                       help="profile writing the oracle (default: oracle role, if set)")
        p.add_argument("--repair-profile",
                       help="profile for repairs from the second on (default: repair role)")
        p.add_argument("--repair-thinking", choices=["low", "on"],
                       help="whether that repair model reasons first (default: on)")
        p.add_argument("--thinking", choices=["low", "on"],
                       help="on: the model reasons at length (slower; GLM gets cut off)")
        p.add_argument("--mode", choices=["learn", "review"])
    plan.add_argument("--parallel", type=int,
                      help="items at once (default: one per four CPU cores, 1-4)")
    plan.add_argument("--plan-profile", help="profile that plans (default: plan role)")
    plan.add_argument("--plan-thinking", choices=["low", "on"],
                      help="whether the planning model reasons first (default: on)")
    args = parser.parse_args(argv)
    # Reject incompatible authorization flags before configuration or unfinished work runs.
    if args.command == "resume":
        if (args.only is not None or args.plan_digest is not None) and not args.approve:
            parser.error("--only and --plan-digest require --approve")
        if args.approve and args.feedback is not None:
            parser.error("--approve and --feedback cannot be combined")
    try:
        from tutor.graph import build
    except ImportError:
        print('langgraph is not installed: pip install -e ".[gen]"', file=sys.stderr)
        return 1
    load_env()
    try:
        workspace = load_workspace().root
        # Before the first LLM call: a draft that cannot be validated is a draft paid for.
        check_course(workspace)
        _check_language(workspace)
        if args.command == "item":
            return _item(args, workspace, build)
        return _batch(args, workspace, build)
    except (WorkspaceError, ContainerError, LLMError, OSError, ValidationError, ValueError) as e:
        print(f"stopped: {e}", file=sys.stderr)
        return 1


def _check_language(workspace: Path) -> None:
    """A C course's exercises are validated only in a container (docs/design.md, "C"): one
    whose tests would run on this machine fails rule `safe` on every draft, so it stops here,
    before any model is paid for. ValueError with what to do."""
    if course_language(workspace) != "c":
        return
    with (workspace / WORKSPACE_FILE).open("rb") as f:
        runner = tomllib.load(f).get("isolation", {}).get("runner")
    if runner != "container":
        raise ValueError('this is a C course, and C exercises are validated only in a '
                         'container: set [isolation] runner = "container" (the Environment '
                         "page, step 3) and build the test image")


def _given(args) -> dict:
    """The settings this run's command line sets: the named flags that were given, then
    each --set (which wins)."""
    given = {}
    for name in _SETTING_FLAGS:
        value = getattr(args, name, None)
        if value is not None:
            given[name] = value == "on" if value in ("on", "low") else value
    return {**given, **parse_set(getattr(args, "set", None))}


def _profiles(args) -> dict:
    """The profile flags that were given, e.g. {"oracle_profile": "gpt-terra"}."""
    return {name: getattr(args, name) for name in _PROFILE_FLAGS if getattr(args, name, None)}


def _write_run(path: Path, profiles: dict, settings: GenerationSettings) -> None:
    """run.json: what the batch was planned with, read again by every resume."""
    path.write_text(json.dumps({"profiles": profiles, "settings": settings.model_dump()},
                               indent=1) + "\n", encoding="utf-8")


def _read_run(path: Path) -> tuple[dict, GenerationSettings]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "settings" in data:
        return data.get("profiles", {}), GenerationSettings.model_validate(data["settings"])
    # Written before the settings existed (until 2026-10-29): one flat dict of the flags,
    # with "on" / "low" for thinking.
    profiles = {k: v for k, v in data.items() if k in _PROFILE_FLAGS and v}
    flags = {k: v == "on" if v in ("on", "low") else v for k, v in data.items()
             if k in _SETTING_FLAGS and v is not None}
    return profiles, GenerationSettings.model_validate(flags)


def _show_settings(settings: GenerationSettings) -> None:
    """One line with the values that differ from the defaults, so a run says how it differs."""
    changed = {k: v for k, v in settings.model_dump().items() if getattr(DEFAULTS, k) != v}
    if changed:
        print("settings: " + ", ".join(f"{k}={v}" for k, v in changed.items()), flush=True)


def _deps(workspace: Path, batch_dir: Path, profiles: dict,
          settings: GenerationSettings = DEFAULTS) -> Deps:
    """The nodes' dependencies: a model per role (from `profiles`, else [llm.roles]) and the
    tunable values from `settings`."""
    config = config_for("generate", profiles.get("profile"), workspace)
    plan_config = _optional_role("plan", profiles.get("plan_profile"), workspace)
    oracle_config = _optional_role("oracle", profiles.get("oracle_profile"), workspace)
    repair_config = _optional_role("repair", profiles.get("repair_profile"), workspace)
    for writer in (config, repair_config):
        if not (oracle_config and writer) or (writer is repair_config and config
                                              and writer.name == config.name):
            continue  # no oracle, no repair role, or repair is the generate profile: said once
        same_place = (oracle_config.provider, oracle_config.base_url) == (writer.provider,
                                                                          writer.base_url)
        if same_place and oracle_config.provider == "assistant":
            # One assistant profile for both: their requests would mix in one queue.
            raise ValueError(f"the oracle ({oracle_config.name}) and {writer.name}, which "
                             "writes the exercise, are one assistant profile, whose requests "
                             "would mix; one assistant may do both through a second profile, "
                             "answered in a fresh context: docs/assistant-mode.md")
        if same_place:
            # Allowed with a note (owner, 2026-11-07; it was refused): not everyone pays for
            # two providers, and an oracle call still reads the question alone, apart from
            # the exercise, where with no oracle role the generating model writes oracle.py
            # in the same reply. One provider's models share training data and habits, so
            # the reading is less independent than another provider's.
            print(f"note: the oracle ({oracle_config.name}) and {writer.name}, which writes "
                  "the exercise, come from one provider: its check is a second reading by "
                  "that provider's model, apart from the exercise but less independent than "
                  "another provider's.", flush=True)
        elif (oracle_config.provider == writer.provider == "assistant"
                and oracle_config.model == writer.model):
            # Allowed (owner, 2026-10-29), but the reader is the same assistant: say so.
            print(f"note: {writer.model} writes the exercise and, through "
                  f"{oracle_config.name}, the oracle; answer the oracle requests in a fresh "
                  f"context that has not seen the exercise's answer. Less independent than "
                  f"two providers.", flush=True)
    roles_used = [c for c in (config, plan_config, oracle_config, repair_config) if c]
    extra = {}
    if any(c.provider == "assistant" for c in roles_used):
        # Items replay from their start after each answer: keep validation reports.
        drafts = workspace / "batches" / ".drafts" / batch_dir.name / "checks"
        extra["check"] = reusing_reports(check_round, drafts)
    return Deps(workspace=workspace, batch_dir=batch_dir, config=config,
                language=course_language(workspace),
                plan_config=plan_config, oracle_config=oracle_config,
                repair_config=repair_config, on_update=_progress, on_stream=_receiving,
                **extra, **{name: getattr(settings, name) for name in DEPS_FIELDS})


def _optional_role(role: str, profile: str | None, workspace: Path):
    """The role's model, or None when [llm.roles] does not name it and no profile is given
    (config_for would fall back to the LLM_* variables, i.e. the generating model)."""
    if not profile and role not in roles(workspace):
        return None
    return config_for(role, profile, workspace)


def _item(args, workspace: Path, build) -> int:
    item = profile_of(workspace).plan_item.model_validate_json(
        args.item_file.read_text(encoding="utf-8"))
    existing = sorted((workspace / "batches").glob(f"*_p3-{args.batch}"))
    batch_dir = (existing[-1] if existing
                 else workspace / "batches" / f"{date.today()}_p3-{args.batch}")
    settings = resolve(workspace, _given(args))
    deps = _deps(workspace, batch_dir, _profiles(args), settings)
    oracle = f", oracle {deps.oracle_config.name}" if deps.oracle_config else ""
    oracle += f", repair {deps.repair_config.name}" if deps.repair_config else ""
    thinking = "on" if settings.thinking else "low"
    print(f"{item.id}: {deps.config.name} ({deps.config.model}, thinking {thinking}"
          f"{oracle}) -> {batch_dir.relative_to(workspace)}", flush=True)
    _show_settings(settings)
    final: dict = {}
    try:
        for update in build.build_item(deps).stream({"item": item}, stream_mode="updates"):
            for node, change in update.items():
                final.update(change or {})
                _progress(item.id, node, change or {})
    except AwaitingAnswer as e:
        # Run the same command again once answered: the item replays up to here.
        print(f"request {e.request_id} waits for the assistant: answer it "
              f"(python -m tutor.exchange show {e.request_id}), then run this again")
        return WAITING
    if final.get("usage", {}).get("input"):  # none from an assistant
        print(f"  {_usage_text(final['usage'])}")
    if final.get("outcome") == "saved":
        write_manifest(deps, [item])
        return 0
    return 1


def _batch(args, workspace: Path, build) -> int:
    """`plan` or `resume`: run the batch's graph until it ends or pauses, for the owner (the
    plan) or for an assistant's answers (assistant mode). The graph's state is in the batch's
    checkpoint.sqlite, so each pause ends this process and a later `resume` goes on."""
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.types import Command

    drafts = workspace / "batches" / ".drafts"
    if args.command == "plan":
        batch_dir, profiles, settings, pending = _new_batch(args, workspace, drafts)
    else:
        batch_dir, profiles, settings = _resumed_batch(args, workspace, drafts)
        pending = None  # nothing new: the graph goes on from where it is
    _show_settings(settings)
    deps = _deps(workspace, batch_dir, profiles, settings)
    parallel = settings.parallel or default_parallel()
    config = {"configurable": {"thread_id": batch_dir.name}, "max_concurrency": parallel}
    with SqliteSaver.from_conn_string(str(deps.drafts / "checkpoint.sqlite")) as saver:
        app = build.build_batch(deps, saver)
        # The owner's answer given as a flag; it answers the first pause at the plan only.
        flag = ({"approve": True} if getattr(args, "approve", False)
                else {"feedback": args.feedback} if getattr(args, "feedback", None) else None)
        if flag and flag.get("approve"):
            if args.only is not None:
                flag["selected_ids"] = args.only.split(",") if args.only else []
            if args.plan_digest is not None:
                flag["plan_digest"] = args.plan_digest
        if flag:
            # _advance can dispatch a stopped item. An approval must target the approval
            # interrupt itself, not implicitly approve a newly finished, unseen plan.
            snapshot = app.get_state(config)
            at_approval = any(isinstance(p.value, dict) and "plan" in p.value
                              for task in snapshot.tasks for p in task.interrupts)
            if not at_approval or snapshot.values.get("approved"):
                raise ValueError("This batch is not waiting for plan approval. Continue it "
                                 "without approval/selection flags.")
            if flag.get("approve"):
                approval_decision(snapshot, deps, workspace, flag)
        if args.command == "resume":
            # Invalid approvals must not change the settings of an existing run.
            _write_run(deps.drafts / "run.json", profiles, settings)
        # Assistant mode: a resume first tells a batch paused for answers to go on, once;
        # if it pauses again in the same run, the new requests are reported instead.
        answered = args.command != "resume"
        # Each turn runs the graph until it stops, then sends what that stop waits for.
        while True:
            try:
                snapshot = _advance(app, pending, config)
            except AwaitingAnswer as e:
                # The plan itself waits for an assistant (items catch their own requests):
                # the batch stops here and `resume` replays the plan call once answered.
                return _report_waiting([e.request_id], args.batch)
            if not snapshot.next:  # ended: every item has its result
                return _summary(snapshot.values.get("results", []), batch_dir, workspace)
            if requests := _waiting_requests(snapshot):
                if answered:
                    return _report_waiting(requests, args.batch)
                answered = True
                pending = Command(resume={"answered": True})
                continue
            # Paused at the plan: the owner's turn.
            pending = _owner_decision(snapshot, deps, workspace, flag, parallel)
            flag = None
            if pending is None:
                print(f"Paused. Continue with: python -m tutor.graph resume --batch "
                      f"{args.batch} --approve   (or --feedback \"...\")")
                return 0


def _new_batch(args, workspace: Path, drafts: Path) -> tuple[Path, dict, GenerationSettings,
                                                              dict]:
    """`plan`: the new batch's folder, its models and settings (saved in its run.json), and
    the graph's first input. A label already used, by a batch or a plan, is refused."""
    if list((workspace / "batches").glob(f"*_p3-{args.batch}")) or list(
            drafts.glob(f"*_p3-{args.batch}/checkpoint.sqlite")):
        raise ValueError(f"a batch labelled {args.batch} exists: resume it or choose "
                         "another label")
    batch_dir = workspace / "batches" / f"{date.today()}_p3-{args.batch}"
    # Resolved once here and saved: the whole batch runs with the same values, even
    # if [generation] changes before a resume.
    profiles, settings = _profiles(args), resolve(workspace, _given(args))
    (drafts / batch_dir.name).mkdir(parents=True)
    _write_run(drafts / batch_dir.name / "run.json", profiles, settings)
    first = {"lectures": args.lectures, "preferences": args.prefs,
             "count": args.count, "max_count": settings.max_count}
    return batch_dir, profiles, settings, first


def _resumed_batch(args, workspace: Path, drafts: Path) -> tuple[Path, dict,
                                                                  GenerationSettings]:
    """`resume`: the planned batch's folder, and its models and settings from run.json, with
    what this command's flags change saved there for later resumes."""
    found = sorted(drafts.glob(f"*_p3-{args.batch}/checkpoint.sqlite"))
    if not found:
        raise ValueError(f"no planned batch labelled {args.batch}")
    batch_dir = workspace / "batches" / found[-1].parent.name
    run_file = found[-1].parent / "run.json"
    profiles, settings = _read_run(run_file)
    # e.g. finish a batch with another model, or with one more repair round; the
    # changes are saved, so later resumes keep them.
    new_profiles, given = _profiles(args), _given(args)
    if new_profiles or given:
        profiles.update(new_profiles)
        settings = resolve(workspace, given, base=settings)
    return batch_dir, profiles, settings


def _advance(app, pending, config):
    """Run the graph with `pending` (the first input, or the answer to the pause it is at;
    None: nothing new), and return its state once it has ended or paused. A run an earlier
    process left midway (stopped, not paused) is continued first: items that finished keep
    their results and are not run again. An assistant request the plan call waits for is
    raised (AwaitingAnswer)."""
    if pending is not None:
        for update in app.stream(pending, config, stream_mode="updates"):
            if "plan" in update:
                print(f"plan: {len(update['plan']['plan'])} items", flush=True)
    snapshot = app.get_state(config)
    # Tasks to run with no interrupt among them: not waiting for anyone, so go on.
    while snapshot.next and not any(task.interrupts for task in snapshot.tasks):
        print(f"continuing the stopped run at: {', '.join(snapshot.next)}", flush=True)
        for _ in app.stream(None, config, stream_mode="updates"):
            pass
        snapshot = app.get_state(config)
    return snapshot


def _waiting_requests(snapshot) -> list[str]:
    """The request ids a batch paused for in assistant mode (the `answers` node's
    interrupt), or [] when it paused for something else (the plan approval)."""
    return [rid for task in snapshot.tasks for pause in task.interrupts
            if isinstance(pause.value, dict) for rid in pause.value.get("waiting", [])]


def _report_waiting(requests: list[str], batch: str) -> int:
    print(f"{len(requests)} requests wait for the assistant: {', '.join(requests)}\n"
          f"Answer them (python -m tutor.exchange list; docs/assistant-mode.md), then run "
          f"python -m tutor.graph resume --batch {batch}")
    return WAITING


def _progress(exercise: str, node: str, change: dict) -> None:
    if node == "retrieve_context":
        text = f"context: {len(change['context']):,} characters of course pages"
    elif node in ("generate", "repair"):
        state = "format error" if change.get("format_error") else "draft ready"
        by = f", {change['model']}" if change.get("model") else ""
        # An assistant reports no tokens: leave the count out rather than print 0.
        tokens = f", {change['tokens']:,} tokens so far" if change["tokens"] else ""
        text = f"{node} (round {change['round']}{by}): {state}{tokens}"
    elif node == "oracle":
        if not change:
            return  # no oracle role: the generating model wrote it
        text = f"oracle: {change['oracle_status']}" + (
            " (" + change["failure"].removeprefix("format: ")[:140] + ")"
            if change.get("oracle_status") == "failed" else "")
    elif node == "validate":
        failure = change.get("failure")
        text = ("validate: VALID" if failure is None
                else "validate: " + failure.splitlines()[0][:160])
        dropped = change["attempts"][-1].get("dropped") if change.get("attempts") else None
        if dropped:
            text += f" (left out, could not be applied: {', '.join(dropped)})"
    else:
        text = node
    print(f"  [{exercise}] {text}", flush=True)


_RECEIVED: dict[str, int] = {}  # characters of the reply arriving for each exercise
RECEIVING_STEP = 20_000


def _receiving(exercise: str, chars: int) -> None:
    """A line each time a streamed reply passes another RECEIVING_STEP characters."""
    if chars // RECEIVING_STEP > _RECEIVED.get(exercise, 0) // RECEIVING_STEP:
        print(f"  [{exercise}] receiving: {chars:,} characters", flush=True)
    _RECEIVED[exercise] = chars


def _usage_text(usage: dict[str, int]) -> str:
    """e.g. "input 45,277 tokens (13,440 from the provider's cache, 30%), output 9,809"."""
    share = usage["cached"] / usage["input"] if usage["input"] else 0.0
    return (f"input {usage['input']:,} tokens ({usage['cached']:,} from the provider's "
            f"cache, {share:.0%}), output {usage['output']:,}")


def _summary(results: list[dict], batch_dir: Path, workspace: Path) -> int:
    results = latest_results(results)  # an item run again after waiting: its last result
    print(f"\n{batch_dir.relative_to(workspace)}:")
    total = {"input": 0, "cached": 0, "output": 0}
    for r in results:
        why = f"  ({r['last_failure'].splitlines()[0][:100]})" if r["outcome"] != "saved" else ""
        print(f"  {r['outcome']:<9} {r['id']}: {r['rounds'] + 1} round(s), "
              f"{r['tokens']:,} tokens{why}")
        for key in total:
            total[key] += r.get("usage", {}).get(key, 0)
    if total["input"]:
        print(f"  all items: {_usage_text(total)}")
    if (batch_dir / "coverage-proposal.md").is_file():
        proposal = (batch_dir / "coverage-proposal.md").relative_to(workspace)
        print(f"coverage.md changes proposed in {proposal}")
    return 0 if any(r["outcome"] == "saved" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
