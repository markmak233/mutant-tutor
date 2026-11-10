"""The GUI in another language (gui/i18n.py, gui/translate.py, the Language page): the
.po file read and written, what may be shown, the request's language and the switch, the
English strings extracted from the pages, and a model's translation checked and kept. No
test calls a model: the chat function is a stand-in keyed by the request's content."""

import json
import re
from pathlib import Path

import pytest

pytest.importorskip("flask")  # the `gui` extra

from tutor import llm  # noqa: E402
from tutor.gui import i18n, language_table, translate  # noqa: E402
from tutor.gui.app import create_app  # noqa: E402

TOKEN = "test-token"
BASE = "http://127.0.0.1:5000"

PO = '''msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"
"X-Name: 中文\\n"

#: templates/base.html:7
msgid "Courses"
msgstr "课程"

msgid "Must exercises: %(passed)d of %(total)d passed"
msgstr "必做题：%(total)d 道中 %(passed)d 道通过"

msgid "%(count)s batch"
msgid_plural "%(count)s batches"
msgstr[0] "%(count)s 批"
msgstr[1] "%(count)s 批"

msgid "No such course."
msgstr "没有这个课程。"

msgid "Paste the key first."
msgstr "先粘贴密钥。"

msgid "Keys"
msgstr ""

#, fuzzy
msgid "Language"
msgstr "语言?"

msgid "Not shown"
msgstr "<b>未显示</b>"

msgid "Next:"
msgstr "下一题 %"

msgid "A line that"
" continues"
msgstr "一行"
"接着"
'''


# --- the file ----------------------------------------------------------------------------------


def test_a_po_file_is_read_with_only_what_may_be_shown():
    catalog, headers = i18n.read_po(PO)
    assert headers["X-Name"] == "中文" and headers["Content-Type"].startswith("text/plain")
    assert catalog["Courses"] == "课程"
    assert catalog["Must exercises: %(passed)d of %(total)d passed"] == (
        "必做题：%(total)d 道中 %(passed)d 道通过")  # the placeholders in another order
    assert catalog[("%(count)s batch", 0)] == catalog[("%(count)s batch", 1)] == "%(count)s 批"
    assert catalog["A line that continues"] == "一行接着"
    assert "Keys" not in catalog  # empty: English shows
    assert "Language" not in catalog  # fuzzy: a guess, not shown
    assert "Not shown" not in catalog  # markup the English has not: never shown
    assert "Next:" not in catalog  # a stray %: Jinja's formatting would fail the page


def test_a_catalog_falls_back_to_english_and_the_file_round_trips(tmp_path):
    catalog, _ = i18n.read_po(PO)
    found = i18n.Translations(catalog, "中文")
    assert found.gettext("Courses") == "课程" and found.gettext("Keys") == "Keys"
    assert found.ngettext("%(count)s batch", "%(count)s batches", 1) == "%(count)s 批"
    assert found.ngettext("a", "b", 1) == "a" and found.ngettext("a", "b", 3) == "b"

    text = i18n.write_po([("Courses", None, "课程", ""), ('Say "hi"\n', None, "说\n", ""),
                          ("%(count)s batch", "%(count)s batches", "%(count)s 批", "%(count)s 批")],
                         {"X-Name": "中文"})
    again, headers = i18n.read_po(text)
    assert headers == {"X-Name": "中文"}
    assert again == {"Courses": "课程", 'Say "hi"\n': "说\n",
                     ("%(count)s batch", 0): "%(count)s 批", ("%(count)s batch", 1): "%(count)s 批"}


@pytest.mark.parametrize(("source", "translation", "ok"), [
    ("%(n)d of %(total)d", "%(total)d 中 %(n)d", True),
    ("%(n)d of %(total)d", "%(n)d 个", False),  # one placeholder dropped
    ("100%% done", "完成 100%%", True),
    ("100%% done", "完成 100%", False),
    ("plain", "plain <br>", False),
    ("plain", "普通", True),
    ('add `vision = "<profile>"`', '加上 `vision = "<profile>"`', True),  # as the English
    ('add `vision = "<profile>"`', '加上 `vision = "<b><profile>"`', False),
])
def test_what_may_be_shown(source, translation, ok):
    assert i18n.safe(source, translation) is ok


# --- the request's language -------------------------------------------------------------------


