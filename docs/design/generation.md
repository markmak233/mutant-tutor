# Design: Generation

The LangGraph pipeline, hand-calculation exercises, prompt layout for caching and assistant mode. Part of the design record; the index of every part is [README.md](README.md).

## Generation pipeline (LangGraph)

```
plan -> [interrupt: owner approves / edits, adds preferences] -> for each plan item:
  retrieve_context -> generate -> validate --pass--> save
                         ^           |
                         +-- repair <-+ fail, round < 3
                                      + fail, round = 3 --> discard + log
```

Design approved 2026-10-28 (owner):
- Pluggable: nodes are plain functions (state in, partial state out), calling `tutor.llm`:
  an item's in `graph/nodes.py`, the oracle's in `oracle.py`, a round's draft and its
  validation in `drafts.py`, the batch's in `batch.py` (one file until 2026-11-08); only `graph/build.py` imports `langgraph` (optional dependency group
  `gen`), and nothing outside `graph/` depends on it. No LangChain. LangGraph is used for
  what is tedious by hand: interrupt and resume with a SQLite checkpointer in the workspace
  (approve the plan tomorrow), conditional edges, and state history for debugging.
- State: sources, preferences, plan (items: knowledge point, slide range, type, interface,
  framework), and per item the context, draft files, validation report, repair round;
  outcomes per item for the phase 5 metrics.
- Nodes: `plan` (LLM; coverage.md gaps + slide titles of the sources + preferences ->
  BatchPlan, then interrupt), `retrieve_context` (no LLM, see below), `generate` (LLM,
  structured output), `validate` (no LLM: `tutor.validation` as it is, rule 0 to api),
  `repair` (LLM; gets the draft and the failing rules' details, not the passing ones, so
  independent failures such as rule 3 and api are fixed in one round), `save` (moves
  the exercise into `batches/`, updates the manifest; a `coverage.md` change is proposed to
  the owner, never written).
- Drafts are written to `batches/.drafts/` and kept when discarded, to study failures.
- The draft (`graph/state.py` ExerciseDraft, sent as a JSON Schema; GLM enforces it) asks
  only for what cannot be derived: skeleton.py is student.py, and a mutant is a list of
  exact replacements in the reference (`old` must occur once) plus its bug, applied by
  code. So every mutant differs from the reference by exactly the stated change, its
  docstring is uniform, and the reply is shorter than 3-6 copies of the reference.
- Prompt: a stable system part (the repo's format rules, with the safe-check allowlist
  read from `safety.py`; the `tutor.testkit` API read from the module; the example
  exercise closest to the item's framework and interface; the workspace's
  `conventions.md`, the same file a hand-drafting assistant reads), then the item and its
  course pages, then, when repairing, the last reply and the failures.
- Stops: 3 repair rounds per item, a token budget per batch, an LLM error stops the current
  item and keeps the finished ones.
- Roles in `[llm.roles]`: `plan` = GLM-5.3-Flash with thinking low (see the experiment
  below; Claude Sonnet 5.5 since 2026-11-10); `generate` = DeepSeek-V4-Flash since
  2026-10-29 (the week 4 rerun below), with thinking on since 2026-10-30 (the week 7
  comparison below), and Claude Sonnet 5.5 on the deep-learning course since 2026-11-11 (next bullet);
  `repair` = V4-Pro from the second repair on; `oracle` = a different model family (GPT,
  decided 2026-10-28 in place of local Gemma; GLM-5.3-Flash at high effort once the OpenAI
  key expires, see the week 4 cross experiment below), since the same model writing
  reference and oracle is not an independent check. The CLI refuses an oracle from the
  generating model's provider.
- Sonnet 5.5 as `generate` (owner, 2026-11-11, the deep-learning course: "Claude is visibly faster"). Measured
  on that course's learn-mode weeks, the same prompts, oracle gpt-6.1-terra: DeepSeek-V4-Flash
  with thinking (weeks 1-2, 22 items) had 13 first drafts clean of 22 and 6 cut off at the
  32k output cap, each cut-off a whole call again, at about $0.04 an item (list prices) and
  131 s a call (median, 36 calls; its output 23.5k tokens a call, the thinking counted in,
  at 183 tokens/s); Sonnet 5.5 (weeks 3-4, 15 items) had 12 clean, none cut off (10.7k
  tokens a call), about $0.15 an item and 71 s a call (median, 19 calls). An item is one
  call and a half a minute with Sonnet against two to three calls and four to five minutes
  with DeepSeek, and the owner waits for the batch; the dollar a week between them is
  worth less than the time. Sonnet's generate request caches its course pages on Anthropic
  (56-61% of the input served from the cache from the second item on, the item's own
  part not). Two faults Sonnet exposed on the way, both on this side: three of its first
  drafts began with a sentence ("Here is the exercise as one JSON object. ...") before the
  object, a format failure and a paid repair each, as the draft schema's free-form
  `data_files` map keeps Anthropic's structured output out, so the parser now takes the
  JSON body of a reply (a fence or a sentence around it dropped, `llm.json_body`; a reply
  with no JSON at all is quoted in the failure); and the plan model wrote slide ranges with
  the lecture's number in front ("4.17-4.19", after the prompt's example) against footers
  that are single numbers, so five items found no page: single-number footers now match
  the last number of each end. Other courses keep DeepSeek until they generate next.
