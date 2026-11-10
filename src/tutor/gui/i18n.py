"""The GUI's pages in another language (owner, 2026-11-07 asked; 2026-11-10 decided: the
repository ships English only, and a model writes the other languages on the Language page).

What is translated: the pages' own text (templates, the notices, a form's problems, the
state explanations the Python modules write). Not: a task's output, `tutor doctor`'s lines
(the command's own, shared with the terminal), what a model wrote, file names, TOML keys,
and the exercise files opened in VS Code: those stay as they are.

How: GNU gettext's shape with the standard library's `gettext` module and Jinja's `i18n`
extension, so the pages need nothing new. English is the only source: every string is
written in English in the code (`_()` and `{% trans %}` in a template, `N_()` on a
module-level string, which the page translates when it shows it). A language's strings are
in `<root>/translations/<language>.po`, written by `python -m tutor.gui.translate` (a model
translates the English strings `tutor.gui.translate` extracts from the templates and modules;
the Language page starts it) or edited by hand; read by `read_po` here (the binary `.mo`
the standard library compiles to is never kept). A string with no translation falls back
to English, so a page changed in English never breaks another language: translating again
asks only for the strings the file lacks. The language: the owner's choice on the footer's
switch, kept for every course in `<root>/.tutor-gui-settings.json`, else the browser's
Accept-Language, else English; the switch goes back and forth, English included.

Safety: Jinja's new-style `_()` marks what gettext returns as safe HTML (so that the values
it formats in are escaped), translated or not, so the callables given to Jinja escape the
text first (`install`): an English string may say `<profile>`, and it shows as written. A
translation is data a model or a file wrote, so one is used only when `safe()` passes: the
same `%` placeholders as the English (Jinja formats it with `%`: a stray one would fail the
page), and no `<...>` the English does not have (`<profile>` in the English may stay; markup
of the model's own may not); otherwise the English shows. The same rule means a literal `%`
in a page's English is written `%%`.

Direction: a language written right to left (Arabic, Hebrew, Persian, Urdu) gets
`<html dir="rtl">`, and the stylesheet lays the pages out in logical properties (start and
end, not left and right), so the browser mirrors them; code, paths and a task's log stay
left to right (`style.css`). The direction comes from the file's `X-Direction` header
(`rtl` or `ltr`; `tutor.gui.translate` writes it), else from the translations themselves:
the side most of their first strong characters are written from (`direction_of`), so a
hand-written file needs no header.
"""

from __future__ import annotations

import gettext
import json
import re
import unicodedata
from pathlib import Path

from flask import Flask, g, request
from markupsafe import escape

ENGLISH = "en"
SETTINGS = ".tutor-gui-settings.json"  # under the GUI's root, beside the launcher's state
TRANSLATIONS = "translations"  # under the GUI's root: <language>.po, one per language
# A language's file name: what the owner typed on the Language page, in lower case, with
# runs of anything but letters, digits, '_' and '-' as one '-' ("日本語" stays as it is).
_SLUG = re.compile(r"[^\w-]+")
NAME_HEADER = "X-Name"  # the .po header that keeps the name as typed, for the switch
DIRECTION_HEADER = "X-Direction"  # the .po header: "rtl" or "ltr" (else found from the text)
LTR, RTL = "ltr", "rtl"
_ENTRY = re.compile(r'^(msgctxt|msgid_plural|msgid|msgstr\[(\d+)\]|msgstr)\s+"(.*)"$')
_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}
# A `%` placeholder, as Jinja's gettext formats one (`%(name)s`, `%s`, `%d`, `%%`).
_PLACEHOLDER = re.compile(r"%(?:\([^)]*\))?[#0 +-]*\d*(?:\.\d+)?[a-zA-Z%]|%")
_ANGLED = re.compile(r"<[^<>]*>|[<>]")  # <...> as the English may write it, or a stray one


def N_(message: str) -> str:
    """Marks a module-level string for extraction; the page translates it when shown."""
    return message


def gettext_(message: str) -> str:
    """`message` in the request's language (English outside a request)."""
    return translations_for(current_language()).gettext(message)


def ngettext_(singular: str, plural: str, n: int) -> str:
    return translations_for(current_language()).ngettext(singular, plural, n)


def current_language() -> str:
    try:
        return g.language
    except (AttributeError, RuntimeError):  # outside a request
        return ENGLISH


def current_root() -> Path | None:
    try:
        return g.root
    except (AttributeError, RuntimeError):
        return None