@pytest.fixture
def root(tmp_path):
    course = tmp_path / "courses" / "demo-course"
    course.mkdir(parents=True)
    (course / "workspace.toml").write_text(
        'course = "demo"\n\n[llm.profiles.m]\nprovider = "deepseek"\nmodel = "x"\n'
        'api_key_env = "DEEPSEEK_API_KEY"\n\n[llm.roles]\nsetup = "m"\n', encoding="utf-8")
    (tmp_path / "courses" / i18n.TRANSLATIONS).mkdir()
    (tmp_path / "courses" / i18n.TRANSLATIONS / "zh.po").write_text(PO, encoding="utf-8")
    return tmp_path / "courses"


def _browser(root):
    client = create_app(root, TOKEN, port=5000).test_client()
    client.get(f"/?token={TOKEN}", base_url=BASE)
    return client


def _page(client, path, **headers):
    return client.get(path, base_url=BASE, headers=headers).get_data(as_text=True)


def test_the_languages_are_english_and_each_file_by_its_name(root):
    assert i18n.available(root) == {"en": "English", "zh": "中文"}
    assert i18n.chosen(root) == ""
    i18n.choose(root, "zh")
    assert i18n.chosen(root) == "zh"
    i18n.choose(root, "fr")  # no file: not a choice
    assert i18n.chosen(root) == ""
    assert i18n.from_browser("zh-CN,zh;q=0.9,en;q=0.8", i18n.available(root)) == "zh"
    assert i18n.from_browser("fr-FR, de", i18n.available(root)) == "en"
    # Files named by the table's tags are found subtag by subtag (language_table).
    files = {"en": "English", "zh-hant-hk": "繁體中文（香港）", "zh-hans": "简体中文",
             "pt-br": "Português (Brasil)", "中文": "中文"}
    assert i18n.from_browser("zh-HK", files) == "zh-hant-hk"  # the likely script
    assert i18n.from_browser("zh-TW", files) == "zh-hant-hk"  # no zh-hant: the same script
    assert i18n.from_browser("zh-SG", files) == "zh-hans"
    assert i18n.from_browser("zh-TW", {"en": "English", "zh-hans": "简体中文"}) == "zh-hans"
    assert i18n.from_browser("zh-CN,en", files) == "zh-hans"
    assert i18n.from_browser("zh", files) == "zh-hans"
    assert i18n.from_browser("pt-PT, en", files) == "pt-br"  # the same language
    assert i18n.from_browser("en-GB, zh-HK", files) == "en"
    assert i18n.from_browser("中文", files) == "中文" and i18n.from_browser("", files) == "en"
    assert i18n.slug(" 日本語 ") == "日本語" and i18n.slug("Deutsch (CH)") == "deutsch-ch"


def test_a_page_shows_the_chosen_language_and_the_switch_goes_back_and_forth(root):
    client = _browser(root)
    text = _page(client, "/")
    assert "Your courses" in text and 'lang="en"' in text  # nothing chosen: English
    assert "课程" not in text
    text = _page(client, "/", **{"Accept-Language": "zh-CN,zh;q=0.9"})
    assert 'lang="zh"' in text and "<title>课程" in text  # the browser's, with a file

    response = client.post("/language", base_url=BASE, headers={"Origin": BASE},
                           data={"language": "zh", "back": "/c/demo-course/models/?x=1"})
    assert response.status_code == 303
    assert response.headers["Location"] == "/c/demo-course/models/?x=1"
    assert json.loads((root / i18n.SETTINGS).read_text(encoding="utf-8")) == {"language": "zh"}
    text = _page(client, "/")  # chosen: for every browser, whatever its language
    assert "<title>课程" in text and "Keys" in text  # the untranslated one stays English
    assert "<b>未显示</b>" not in text
    assert "<strong>中文</strong>" in text and 'name="language" value="en"' in text  # the footer

    response = client.post("/language", base_url=BASE, headers={"Origin": BASE},
                           data={"language": "en", "back": "//evil.example/"})
    assert response.headers["Location"] == "/language"  # back to English; a bad `back` dropped
    assert "Your courses" in _page(client, "/")
    assert client.post("/language", base_url=BASE, headers={"Origin": BASE},
                       data={"language": "fr"}).status_code == 400


# --- right to left -----------------------------------------------------------------------------

ARABIC = '''msgid ""
msgstr ""
"X-Name: العربية\\n"

msgid "Courses"
msgstr "الدورات"

msgid "Keys"
msgstr "المفاتيح 2"
'''


