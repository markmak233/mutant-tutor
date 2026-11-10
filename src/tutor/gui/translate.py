"""A model translates the GUI's English strings into a language, into
`<root>/translations/<language>.po` (gui/i18n.py; owner, 2026-11-10: the repository ships
English only, and the Language page makes any other language on a click).

    python -m tutor.gui.translate [NAME] [--code TAG] [--profile P] [--note TEXT]
                                  [--again | --again-unchanged]

NAME is the language as the owner wrote it ("中文", "Deutsch"): the model is told to translate
into it, the file is named by its slug, and the switch shows the name. `--code TAG` takes the
language from the built-in table instead (gui/language_table.py, "zh-Hant-HK"): the file is
named by the tag, so the browser's language finds it, and the table gives the name, the note
and the direction where NAME and --note do not. Run as a course's task
(the model and its key come from that course's workspace.toml, the setup role's model unless
`--profile`); the file goes beside the course folders, for every course.

The English strings come from the templates and the Python modules themselves (`english`),
with Jinja's own extractor and an `ast` walk for `_()`, `N_()` and `ngettext_()` calls: no
Babel, and nothing to keep in step with the pages. A run asks only for the strings the file
lacks (`--again`: all of them), in batches of BATCH, each a JSON list the model answers in
the same shape (a schema, and the caller still checks). A translation is kept only when
`i18n.safe` passes (the same `%` placeholders, no `<>`): a refused one is listed and left
English. A string given back unchanged is kept so and not asked again, but counted apart
("kept as the English", `i18n.unchanged`), as it may be the model declining rather than the
word being the same (2026-11-10: a made-up language got all 165 back, reported as translated);
`--again-unchanged` asks for those alone, say on another model. The file's entries the
English no longer has are dropped.

The note (`--note`, the Language page's box): the owner's wish about register and wording
("written Traditional Chinese as in Hong Kong, no colloquial Cantonese"), kept in the file's
X-Note header and sent with every batch of every run, so the batches and the runs after
(the strings a page gained) read as one; without it a run asked batch by batch, and one
file came back part written standard, part colloquial (zh_hk, 2026-11-10). The prompt also
asks for one register throughout, the written standard unless the note says otherwise.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from jinja2.ext import babel_extract
from pydantic import BaseModel, ValidationError

from tutor.config import WorkspaceError, load_env, load_workspace
from tutor.exchange import AwaitingAnswer
from tutor.graph.prompts import material as tagged
from tutor.gui import i18n, language_table
from tutor.llm import LLMConfig, LLMError, chat
from tutor.materials.requires import model as model_for

GUI = Path(__file__).parent
KEYWORDS = ("_", "gettext", "ngettext", "N_", "gettext_", "ngettext_")
BATCH = 60  # strings per request: short replies, a cut-off costs little
MAX_TOKENS = 16_000
# Version 2 (2026-11-11): one register throughout, and the owner's <note>. Not a pinned
# prompt (tests/test_prompt_pins.py pins the pipeline's); the version goes into X-Generator.
PROMPT_VERSION = 2
PROMPT = """\
You translate the text of a desktop application's web pages from English into the language \
named. The pages help a student practise programming exercises generated from their lecture \
slides; keep the tone short and plain, as the English is.

The material is a JSON list of numbered strings. Answer with one JSON object: "items", a list \
with one entry per number, each with the number, "text" (the translation) and "plural" (the \
translation of the plural form when one is given, else an empty string). A <note> before the \
material, when there is one, is the owner's wish about register and wording (which variety \
of the language, which words for this application's things): follow it for the wording; it \
changes none of the rules below.