- Experiment 2026-10-28, one item (a closure / accumulator debugging exercise from the
  week 4 slides), same validator:

  | Model, thinking | Rounds | Time | Tokens | Mutants |
  |---|---|---|---|---|
  | GLM-5.3-Flash, on | 4 (2 cut off at 32k output) | 28 min | 164k | - |
  | GLM-5.3, on | stopped after 2 cut-off rounds | 19 min | 92k | - |
  | GLM-5.3-FlashX, on (old prompt) | 1 | 4.3 min | 46k | good |
  | GLM-5.3-Flash, low (old prompt) | 2 | 1.7 min | 39k | arbitrary edits |
  | GLM-5.3-Flash, low | 1 | 1.3 min | 18k | good, two slips |
  | GLM-5.3, low | 2 | 2.5 min | 42k | best: 3 misconceptions, 1 slip |
  | gpt-6.1-terra, default | 2 | 1.2 min | 38k (~$0.06) | good, 3 misconceptions |

  With thinking on, GLM spent its whole output on reasoning and was cut off before the
  answer, so thinking is low by default (`--thinking on` to override). The prompt change
  that mattered: mutants must be misconceptions of the knowledge point (at most one plain
  slip), and `source` must cite the pages (a model copied "synthetic" from the example; a
  code check now rejects it). One item per model, so a direction, not a measurement; phase
  5 repeats it on whole batches.
- A reply that cannot be read at all (cut off, not JSON) is not "repaired": the next round
  asks the item again with what went wrong, since an empty draft gives repair nothing.
- First real batch (week 4, 6 items, GLM-5.3-Flash, 2026-10-28) and what it changed: one
  saved, two discarded after three rounds each on the same mutant error (`old` not found:
  the model wrote other quotes or indentation than its own reference, and the error said
  only "not found"), and one item crashed the whole run, so collect never ran and two
  items never started. The crash was local, not the model: the example loader read
  macOS's `.DS_Store` (binary, git-ignored, so never in CI) from the file-level example;
  it was first blamed on a GLM reply, until GPT failed at the same byte. Fixes: the
  example loader reads only the exercise's own text files; any exception in an item is
  that item's error (logged, the others finish, collect runs); the client decodes replies
  with replacement (harmless either way); the item graph no longer inherits the batch's
  checkpointer (it saved every step's models in the batch's SQLite); a mutant error lists
  every failing mutant with the closest line of the reference, copied exactly; `resume`
  continues a run that stopped midway without redoing finished items (tested), and
  `resume --profile` finishes it with another model; the manifest names every model that
  generated its exercises.