# --- the catalog -------------------------------------------------------------------------------


class Translations(gettext.NullTranslations):
    """A language's strings, from its .po file: `gettext` and `ngettext` as the standard
    library's, falling back to the English given when the file has no translation. Two
    plural forms, the first for one (every language gets both, the same text where the
    language does not tell them apart). `direction` is "ltr" or "rtl", for `<html dir>`."""

    def __init__(self, catalog: dict, name: str = "", direction: str = LTR):
        super().__init__()
        self._catalog, self.name, self.direction = catalog, name, direction

    def gettext(self, message: str) -> str:
        return self._catalog.get(message) or message

    def ngettext(self, msgid1: str, msgid2: str, n: int) -> str:
        return self._catalog.get((msgid1, int(n != 1))) or (msgid1 if n == 1 else msgid2)


_loaded: dict[Path, tuple[float, Translations]] = {}
_NONE = Translations({}, "English")


def translations_for(language: str, root: Path | None = None) -> Translations:
    """The language's catalog from `<root>/translations/<language>.po` (the request's
    root), read again only when the file changed; English, or a language with no file,
    has an empty one."""
    root = root or current_root()
    if language == ENGLISH or root is None:
        return _NONE
    path = root / TRANSLATIONS / f"{language}.po"
    try:
        mtime = path.stat().st_mtime
        if path in _loaded and _loaded[path][0] == mtime:
            return _loaded[path][1]
        text = path.read_text(encoding="utf-8")
    except OSError:
        return _NONE
    catalog, headers = read_po(text)
    name = headers.get(NAME_HEADER, language)
    # The header decides when it is one of the two values; else the text itself does.
    direction = headers.get(DIRECTION_HEADER, "").strip().lower()
    if direction not in (LTR, RTL):
        direction = direction_of(catalog, name)
    found = Translations(catalog, name, direction)
    _loaded[path] = (mtime, found)
    return found


def read_po(text: str) -> tuple[dict, dict[str, str]]:
    """A .po file's translations (msgid -> msgstr, and (msgid, form) -> msgstr[form] for
    a plural entry) and its headers (the msgid "" entry's lines, "Key: value"). An entry
    with no translation, a fuzzy one (a guess at a changed string, to be checked) and one
    that fails `safe()` are left out: the English shows for them."""
    catalog: dict = {}
    headers: dict[str, str] = {}
    entry: dict = {}
    fuzzy = False

    def close():
        if entry and not fuzzy:
            msgid, single = entry.get("msgid", ""), entry.get("msgstr", "")
            if not msgid:
                for line in single.splitlines():
                    key, _, value = line.partition(":")
                    if value:
                        headers[key.strip()] = value.strip()
            elif single and safe(msgid, single):
                catalog[msgid] = single
            for index, form in entry.get("forms", {}).items():
                source = msgid if index == 0 else entry.get("msgid_plural", msgid)
                if msgid and form and safe(source, form):
                    catalog[(msgid, index)] = form
        entry.clear()

    last: list | None = None  # [text so far, plural index or None, key]: continuations
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            close()
            fuzzy, last = False, None
            continue
        if line.startswith("#"):
            fuzzy = fuzzy or (line.startswith("#,") and "fuzzy" in line)
            continue
        if line.startswith('"') and line.endswith('"') and last is not None:
            last[0] += _unescape(line[1:-1])
            if last[1] is not None:
                entry["forms"][last[1]] = last[0]
            else:
                entry[last[2]] = last[0]
            continue
        match = _ENTRY.match(line)
        if match is None:
            continue
        key, index, value = match.group(1), match.group(2), _unescape(match.group(3))
        if index is not None:
            entry.setdefault("forms", {})[int(index)] = value
            last = [value, int(index), key]
        else:
            entry[key] = value
            last = [value, None, key]
    close()
    return catalog, headers


def write_po(entries: list[tuple[str, str | None, str, str]], headers: dict[str, str]) -> str:
    """A .po file: `headers` as its first entry, then (msgid, msgid_plural or None,
    msgstr, msgstr[1]) each; a plural entry writes msgstr[0] and msgstr[1]."""
    out = ['msgid ""', 'msgstr ""']
    out += [_quote(f"{k}: {v}\n") for k, v in headers.items()]
    for msgid, plural, single, second in entries:
        out.append("")
        out.append(f"msgid {_quote(msgid)}")
        if plural is None:
            out.append(f"msgstr {_quote(single)}")
        else:
            out.append(f"msgid_plural {_quote(plural)}")
            out.append(f"msgstr[0] {_quote(single)}")
            out.append(f"msgstr[1] {_quote(second)}")
    return "\n".join(out) + "\n"