def test_the_direction_is_read_off_the_text_unless_the_header_says():
    assert i18n.direction_of({}, "") == "ltr"  # English: nothing to read
    assert i18n.direction_of({"Courses": "课程"}, "中文") == "ltr"
    assert i18n.direction_of({"Courses": "الدورات", ("x", 0): "1 عنصر"}, "ar") == "rtl"
    assert i18n.direction_of({"Courses": "קורסים"}, "עברית") == "rtl"
    # A name typed as a code does not outvote the text; punctuation and digits decide
    # nothing; a file with no translations yet goes by its name.
    assert i18n.direction_of({"Next:": "- 2 הבא"}, "he") == "rtl"
    assert i18n.direction_of({}, "العربية") == "rtl"
    assert i18n.direction_of({"Courses": "Courses"}, "العربية") == "ltr"  # given back English
    assert i18n.read_po(ARABIC)[1]["X-Name"] == "العربية"


def test_a_right_to_left_language_sets_the_page_direction(root):
    (root / i18n.TRANSLATIONS / "ar.po").write_text(ARABIC, encoding="utf-8")
    client = _browser(root)
    assert 'dir="ltr"' in _page(client, "/")  # English
    i18n.choose(root, "zh")
    assert 'dir="ltr"' in _page(client, "/")
    i18n.choose(root, "ar")
    text = _page(client, "/")
    assert 'lang="ar" dir="rtl"' in text and "<title>الدورات" in text
    # The header, when it says, wins over the text: a hand-edited file may set it.
    (root / i18n.TRANSLATIONS / "ar.po").write_text(
        ARABIC.replace('"X-Name: العربية\\n"', '"X-Name: العربية\\n"\n"X-Direction: LTR\\n"'),
        encoding="utf-8")
    assert 'lang="ar" dir="ltr"' in _page(client, "/")


def test_the_stylesheet_names_no_physical_side():
    """`<html dir="rtl">` mirrors the pages only when the stylesheet says start and end,
    never left and right (gui/static/style.css)."""
    css = (Path(i18n.__file__).parent / "static" / "style.css").read_text(encoding="utf-8")
    physical = re.compile(r"\b(?:margin|padding|border)-(?:left|right)\b|\b(?:left|right)\s*:"
                          r"|text-align\s*:\s*(?:left|right)\b")
    rules = re.sub(r"/\*.*?\*/", "", css, flags=re.S)  # the comments may say "left"
    assert [line for line in rules.splitlines() if physical.search(line)] == []
    assert "code, pre, kbd, .log { direction: ltr;" in css  # code stays left to right


def test_a_notice_and_a_plural_are_translated(root):
    client = _browser(root)
    i18n.choose(root, "zh")
    text = _page(client, "/c/demo-course/?notice=opened-folder")
    assert "Opened in the file manager." in text  # no translation in the file: English
    text = _page(client, "/")
    assert "0 批" in text  # {% trans %} with pluralize, through the file's msgstr[1]


def test_an_error_page_and_a_form_problem_are_translated(root):
    """The abort messages are marked N_() and translated by the error handler (gettext
    alone: a command's text may carry a `%`); a form's problem is translated where the
    module returns it (keys.py), inside the request."""
    client = _browser(root)
    i18n.choose(root, "zh")
    assert "没有这个课程。" in _page(client, "/c/no-such-course/")
    response = client.post("/keys/set", base_url=BASE, headers={"Origin": BASE},
                           data={"name": "X_KEY", "value": " "})
    assert response.status_code == 400 and "先粘贴密钥。" in response.get_data(as_text=True)
    i18n.choose(root, "en")
    assert "No such course." in _page(client, "/c/no-such-course/")


