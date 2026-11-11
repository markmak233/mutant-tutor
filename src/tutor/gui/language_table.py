"""The languages the Language page offers by name (owner, 2026-11-10: "is there a standard
table?"; 2026-11-11). A short built-in table, standard library only: each row a BCP 47 tag
as the IANA Language Subtag Registry spells it (language, script and region subtags:
`zh-Hant`), the language's name in itself as Unicode CLDR writes it (what the switch
shows), a sentence for the model where the tag alone leaves the variety or register open
(the Hong Kong file came back part written standard, part colloquial Cantonese when the
model was told only "zh_hk"), and whether it is written right to left.

A language chosen from this table gets its file named by the tag (`zh-hant.po`, lower
case: gui/i18n.py's slug), so the browser's Accept-Language finds it (`i18n.from_browser`
matches subtag by subtag), its name in the X-Name header, its sentence as the note to the
translator when the owner wrote none, and its direction in X-Direction. A language typed
by hand (one not here, or a made-up one) works as before: named and filed by what was
typed. The table is not a limit, only the common cases spelled right; add a row when a
language is wanted often.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A BCP 47 tag of the shapes used here: language (2-3 letters), an optional script (4
# letters, capitalised) and an optional region (2 letters, upper case), as the registry
# spells them. Checked by a test for every row, so a typo in a tag is caught at once.
TAG = re.compile(r"^[a-z]{2,3}(-[A-Z][a-z]{3})?(-[A-Z]{2})?$")


@dataclass(frozen=True)
class Language:
    code: str  # the BCP 47 tag, as the registry spells it
    name: str  # in the language itself (CLDR's spelling): what the switch shows
    note: str = ""  # for the model: the variety and register, where the tag leaves it open
    rtl: bool = False  # written right to left: <html dir="rtl"> (gui/i18n.py)


# In the order of the tags (owner, 2026-11-11: the drop-down alphabetical; Hong Kong and
# Cantonese rows taken out the same day). Notes only
# where a tag alone would leave the model a choice that showed in a file (register,
# script, regional words).
LANGUAGES: tuple[Language, ...] = (
    Language("ar", "العربية", "Modern Standard Arabic.", rtl=True),
    Language("bn", "বাংলা"),
    Language("cs", "Čeština"),
    Language("da", "Dansk"),
    Language("de", "Deutsch"),
    Language("el", "Ελληνικά"),
    Language("es", "Español",
             "Spanish in a neutral register readable in Spain and Latin America."),
    Language("fa", "فارسی", rtl=True),
    Language("fi", "Suomi"),
    Language("fr", "Français"),
    Language("he", "עברית", rtl=True),
    Language("hi", "हिन्दी"),
    Language("hu", "Magyar"),
    Language("id", "Indonesia"),
    Language("it", "Italiano"),
    Language("ja", "日本語"),
    Language("ko", "한국어"),
    Language("ms", "Melayu"),
    Language("nb", "Norsk bokmål"),
    Language("nl", "Nederlands"),
    Language("pl", "Polski"),
    Language("pt-BR", "Português (Brasil)", "Brazilian Portuguese."),
    Language("pt-PT", "Português (Portugal)", "European Portuguese."),
    Language("ro", "Română"),
    Language("ru", "Русский"),
    Language("sv", "Svenska"),
    Language("ta", "தமிழ்"),
    Language("th", "ไทย"),
    Language("tr", "Türkçe"),
    Language("uk", "Українська"),
    Language("ur", "اردو", rtl=True),
    Language("vi", "Tiếng Việt"),
    Language("zh-Hans", "简体中文",
             "Simplified Chinese, the written standard as used in mainland China."),
    Language("zh-Hant", "繁體中文",
             "Traditional Chinese, the written standard as used in Taiwan."),
)

# CLDR's likely script for a Chinese region tag without one: what a browser sends as
# "zh-TW" or "zh-HK" is the Traditional Chinese file, "zh-CN" the Simplified one.
LIKELY_SCRIPT = {("zh", "tw"): "hant", ("zh", "hk"): "hant", ("zh", "mo"): "hant",
                 ("zh", "cn"): "hans", ("zh", "sg"): "hans", ("zh", None): "hans"}


def by_code(code: str) -> Language | None:
    """The row whose tag is `code`, case apart ("zh-hant" finds zh-Hant)."""
    wanted = code.strip().lower()
    return next((x for x in LANGUAGES if x.code.lower() == wanted), None)


def subtags(tag: str) -> tuple[str, str | None, str | None]:
    """(language, script, region) of a tag, lower case, from its shape alone (a script is
    4 letters, a region 2 letters or 3 digits); what it does not have is None."""
    parts = tag.strip().lower().split("-")
    language, script, region = parts[0], None, None
    for part in parts[1:]:
        if len(part) == 4 and part.isalpha() and script is None and region is None:
            script = part
        elif (len(part) == 2 and part.isalpha()) or (len(part) == 3 and part.isdigit()):
            region = region or part
    return language, script, region


def likely(tag: str) -> tuple[str, str | None]:
    """(language, script) of a tag, the script filled in from LIKELY_SCRIPT when the tag
    has none: "zh-TW" is (zh, hant), "zh-hant" (zh, hant), "de" (de, None)."""
    language, script, region = subtags(tag)
    return language, (script or LIKELY_SCRIPT.get((language, region))
                      or LIKELY_SCRIPT.get((language, None)))


def candidates(tag: str) -> list[str]:
    """The file names (lower-case tags) a browser's tag may mean, the closest first:
    the tag itself, then with the likely script filled in, then shorter forms down to
    the language alone. `from_browser` tries them in this order against the files."""
    language, script, region = subtags(tag)
    script = likely(tag)[1]
    found = [tag.strip().lower()]
    for form in ((language, script, region), (language, script, None), (language, None, region),
                 (language, None, None)):
        name = "-".join(part for part in form if part)
        if name not in found:
            found.append(name)
    return found
