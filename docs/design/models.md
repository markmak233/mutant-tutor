# Design: Models

Model access, providers and the per-role model comparisons. Part of the design record; the index of every part is [README.md](README.md).

## LLM engineering candidates (review at the start of each phase)

Proposed 2026-10-26 from a survey of current LLM application engineering, mapped onto this
project. Not decided yet: at the start of the phase named in brackets, review each item with
the owner and then move it into that phase's plan, the backlog, or "Decisions not to do".
Already decided elsewhere and not repeated here: LangGraph loop, Pydantic parsing, the
deterministic judge, hybrid RAG with recall@k, the phase 5 metrics, the hint leak guard.

- [3] Model access, decided in principle 2026-10-26 (details reviewed at phase 3 start):
  - one provider-agnostic client: Anthropic, OpenAI, and local models through an
    OpenAI-compatible endpoint (Ollama, LM Studio, llama.cpp; Apple silicon runs them);
  - bring your own key: from `.env` or the OS keychain, never logged, never written into
    the workspace, an export or an error report;
  - README states the data flow: with an API model, course material and the learner's
    code go to that provider; a local model keeps them on the machine;
  - cost estimate before a batch is generated, and a budget cap that stops the run (the
    user pays), using the call log below.
  - Local first (decided 2026-10-28, owner): development and vision transcription start on
    a local model through LM Studio's OpenAI-compatible server; a cloud provider is added
    later through the same client. Local vision model: Gemma 4 26B-A4B (mixture of
    experts, about 4B active, about 15 GB, on the owner's M2 Pro with 32 GB); Qwen3.8 27B
    was dropped (owner: too power-hungry). A 15 GB model and Spark validation compete for
    memory, so generation and validation do not run at the same time.
  - First cloud provider (owner, 2026-10-28): Zhipu's bigmodel.cn, OpenAI-compatible at
    `https://open.bigmodel.cn/api/paas/v4`, with GLM-5.3-Flash (native image input, base64
    data URLs, 1M context; the owner has free tokens for it, and for GLM-5.3-FlashX,
    owner 2026-11-07). How to minimise thinking
    differs by provider and model: LM Studio takes `reasoning_effort: "none"`; GLM takes
    `thinking: {"type": "disabled"}`, but GLM-5.3 models reject it and accept only
    `reasoning_effort` low / high / max, so the client tries `disabled` and falls back to
    `low` on a 400. The switch a model accepted is remembered for the rest of the run, by
    provider, base URL and model (owner, 2026-11-07: before, every GLM-5.3 call was refused
    once first); a new run tries the first switch again. With a cloud model the slide
    images go to that provider; the README states this data flow.
  - Several models (owner, 2026-10-28): keys stay in `.env`, one per provider
    (`ZHIPU_API_KEY=...`); everything else is named profiles in the workspace's
    `workspace.toml` (`[llm.profiles.<name>]`: provider, model, optional base_url,
    api_key_env, reasoning_effort, timeout_s), and `[llm.roles]` maps a task (vision,
    generate, oracle, ...) to a profile, so the oracle can come from a different model than
    the exercise and phase 5 can compare models; `--profile` overrides for one run. The
    provider (`lmstudio`, `glm`, `custom`) supplies the default base URL and the thinking
    switches. A profile holding an `api_key` field or a key-like value is refused, unknown
    fields are errors, and a missing variable is named in the message. Without an `[llm]`
    section, `LLM_PROVIDER` / `LLM_MODEL` / `LLM_BASE_URL` / `LLM_API_KEY` act as one
    profile. Like `[safety]`, the section is the owner's: generation never writes it.
    A profile's `temperature = "default"` (owner, 2026-11-09) leaves the temperature out of
    its requests: Claude Opus 5.5, reached through Anthropic's OpenAI-compatible address as a
    `custom` profile, answered HTTP 400 ("`temperature` is deprecated for this model") to the
    0 every other call sends. It is the field's one value, as callers choose the temperature
    and the profile only says the model takes none; the Models form offers it under More.
    Such a model answers at its own temperature, so a rerun is not the same answer, and the
    repair rounds' rising temperature (0.2 * N) does not apply to it. Chosen over retrying
    without the field after a refusal: one refused request per run, and less plain. That
    address was for trying Claude out; since 2026-11-10 Claude has its own provider (next
    entry). Apple Intelligence's on-device model is reachable too, through third-party
    bridges that serve it as an OpenAI-compatible local API (apfel and similar; not an
    Apple interface), so the same client can call it with no extra code. It is not a default:
    about 4096 tokens for input and output together (one week's material and the format spec
    do not fit), no image input found, one fixed small model, and rate limits for
    background use. It may serve as one more local arm in the phase 5 model comparison or for
    short text tasks.
  - Anthropic provider (owner, 2026-11-10: "use the native provider"), `provider =
    "anthropic"` in `tutor/llm_anthropic.py`, standard library like the rest of the client.
    Why not the compatible address: Anthropic's own docs call that layer a way "to test and
    compare model capabilities", "not a long-term or production-ready solution", and it has
    no prompt caching at all (`prompt_tokens_details` "always empty"; the 11-10 judge
    calibration with Sonnet 5.5 paid $2.52 for 1.1M input tokens that way); the Messages
    API opts into caching per request. Measured before deciding (one course, 11-10): plan
    (Sonnet 9/9 coverage rows), oracle (Sonnet 54/58 agree with the reference, mutants 91%;
    GLM-5.3-FlashX, measured 2026-11-16 once it had become two courses' oracle unmeasured:
    49/58 on the big-data course, mutants 91%, and 67/70 on the deep-learning course,
    mutants 97% like the oracles already there, on the free quota; kept on both, as a
    disagreement costs a repair round, not a wrong exercise)
    and the judge (Sonnet as good as gpt-terra on planted defects, 13/18 to 14/18, with no
    owner-approved exercise marked unclear against gpt's 6). The mapping, in the module's
    docstring and here in short: the system prompt is a top-level field; `max_tokens` is
    required (32,000 when a caller gives none, the pipeline's thinking default; thinking
    counts towards it); no `thinking` field (adaptive and on by default on the 5.5 models,
    Opus cannot turn it off) and no temperature (refused), so a call's depth goes through
    `output_config.effort`: `thinking=False` sends the profile's `reasoning_effort` or
    "low", `thinking=True` its `reasoning_effort_on` or nothing (the model's default), the
    same profile fields every provider uses; a JSON schema goes to `output_config.format`
    (structured output), after the constraints Anthropic refuses are dropped from the copy
    sent (`exclusiveMinimum`, `maxItems`, ...: HTTP 400 for the draft and doctor schemas in
    the first probe; the caller's pydantic model still checks them), and a schema with a
    free-form map (the draft's `data_files`, which the API refuses outright) goes into the
    prompt as DeepSeek's JSON mode does, the caller validating as always; the reply's text
    blocks are joined, thinking blocks left out, `max_tokens` becomes the "length" finish
    reason the graph reads; the event stream is put back together like the OpenAI one.
    Usage keeps the client's one meaning: Anthropic's `input_tokens` leaves out the tokens
    read from or written to the cache, so the reply adds the three back (costs are computed
    from the total), with `cached_tokens` and a new `cache_write_tokens` kept apart in the
    call log. The request and error handling are one function (`llm.exchange`) for both
    shapes; only the path, the headers (`x-api-key`, `anthropic-version`) and the reading of
    the reply differ. No SDK: the owner's keys stay in `.env` as for every provider; the
    SDK's credential chain (`ant auth login`, workload identity) is a later option, not a
    reason for a dependency now. Checked with Haiku 5.5 against the real API before the
    tests were final (8 calls, $0.0004); the Models page clicked in headless Chrome on a
    scratch course (New model, Anthropic from the list, Use, Save: `provider = "anthropic"`,
    no base_url written).
  - Cache breakpoints (owner, 2026-11-10, the same day). A caller that gives a message as
    an `llm.Prompt` (its parts: the shared one first, then this call's own) tells the
    provider where the prefix ends: the anthropic module sends one text block per part
    with `cache_control` after every part but the last, and marks the system prompt's last
    block too (at most four marks, the API's limit). To every other reader a Prompt is the
    plain string its parts make (a `str` subclass): the OpenAI-shaped providers, the
    assistant's request files and their ids, the tests' fakes and the prompt pins see the
    bytes a string would give, so DeepSeek's generate and repair requests did not change
    by a byte (the pins prove it), and the pins were not recomputed. Explicit breakpoints,
    not the API's automatic one: that lands on the last block, which in these prompts is
    the part that changes every call. Who gives parts: the generate, repair and retry
    prompts (the course pages first, the example and the item after, so the items of a
    lecture and the rounds of an item share the pages); the judge (below). Not the oracle
    (its spec is about 260 tokens, under the 512 a Claude 5.5 model caches), nor the plan
    (one call) or the hints judge (no slides). The judge's prompt had to change order for
    this: the exercise came first and the slides last, with the exercise's own pages
    marked in their headings, so no two calls shared more than the system prompt (the 7%
    hit rate under "Prompt layout for caching"). Now the batch mode, the lectures taught
    and the unmarked pages come first, then a line naming the exercise's own pages, the
    question, the starting file, Useful functions and the reference; the words did not
    change, the order did, so `PROMPT_VERSION` went to 5 and the calibration was run
    again with Sonnet 5.5, sequentially so that the first call of a lecture fills the
    cache before the next reads it. Measured (54 rated items and 18 planted defects, the
    same set as the morning's run on the compatible address): 71% of the input served from
    the cache (781,749 of 1,101,216 tokens; 53 of 54 calls read, 7 wrote), $0.96 against
    $2.39 for the same calls uncached, a 60% saving; 215 s. The verdicts: planted defects
    12 of 18 (clear 1/5, fits 3/5, taught 3/3, targets 5/5) against 13 of 18 in the
    morning, and the real ratings the same (clear 1 of 5 found, no owner-approved exercise
    marked n), within the rerun noise this section records (3 of 13 letters); one of the
    two fits misses was a verdict with an empty `evidence` and ok=true, which the judge's
    retries do not catch (only a reply that fails the schema is asked again). So the judge
    role moved to `claude-sonnet-5-5` in every course (owner's condition: not worse than
    the morning's run), and the last OpenAI role is gone; the OpenAI profiles stay in the
    files until the key ends. A 1-hour cache lifetime (writes at 2x) was not needed:
    sequential judging stays within the 5 minutes; generation's repair rounds, with
    minutes of validation between calls, are where it may matter, to be read from the
    call log of the next batch before deciding.
- [3] Summary of recently covered material in the generation prompt (ContentGen found it
  improved generated questions); built from `coverage.md` and the batch's sources.
- [3] Call log: one JSONL line per LLM call (node, model, input / output / cached tokens,
  latency, repair round, outcome, failure reason). The phase 5 cost metric needs it; same
  idea as `attempts.jsonl`. A tracing UI (e.g. Langfuse) only if the log is not enough.
- [3] Prompt caching: the generate prompt has a long stable prefix (exercise format spec,
  example exercises, the week's material), resent on up to 3 repair rounds. Measure cost
  with and without it.
- [3, prerequisite] Image slides through a vision model, transcribed once and stored in the
  workspace with slide footer and PDF page; the input side of RAG. Decided 2026-10-28, see
  "Reading files".
- [3] Native structured output (JSON schema / tool use) instead of free text parsed after
  the fact. Count format failures apart from validation failures, or the validity metric
  mixes two causes.
- [3] Human-in-the-loop: a LangGraph interrupt after a batch plan (knowledge points,
  exercise types, sources) so the owner approves before generation, as batches are approved
  now.
- [3] Preference chat at generation (owner, 2026-10-26, in place of measuring manual edits):
  a free-text box (a CLI prompt first, a GUI chat box later) where the user states
  preferences before and during generation: focus topics, difficulty, interface mix, "more
  combiner practice", "avoid X", or "make exercise 3 harder". Preferences feed the batch
  plan and the repair / regenerate step and are stored in `manifest.json`, so a batch can
  be explained and rerun. They steer content only: validity stays with the deterministic
  validator, and a request outside what has been taught (e.g. an API from a later week) is
  allowed only when the user asks for it explicitly, and is flagged in the manifest. The
  number of follow-up requests per batch and per exercise is logged: a cheap proxy for the
  manual effort the ablation below does not measure. Shape decided 2026-10-26:
  - per batch: three points to speak, before the plan ("more combiner practice"), at the
    plan (swap an exercise, which doubles as the human-in-the-loop approval) and after
    generation ("exercise 3 harder", "exercise 6 unclear, redo"); a redone exercise is
    validated again like any other;
  - standing preferences: habits that hold across batches (course tokenizer line in text
    skeletons, interface mix, reference written as numbered steps) live in one
    machine-readable workspace file (e.g. `preferences.toml`) that every generation reads,
    instead of being scattered over notes the generator cannot see;
  - feedback after solving is not a separate feature: the one-line teaching-quality rating
    (see "Validity vs teaching quality") gains an optional free-text remark, and the next
    batch's plan reads the ratings and remarks of earlier batches.
- [3] Batch API for whole-batch generation: not interactive, usually much cheaper.
- Review of the [3] items (2026-10-28, owner adopted the proposals): adopted, as part of 3a:
  model access (local first; cost estimate and budget cap once a cloud provider is added),
  the recent-material summary, the call log, prompt caching as a prompt layout with a long
  stable prefix now (its cost effect measured once a cloud provider is used), structured
  output (JSON for the plan, metadata and manifest; if one large JSON with every code file
  fails often on local models, code files come as separate outputs and are assembled and
  checked in code; format failures counted apart from validation failures), plan approval,
  and the preference chat (CLI first). Batch API: backlog until a cloud provider is used;
  decided against on 2026-11-18 (README.md, "Decisions not to do").
  Isolation and vision transcription: decided separately (see their items).
- [3] Revisit isolation: phase 3 runs LLM-generated code, and the runner is not a sandbox
  (see "Code execution safety"). Decide whether validation of generated batches moves into
  a local container, which changes the "Docker only for reproducibility" decision there
  (allowed by "Local-first": it stays on the machine). Main reason, see the phase 3 note on
  malicious material. Also consider a static check on generated code that rejects network
  access, subprocesses and reads or writes outside the exercise directory. Decided
  2026-10-28: static check first, container later (see "Code execution safety").
- [3c] Calibrated LLM judge for hint quality only (leaks the answer? right direction?),
  checked against owner-labelled hints before its scores are trusted. Never for validity.
  Built 2026-10-31 (`tutor/judge/hints.py`), on simulated students since the owner had
  asked `tutor.hint` for nothing yet: `simulate` makes two mutants per exercise of a batch
  play students (their docstring, which names the bug, removed), runs their tests and
  asks the hint model for the level-1 layer 3 hint; `calibrate` has the judge answer
  "leaks?" and "right direction?" (evidence first; it sees the real bug and the
  reference) and scores it against the owner's labels as the teaching judge is scored.
  - Week 7, 22 hints by deepseek-flash: the owner found all 22 fine for learn mode, even
    those naming the change in words, so the leak rule is mode-aware: in learn mode only
    corrected code (or a whole corrected statement) leaks; in review mode naming the
    place or the change does. gpt-terra agreed on all 22.
  - With no bad label, nothing showed it could catch one, so 16 bad hints were planted
    by construction (a hint paired with another exercise's bug; hints naming the change
    judged as review hints; a corrected line pasted in): it found 10/10 leaks and 6/6
    wrong directions, with no false flag on the 38. Trusted on this data; planted bad
    hints are easier than real ones, so real requests (and review-mode batches) should
    be labelled when they come.
- [4] Reranker after hybrid retrieval, as one more arm of the recall@k comparison. Not in the
  first round (2026-11-02): only if hybrid's misses are pages it found but ranked too low.
- [5] Personal workflow cost (Codex review 4): steps and minutes from adding lecture files
  to the first exercise, owner minutes spent fixing each exercise, validation wait, and
  whether a failed run can resume. The first batch is the hand-made baseline, taken from
  this document's batch notes (fixes: page references, stripes, an equivalent mutant,
  too-regular data) and `attempts.jsonl`, with no new logging for the owner. Reviewed 2026-11-05
  (owner): only what the records hold, runs up to the first pass; minutes were never recorded,
  so they are dropped.
- [5] Local model as one more arm of the model comparison: validity, repair rounds and
  cost of a local model (run on the owner's Mac) against API models, judged by the same
  deterministic validator. Moved to the backlog 2026-11-05 (owner), as the full comparison was
  not run.
- [5] Long-context baseline: "without RAG" means the whole week's material in the prompt
  (with caching), not no material, so the comparison shows whether retrieval earns its keep.
  A third arm (2026-10-28): the labelled page slice from `coverage.md`, i.e. perfect
  retrieval, the upper bound that shows how far RAG falls short of it. Done in phase 4 (see
  "RAG"), where the slice proved a reference and not an upper bound on generation.
- [after 3, optional] MCP server exposing `validate` (and later `hint`) so any coding agent
  can check the exercises it writes.
- Not proposed: fine-tuning or RL (too little data; model + validator suffices), multi-agent
  debate, GraphRAG unless the cross-lecture queries call for it (see "RAG"), a hosted vector
  database (a few hundred pages fit in memory),
  an LLM judging validity (already in "Decisions not to do").