def test_the_language_page_lists_offers_and_starts_a_translation(root, monkeypatch):
    # A second course: two chat profiles, one of them the first course's model again
    # (listed once, under the first course), and an embedding profile (not a chat).
    other = root / "other-course"
    other.mkdir()
    (other / "workspace.toml").write_text(
        'course = "other"\n\n[llm.profiles.big]\nprovider = "anthropic"\n'
        'model = "claude-opus-5-5"\napi_key_env = "ANTHROPIC_API_KEY"\n'
        '[llm.profiles.same]\nprovider = "deepseek"\nmodel = "x"\napi_key_env = "D"\n'
        '[llm.profiles.vec]\nprovider = "openai"\nmodel = "text-embedding-3-small"\n'
        'api_key_env = "OPENAI_API_KEY"\n\n[llm.roles]\nplan = "big"\nembedding = "vec"\n',
        encoding="utf-8")
    client = _browser(root)
    text = _page(client, "/language")
    assert "<strong>English</strong>" in text and ">中文</button>" in text
    assert "zh.po" in text and "English strings; about" in text
    options = re.findall(r'<option value="([^"]*)"( selected)?>([^<]*)</option>', text)
    assert [o for o in options if "|" in o[0]] == [
        ("demo-course|m", " selected", "demo: m (deepseek: x)"),  # the setup model, chosen
        ("other-course|big", "", "other: big (anthropic: claude-opus-5-5)")]
    # The file's line: what it has against the English, none kept as the English here.
    shown, same = i18n.translations_for("zh", root).counts()
    assert same == 0 and f"{shown} of {len(translate.english())} translated</span>" in text

    started = []
    monkeypatch.setattr("tutor.gui.routes.language.start_task",
                        lambda course, title, commands, meta=None: started.append(
                            (course.folder, title, commands, meta)) or ("ok", 303))
    client.post("/language/translate", base_url=BASE, headers={"Origin": BASE},
                data={"name": " Deutsch ", "model": "demo-course|m", "again": "1",
                      "note": " Swiss spelling,\n no ß "})
    folder, title, commands, meta = started[0]
    assert (folder, title, meta) == ("demo-course", "Translate the GUI into Deutsch",
                                     {"kind": "translate"})
    assert commands[0][1:] == ["-m", "tutor.gui.translate", "--root", str(root),
                               "--profile", "m", "--note", "Swiss spelling, no ß", "--again",
                               "--", "Deutsch"]  # after "--": "-x" is a name, not an option
    response = client.post("/language/translate", base_url=BASE, headers={"Origin": BASE},
                           data={"name": "en", "model": "demo-course|m"})
    assert response.headers["Location"].endswith("notice=no-language-name")
    # A language from the table: `--code TAG`, no name; the typed name is then ignored.
    # The other course's profile: the task runs there, with its key.
    assert "<option value=\"zh-Hant\">繁體中文 · zh-Hant</option>" in text
    client.post("/language/translate", base_url=BASE, headers={"Origin": BASE},
                data={"code": "zh-Hant", "name": "x", "model": "other-course|big"})
    folder, title, commands, meta = started[1]
    assert (folder, title) == ("other-course", "Translate the GUI into 繁體中文")
    assert commands[0][1:] == ["-m", "tutor.gui.translate", "--root", str(root),
                               "--profile", "big", "--code", "zh-Hant"]
    # Nothing chosen and nothing typed: told so, nothing started; so with a profile the
    # course does not have (the embedding one included), or a course that is not there.
    response = client.post("/language/translate", base_url=BASE, headers={"Origin": BASE},
                           data={"code": "", "name": "", "model": "demo-course|m"})
    assert response.headers["Location"].endswith("notice=no-language-name")
    for model in ("other-course|vec", "demo-course|nope", "demo-course", ""):
        response = client.post("/language/translate", base_url=BASE,
                               headers={"Origin": BASE}, data={"code": "de", "model": model})
        assert response.status_code in (303, 404), model
        if response.status_code == 303:
            assert response.headers["Location"].endswith("notice=no-translate-model"), model
    assert len(started) == 2


# --- the language table ------------------------------------------------------------------------


def test_the_language_table_is_well_formed():
    """Each row a BCP 47 tag the registry's way, a name, unique tags and unique file names
    (the tag in lower case, i18n.slug), the right-to-left rows marked (language_table)."""
    rows = language_table.LANGUAGES
    assert len(rows) >= 30
    assert [x.code for x in rows] == sorted((x.code for x in rows), key=str.lower)  # the list
    assert all(language_table.TAG.match(x.code) for x in rows), [
        x.code for x in rows if not language_table.TAG.match(x.code)]
    assert all(x.name.strip() for x in rows)
    assert len({x.code for x in rows}) == len(rows)
    assert len({i18n.slug(x.code) for x in rows}) == len(rows)
    assert all(i18n.slug(x.code) == x.code.lower() for x in rows)
    assert {x.code for x in rows if x.rtl} == {"ar", "he", "fa", "ur"}
    assert all(i18n.direction_of({"x": x.name}) == ("rtl" if x.rtl else "ltr") for x in rows)
    assert language_table.by_code("zh-hant").name == "繁體中文"
    assert "Taiwan" in language_table.by_code("zh-Hant").note
    assert language_table.by_code("zh-Hant-HK") is None  # taken out (owner, 2026-11-11)
    assert language_table.by_code("xx") is None and language_table.by_code("") is None
    assert language_table.subtags("zh-Hant-HK") == ("zh", "hant", "hk")
    assert language_table.subtags("pt-BR") == ("pt", None, "br")
    assert language_table.subtags("es-419") == ("es", None, "419")
    assert language_table.candidates("zh-HK") == ["zh-hk", "zh-hant-hk", "zh-hant", "zh"]
    assert language_table.candidates("de") == ["de"]
    assert language_table.candidates("pt-BR") == ["pt-br", "pt"]


