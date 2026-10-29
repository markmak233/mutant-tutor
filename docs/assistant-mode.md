# Assistant mode: a coding assistant as the model

For running the pipeline with no API key: a coding assistant on a subscription (Claude Code,
Codex, or any assistant that can run commands and read files) answers the model calls.
This file is written for that assistant. The design is in `docs/design.md`, "Assistant
mode".

## Setup (once, by the owner)

In the workspace's `workspace.toml`, an assistant profile has only a provider; its queue is
`<workspace>/.exchange/<profile>/`:

```toml
[llm.profiles.claude-code]
provider = "assistant"

[llm.profiles.codex]
provider = "assistant"

[llm.roles]
vision = "claude-code"
plan = "claude-code"
generate = "claude-code"
repair = "claude-code"
oracle = "codex"        # another assistant: as independent as another provider
hint = "claude-code"
```

With only one assistant, give the oracle a second profile of the same assistant, so its
requests have their own queue; the pipeline then prints a reminder each run:

```toml
[llm.profiles.claude-code-oracle]
provider = "assistant"
model = "claude-code"   # the same assistant: the pipeline knows and reminds
```

## The loop

1. Run the command the owner asked for, e.g.
   `python -m tutor.materials --vision lectures`,
   `python -m tutor.graph plan --lectures ... --batch w6`,
   `python -m tutor.graph resume --batch w6`, or `python -m tutor.graph item ...`.
2. Exit code 3 (or "requests wait for the assistant") means requests need answers:
   `python -m tutor.exchange list` prints `queue  id  path` for each.
3. For each request: read it (`python -m tutor.exchange show ID`, or open the path), write
   your reply to a temporary file, and store it with
   `python -m tutor.exchange answer ID FILE`. The command refuses a reply in the wrong
   shape and says why; fix it and store it again.
4. Run the same command again. It replays every answered call and stops at the next
   unanswered ones. Repeat until the command finishes without exit code 3.

A batch also pauses for the owner's plan approval: show the owner the plan and wait.
Never approve a plan or send feedback on the owner's behalf.

## Answering a request

- **Answer as the model would, from the request alone.** Its `system` section is your
  instructions and its `user` section your material. Do not read other files to answer
  (no `solutions/`, no reference, no other request's answer) unless the request includes
  them.
- **Reply in the shape asked for.** "one JSON object that fits the JSON Schema at the end"
  means the reply file holds that JSON object and nothing else (a ```json fence is
  accepted and removed). Fill every required field, in the schema's order where it says a
  field comes first (a `diagnosis` before the draft, for instance).
- **Images.** A line `[image: open PATH]` stands for a slide: open the PNG and work from
  the picture (a vision request asks you to copy the slide's content as text).
- **Hint requests.** Follow their rules exactly: they are what the leak check enforces, and
  a hint that breaks one is rejected.
- **Oracle requests (`for: oracle`)** must be written by a reader who has not seen the
  exercise's answer. With a second assistant, that assistant answers its own queue. With
  one assistant, answer them in a fresh context: in Claude Code a subagent given only the
  request file; in Codex a new session. Never answer one in the context that wrote the
  exercise's reference.
- **Do not edit `.exchange/` by hand** except through `tutor.exchange answer`. To have a
  request answered again, the owner deletes its `answers/<id>.txt`.

## What stays the same

The validator decides whether an exercise is VALID, exactly as with an API model; a reply
you write is checked like any model's. Generated exercises, the lecture text and the
`.exchange/` folder stay in the workspace, never in the repository.