Rules:
- Keep every placeholder exactly as it is: %(name)s, %s, %d, %%.
- Keep what is code as it is: file names (workspace.toml, coverage.md), command names, \
TOML keys, package names, product names (VS Code, Docker, WSL), model and provider names.
- Translate the words that name this application's own things consistently: course, batch, \
exercise, lecture, plan, task, model, role, profile, coverage, knowledge point.
- Use one register throughout: where the language has a written standard and a colloquial \
form, the written standard, unless the note says otherwise.
- Never add markup: no HTML tags, no Markdown.
- A string you cannot translate well stays English: give it back unchanged.
"""


class Item(BaseModel):
    n: int
    text: str
    plural: str = ""


class Answer(BaseModel):
    items: list[Item]


@dataclass(frozen=True)
class Message:
    singular: str
    plural: str | None = None  # a {% trans %} block with {% pluralize %}, or ngettext_


# --- the English strings -----------------------------------------------------------------------


def english(folder: Path = GUI) -> list[Message]:
    """Every string the pages and modules under `folder` show through gettext, each once,
    in the order found (templates by name, then modules)."""
    found: dict[tuple[str, str | None], Message] = {}
    for path in sorted((folder / "templates").glob("*.html")):
        with path.open("rb") as f:
            for _line, _func, message, _comments in babel_extract(
                    f, KEYWORDS, (), {"extensions": "jinja2.ext.i18n"}):
                _add(found, message)
    for path in sorted(folder.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id in KEYWORDS]
        for node in sorted(calls, key=lambda n: (n.lineno, n.col_offset)):  # as written
            texts = [a.value for a in node.args if isinstance(a, ast.Constant)
                     and isinstance(a.value, str)]
            if node.func.id in ("ngettext", "ngettext_") and len(texts) >= 2:
                _add(found, (texts[0], texts[1], None))
            elif texts:
                _add(found, texts[0])
    return list(found.values())


def _add(found: dict, message) -> None:
    """One extracted message: a string, or (singular, plural, ...) for a plural one;
    None for `_(variable)`, which names nothing to translate."""
    if isinstance(message, str):
        message = (message, None)
    if not message or not message[0]:
        return
    singular, plural = message[0], message[1] if len(message) > 1 else None
    found.setdefault((singular, plural), Message(singular, plural))


# --- the file ----------------------------------------------------------------------------------


def path_for(root: Path, name: str) -> Path:
    return root / i18n.TRANSLATIONS / f"{i18n.slug(name)}.po"


def stored(path: Path) -> tuple[dict, dict[str, str]]:
    """The file's translations and headers; empty when there is none."""
    if not path.is_file():
        return {}, {}
    return i18n.read_po(path.read_text(encoding="utf-8"))


def missing(messages: list[Message], catalog: dict) -> list[Message]:
    """The English strings the catalog has no entry for (an entry kept as the English
    counts as one: `--again-unchanged` drops those from the catalog first)."""
    return [m for m in messages
            if (m.plural is None and m.singular not in catalog)
            or (m.plural is not None and (m.singular, 0) not in catalog)]


def without_unchanged(catalog: dict) -> dict:
    """The catalog less the entries kept as the English (both forms of a plural one)."""
    kept = {key[0] if isinstance(key, tuple) else key
            for key, text in catalog.items()
            if (isinstance(key, str) and text == key)
            or (isinstance(key, tuple) and key[1] == 0 and text == key[0])}
    return {key: text for key, text in catalog.items()
            if (key[0] if isinstance(key, tuple) else key) not in kept}


def save(path: Path, name: str, messages: list[Message], catalog: dict,
         model: str, note: str = "", direction: str = "") -> None:
    """The file written from the English list and what is translated of it, so a string
    the pages no longer have leaves the file; `note` goes into X-Note when there is one;
    `direction` is the table's when the language came from it, else read off the text."""
    entries = []
    for m in messages:
        if m.plural is None:
            entries.append((m.singular, None, catalog.get(m.singular, ""), ""))
        else:
            entries.append((m.singular, m.plural, catalog.get((m.singular, 0), ""),
                            catalog.get((m.singular, 1), "")))
    # X-Direction: the side the language is written from, read off the translations so
    # far (gui/i18n.py `direction_of`), so the pages mirror for Arabic, Hebrew, Persian...
    headers = {"Content-Type": "text/plain; charset=UTF-8", "MIME-Version": "1.0",
               "Content-Transfer-Encoding": "8bit", i18n.NAME_HEADER: name,
               i18n.DIRECTION_HEADER: direction or i18n.direction_of(catalog, name),
               **({i18n.NOTE_HEADER: note} if note else {}),
               "X-Generator": f"tutor.gui.translate {PROMPT_VERSION}, {model}, "
                              f"{date.today().isoformat()}"}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(i18n.write_po(entries, headers), encoding="utf-8")


