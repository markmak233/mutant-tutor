"""A coding assistant as the model: requests and answers as files (docs/design.md,
"Assistant mode").

A profile with `provider = "assistant"` sends no request over the network. Each call
becomes a request file in the workspace, `.exchange/<profile>/requests/<id>.md`, which a
coding assistant (Claude Code, Codex, ...) reads and answers; the pipeline stops with
AwaitingAnswer, and when it runs again the answer is there and the call returns it.

The id is a hash of the request (purpose, messages, reply schema), so the same request
always finds the same answer: a run that stops and starts again replays every call it
made before from the answers, up to the first one without an answer. Answers are kept,
which also makes every model reply of a batch readable afterwards.

    python -m tutor.exchange list                     requests without an answer
    python -m tutor.exchange show ID                  print one request
    python -m tutor.exchange answer ID FILE           check FILE as the reply and store it

docs/assistant-mode.md is what the assistant follows.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from tutor.llm import LLMConfig, LLMError, Reply

EXCHANGE_DIR = ".exchange"  # in the workspace, one folder per assistant profile


class AwaitingAnswer(LLMError):
    """The request is written and has no answer yet. A subclass of LLMError, so a caller
    that does not know assistant mode treats it as a model that could not answer."""

    def __init__(self, request: Path):
        self.request = request
        self.request_id = request.stem
        super().__init__(f"waiting for an answer to request {self.request_id} "
                         f"({request}); see docs/assistant-mode.md")


@dataclass(frozen=True)
class Queue:
    """One assistant profile's folder: requests/ waiting, answers/ given."""
    root: Path

    @property
    def requests(self) -> Path:
        return self.root / "requests"

    @property
    def answers(self) -> Path:
        return self.root / "answers"

    def answer_file(self, request_id: str) -> Path:
        return self.answers / f"{request_id}.txt"

    def schema_file(self, request_id: str) -> Path:
        return self.requests / f"{request_id}.schema.json"

    def waiting(self) -> list[Path]:
        """Request files without an answer, oldest first."""
        if not self.requests.is_dir():
            return []
        found = [p for p in self.requests.glob("*.md") if not self.answer_file(p.stem).exists()]
        return sorted(found, key=lambda p: p.stat().st_mtime)