- Week 4 again with four models (2026-10-28/29, same plan items; small samples): GPT 6/6
  VALID (about $0.07 each), DeepSeek 5/6 (about $0.01 each, the fastest), GLM-5.3-Flash
  saved 4 and discarded 2, GLM-5.3 discarded 3 of 3. DeepSeek became the `generate` role.
  The four discards that were not the model's fault changed two things:
  - GLM-5.3 lost all three to one mutant each whose `old` it invented (a "placeholder"
    line not in its reference) and sent back unchanged through three repairs. A mutant
    that cannot be applied is now left out while at least three others remain
    (`MIN_MUTANTS`, the schema's minimum): a VALID draft is saved without it, and a draft
    repaired for another reason is asked to fix it too. The round's `dropped` list goes to
    `generation.jsonl`. Cost: an exercise may have one mutant fewer, never fewer than
    three.
  - DeepSeek put the API calls it wanted used into `required_any`, which lists names the
    code defines, and could not read that from "must define one of". The constraint
    fields now carry descriptions, which reach the model in the draft's JSON Schema, and
    the api messages say the names are defined, not called. There is still no "must call"
    constraint: what an exercise must use is checked by its tests and `forbidden_calls`.
- A plain-Python example (2026-11-05): plain-Python items saw the Spark function-level example until
  then, so `examples/station-longest-rise` was written for them, about 1000 tokens shorter. Spark items
  keep theirs, so their prompts do not change. Rerunning 4 items of one long lecture with the same
  settings took VALID first drafts from 2 to 4 of 4 and tokens from 590k to 394k. That shows it did no
  harm, not that it helped: the two that passed both times cost about the same (201k and 203k), one
  earlier failure was a `getattr` the course conventions have since ruled out, and the other a reply
  that did not fit the schema, which may simply not recur.
- Week 7 with five arms (2026-10-29/30): the same plan of 11 plain-Python items from
  lectures 7.1-7.2 (stream algorithms) for every arm, arm D on 4 of them. Repairs from the
  second on are V4-Pro with thinking in every API arm; "s/item" is one item at a time.

  | Arm | Generate (thinking) | Oracle | First-round VALID | Saved | Rounds/item | s/item | Layout |
  |---|---|---|---|---|---|---|---|
  | A | V4-Flash, off | GPT | 2/11 | 11/11 | 2.3 | (parallel) | 5/11 |
  | B | V4-Flash, off | GLM-5.3 | 1/11 | 11/11 | 2.4 | 116 | 9/11 |
  | C | V4-Pro, on | GPT | 8/11 | 11/11 | 1.4 | 296 | 7/11 |
  | D | Claude Code (assistant mode) | GPT | 4/4 | 4/4 | 1.0 | 60-120 | 4/4 |
  | E | V4-Flash, on | GPT | 7/11 | 11/11 | 1.2 | 180 | 10/11 |

  - Thinking mattered, not model size: with thinking off, most first drafts failed rule 1
    on expected values traced by hand (DGIM bucket states, lossy-counting boundaries);
    Flash with thinking came close to Pro in about 60% of its time. E needed Pro once
    (33k of 699k tokens).
  - The time is reasoning: a Flash draft took 129 s and about 27k output tokens with
    thinking (about 210 tokens/s), 16 s and 4k without. GLM-5.3 wrote oracles in 9 s
    without thinking; V4-Pro with thinking took 280 s per draft and 120 s per repair.
  - GLM-5.3 as the oracle: 19 calls, all usable; its one rule 4 failure was the generating
    model's own test_oracle.py.
  - Layout: counted before the examples were rewritten in it (see "Learning rules").
  - Decided (owner, 2026-10-30): arm E's shape, Flash with thinking, Pro only when Flash's
    repair fails, so the time stays bounded; GLM-5.3 as the oracle after 2026-11-04
    (replaced 2026-11-01 by GLM-5.3-Flash, see the week 4 cross experiment). The
    oracle's thinking became its own setting, `oracle_thinking`, off by default: it
    followed `thinking`, so turning generation thinking on would have made GLM think.
  - Speed, measured 2026-10-30 on the same 11 items (owner asked for tests, not guesses):

    | Setup | Wall time | First-round VALID | Flash draft | Output tokens | Layout |
    |---|---|---|---|---|---|
    | E, one at a time (high effort) | 1983 s | 7/11 | 129 s | - | 10/11 |
    | high effort, 8 in parallel | 441 s | 8/11 | 119 s | 369k | 10/11 |
    | low effort, 8 in parallel | 211 s | 7/11 | 90 s | 261k | 11/11 |

    Eight requests at once did not slow DeepSeek (about 215 tokens/s each), so a batch
    takes about as long as its slowest item. DeepSeek's `reasoning_effort` has three
    levels, low / high / max, default high (medium counts as high); a profile's new
    `reasoning_effort_on` sets the level used when thinking is on. Low drafted 24% faster,
    repaired in 31 s instead of 104, with 29% fewer output tokens and the same first-round
    rate on this sample (one run each; part of the 211 against 441 s is that no low item
    needed a third round). Question quality per setup goes to the owner's blind comparison.
    The examples' rewrite showed too: 10 and 11 of 11 questions in the layout, against 5
    of 11 for arm A.
  - Validation runs its independent checks at once (after the reference passes: skeleton,
    each mutant, oracle, the partition runs, main), under process-wide caps on child runs:
    heavy runs, which start a JVM (Spark, the Hadoop runner, any framework not yet
    classified), default a third of the cores (`TUTOR_MAX_HEAVY_RUNS`); light runs (plain
    Python, MRJob inline) half the cores (`TUTOR_MAX_RUNS`). Heavy or light is derived from
    metadata `framework` and `runner`, never declared by an exercise (a model could get
    it wrong); a test fails when the format allows a framework the table does not list. A Spark exercise took 47 s
    instead of 96, a file-level one 27 instead of 65. The cap matters: with 8 or 12 runs
    at once the exercise's hypothesis run passed its 30 s timeout and the exercise came
    out INVALID; 4 and 6 took the same time, 4 with 10 s to spare. Checked: all 70 week 7
    exercises (plain Python, every arm) still VALID; `pytest -m spark` passes in 217 s
    instead of 462; the Hadoop exercise of week 3 took 94 s instead of 211; and 2 Hadoop
    and 13 Spark exercises validated at once in one process, as a batch does, came out
    VALID twice (about 400 s, against over 20 minutes one after another).
- What repair is told (2026-10-29). A week 4 item came back word for word through all
  three repairs: rule 1 said only pytest's last line ("2 failed, 3 passed"), so the model
  could not see what to fix, and at temperature 0 an unchanged prompt returned the same
  reply. Now: a failing rule 1, 2 (crash), 4 or 5 carries `failure_excerpt` of pytest's
  output (each failing test, its line in the test file and the `E` lines, at most 2500
  characters, absolute paths cut to the file name since the text goes to a model; the CLI
  report still shows the one-line detail); repair round N asks at temperature 0.2 * N; and
  a reply identical to the previous one is recorded (`unchanged` in the attempts) and the
  next repair is told so.
- Prompt rules added for failures seen on week 4 (2026-10-29), each tied to one: test
  expected values are worked out by hand from the question on small data, never taken
  from the reference (a test said 2 bids where the data had 3); a rule 1 failure tells
  repair that the test can be the wrong side (three repairs had edited a right reference);
  the reference is written as a good student would, never forcing an API into a role it
  does not fit (GPT's reference sent a countByKey result back to the cluster to join).
  Rules stay few and concrete: a longer prompt is followed less closely, and what code can
  check (constraints, mutants) is checked in code. After the first rule the same item
  passed rule 1 at once, and the independent oracle caught a real bug in its reference
  (`reduce()` on an empty input, which the question says gives no lines). The constraint
  descriptions had named "the entry point" as a required definition, and a model then
  wrote `AuctionStats.run`, which the api check did not know: it now accepts
  `Class.method`, the form `entry_point` uses.
- Prompt order and cache accounting (2026-10-29). Providers cache the longest prompt
  prefix they have seen, and the item's own lines came before its course pages, so items
  of one lecture shared only the system prompt (DeepSeek: about 6k of 16k input tokens
  cached on a first call). Now the system prompt holds what the whole course shares
  (rules, testkit API, conventions) and the user message gives the course pages, then the
  example exercise (chosen by framework and interface), then the item; a batch starts its
  items sorted by lecture, framework and interface. Each item counts input, cached input
  and output (`usage` in generation.jsonl), and the CLI prints the batch's share served
  from the cache. Considered: a summary of the lecture instead of its pages, to shrink
  the prompt; rejected, since exercises must match the slides' code and cite their pages,
  a summary is one more model output that can be wrong for every item after it, and with
  caching the pages cost little (output tokens dominate the cost).
- Streaming (2026-10-29): generate, repair and oracle calls receive the reply in pieces,
  so `timeout_s` bounds a pause, not a whole long reply, and the CLI shows
  `receiving: N characters` as it arrives; usage comes from the last piece
  (`stream_options` only for OpenAI and DeepSeek, which document it). A stream that ends
  with neither `[DONE]` nor a finish reason is an error, not a reply (outside review,
  2026-10-29): a cut-off draft was counted as the model's format error. Plan and vision
  calls are unchanged. The first real run showed why it is worth having: DeepSeek ran away
  for 32,000 output tokens (cut off, retried) and the progress lines made it visible.
  A rule 3 failure with a survivor now reminds repair to catch a bug in how the result is
  computed with a spy, or to replace a mutant whose result equals the reference's (a
  `groupByKey` then sum mutant had survived three repairs).
- Who is wrong when rule 1 fails (2026-10-29). A reference and a test disagreeing can be
  either side's fault, and DeepSeek, unable to tell, sent the same draft back word for
  word (copying is near-certain even at temperature 0.6). The oracle does not use the
  tests, so the pipeline validates with `oracle_on_failure`: rule 4 still runs after a
  rule 1 failure. If the reference agrees with the oracle (an independent reading when
  the `oracle` role is set), repair is told the tests' expected values are the likely
  mistake; if not, the disagreement and the oracle's code go to repair as usual. The CLI
  keeps skipping rules 2-5 after rule 1. Cost: one rule 4 run (15-30 s) per such round.
- A `repair` role (2026-10-29): a larger model takes over from the second repair
  (`stronger_repair_from`), since the cheap model fails at diagnosis, not at writing: on
  the hardest week 4 item DeepSeek-Flash sent drafts back unchanged or half fixed, while
  GPT generating fixed it in one repair. Each attempt records its model; the manifest
  names the repair model; the oracle must come from another provider than both writers
  (since 2026-11-07 best, not must: see "One provider's oracle" under the Models page).
  First try with DeepSeek-V4-Pro (thinking off) did not fix that item either, and lost a
  round copying the oracle's code, which rule 4's failure shows, into the oracle
  interface; the message now says not to.
- Planning thinks (2026-10-29). The plan is one short reply per batch, so reasoning costs
  little (about $0.03 with DeepSeek-V4-Pro). Week 5 planned three ways: GLM-5.3-Flash at its
  lowest reasoning repeated a word count the coverage table had dropped; Pro with thinking
  off wrote vague checks and gave one file-level item of six against a preference to mix;
  Pro with thinking on dropped the repeat, chose the slides' "one problem in DataFrame and
  SQL" practice, mixed the interfaces and named edge cases, but left out the schema point.
  `plan_thinking` is on by default; `--plan-profile` picks the planner for one batch. The
  first week 5 batch (GLM plan, Flash writing, Pro repairing with thinking, GPT oracle)
  saved 6 of 6: two after a Flash repair, four rescued by one Pro repair each, about $0.39
  in all at peak prices.
- Week 4 cross experiment (2026-11-01, native Windows; owner asked for it). One plan by
  GPT, then the same 4 RDD items in two arms, 4 at once each. DeepSeek writing with a GLM-5.3
  oracle saved 4 of 4, 2 at the first round; its 3 first-round failures were all timeouts
  (Spark on Windows, 4 exercises validating at once), fixed by one repair each. GLM-5.3
  writing with a DeepSeek oracle failed on content (a mutant patch whose `old` text did not
  occur, a wrong expected value, a surviving mutant, an oracle interface without the
  question) and stopped after 2 items when GLM-5.3's quota ran out. So the planned oracle
  for after 2026-11-04 had no quota. GLM-5.3-Flash rewrote the 4 oracles from the same
  question and interface: at `reasoning_effort` high it matched GLM-5.3 exactly (reference
  agrees 4 of 4; 14 of 18 mutants disagree with it, the 4 others change no output and are
  killed by the tests) in 3-6 s each; thinking at the model's default (no effort field,
  the deepest) gave the same result in 6-50 s. Decided (owner): the oracle becomes
  GLM-5.3-Flash at high effort when the OpenAI key expires, through its own profile
  (`glm-flash-oracle`, `reasoning_effort_on = "high"`, `oracle_thinking` on), since the plan
  role uses `glm-flash` with the model's default. A FAIL of rule 4 on a mutant is not
  "caught" by itself: when the check was first run without `JAVA_HOME`, 13 of 18 "caught"
  were Spark failing to start.
