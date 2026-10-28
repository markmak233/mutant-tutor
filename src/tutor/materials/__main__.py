"""python -m tutor.materials [--vision [--profile NAME]] <file or folder> ...

Reads every PDF given (a folder: the PDFs directly in it) into the workspace's cache,
`$WORKSPACE_DIR/cache/<path>.md`. Pages whose content is unchanged since the last run are
not read again. Image pages are marked `pending` until a run with `--vision` transcribes
them with the workspace's `vision` model ([llm.roles] in workspace.toml, or `--profile` to
pick another; see tutor.llm); calls are logged in
`$WORKSPACE_DIR/logs/llm_calls.jsonl`. If the model fails, the pages done so far are kept
and the rest stay pending for the next run. Pages transcribed with an older prompt version
are transcribed again, except pages corrected by hand, which are listed instead.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from tutor.config import WorkspaceError, load_env, load_workspace
from tutor.llm import LLMConfig, LLMError, config_for
from tutor.materials import cache
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
        workspace = load_workspace().root
        config = config_for("vision", profile, workspace) if vision else None
    except (WorkspaceError, LLMError) as e:
        print(f"setup: {e}", file=sys.stderr)
        return 1
    failed: list[str] = []  # the first model error stops transcription for this run
    sources = []
    for arg in map(Path, args):
        sources += sorted(arg.glob("*.pdf")) if arg.is_dir() else [arg]
    status = 0
    for source in sources:
        if source.suffix.lower() != ".pdf" or not source.is_file():
            print(f"skipped {source}: only PDF files are read for now")
            continue
        try:
            target = cache.cache_path(workspace, source)
        except ValueError:
            print(f"skipped {source}: not inside the workspace {workspace}", file=sys.stderr)
            status = 1
            continue
        pages = read_pdf(source)
        transcribe = (_transcriber(config, source, workspace, failed) if config else None)
        prompt = str(PROMPT_VERSION) if config else None
        old = cache.load(target)
        entries, read_now = cache.update(pages, old, transcribe,
                                         model=f"{config.name}: {config.model}" if config else None,
                                         today=date.today().isoformat(), prompt=prompt)
        cache.save(target, cache.render(source.name, entries))
        pending = sum(e.read in ("pending", "partial") for e in entries)
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
    return status


def _transcriber(config: LLMConfig, source: Path, workspace: Path, failed: list[str]):
    inner = make_transcriber(config, lambda number: render_png(source, number),
                             workspace / "logs" / "llm_calls.jsonl")

    def transcribe(page: Page) -> str | None:
        if failed:
            return None
        try:
            text = inner(page)
        except LLMError as e:
            failed.append(str(e))
            return None
        print(f"  p.{page.number} transcribed")
        return text
    return transcribe


if __name__ == "__main__":
    sys.exit(main())
