"""python -m tutor.materials [--vision [--profile NAME]] <file or folder> ...

Reads every lecture given (a folder: the lectures directly in it) into the workspace's cache,
a PDF page by page and a lecture saved as a web page (.html, .htm) section by section
(materials/html.py: text only, no model), as `$WORKSPACE_DIR/cache/<path>.md`
(`cache/lectures/<file>.md` for the lecture folder, `LECTURE_DIR` or `$WORKSPACE_DIR/lectures`).
Pages whose content is unchanged since the last run are not read again. Image pages are
marked `pending` until a run with `--vision` transcribes them with the workspace's `vision`
model ([llm.roles] in workspace.toml, or `--profile` to pick another; see tutor.llm); calls
are logged in `$WORKSPACE_DIR/logs/llm_calls.jsonl`. If the model fails, the pages done so
far are kept and the rest stay pending for the next run. Pages transcribed with an older
prompt version are transcribed again, except pages corrected by hand, which are listed
instead.

With an assistant profile as the vision model (provider "assistant", no API), one run
writes a request per page to transcribe, with the page as a PNG; once the assistant has
answered them, the next `--vision` run stores the transcriptions. Exit code 3 means
requests are waiting.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from tutor.config import WorkspaceError, load_env, load_workspace
from tutor.exchange import AwaitingAnswer
from tutor.llm import LLMConfig, LLMError, config_for
from tutor.materials import cache
from tutor.materials.html import read_html
from tutor.materials.pdf import Page, read_pdf, render_png
from tutor.materials.vision import PROMPT_VERSION, make_transcriber


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    vision = "--vision" in args
    args = [a for a in args if a != "--vision"]
    profile = None
    if "--profile" in args:
        i = args.index("--profile")
        profile = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
    if not args or profile == "" or (profile and not vision):
        print("Usage: python -m tutor.materials [--vision [--profile NAME]] <file or folder> ...")
        return 2
    load_env()
    try:
        loaded = load_workspace()
        workspace = loaded.root
        config = config_for("vision", profile, workspace) if vision else None
    except (WorkspaceError, LLMError) as e:
        print(f"setup: {e}", file=sys.stderr)
        return 1
    failed: list[str] = []  # the first model error stops transcription for this run
    waiting: list[str] = []  # assistant requests written this run, one per page
    sources = []
    for arg in map(Path, args):
        sources += sorted(p for p in arg.iterdir() if cache.is_lecture(p)) if arg.is_dir() \
            else [arg]
    status = 0
    for source in sources:
        if not cache.is_lecture(source) or not source.is_file():
            print(f"skipped {source}: only PDF and HTML files (.html, .htm) are read")
            continue
        try:
            target = cache.cache_path(workspace, source, loaded.lecture_dir)
        except ValueError:
            print(f"skipped {source}: not inside the workspace {workspace} or the lecture "
                  f"folder {loaded.lecture_dir}", file=sys.stderr)
            status = 1
            continue
        web_page = source.suffix.lower() != ".pdf"
        pages = read_html(source) if web_page else read_pdf(source)
        if not pages:
            print(f"skipped {source.name}: no text found in it (an HTML page saved whole, "
                  "\"Webpage, Complete\", holds its text)", file=sys.stderr)
            status = 1
            continue
        transcribe = (_transcriber(config, source, workspace, failed, waiting)
                      if config else None)
        prompt = str(PROMPT_VERSION) if config else None
        old = cache.load(target)
        entries, read_now = cache.update(pages, old, transcribe,
                                         model=f"{config.name}: {config.model}" if config else None,
                                         today=date.today().isoformat(), prompt=prompt)
        cache.save(target, cache.render(source.name, entries))
        pending = sum(e.read in ("pending", "partial") for e in entries)
        if web_page:  # no image is read from a web page, so nothing waits for vision
            print(f"{source.name}: {len(pages)} sections, {read_now} read now -> "
                  f"{target.relative_to(workspace)}")
        else:
            print(f"{source.name}: {len(pages)} pages, {read_now} read now, "
                  f"{pending} image pages pending vision -> {target.relative_to(workspace)}")
        again = sum(1 for e in entries if e.number in old and cache.outdated(old[e.number], prompt)
                    and e.prompt == prompt)
        if again:
            print(f"  {again} pages transcribed again for prompt version {prompt}")
        kept = [e.number for e in entries
                if prompt and e.corrected and e.read == "vision" and e.prompt != prompt]
        if kept:
            print(f"  {len(kept)} hand-corrected pages kept on an older prompt: "
                  f"{', '.join(f'p.{n}' for n in kept)} (remove `· corrected` from a page's "
                  f"comment line to have it transcribed again)")
    if failed:
        print(f"vision stopped: {failed[0]}", file=sys.stderr)
        status = 1
    if waiting:
        print(f"{len(waiting)} pages wait for the assistant: answer the requests "
              f"(python -m tutor.exchange list; docs/assistant-mode.md), then run --vision "
              f"again", file=sys.stderr)
        status = status or 3
    return status


def _transcriber(config: LLMConfig, source: Path, workspace: Path, failed: list[str],
                 waiting: list[str]):
    inner = make_transcriber(config, lambda number: render_png(source, number),
                             workspace / "logs" / "llm_calls.jsonl")

    def transcribe(page: Page) -> str | None:
        if failed:
            return None
        try:
            text = inner(page)
        except AwaitingAnswer as e:
            # An assistant reads this page later: the page stays pending, and the run goes
            # on, so one run asks for every page at once instead of one per run.
            waiting.append(e.request_id)
            return None
        except LLMError as e:
            failed.append(str(e))
            return None
        print(f"  p.{page.number} transcribed")
        return text
    return transcribe


if __name__ == "__main__":
    sys.exit(main())