- A diagnosis first, and the oracle's own mistakes (2026-10-29). The draft's first field
  is `diagnosis`, filled by repairs before any file: what failed, the right result worked
  out by hand, which file is wrong and why. A scratchpad that works without thinking, and
  a log of what the model believed. With Flash alone repairing two week 5 items that had
  needed Pro, one passed (three bugs, each named and fixed in turn) and the other made
  progress every round instead of being copied back. The Pro-written week 5 batch (2 of 5)
  showed two faults of the pipeline: Pro wrote whole oracles where an interface was asked
  for, on every first draft, and the oracle model imported `io`, which the safety check
  refuses, after which repair asked the generating model, which cannot change that code,
  three times. Now a whole oracle is cut to an interface in code (signatures and
  docstrings stay, the oracle model never sees the bodies); the oracle model is given the
  import allowlist, its oracle is safety-checked in the node, and it rewrites its own
  oracle once when told what is wrong. The rule 4 note goes with a real disagreement only,
  not with a differential test that cannot run.
- Batch (step 2, approved 2026-10-28): `plan` (LLM) reads coverage.md, one line per cached
  page (PDF page, footer, title), the existing exercise ids and the owner's preferences,
  and returns `count` PlanItems with a reason each; code checks the count, unique and new
  ids, lecture names and slide ranges, and asks again (twice at most) with the problems.
  `approve` is a LangGraph `interrupt`: the CLI shows the plan and writes it to
  `.drafts/<batch>/plan.json`; the owner approves (with any edits made to that file) or
  sends it back with feedback, which goes to `plan`. A resume rewrites plan.json only when
  the model has planned again (`plan.model.json` keeps its last plan), so the owner's
  edits survive a pause; a batch paused before that file existed keeps its plan.json, and
  the CLI says when plan.json differs from the model's latest plan. Every row must have a
  unique, identifiable ID; selected rows, edited or not, must pass the schema and blocking
  checks (new ids, since two items with one id would share a drafts folder; known lectures;
  slide ranges), in the CLI and again in `approve`; a count other than the one
  asked for is the owner's choice (both found in an outside review, 2026-10-29). A SQLite checkpointer in
  `.drafts/<batch>/` keeps the paused batch, so `python -m tutor.graph resume` continues it
  in another terminal or another day. Approved items fan out with `Send` and run the item
  graph in parallel, at most `--parallel` at once (default: CPU cores / 4, 1-4: each
  validation runs Spark on every core). One failing item (model unreachable, name clash)
  does not stop the others. `save` copies both parts into hidden staging folders and
  renames them into place, so a failed copy leaves nothing behind; an item saved before
  whose result was lost (a resume runs it again) counts as saved, its folders untouched
  (outside review, 2026-10-29). `collect` writes the manifest once (parallel items never
  write it) with the preferences in `notes`, and `coverage-proposal.md`, the coverage.md
  changes for the owner to apply. Listing a saved item again changes nothing, and the file
  is replaced in one step, so a resume that runs `collect` again cannot fail on it (outside
  review, 2026-10-29). Which types a batch mixes (e.g. debugging exercises) is
  left to the preferences, not a fixed rule.