# --- the English strings, and a model's translation of them -----------------------------------


def test_the_english_strings_come_from_the_pages_and_modules(tmp_path):
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "a.html").write_text(
        '{{ _("Hello %(name)s", name=x) }} {% trans count=n %}{{ count }} run{% pluralize %}'
        '{{ count }} runs{% endtrans %} {{ _(notice.text) }} {{ _("Hello %(name)s") }}',
        encoding="utf-8")
    (tmp_path / "m.py").write_text(
        'N = {"a": ("ok", N_("Saved.")), "b": N_("Gone %s")}\n'
        'def f(n):\n    return ngettext_("one", "many", n) + gettext_("Later")\n',
        encoding="utf-8")
    found = translate.english(tmp_path)
    assert found == [translate.Message("Hello %(name)s"),
                     translate.Message("%(count)s run", "%(count)s runs"),
                     translate.Message("Saved."), translate.Message("Gone %s"),
                     translate.Message("one", "many"), translate.Message("Later")]
    assert translate.tokens_about(found) == 1  # mostly the prompt; never 0 with strings
    assert translate.tokens_about([]) == 0


def test_the_real_pages_strings_are_found_and_each_literal_percent_is_doubled():
    """A `%` in a page's English must be `%%` (Jinja formats a translation with `%`)."""
    found = translate.english()
    assert len(found) > 50
    for m in found:
        assert "%" not in i18n.placeholders(m.singular), m.singular
        assert m.plural is None or "%" not in i18n.placeholders(m.plural), m.plural


def test_english_with_angle_brackets_shows_as_written(root):
    """Jinja marks what gettext returns as safe: the callables escape it first."""
    client = _browser(root)
    text = _page(client, "/c/demo-course/?notice=no-vision-model")
    assert "`vision = &#34;&lt;profile&gt;&#34;`" in text and "<profile>" not in text


def _fake_chat(answers: dict):
    """A chat stand-in: the reply for the batch whose first English string is the key."""
    def chat(config, messages, *, purpose, max_tokens, thinking, response_schema, log_path):
        material = messages[1]["content"]
        for key, items in answers.items():
            if f'"text": "{key}"' in material:
                return llm.Reply(json.dumps({"items": items}), 10, 10, 0.1, "stop")
        raise AssertionError(f"no answer for {material[:80]}")
    return chat


def test_a_translation_is_kept_when_it_passes_and_refused_when_it_does_not():
    batch = [translate.Message("Courses"), translate.Message("%(n)d of %(total)d passed"),
             translate.Message("%(count)s batch", "%(count)s batches"),
             translate.Message("Keys"), translate.Message("Not shown"),
             translate.Message("Stays")]
    chat = _fake_chat({"Courses": [
        {"n": 1, "text": "课程"}, {"n": 2, "text": "通过 %(n)d 个"},  # a placeholder dropped
        {"n": 3, "text": "%(count)s 批", "plural": ""},  # one form: used for both
        {"n": 4, "text": "<b>密钥</b>"}, {"n": 6, "text": "Stays"}]})  # 5 not answered
    config = llm.LLMConfig("m", "deepseek", "https://api.deepseek.com", "x")
    got, refused = translate.ask(config, "中文", batch, None, chat_fn=chat)
    assert got == {"Courses": "课程", ("%(count)s batch", 0): "%(count)s 批",
                   ("%(count)s batch", 1): "%(count)s 批", "Stays": "Stays"}
    assert [r.split(":")[0] for r in refused] == ["refused (placeholders differ, or markup)"] * 2
    # Unanswered and refused: still missing, so asked again next time. Given back unchanged
    # ("Stays"): kept, so the file is complete and the run after asks nothing (2026-11-10).
    assert [m.singular for m in translate.missing(batch, got)] == [
        "%(n)d of %(total)d passed", "Keys", "Not shown"]