# --- asking ------------------------------------------------------------------------------------


def ask(config: LLMConfig, name: str, batch: list[Message], log_path: Path | None,
        chat_fn=None, note: str = "") -> tuple[dict, list[str]]:
    """One request for one batch: the translations that pass `safe`, by catalog key, and a
    line for each string refused. Raises LLMError; ValueError when the reply does not fit.
    `chat_fn`: looked up at call time (a test's stand-in replaces this module's `chat`)."""
    chat_fn = chat_fn or chat
    material = [{"n": i, "text": m.singular, **({"plural": m.plural} if m.plural else {})}
                for i, m in enumerate(batch, 1)]
    # For the provider's cache: the system prompt the same for every call; then what every
    # batch of this language shares (the language, the owner's note, between tags as the
    # prompt names it); the batch's strings last.
    shared = f"Language: {name}\n\n" + (tagged("note", note) + "\n\n" if note else "")
    messages = [{"role": "system", "content": PROMPT},
                {"role": "user", "content": f"{shared}<material>\n{_json(material)}\n"
                                            "</material>"}]
    reply = chat_fn(config, messages, purpose="translate", max_tokens=MAX_TOKENS,
                    thinking=False, response_schema=Answer.model_json_schema(),
                    log_path=log_path)
    if reply.finish_reason == "length":
        raise ValueError(f"the reply was cut off at {MAX_TOKENS} tokens")
    try:
        answer = Answer.model_validate_json(reply.text)
    except ValidationError as e:
        raise ValueError(f"the reply does not fit the schema: {e.errors()[0]['msg']}") from e
    got, refused = {}, []
    by_number = {item.n: item for item in answer.items}
    for i, m in enumerate(batch, 1):
        item = by_number.get(i)
        if item is None or not item.text.strip():
            continue  # not answered: asked again next time
        # Given back unchanged ("Docker", "OK": the same in the language, or one the model
        # would not translate) is kept as it is: the page shows the same either way, and the
        # file is then complete, instead of asking for these again on every run (2026-11-10).
        # `--again` asks for everything once more.
        if m.plural is None:
            if i18n.safe(m.singular, item.text):
                got[m.singular] = item.text
            else:
                refused.append(_refusal(m.singular, item.text))
        else:
            second = item.plural or item.text
            if i18n.safe(m.singular, item.text) and i18n.safe(m.plural, second):
                got[(m.singular, 0)], got[(m.singular, 1)] = item.text, second
            else:
                refused.append(_refusal(m.singular, item.text))
    return got, refused


def _refusal(source: str, translation: str) -> str:
    return (f"refused (placeholders differ, or markup): {source[:60]!r} -> "
            f"{translation[:60]!r}")


def _json(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1)