- Logs: `llm_calls.jsonl` with the node as purpose; one `generation.jsonl` per batch
  (item, rounds, outcome, failure reasons, and the settings it ran with).
- Generation settings (owner-approved 2026-10-29; owner: keep tuning possible later,
  including from a GUI): every tunable value is a field of `GenerationSettings`
  (`graph/settings.py`) with its default, description and bounds: repair rounds, the round
  the repair model takes over from, the temperature step, thinking per role, token limits,
  the per-item token budget, context size, plan size, parallelism, mode. Three sources,
  later ones winning: the defaults (the measured values; `Deps` takes its defaults from
  them, so tests and scripts run like the CLI), workspace.toml `[generation]` (the owner by
  hand; the GUI's settings page writes only [llm] so far; the pipeline reads
  workspace.toml and never writes it), and the
  run's flags (`--set name=value`, plus the older named flags). Unknown names and values
  out of range are errors that name them. A batch saves what it resolved in run.json, so
  every resume runs with the same values even if `[generation]` changed; `resume --set`
  changes them for the rest of the batch. Each item's log line carries them, so a model
  comparison can tell its arms apart, and an arm is just a set of `--set` values.
  `schemas/generation-settings.schema.json` is the form a GUI renders (descriptions,
  types, bounds). Per-model values (`reasoning_effort`, `timeout_s`) stay in
  `[llm.profiles]`.