def request_id(purpose: str, messages: list[dict], response_schema: dict | None) -> str:
    """A hash of everything that decides the reply: the same request, the same id."""
    canonical = json.dumps([purpose, messages, response_schema], sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def ask(config: LLMConfig, messages: list[dict], purpose: str,
        response_schema: dict | None) -> Reply:
    """The stored answer to this request, or AwaitingAnswer after writing the request."""
    if config.queue is None:
        raise LLMError(f"[llm.profiles.{config.name}] is an assistant profile: it needs a "
                       "workspace to keep its requests in")
    queue = Queue(config.queue)
    rid = request_id(purpose, messages, response_schema)
    answer = queue.answer_file(rid)
    if answer.is_file():
        # Replayed or just answered: the reply as the assistant wrote it.
        return Reply(answer.read_text(encoding="utf-8"), None, None, 0.0, "stop")
    queue.requests.mkdir(parents=True, exist_ok=True)
    request = queue.requests / f"{rid}.md"
    if not request.exists():
        request.write_text(_render(rid, config, messages, purpose, response_schema, queue),
                           encoding="utf-8")
        if response_schema is not None:
            queue.schema_file(rid).write_text(json.dumps(response_schema, indent=1),
                                              encoding="utf-8")
    raise AwaitingAnswer(request)


def _render(rid: str, config: LLMConfig, messages: list[dict], purpose: str,
            response_schema: dict | None, queue: Queue) -> str:
    """The request as markdown: what to reply, then each message; images saved as PNG
    files beside it, named in the text where they were."""
    reply = ("one JSON object that fits the JSON Schema at the end, nothing else"
             if response_schema is not None else "plain text")
    lines = [f"# Request {rid}", "",
             f"- for: {purpose} (profile {config.name})",
             f"- reply with: {reply}",
             f"- answer: python -m tutor.exchange answer {rid} <file with your reply>", ""]
    images = 0
    for message in messages:
        lines.append(f"## {message['role']}")
        content = message["content"]
        parts = content if isinstance(content, list) else [{"type": "text", "text": content}]
        for part in parts:
            if part.get("type") == "image_url":
                images += 1
                png = queue.requests / f"{rid}-{images}.png"
                data = part["image_url"]["url"].split(",", 1)[1]  # data:image/png;base64,...
                png.write_bytes(base64.b64decode(data))
                lines.append(f"[image: open {png}]")
            else:
                lines.append(part.get("text", ""))
        lines.append("")
    if response_schema is not None:
        lines += ["## JSON Schema of the reply", "```json",
                  json.dumps(response_schema, indent=1), "```", ""]
    return "\n".join(lines)


def store_answer(queue: Queue, rid: str, text: str) -> None:
    """Keep `text` as the answer to request `rid`, after the checks that catch a reply in
    the wrong shape before the pipeline reads it (the pipeline still validates it)."""
    if not (queue.requests / f"{rid}.md").is_file():
        raise ValueError(f"no request {rid} in {queue.requests}")
    schema_file = queue.schema_file(rid)
    if schema_file.is_file():
        try:
            data = json.loads(_unfenced(text))
        except json.JSONDecodeError as e:
            raise ValueError(f"the reply must be one JSON object: {e}") from None
        schema = json.loads(schema_file.read_text(encoding="utf-8"))
        missing = [f for f in schema.get("required", []) if not isinstance(data, dict)
                   or f not in data]
        if missing:
            raise ValueError(f"the reply lacks required fields: {', '.join(missing)}")
        text = json.dumps(data, ensure_ascii=False)
    queue.answers.mkdir(parents=True, exist_ok=True)
    queue.answer_file(rid).write_text(text, encoding="utf-8")


def _unfenced(text: str) -> str:
    """A reply wrapped in a ```json fence, as assistants often write it, without the fence."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.rsplit("```", 1)[0]
    return stripped


def queues(workspace: Path) -> list[Queue]:
    root = workspace / EXCHANGE_DIR
    return [Queue(p) for p in sorted(root.iterdir()) if p.is_dir()] if root.is_dir() else []


def find(workspace: Path, rid: str) -> Queue:
    """The queue holding request `rid`."""
    for queue in queues(workspace):
        if (queue.requests / f"{rid}.md").is_file():
            return queue
    raise ValueError(f"no request {rid} in {workspace / EXCHANGE_DIR}")


def main(argv: list[str] | None = None) -> int:
    from tutor.config import WorkspaceError, load_env, load_workspace

    args = sys.argv[1:] if argv is None else argv
    load_env()
    try:
        workspace = load_workspace().root
        if args[:1] == ["list"] and len(args) == 1:
            waiting = [(q, p) for q in queues(workspace) for p in q.waiting()]
            for queue, request in waiting:
                print(f"{queue.root.name}\t{request.stem}\t{request}")
            print(f"{len(waiting)} requests waiting for an answer", file=sys.stderr)
            return 0
        if args[:1] == ["show"] and len(args) == 2:
            print((find(workspace, args[1]).requests / f"{args[1]}.md").read_text(encoding="utf-8"))
            return 0
        if args[:1] == ["answer"] and len(args) == 3:
            store_answer(find(workspace, args[1]), args[1],
                         Path(args[2]).read_text(encoding="utf-8"))
            print(f"stored the answer to {args[1]}")
            return 0
    except (WorkspaceError, ValueError, OSError) as e:
        print(f"stopped: {e}", file=sys.stderr)
        return 1
    print(__doc__.split("\n\n")[3], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