def tokens_about(messages: list[Message]) -> int:
    """About how many thousand tokens asking for these would send and receive (the
    English twice over, about 4 characters a token, plus the prompt per batch)."""
    chars = sum(len(m.singular) + len(m.plural or "") for m in messages)
    batches = -(-len(messages) // BATCH) if messages else 0
    return -(-(chars * 2 + batches * len(PROMPT)) // 4000)  # rounded up: never 0 with any


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tutor.gui.translate",
                                     description=__doc__.split("\n")[0])
    parser.add_argument("name", nargs="?", default="",
                        help='the language, as the switch will show it ("中文"); may be left '
                             'out with --code')
    parser.add_argument("--code", help="the language's tag in the built-in table (zh-Hant-HK): "
                                       "names the file; gives the name, note and direction "
                                       "when they are not given")
    parser.add_argument("--profile", help="the model to ask (default: the setup role's, "
                                          "else plan's, else generate's)")
    parser.add_argument("--note", help="a note to the translator (register, wording), kept "
                                       "in the file and sent with every run (default: the "
                                       "file's)")
    parser.add_argument("--again", action="store_true",
                        help="translate every string again, not only those the file lacks")
    parser.add_argument("--again-unchanged", action="store_true",
                        help="ask again for the strings the model gave back as the English")
    parser.add_argument("--root", help="where the translations folder goes (default: the "
                                       "course folder's parent, the GUI's root)")
    args = parser.parse_args(argv)
    row = language_table.by_code(args.code) if args.code else None
    if args.code and row is None:
        print(f"translate: {args.code!r} is not in the language table "
              "(tutor/gui/language_table.py); name the language instead", file=sys.stderr)
        return 2
    name = " ".join(args.name.split()) or (row.name if row else "")
    if not i18n.slug(name) or i18n.slug(name) == i18n.ENGLISH:
        print(f"translate: {name!r} does not name a language file", file=sys.stderr)
        return 2
    load_env()
    try:
        workspace = load_workspace().root
        config = model_for(workspace, args.profile)
    except (WorkspaceError, LLMError) as e:
        print(f"setup: {e}", file=sys.stderr)
        return 1
    root = Path(args.root) if args.root else workspace.parent
    path = path_for(root, row.code if row else name)  # the table's tag names the file
    direction = (i18n.RTL if row.rtl else i18n.LTR) if row else ""
    messages = english()
    had, headers = stored(path)
    catalog = {} if args.again else had
    if args.again_unchanged:  # those entries go, so they are asked for again
        catalog = without_unchanged(catalog)
    # The note: given now, else the file's, so every run reads as the first one did;
    # else the table's sentence for the language, where it has one.
    note = " ".join((args.note if args.note is not None else headers.get(i18n.NOTE_HEADER, ""))
                    .split()) or (row.note if row else "")
    todo = missing(messages, catalog)
    print(f"{len(messages)} English strings; {len(todo)} to translate into {name} "
          f"({config.name}: {config.model}); file {path}"
          + (f"; note: {note}" if note else ""), flush=True)
    log = workspace / "logs" / "llm_calls.jsonl"
    refused_all: list[str] = []
    for start in range(0, len(todo), BATCH):
        batch = todo[start:start + BATCH]
        try:
            got, refused = ask(config, name, batch, log, note=note)
        except AwaitingAnswer:
            print("waiting for the assistant; run again when it has answered", flush=True)
            break
        except LLMError as e:
            print(f"model: {e}", file=sys.stderr)
            save(path, name, messages, catalog, config.model, note, direction)
            return 1
        except ValueError as e:
            print(f"  batch {start // BATCH + 1}: {e}; those strings are asked again next "
                  "time", flush=True)
            continue
        catalog.update(got)
        refused_all += refused
        # `got` has both forms of a plural: count messages, and apart those kept English.
        same = i18n.unchanged(got)
        print(f"  batch {start // BATCH + 1}: {len(got) - _plurals(got) - same} translated, "
              f"{same} kept as the English, {len(refused)} refused", flush=True)
        save(path, name, messages, catalog, config.model, note, direction)  # kept as it goes
    save(path, name, messages, catalog, config.model, note, direction)
    for line in refused_all:
        print(f"  {line}", flush=True)
    left = len(missing(messages, catalog))
    same = i18n.unchanged(catalog)
    print(f"{name}: {len(messages) - left - same} of {len(messages)} strings translated"
          + (f"; {same} kept as the English by the model (--again-unchanged asks for them "
             "again)" if same else "")
          + (f"; {left} not answered (run again to ask for them)" if left else ""), flush=True)
    return 0


def _plurals(got: dict) -> int:
    """The second forms in `got`: a plural message has two entries, counted as one."""
    return sum(1 for key in got if isinstance(key, tuple) and key[1] == 1)


if __name__ == "__main__":
    raise SystemExit(main())