- Built in three steps: one item generate -> validate -> repair -> save (done); then plan
  with the interrupt, preferences and parallel items (done); then the oracle in its own
  context (done 2026-10-29, below; without an `oracle` role `generate` writes the whole
  oracle, as hand drafting does).
- The independent oracle (approved 2026-10-29). The generating model still writes
  `test_oracle.py` (how random inputs are built and fed to the program, which is tied to
  Spark and the file format), but `oracle.py` only as an interface: signatures, a
  docstring per function saying what goes in and out, `raise NotImplementedError`. A
  `write_oracle` node between generate / repair and validate gives the oracle model the
  question (student.py's docstring), that interface and the sample data, never the
  reference, tests, mutants or hints, and puts its oracle.py into the draft. It is written
  again only when one of its inputs changes (a hash), so most items cost one extra short
  call. When rule 4 fails, repair is told that the oracle is an independent reading of
  the question and gets its code: if the reference is right, the question is ambiguous
  there and is the thing to fix, which is what an independent reader is for. An
  interface with bodies, or an oracle the model could not write (missing or unwritten
  functions, a reply that does not fit), is a failure of the round that goes to repair
  without validating. Considered: the oracle model also writing `test_oracle.py`;
  rejected because Spark and file-level harnesses are where generation fails most, and
  the independence that matters is in the oracle's logic. The manifest and
  `generation.jsonl` name the oracle model.
- The LLM never touches the disk or runs code: nodes read the cache and write files, and
  generated code reaches `batches/` only after `validate` (including `safe`).
- LLM output parsed into Pydantic models, the same `schema.py` models that rule 0 checks;
  format failures are counted apart from validation failures.
- `validate` is pure Python and acts as the judge; repair receives the failure report.
- `retrieve_context` before RAG (phase 3, decided 2026-10-28): the cache of the batch's
  `sources`, whole (a lecture is 4-11k tokens at 4 characters per token). When that does not fit the model's
  context, a slice instead: the slides that `coverage.md` lists for the batch plan's
  knowledge points. A slice is chosen by label, not by a query, so it is deterministic and the owner
  sees exactly which pages went in. For a local model, raising the context length it is
  loaded with (8192 on the owner's Mac so far; the model supports more, at the cost of
  memory) comes before slicing. A page of the item's slides that is `pending` or `partial`
  stops the item with its page numbers (a formula or diagram may be missing, and no test
  can show the question is faithful to the slide); the item's pages run from its first
  slide's PDF page to its last, footer or not. Such pages elsewhere are left out. Slides
  the lecture has no footer for stop the item too (both from an outside review, 2026-10-29).
  A lecture that prints no slide numbers is cited by PDF page, written "p.12-p.13" (owner,
  2026-11-18: "by page number, and show which numbers are the slides' and which are
  ours"; the reader's rule is in layout.md, "Slide numbers or PDF pages"). Before, the
  plan's page list read "slide ?" on every line of such a lecture, the plan model made a
  range up ("1.14-1.15"), and the lecture went whole with no page checked, so an exercise
  never matched its coverage row (written "p.4-p.5" all along). Now the list gives "p.N"
  where the footer goes, the plan check takes either form at each end of a range, and the
  item's pages are found and checked like any other's; bare numbers in such a lecture are
  PDF pages too, so the older ranges still resolve. Nothing pinned changed: the plan's
  system prompt is as it was, and for a lecture that prints slide numbers the list is the
  same byte for byte (a page without a footer there keeps its "?"). Checked on the test
  course made from handwritten notes: a plan with "p.7-p.9" passed the check and resolved
  to pages 7 to 9; "888" and "p.20" found none.
- Every generated batch mixes function-level and file-level exercises (see "Exercise interfaces").

## Hand-calculation exercises

Decided 2026-11-05 (owner), for a course examined closed-book with a calculator: the learner works numbers
out by hand and writes them into `student.py` as named constants. No rule changes, as the format already
fits. The tests compare each constant within a tolerance, a mutant is the numbers one specific mistake gives,
and the oracle computes them from the formula, so a wrong answer key fails rule 4. A hand-made one and 9
generated ones are VALID. The format lives in the workspace's `conventions.md`, not in the prompts, as other
courses still write code.

A failing test's traceback prints the expected value, which here is the answer: the generation setting
`hide_answers` writes a `pytest.ini` in the batch folder, so a run shows only each test's name and result. It
is set per run, as a code batch of the same course needs its tracebacks, and not inferred, as no field marks
an exercise as hand calculation. The answers are still in `test_student.py`, and in `first_error` in the
attempt log. Multiple choice and short answer are outside this: the validator cannot check them.

## Prompt layout for caching

Decided 2026-11-03 (owner): every prompt is designed for a high cache hit rate where conditions
allow. Providers reuse only an identical start of a prompt (OpenAI: from 1024 tokens), so stable
content goes first: the system prompt, rules and shared course material. What varies per item goes
last. The shared part stays the same byte for byte, with no per-item marks, dates or ids. With
parallel calls and a large shared part, one call goes first to fill the cache: the first generate
call of each lecture goes alone and the lecture's other items wait for it (`nodes.CacheGate`, built
2026-11-11 after a Spark batch with Sonnet at parallel 8 sent its first eight calls at once, each
writing the course pages and none reading them, about a dollar of the batch's three; repair rounds
do not wait, a first call that fails frees the rest, and none waits past ten minutes). The wait is
not for the first call to end but for its provider's cache to be readable, which differs per
provider, so each family has its own way to start the rest (owner, 2026-11-12), a profile's
`parallel_start`: `together` (no gate), `first-event` (when the first reply begins to stream),
`first-event+N` (N seconds after that) or `first-done` (when the first call returns), with a
measured default per provider in `llm.PARALLEL_START` that a profile may override. Measured
2026-11-11/12 with a 23k-33k-token shared prefix, one call started and three released at once,
their cached share: Anthropic serves it from the first streamed event (99.9% for calls sent
1 s after it, as the docs say), so `first-event`; DeepSeek builds it in the background (0% at
the first event, 0.6 s in; 99% at 3, 8, 15 and 30 s), so `first-event+10` (owner, 2026-11-12:
10 for now, as 3 s was the edge seen once);
GLM only once the first call has ended (0% at 30 s, 99.9% after the end), so `first-done`.
Local servers report no cached tokens, so their time to the first text told it: LM Studio
(gemma 26B, four slots) let the others reuse the prefix from the first event (first text in
4 s against 78-103 s cold), but four replies decoding at once slowed the first, so the set
took 84 s against 68 s after the first had ended and 183 s all together; Ollama answers one
request at a time by default, so every choice took about 16 s. Both stay `first-done`, as do
OpenAI and `custom` (not measured). A profile's `max_parallel` caps the calls one endpoint
(provider and URL) gets at once, generate, repair and oracle alike (`nodes.CallSlots`), for an
account whose quota allows few; absent, the course's `[generation] parallel` alone bounds
them. The model form sets both under "More": a list of the four ways, its first entry the
provider's own (which writes nothing, so the profile follows the tool's measured default),
a seconds box for `first-event+N` (0.5 to 600; the file keeps one value, the page splits it
again), and a cap box (1 to 32, empty for none); clicked in Chrome on a scratch course
(`notes/gui-checks/parallel_start_click.py`). "Measure start" beside each chat model's Check
(owner, 2026-11-12) runs `python -m tutor.llm_timing NAME` as a task: four rounds (released
at the first event, +3 s, +10 s, after the end), each one long call and three short ones on
a 40,000-character prefix of this tool's own source with a new id in front (no course text,
and present in an installed tool), thinking off; about sixteen paid calls, so on the click
only, the button's title saying so. It suggests a `parallel_start` and writes nothing: for
a provider that counts cached tokens the earliest round whose three followers all read at
least 80% of their input from the cache (an earlier start that misses pays for the prefix
again); for a server that counts none (LM Studio, Ollama) the round that finished soonest
among those whose followers reached their first text in at most half the first call's time;
`together` when no round read the cache. A round whose first call ended before its release
is marked and left out, as it was really `first-done`. Clicked in Chrome with Ollama's
qwen3 0.6B (free; `notes/gui-checks/measure_start_click.py`): 17 s a round, `first-done`
suggested, as measured by hand. On a deep-learning course's lecture with Sonnet (first call about 70 s) the gate costs a
few seconds instead of a call; the stream readers report the first event as `on_progress(0)`.
`cached_tokens` in the call log shows the result. Meaning comes first: a short prompt gains nothing and is not
contorted, and reordering a calibrated prompt means calibrating it again.