def safe(source: str, translation: str) -> bool:
    """A translation this GUI may show: the same `%` placeholders as the English (Jinja
    formats it with `%`: a stray one would fail the page), and the same `<...>` pieces
    (markup of the model's own is never wanted; it would show as written, escaped)."""
    if sorted(_ANGLED.findall(source)) != sorted(_ANGLED.findall(translation)):
        return False
    return sorted(placeholders(source)) == sorted(placeholders(translation))


def placeholders(text: str) -> list[str]:
    """The `%` placeholders of a string as Jinja's gettext formats them; a `%` that is not
    one (a literal that should be `%%`) comes out as "%" alone."""
    return _PLACEHOLDER.findall(text)


def direction_of(catalog: dict, name: str = "") -> str:
    """The side a language is written from, "rtl" or "ltr", read off its text: the first
    strong character of each translation (Unicode's bidirectional class: R and AL are
    right to left, L is left to right; digits, spaces and punctuation decide nothing),
    and the side most of them are on wins. The name decides a tie only (so a name typed
    as a code, "ar", does not outvote the text; a file with no translations yet goes by
    its name). English, or nothing to read, is "ltr"."""
    def side(text: str) -> int:
        for char in text:
            cls = unicodedata.bidirectional(char)
            if cls in ("R", "AL"):
                return 1
            if cls == "L":
                return -1
        return 0

    votes = sum(side(text) for text in catalog.values()) or side(name)
    return RTL if votes > 0 else LTR


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _unescape(text: str) -> str:
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(0)), text)


# --- the languages, and the one of a request --------------------------------------------------


def slug(name: str) -> str:
    return _SLUG.sub("-", name.strip().lower()).strip("-")[:40]


def available(root: Path) -> dict[str, str]:
    """English, then each language with a file under the root: code -> name."""
    found = {ENGLISH: "English"}
    folder = root / TRANSLATIONS
    for path in sorted(folder.glob("*.po")) if folder.is_dir() else []:
        code = path.name.removesuffix(".po")
        if code != ENGLISH and slug(code) == code:
            found[code] = translations_for(code, root).name
    return found


def chosen(root: Path) -> str:
    """The language the owner chose on the switch, "" for none (or an unreadable file)."""
    try:
        data = json.loads((root / SETTINGS).read_text(encoding="utf-8"))
        language = data.get("language", "") if isinstance(data, dict) else ""
    except (OSError, ValueError):
        return ""
    return language if isinstance(language, str) and language in available(root) else ""


def choose(root: Path, language: str) -> None:
    (root / SETTINGS).write_text(json.dumps({"language": language}) + "\n", encoding="utf-8")


def from_browser(header: str, languages: dict[str, str]) -> str:
    """The first of the browser's languages the root has a file for ("zh-CN" counts as
    "zh" when the file is named so), else English."""
    for part in header.split(","):
        code = part.split(";")[0].strip().lower()
        for candidate in (code, code.split("-")[0]):
            if candidate in languages:
                return candidate
    return ENGLISH


def install(app: Flask) -> None:
    """Jinja's `_()`, `gettext`, `ngettext` and `{% trans %}` in every template, in the
    request's language; `gui_language`, `gui_direction` ("ltr" or "rtl", for `<html dir>`)
    and `gui_languages` in every template's context (the Environment page has `language`
    and `languages` of its own)."""
    app.jinja_env.add_extension("jinja2.ext.i18n")
    # Escaped here, as Jinja marks the result safe (see the module's note on safety).
    app.jinja_env.install_gettext_callables(
        lambda message: escape(gettext_(message)),
        lambda singular, plural, n: escape(ngettext_(singular, plural, n)), newstyle=True)

    def resolve() -> str:
        """The request's language, found once per request (also for an error page a
        check refused before the request's handlers ran)."""
        if "language" not in g:
            g.root = app.config["ROOT"]
            g.language = (chosen(g.root)
                          or from_browser(request.headers.get("Accept-Language", ""),
                                          available(g.root)))
        return g.language

    @app.before_request
    def set_language():
        resolve()

    @app.context_processor
    def language_context():
        language = resolve()
        return {"gui_language": language, "gui_languages": available(g.root),
                "gui_direction": translations_for(language).direction}