def test_the_command_writes_the_file_in_batches_and_asks_only_for_what_is_missing(
        tmp_path, monkeypatch):
    course = tmp_path / "courses" / "demo-course"
    (course / "lectures").mkdir(parents=True)
    (course / "workspace.toml").write_text(
        'course = "demo"\n\n[llm.profiles.m]\nprovider = "deepseek"\nmodel = "x"\n'
        'api_key_env = "DEEPSEEK_API_KEY"\n\n[llm.roles]\nsetup = "m"\n', encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_DIR", str(course))
    monkeypatch.setenv("LECTURE_DIR", str(course / "lectures"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setattr(translate, "BATCH", 2)
    strings = [translate.Message("Courses"), translate.Message("Keys"),
               translate.Message("%(count)s batch", "%(count)s batches")]
    monkeypatch.setattr(translate, "english", lambda folder=None: strings)
    asked = []

    def chat(config, messages, **kw):
        asked.append(messages[1]["content"])
        if '"text": "Courses"' in messages[1]["content"]:
            items = [{"n": 1, "text": "课程"}, {"n": 2, "text": "密钥"}]
        else:
            items = [{"n": 1, "text": "%(count)s 批", "plural": "%(count)s 批"}]
        return llm.Reply(json.dumps({"items": items}), 10, 10, 0.1, "stop")

    monkeypatch.setattr(translate, "chat", chat)
    assert translate.main(["中文", "--root", str(tmp_path / "courses")]) == 0
    path = tmp_path / "courses" / i18n.TRANSLATIONS / "中文.po"
    catalog, headers = i18n.read_po(path.read_text(encoding="utf-8"))
    assert headers["X-Name"] == "中文" and "tutor.gui.translate" in headers["X-Generator"]
    assert headers["X-Direction"] == "ltr"  # read off the translations (gui/i18n.py)
    assert catalog == {"Courses": "课程", "Keys": "密钥", ("%(count)s batch", 0): "%(count)s 批",
                       ("%(count)s batch", 1): "%(count)s 批"}
    assert len(asked) == 2  # two batches of two
    assert "Language: 中文" in asked[0] and "<material>" in asked[0]
    assert i18n.available(tmp_path / "courses") == {"en": "English", "中文": "中文"}

    # The English grew by one string: only that one is asked for; a string gone leaves.
    strings[:] = [translate.Message("Courses"), translate.Message("New course"),
                  translate.Message("%(count)s batch", "%(count)s batches")]
    asked.clear()

    def chat_again(config, messages, **kw):
        asked.append(messages[1]["content"])
        return llm.Reply(json.dumps({"items": [{"n": 1, "text": "新课程"}]}), 1, 1, 0.1, "stop")

    monkeypatch.setattr(translate, "chat", chat_again)
    assert translate.main(["中文", "--root", str(tmp_path / "courses")]) == 0
    assert len(asked) == 1 and '"text": "New course"' in asked[0]
    catalog, _ = i18n.read_po(path.read_text(encoding="utf-8"))
    assert catalog["New course"] == "新课程" and "Keys" not in catalog
    assert catalog["Courses"] == "课程"
    assert translate.main(["en", "--root", str(tmp_path)]) == 2


def test_the_note_to_the_translator_goes_with_every_run_and_stays_in_the_file(
        tmp_path, monkeypatch):
    """The owner's note (register, wording) is sent before each batch's material, between
    tags the prompt names, kept in X-Note, and a run without one sends the file's."""
    course = tmp_path / "courses" / "demo-course"
    (course / "lectures").mkdir(parents=True)
    (course / "workspace.toml").write_text(
        'course = "demo"\n\n[llm.profiles.m]\nprovider = "deepseek"\nmodel = "x"\n'
        'api_key_env = "DEEPSEEK_API_KEY"\n\n[llm.roles]\nsetup = "m"\n', encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_DIR", str(course))
    monkeypatch.setenv("LECTURE_DIR", str(course / "lectures"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-not-a-real-key")
    strings = [translate.Message("Courses")]
    monkeypatch.setattr(translate, "english", lambda folder=None: strings)
    asked = []

    def chat(config, messages, **kw):
        asked.append(messages[1]["content"])
        n = 1 if "Courses" in messages[1]["content"] else 2
        return llm.Reply(json.dumps({"items": [{"n": 1, "text": f"譯{n}"}]}), 1, 1, 0.1, "stop")

    monkeypatch.setattr(translate, "chat", chat)
    root = tmp_path / "courses"
    assert translate.main(["中文", "--root", str(root),
                           "--note", "written  standard,\nHong Kong"]) == 0
    assert asked[0].startswith("Language: 中文\n\n<note>\nwritten standard, Hong Kong\n</note>\n\n"
                               "<material>\n")
    path = root / i18n.TRANSLATIONS / "中文.po"
    catalog, headers = i18n.read_po(path.read_text(encoding="utf-8"))
    assert headers["X-Note"] == "written standard, Hong Kong"
    assert i18n.translations_for("中文", root).note == "written standard, Hong Kong"
    # A string the pages gained, asked for with the file's note; a new note replaces it.
    strings.append(translate.Message("Keys"))
    assert translate.main(["中文", "--root", str(root)]) == 0
    assert "<note>\nwritten standard, Hong Kong\n</note>" in asked[1]
    assert translate.main(["中文", "--root", str(root), "--again", "--note", "plain"]) == 0
    assert "<note>\nplain\n</note>" in asked[2]
    assert i18n.read_po(path.read_text(encoding="utf-8"))[1]["X-Note"] == "plain"
    # Without a note, no tag and no header; the system prompt asks for one register.
    assert translate.main(["日本語", "--root", str(root)]) == 0
    assert asked[3].startswith("Language: 日本語\n\n<material>\n")
    assert "X-Note" not in i18n.read_po(
        (root / i18n.TRANSLATIONS / "日本語.po").read_text(encoding="utf-8"))[1]
    assert "Use one register throughout" in translate.PROMPT and translate.PROMPT_VERSION == 2


def test_a_language_from_the_table_is_filed_by_its_tag_with_its_name_note_and_direction(
        tmp_path, monkeypatch, capsys):
    course = tmp_path / "courses" / "demo-course"
    (course / "lectures").mkdir(parents=True)
    (course / "workspace.toml").write_text(
        'course = "demo"\n\n[llm.profiles.m]\nprovider = "deepseek"\nmodel = "x"\n'
        'api_key_env = "DEEPSEEK_API_KEY"\n\n[llm.roles]\nsetup = "m"\n', encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_DIR", str(course))
    monkeypatch.setenv("LECTURE_DIR", str(course / "lectures"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setattr(translate, "english", lambda folder=None: [translate.Message("Courses")])
    asked = []

    def chat(config, messages, **kw):
        asked.append(messages[1]["content"])
        return llm.Reply(json.dumps({"items": [{"n": 1, "text": "Courses"}]}), 1, 1, 0.1, "stop")

    monkeypatch.setattr(translate, "chat", chat)
    root = tmp_path / "courses"
    assert translate.main(["--code", "zh-Hant", "--root", str(root)]) == 0
    path = root / i18n.TRANSLATIONS / "zh-hant.po"  # the tag, lower case
    catalog, headers = i18n.read_po(path.read_text(encoding="utf-8"))
    assert headers["X-Name"] == "繁體中文" and headers["X-Direction"] == "ltr"
    assert headers["X-Note"] == language_table.by_code("zh-Hant").note  # the table's
    assert asked[0].startswith("Language: 繁體中文\n\n<note>\nTraditional Chinese, the written "
                               "standard as used in Taiwan.")
    assert i18n.available(root)["zh-hant"] == "繁體中文"
    assert i18n.from_browser("zh-HK", i18n.available(root)) == "zh-hant"  # likely script
    # Arabic: right to left by the table, whatever the strings so far say (all English here).
    assert translate.main(["--code", "ar", "--root", str(root), "--note", "formal"]) == 0
    _, headers = i18n.read_po((root / i18n.TRANSLATIONS / "ar.po").read_text(encoding="utf-8"))
    assert headers["X-Direction"] == "rtl" and headers["X-Note"] == "formal"
    assert i18n.translations_for("ar", root).direction == "rtl"
    # A tag not in the table is refused: the name is the way for such a language.
    assert translate.main(["--code", "tlh", "--root", str(root)]) == 2
    assert "not in the language table" in capsys.readouterr().err


def test_strings_kept_as_the_english_are_counted_apart_and_can_be_asked_again(
        tmp_path, monkeypatch, capsys):
    """A model gives "Docker" back unchanged (the same word), or every string (a language
    it would not write, 2026-11-10): the file keeps them, the report counts them apart from
    the translated ones, and `--again-unchanged` asks for those alone."""
    course = tmp_path / "courses" / "demo-course"
    (course / "lectures").mkdir(parents=True)
    (course / "workspace.toml").write_text(
        'course = "demo"\n\n[llm.profiles.m]\nprovider = "deepseek"\nmodel = "x"\n'
        'api_key_env = "DEEPSEEK_API_KEY"\n\n[llm.roles]\nsetup = "m"\n', encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_DIR", str(course))
    monkeypatch.setenv("LECTURE_DIR", str(course / "lectures"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setattr(translate, "english", lambda folder=None: [
        translate.Message("Courses"), translate.Message("Docker"),
        translate.Message("%(count)s batch", "%(count)s batches")])
    asked = []

    def chat(config, messages, **kw):
        asked.append(messages[1]["content"])
        items = [{"n": 1, "text": "课程"}, {"n": 2, "text": "Docker"},
                 {"n": 3, "text": "%(count)s batch", "plural": "%(count)s batches"}]
        if "Courses" not in messages[1]["content"]:  # the second run: those two alone
            items = [{"n": 1, "text": "容器"},
                     {"n": 2, "text": "%(count)s 批", "plural": "%(count)s 批"}]
        return llm.Reply(json.dumps({"items": items}), 1, 1, 0.1, "stop")

    monkeypatch.setattr(translate, "chat", chat)
    root = tmp_path / "courses"
    assert translate.main(["中文", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "batch 1: 1 translated, 2 kept as the English, 0 refused" in out
    assert "中文: 1 of 3 strings translated; 2 kept as the English by the model" in out
    catalog = i18n.read_po((root / i18n.TRANSLATIONS / "中文.po").read_text(encoding="utf-8"))[0]
    assert i18n.unchanged(catalog) == 2 and translate.missing(
        translate.english(), catalog) == []  # kept: a plain run asks for nothing more
    assert i18n.translations_for("中文", root).counts() == (4, 2)  # 4 entries: the plural's two
    assert translate.main(["中文", "--root", str(root)]) == 0 and len(asked) == 1

    assert translate.main(["中文", "--root", str(root), "--again-unchanged"]) == 0
    assert len(asked) == 2 and "Courses" not in asked[1] and '"text": "Docker"' in asked[1]
    catalog = i18n.read_po((root / i18n.TRANSLATIONS / "中文.po").read_text(encoding="utf-8"))[0]
    assert catalog == {"Courses": "课程", "Docker": "容器", ("%(count)s batch", 0): "%(count)s 批",
                       ("%(count)s batch", 1): "%(count)s 批"}
    assert "中文: 3 of 3 strings translated" in capsys.readouterr().out


def test_a_failed_model_keeps_what_was_translated(tmp_path, monkeypatch, capsys):
    course = tmp_path / "courses" / "demo-course"
    (course / "lectures").mkdir(parents=True)
    (course / "workspace.toml").write_text(
        'course = "demo"\n\n[llm.profiles.m]\nprovider = "deepseek"\nmodel = "x"\n'
        'api_key_env = "DEEPSEEK_API_KEY"\n\n[llm.roles]\nsetup = "m"\n', encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_DIR", str(course))
    monkeypatch.setenv("LECTURE_DIR", str(course / "lectures"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setattr(translate, "BATCH", 1)
    monkeypatch.setattr(translate, "english", lambda folder=None: [
        translate.Message("Courses"), translate.Message("Keys")])
    calls = []

    def chat(config, messages, **kw):
        calls.append(1)
        if len(calls) == 1:
            return llm.Reply(json.dumps({"items": [{"n": 1, "text": "课程"}]}), 1, 1, 0.1, "stop")
        raise llm.LLMError("HTTP 500 from the server")

    monkeypatch.setattr(translate, "chat", chat)
    assert translate.main(["中文", "--root", str(tmp_path / "courses")]) == 1
    assert "model: HTTP 500" in capsys.readouterr().err
    catalog, _ = i18n.read_po((tmp_path / "courses" / i18n.TRANSLATIONS / "中文.po")
                              .read_text(encoding="utf-8"))
    assert catalog == {"Courses": "课程"}
    assert Path(tmp_path / "courses" / i18n.TRANSLATIONS / "中文.po").is_file()