Measured so far: DeepSeek hits 53-75% on generate and repair. `gpt-6.1-terra` as the judge hits 7%,
its largest OpenAI cost. Its prompt then put each exercise before the slides, and the slides carried
marks for that exercise; moving the slides first was not enough there. In a small test (2026-11-03, week 8's
11 exercises and 2 first drafts with known errors), exercises sharing about 9000 tokens at the start
hit 0%, sent one at a time or in parallel, while the same whole prompt sent again minutes later hit
99.98%. A 90-second wait before the second call, a `prompt_cache_key` and the slides in a message of
their own all hit 0% as well. Why is not known, and the search stopped there, as the rule set for it
said. So the judge's layout stays, and a hit rate is measured before a saving is claimed.

The same test shows what a layout change would risk. The judge run again on its own prompt changed
the letters of 3 of the 13 and whether an exercise is fully fine on 1. The new layout changed 3 of
13 against the first run, no more than that noise, and still flagged both known errors. 13 items
show a direction only, but phase 5 has to report the judge's numbers with this noise in mind.

On Anthropic the prefix is explicit (2026-11-10, "Anthropic provider", cache breakpoints): with
the slides first and the exercise's pages named apart, the judge hit 71% of its input on the
54-item calibration, a 60% saving at Sonnet 5.5's prices; the same layout change that hit 0%
on OpenAI for a reason never found.

