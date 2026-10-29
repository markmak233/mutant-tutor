"""Reading course PDFs into the markdown cache, on small PDFs written here (no course files)."""

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from tutor.materials import cache
from tutor.materials.__main__ import main
from tutor.materials.pdf import read_pdf, render_png
from tutor.materials.vision import (
    PROMPT,
    PROMPT_VERSION,
    code_notes,
    strip_outer_fence,
    trim_repetition,
)

RGB = {"Red": bytes([255, 0, 0]) * 4, "Blue": bytes([0, 0, 255]) * 4}  # 2x2 pixel images
LOGO = ("Red", 400, 330, 300, 100)  # 8% of the page, clear of text: flagged unless repeated


def make_pdf(path: Path, pages: list[dict]) -> Path:
    """A 720x540 PDF. Each page: "lines" of text, an optional "footer", and "images" as
    (name, x, y, width, height). Enough PDF for pypdfium2, nothing more."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # the page tree, filled in below
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        *(b"<< /Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceRGB "
          b"/BitsPerComponent 8 /Length 12 >>\nstream\n" + data + b"\nendstream"
          for data in RGB.values()),
    ]
    kids = []
    for page in pages:
        ops = [f"BT /F1 14 Tf 72 {480 - 20 * i} Td ({line}) Tj ET"
               for i, line in enumerate(page.get("lines", []))]
        if page.get("footer"):
            ops.append(f"BT /F1 10 Tf 350 20 Td ({page['footer']}) Tj ET")
        ops += [f"q {w} 0 0 {h} {x} {y} cm /{name} Do Q" for name, x, y, w, h in
                page.get("images", [])]
        content = "\n".join(ops).encode()
        objects.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 720 540] /Resources << /Font "
            b"<< /F1 3 0 R >> /XObject << /Red 4 0 R /Blue 5 0 R >> >> /Contents %d 0 R >>"
            % (len(objects)))
        kids.append(len(objects))
    objects[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % k for k in kids), len(kids))
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, xref)
    path.write_bytes(bytes(out))
    return path


def lecture(path, third_page_image="Blue", second_page_line="Map then reduce"):
    """Four slides with the same logo; slide 3 also has a large diagram image."""
    return make_pdf(path, [
        {"lines": ["Combiner design", "Sometimes reducers serve"], "footer": "2.31",
         "images": [LOGO]},
        {"lines": ["MapReduce", second_page_line], "footer": "2.32", "images": [LOGO]},
        {"lines": ["Mean v1"], "footer": "2.33",
         "images": [LOGO, (third_page_image, 300, 20, 400, 300)]},
        {"lines": ["Summary"], "images": [LOGO]},
    ])


def test_pages_have_text_labels_and_vision_flags(tmp_path):
    pages = read_pdf(lecture(tmp_path / "l.pdf"))

    assert [p.number for p in pages] == [1, 2, 3, 4]
    assert "Combiner design" in pages[0].text
    assert [p.label for p in pages] == ["2.31", "2.32", "2.33", None]
    # The logo covers 8% of every page but repeats, so only slide 3's diagram counts.
    assert [p.needs_vision for p in pages] == [False, False, True, False]


def test_small_code_images_count_and_images_under_text_do_not(tmp_path):
    """A 6% screenshot (code) needs vision; a large image with a title drawn on top of it
    (a shadow effect) does not, since the text layer has its words."""
    pdf = make_pdf(tmp_path / "l.pdf", [
        {"lines": ["Lambda functions"], "images": [("Blue", 300, 100, 160, 146)]},
        {"lines": ["Part 1 Spark Structured APIs"], "images": [("Red", 60, 440, 500, 60)]},
    ])
    assert [p.needs_vision for p in read_pdf(pdf)] == [True, False]


def test_different_images_in_the_same_place_are_not_decoration(tmp_path):
    """Three slides, each with its own picture in the same box: not a repeated logo."""
    pdf = make_pdf(tmp_path / "l.pdf", [
        {"lines": [f"Figure {n}"], "images": [(name, 0, 20, 720, 380)]}
        for n, name in enumerate(["Red", "Blue", "Red"], 1)])
    assert [p.needs_vision for p in read_pdf(pdf)] == [True, True, True]


def test_scanned_pages_without_a_text_layer_need_vision(tmp_path):
    """The same full-page image on every page, and no text: there is nothing else to read."""
    pdf = make_pdf(tmp_path / "l.pdf", [{"images": [("Red", 0, 0, 720, 540)]}] * 4)
    assert [p.needs_vision for p in read_pdf(pdf)] == [True] * 4


def test_hash_follows_text_and_image_data(tmp_path):
    base = read_pdf(lecture(tmp_path / "a.pdf"))
    again = read_pdf(lecture(tmp_path / "b.pdf"))
    new_text = read_pdf(lecture(tmp_path / "c.pdf", second_page_line="Map, shuffle, reduce"))
    new_image = read_pdf(lecture(tmp_path / "d.pdf", third_page_image="Red"))

    hashes = [p.content_hash for p in base]
    assert [p.content_hash for p in again] == hashes
    assert [p.content_hash == h for p, h in zip(new_text, hashes, strict=True)] == [
        True, False, True, True]
    assert [p.content_hash == h for p, h in zip(new_image, hashes, strict=True)] == [
        True, True, False, True]


def test_render_png(tmp_path):
    assert render_png(lecture(tmp_path / "l.pdf"), 3, dpi=36).startswith(b"\x89PNG")


def test_first_read_marks_image_pages_pending(tmp_path):
    entries, read_now = cache.update(read_pdf(lecture(tmp_path / "l.pdf")), {})

    assert read_now == 4
    assert [e.read for e in entries] == ["text", "text", "pending", "text"]
    assert "Mean v1" in entries[2].body  # the text layer stands in until vision runs


def test_cache_round_trip(tmp_path):
    entries, _ = cache.update(read_pdf(lecture(tmp_path / "l.pdf")), {})
    entries[0] = cache.Entry(1, "2.31", "vision", "abc123", "## p.9 looks like a header\nx",
                             "glm: glm-5.3-flash, corrected by hand", "2026-10-28", "2", True)
    text = cache.render("l.pdf", entries)

    assert "<!-- read: vision · model: glm: glm-5.3-flash, corrected by hand · prompt: 2 · " \
           "corrected · 2026-10-28 · hash: abc123 -->" in text
    assert list(cache.parse(text).values()) == entries


def test_a_comment_line_without_prompt_still_parses():
    old = cache.parse("## p.3\n<!-- read: vision · model: gemma · 2026-10-27 · hash: ab12 -->\nx")
    assert (old[3].model, old[3].date, old[3].prompt, old[3].corrected) == (
        "gemma", "2026-10-27", None, False)


def test_unchanged_pages_are_not_read_again(tmp_path):
    first, _ = cache.update(read_pdf(lecture(tmp_path / "a.pdf")), {})
    old = cache.parse(cache.render("l.pdf", first))

    _, read_now = cache.update(read_pdf(lecture(tmp_path / "b.pdf")), old)
    assert read_now == 0
    entries, read_now = cache.update(
        read_pdf(lecture(tmp_path / "c.pdf", second_page_line="Map, shuffle, reduce")), old)
    assert read_now == 1
    assert "Map, shuffle, reduce" in entries[1].body


def test_a_transcriber_reads_only_image_pages_and_only_once(tmp_path):
    pages = read_pdf(lecture(tmp_path / "l.pdf"))
    calls = []

    def transcribe(page):
        calls.append(page.number)
        return f"transcribed page {page.number}"

    first, _ = cache.update(pages, {})
    second, _ = cache.update(pages, cache.parse(cache.render("l.pdf", first)), transcribe,
                             model="gemma", today="2026-10-28")
    assert calls == [3]
    assert (second[2].read, second[2].body, second[2].model) == (
        "vision", "transcribed page 3", "gemma")

    cache.update(pages, cache.parse(cache.render("l.pdf", second)), transcribe)
    assert calls == [3]  # nothing pending, nothing changed


def test_cache_path_mirrors_the_workspace(tmp_path):
    source = tmp_path / "lectures" / "l.pdf"
    assert cache.cache_path(tmp_path, source) == tmp_path / "cache" / "lectures" / "l.pdf.md"
    assert cache.lecture_cache(tmp_path, "l") == cache.cache_path(tmp_path, source)
    with pytest.raises(ValueError):
        cache.cache_path(tmp_path / "workspace", tmp_path / "other" / "l.pdf")


def test_an_outside_lecture_folder_is_cached_like_the_default_one(tmp_path, monkeypatch, capsys):
    """LECTURE_DIR may be outside the workspace, or lectures/ a link to another folder."""
    workspace, elsewhere = tmp_path / "ws", tmp_path / "course-pdfs"
    workspace.mkdir()
    elsewhere.mkdir()
    (workspace / "workspace.toml").write_text('course = "demo"\n')
    lecture(elsewhere / "L1.pdf")
    monkeypatch.setenv("WORKSPACE_DIR", str(workspace))
    monkeypatch.setenv("LECTURE_DIR", str(elsewhere))
    assert main([str(elsewhere)]) == 0
    assert cache.lecture_cache(workspace, "L1").is_file()
    (workspace / "lectures").symlink_to(elsewhere, target_is_directory=True)
    assert cache.cache_path(workspace, workspace / "lectures" / "L1.pdf") == (
        cache.lecture_cache(workspace, "L1"))


def test_cli_writes_the_cache_and_skips_unchanged_pages(tmp_path, monkeypatch, capsys):
    (tmp_path / "workspace.toml").write_text('course = "demo"\n')
    (tmp_path / "lectures").mkdir()
    lecture(tmp_path / "lectures" / "l.pdf")
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))

    assert main([str(tmp_path / "lectures")]) == 0
    assert "4 read now, 1 image pages pending" in capsys.readouterr().out
    assert (tmp_path / "cache" / "lectures" / "l.pdf.md").is_file()
    assert main([str(tmp_path / "lectures" / "l.pdf")]) == 0
    assert "0 read now" in capsys.readouterr().out


def test_a_failed_transcription_keeps_the_page_pending(tmp_path):
    pages = read_pdf(lecture(tmp_path / "l.pdf"))
    entries, _ = cache.update(pages, {}, lambda page: None)
    assert entries[2].read == "pending"
    assert "Mean v1" in entries[2].body


def test_code_notes_flag_python_that_does_not_parse():
    markdown = (
        "```python\n>>> nums = sc.parallelize([1, 2, 3])\n>>> nums.count()\n3\n```\n"
        "```python\nrdd.foreach(lambda x: counter += x)\n```\n"
        "```text\nnot python at all (\n```\n"
    )
    notes = code_notes(markdown)
    assert len(notes) == 1 and notes[0].startswith("code block 2 does not parse")


def test_a_wrapping_markdown_fence_is_removed():
    assert strip_outer_fence("```markdown\n## Title\ntext\n```") == "## Title\ntext"
    assert strip_outer_fence("## Title\n```python\nx = 1\n```") == (
        "## Title\n```python\nx = 1\n```")


def test_cli_vision_needs_a_model_and_profile_needs_vision(tmp_path, monkeypatch, capsys):
    (tmp_path / "workspace.toml").write_text('course = "demo"\n')
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setattr("tutor.materials.__main__.load_env", lambda: None)
    assert main(["--vision", str(tmp_path)]) == 1
    assert "no model configured" in capsys.readouterr().err
    assert main(["--profile", "x", str(tmp_path)]) == 2  # --profile only with --vision


def test_a_cut_off_transcription_is_partial_and_done_again(tmp_path):
    pages = read_pdf(lecture(tmp_path / "l.pdf"))
    first, _ = cache.update(pages, {}, lambda page: "x x x x\n" + cache.TRUNCATED_NOTE)
    assert first[2].read == "partial"

    old = cache.parse(cache.render("l.pdf", first))
    second, read_now = cache.update(pages, old, lambda page: "complete")
    assert (read_now, second[2].read, second[2].body) == (1, "vision", "complete")


def test_a_newer_prompt_transcribes_older_pages_again(tmp_path):
    pages = read_pdf(lecture(tmp_path / "l.pdf"))
    first, _ = cache.update(pages, {}, lambda page: "version 1", prompt="1")
    old = cache.parse(cache.render("l.pdf", first))
    assert old[3].prompt == "1"

    _, read_now = cache.update(pages, old, lambda page: "again", prompt="1")
    assert read_now == 0  # same prompt
    _, read_now = cache.update(pages, old)
    assert read_now == 0  # a text-only run never redoes transcriptions
    entries, read_now = cache.update(pages, old, lambda page: "version 2", prompt="2")
    assert (read_now, entries[2].body, entries[2].prompt) == (1, "version 2", "2")

    unrecorded = {**old, 3: replace(old[3], prompt=None)}  # cached before versions existed
    _, read_now = cache.update(pages, unrecorded, lambda page: "version 2", prompt="2")
    assert read_now == 1


def test_a_hand_corrected_page_is_kept_on_a_newer_prompt(tmp_path):
    pages = read_pdf(lecture(tmp_path / "l.pdf"))
    first, _ = cache.update(pages, {}, lambda page: "checked against the slide", prompt="1")
    first[2] = replace(first[2], corrected=True)
    old = cache.parse(cache.render("l.pdf", first))

    entries, read_now = cache.update(pages, old, lambda page: "version 2", prompt="2")
    assert (read_now, entries[2].body, entries[2].prompt) == (0, "checked against the slide", "1")


def test_prompt_changes_come_with_a_new_version():
    digest = hashlib.sha256(PROMPT.encode()).hexdigest()[:16]
    assert (PROMPT_VERSION, digest) == (1, "ce9a87beee41811d"), (
        "vision.PROMPT changed: raise PROMPT_VERSION, then update this test's version and hash")


def test_runaway_repetition_is_cut_and_short_repeats_stay():
    looped = "```text\nr_avg <- sum/cnt\n```\n**Fixed?** " + "&nbsp;" * 300
    text, found = trim_repetition(looped)
    assert found and text.endswith("**Fixed?** &nbsp;") and "r_avg" in text

    words = 'Tag cloud: ' + '"BIG", "DATA", "STORAGE", ' * 40
    text, found = trim_repetition(words)
    assert found and text.rstrip() == 'Tag cloud: "BIG", "DATA", "STORAGE",'

    table = "| String | Int |\n" * 6 + "----------------\n"
    assert trim_repetition(table) == (table, False)


def test_an_assistant_is_asked_for_every_image_page_in_one_run(tmp_path, monkeypatch, capsys):
    """No API: each page to read becomes a request with its PNG; the next run stores the
    answers as transcriptions."""
    from tutor.exchange import Queue, store_answer

    (tmp_path / "workspace.toml").write_text(
        'course = "demo"\n[llm.profiles.claude-code]\nprovider = "assistant"\n'
        '[llm.roles]\nvision = "claude-code"\n')
    (tmp_path / "lectures").mkdir()
    pdf = make_pdf(tmp_path / "lectures" / "l.pdf", [
        {"lines": [f"Figure {n}"], "images": [(name, 0, 20, 720, 380)]}
        for n, name in enumerate(["Red", "Blue"], 1)])
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))

    assert main(["--vision", str(pdf)]) == 3
    assert "2 pages wait for the assistant" in capsys.readouterr().err
    queue = Queue(tmp_path / ".exchange" / "claude-code")
    requests = queue.waiting()
    assert len(requests) == 2 and all(r.with_name(f"{r.stem}-1.png").is_file()
                                      for r in requests)
    for n, request in enumerate(requests, 1):
        store_answer(queue, request.stem, f"Figure {n}: a coloured box")
    assert main(["--vision", str(pdf)]) == 0
    entries = cache.load(cache.lecture_cache(tmp_path, "l"))
    assert [e.read for e in entries.values()] == ["vision", "vision"]
    assert entries[1].model == "claude-code: claude-code"