## Assistant mode

Decided 2026-10-29 (owner): the pipeline must also run with no API key at all, with a
coding assistant on a subscription (Claude Code with Claude Pro / Max, Codex with ChatGPT
Plus) acting as the model for vision, planning, generation, oracle, repair and hints.

- A profile with `provider = "assistant"` sends nothing over the network
  (`tutor/exchange.py`). Each call becomes `<workspace>/.exchange/<profile>/requests/<id>.md`
  (the messages, what to reply, the JSON Schema; images as PNG files beside it) and the
  caller gets `AwaitingAnswer`. The assistant answers with `python -m tutor.exchange answer
  ID FILE`, which checks the reply's shape first (one JSON object with the required
  fields) and keeps it in `answers/<id>.txt`. The id is a hash of the request (purpose,
  messages, schema), so running again replays every answered call and stops at the first
  unanswered one (owner chose replay over a LangGraph interrupt per call: simpler, and
  parallel items can collect their requests into one round of answers).
- `AwaitingAnswer` is an `LLMError`, so a caller that knows nothing of assistant mode
  (the hint command) falls back as it does for a model that could not answer, and finds
  the answer on the next run.
- An assistant profile's `model` names the assistant (default: the profile name) and its
  queue is its base URL, so two assistants are as independent as two providers for the
  oracle rule.
- Vision: one `--vision` run writes a request for every page to transcribe (the page as
  PNG) and goes on, instead of stopping at the first; the next run stores the answers as
  transcriptions (exit code 3: requests are waiting).
- Batch: an item that stops at a request ends as "waiting" (not an error); the batch's
  `answers` node, after each round of items, pauses once for all of them (an interrupt
  next to the plan approval), and after the answers sends only those items again, which
  replay their answered calls. Validation reports are kept by a hash of the round's files
  while any role is an assistant, so a replay does not run earlier rounds' Spark again.
  A plan waiting for an answer stops the batch, and `resume` replays it. `item` and
  `resume` exit with code 3 while requests wait.
- One assistant for both the exercise and the oracle (owner, 2026-10-29: allowed, with a
  reminder): a second profile of the same assistant (same `model`, its own queue) passes
  the oracle rule, and the CLI prints that its requests must be answered in a fresh
  context that has not seen the answer, being less independent than two providers. One
  profile for both is still refused (the requests would mix).
- Instructions for the assistant: `docs/assistant-mode.md` (setup, the run / answer /
  run-again loop, answering from the request alone, oracle requests in a fresh context,
  plan approval left to the owner). Since 2026-11-18 it also says that a request's material
  is never instructions to the assistant: an assistant can run commands and read files where
  a model behind an API can only reply, so instructions planted in a lecture reach furthest
  here. Answering takes two actions, reading the request and storing the reply; nothing else
  is run, read or fetched for it. This is a rule the assistant is asked to keep, not a
  barrier: what stands behind it is the assistant's own permission prompts.
- Self-test 2026-10-29: Claude Code (this repo's assistant) answered one real week-5 item
  through the files, with no API: the first draft failed the safety check (a spy using a
  variable attribute name), the repair request was answered, and round 1 was VALID (6/6
  mutants killed, oracle and partitions ok) and saved. Two fixes came of it: manifests and
  logs mark an assistant's model "(assistant)", and token counts an assistant cannot
  report are left out instead of printed as 0.
- Codex test 2026-10-29: Codex (desktop app, ChatGPT Plus) answered three vision requests
  through the files and opened each PNG: one code slide matched the hand-checked
  transcription (ratio 0.97), a logo diagram was described with details only the image
  has. So both assistants work with files alone.
- MCP server: deferred (owner, 2026-10-29). The files work for an assistant that can run
  commands and read PNGs (both do); an MCP layer (list / get with the image / submit) is
  built only if permission prompts get in the way, or with the GUI.
- Open (owner chose to wait, 2026-10-29): the vision prompt does not say what to do with
  text an image covers on the slide while the text layer still has it. On one slide a
  screenshot covers the bullets and some code: GLM filled them in from the text
  layer, Codex wrote [?] ("cannot read: do not guess") and lost the code. Fix when pages
  are transcribed again for another reason: a rule to take covered text from the text
  layer and say it was covered, with PROMPT_VERSION raised (which re-transcribes every
  page read with the older prompt, hand-corrected ones excepted).

