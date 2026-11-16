# Design: GUI: settings and environment

Models and providers, and the Environment page. Part of the design record; the index of every part is [README.md](README.md).

- Settings (step 6, owner, 2026-11-06):
  - Keys in the repository's `.env`, the file the commands already read, not the OS
    keychain for now (that would need a dependency and a change to how llm.py reads keys,
    and Linux without a desktop has none). The user adds and removes keys by name, any
    key-like name (`…_KEY`, `…_TOKEN`, `…_SECRET`) or one a profile names; WORKSPACE_DIR
    and the other settings in the file are never listed or touched. A page shows the last
    4 characters of a long key and never sends one back, even in a refused form. The file
    is parsed as text, never loaded into the GUI's environment, kept line for line
    otherwise, replaced in one step and readable by its owner only. A key also set in the
    environment the GUI started from wins (load_env's rule), and the page says so.
    "Used by" lists each course once, with that course's models after it. Adding a key
    (owner, 2026-11-10): choosing a provider fills the name with the variable its key
    goes in, from the models.dev list kept for the model forms (`catalog.py`'s `key_env`);
    the list is only read, by a GET of the page, and a typed name still works, as it must
    when no list is kept yet. A provider whose key is set already says so and the form
    replaces it. A "not set" link opens the form with the name filled. The provider is
    not guessed from the key's shape: a plain `sk-` key fits several. Since 2026-11-12
    the keys are on the Providers page ("Shared models", below): a provider card's key
    on its card, the others under "Other keys"; `/keys/` leads there.
  - A course's Models page writes its workspace.toml's [llm] with tomlkit (owner), which
    keeps the owner's comments; a changed value loses only its own line's comment. Every
    save is checked before the file is replaced: read back with tomllib, [llm] must be
    exactly what was intended, worked out from the old data and not from tomlkit, and every
    other table unchanged; the file before is kept as `workspace.toml.bak`. A new key in a
    table goes after its last key, not after the comments that end it and belong to the
    next table (tomlkit's `Container._insert_after`, pinned by a test). Profiles are
    checked by llm.py's own rules; roles by generation's (the oracle from another provider
    than generate and repair); a profile in use cannot be removed, and a renamed one takes
    its roles along. The checked write is `gui/toml_edit.py` since step 7a, which writes
    [requires] and [isolation] the same way, and since 2026-11-12 the shared `llm.toml`
    (`edit_path`). Since that day the page writes only [llm.roles]: the models are shared
    ("Shared models", next), a course's own ones from before are edited there until moved,
    and "Copy from another course" is gone (a new course chooses roles from the shared
    models).
  - Shared models (owner, 2026-11-12: "the models span every course; roles stay each
    course's"). Every model lives once, in `llm.toml` beside the courses (the folder the GUI
    lists them in, a course's parent; the CLI finds it the same way): `[providers.NAME]`
    are the provider cards, an endpoint (`kind`, the provider llm.py knows, left out when
    the name is one; `base_url`), its key's name, its `parallel_start` and its
    `max_parallel` (now truly one cap per provider), and `[profiles.NAME]` the models, each
    naming its card in `provider` (or a kind, as before) and free to set its own
    `parallel_start` (DeepSeek's flash and pro may differ). A course keeps only its
    `[llm.roles]`; roles are not shared and not copied, a new course chooses its own. No
    model of a course's own any more (the owner: "to use another model in one course,
    choose it in that role"): a profile still in a course's workspace.toml, from before,
    is read first so a course nobody has moved works as it did, but the pages write none
    there, and a "Move to shared" offers the move (equal profiles become one; one name
    with two contents stays and is shown). The Keys page goes into the Providers page
    (keys no card uses under "Other keys"); "Copy from another course" goes. Built so far
    (4b): `llm.section()` merges the two, each profile with its card's fields, and every
    page that reads which model does what reads it (generation, lectures' vision model,
    environment's setup model and chat profiles, roles). (4c-1) The Providers page
    (`/providers/`, the header's link; `gui/providers.py`): one card per provider with
    its kind, address (its own or the kind's), key (set, replaced and removed in place,
    by name, shown by its tail), start and cap, and its models under it with the course
    roles naming each; a card form with Apply filling the kind's address, as the model
    form's; a card no model names can be removed; shared models that name a kind
    directly (written by hand) are listed as "Models with no card". The Keys page is
    gone: `/keys/` sends to the Providers page's "Other keys" (keys no card names, with
    the courses' own models naming them, and the Add-a-key form). llm.toml is written
    through `toml_edit.edit_path`, the same checked write as workspace.toml's (backup
    `llm.toml.bak`). Clicked in Chrome on a scratch root
    (`notes/gui-checks/providers_click.py`). New provider fills from a list as New model
    did (owner, 2026-11-12, after the first use: "where is the models.dev choice?"): the
    servers on this machine and the kept models.dev list, an entry giving the card its
    name (the id), its kind (the one llm.py knows for it, else custom), its address (a
    known kind's own, else the list's, with the check-the-domain note) and its key's
    variable; a card that exists is marked in the list. (4c-2) A card's models are added, edited and
    removed on this page (`/providers/p/<card>/models/new`, `/providers/m/<model>/`): the
    card fixes the provider, address and key, so the form has only the model's own fields
    (the provider's name for it, the two reasoning efforts, the time limit, temperature,
    and its own `parallel_start` when it should differ from the card's); above a new one,
    the provider's models (a server on this machine lists its own at the card's address;
    a cloud card's come from the kept models.dev list, the entry at the card's address,
    else the first of its kind; custom has none), "Use" filling the form as the course
    page did. Check and Measure start run as a task of the first course whose roles name
    the model (else the first course; a task and its log live in a course) and come back
    to this page. A model a course's role names cannot be renamed or removed here (the
    roles are in the courses' files, which this page does not write): the row says who
    names it. A course's Models page adds no model any more; its button leads here, and
    a model still in the course's own file is edited there until it is moved. Clicked in
    Chrome with Ollama (`notes/gui-checks/shared_model_click.py`): the server's list,
    Use, Save, Check as the course's task (ok in 2.3 s), edit, Remove refused while named
    and done after. (4d) "Move to shared" (`gui/move_models.py`): a course whose own file
    still has models shows them in a box on its Models page with the move's plan: each
    profile's card is the shared card of its kind at its address (its own base_url, else
    the kind's), else a new one named after the kind (`kind-2` when that name is a card at
    another address or with another key), which takes the key's name from the first
    profile that made it; a profile whose key's name differs from its card's stays (one
    address, one key: the owner decides), as does one whose shared namesake differs
    (equal ones merge, nothing written); an assistant profile moves with no card. Two
    clicks (a box, then the button); the shared file is written first through the checked
    write, the course's own only after that went in, dropping the moved profiles and
    keeping the roles, so a failure between the two leaves the models in both files,
    which is harmless. The roles' choices list every model the course can use, the
    shared ones and its own not moved yet (marked); "Copy from another course" is gone.
    Clicked in Chrome on a scratch course with four providers
    (`notes/gui-checks/move_models_click.py`): the box, the unticked refusal, the move,
    the two files, the Providers page's cards with "Used by", the roles saved after.
  - Filling New model from models.dev (owner, 2026-11-07: the form was plain, with only
    the providers llm.py knows by name; "custom" meant finding and typing a base URL). The
    public catalog at `https://models.dev/api.json` (MIT, no key) lists providers with
    their base URL and key variable, and their models with release date, thinking and how
    far it can be turned down, image input, context and USD price. Downloaded only on
    "Fetch the list" / "Refresh the list" (beside the provider choice), never by itself;
    the request carries nothing of the courses. Kept in `<root>/.cache/models-dev.json`,
    reduced to what is used (about 1.2 MB, 204 providers of 235): the providers models.dev
    marks as taking OpenAI's requests, plus those it lists with their own SDK whose
    OpenAI-compatible URL is checked in their docs (`catalog.OPENAI_URLS`: Gemini, xAI,
    Mistral, Groq, Together, MiniMax, OpenRouter); the rest are counted on the
    page. A downloaded value is checked before it is kept (https, or http on this machine;
    a key name in capitals; a plain model id) and shown escaped. Above the hand-written
    form, which stays: a provider (common ones first, the key's state in `.env` by name),
    its models newest first without withdrawn ones, and "Use", which fills the form and
    saves nothing. A provider llm.py knows (DeepSeek, Zhipu and z.ai as `glm`, OpenAI,
    Anthropic since 2026-11-10, LM Studio) keeps its own way to turn thinking off; another
    one is `custom` with its URL, and a thinking model gets the lowest `reasoning_effort` it
    takes (none, minimal, low), or a note that each call thinks. No script: choosing a
    provider is a GET form, so the page can be reloaded. A `custom` profile at Anthropic's
    own address is refused with the advice to choose `anthropic` (the rule every known
    provider's address has).
  - The pages in another language (owner, 2026-11-07 asked; 2026-11-10: "I ship no
    translation; a Language page has a model write the language you want, and the switch
    goes back and forth, English included"; `gui/i18n.py`, `gui/translate.py`, the Language
    page). The pages' own text goes through gettext: `_()` and `{% trans %}` in the
    templates (`{% pluralize %}` for a count), `N_()` on a module-level English string
    (the notices), which the page translates when it shows it. Not translated: a task's
    output, `tutor doctor`'s lines (the command's own), what a model wrote, file names and
    TOML keys, the exercise files VS Code opens. At run time only the standard library's
    `gettext` and Jinja's `i18n` extension: a language is `<root>/translations/<name>.po`
    (the name as the owner typed it, "中文"; its header keeps it for the switch), read by
    this tool's own small .po reader, so no binary `.mo` and no Babel. The English strings
    are extracted from the pages and modules themselves (Jinja's own `babel_extract` on
    the templates, an `ast` walk for the calls in the modules), so nothing is kept in
    step by hand. "Translate into" on the Language page runs `python -m tutor.gui.translate
    NAME` as a task of the course whose setup model it asks (paid, so on that click only;
    the page says about how many thousand tokens): batches of 60 strings as a numbered
    JSON list, a schema for the answer, and each translation kept only when `i18n.safe`
    passes (the same `%` placeholders, as Jinja formats a translation with `%` and a stray
    one would fail the page; the same `<...>` pieces, so no markup of the model's own);
    a refused or unanswered string stays English and is asked again next time, as a run
    asks only for what the file lacks, so a page changed in English never breaks another
    language; a string the model gives back unchanged ("Docker") is kept so, as done,
    else it was asked again on every run and the count never reached the total
    (2026-11-10), but counted apart, "kept as the English by the model", in each batch's
    line, the run's last line and the Language page's line for the file (`i18n.unchanged`:
    an entry whose text is its msgid), since it may be the model declining rather than the
    word being the same (2026-11-10: a made-up language, 喵语, got all 165 strings back
    and the run said "165 of 165 translated"); `--again-unchanged` asks for those alone,
    say on another model (2026-11-11). A note to the translator (owner, 2026-11-10: the
    Hong Kong file came back part written Traditional Chinese, part colloquial Cantonese,
    as each batch was asked alone; 2026-11-11): the Language page's optional box, `--note`,
    the owner's wish about variety and register, kept in the file's `X-Note` header and
    sent with every batch of every run (a run without one sends the file's), between
    `<note>` tags after the language line and before the material, so the system prompt
    stays the same for every call and the language's calls share their prefix (prompt
    cache); the system prompt names the note as the owner's wish about wording that
    changes none of the rules, and asks for one register throughout, the written standard
    unless the note says otherwise (prompt version 2, in `X-Generator`; not a pinned
    prompt). A built-in language table (owner, 2026-11-10 "is there a standard table, a
    UN one?"; 2026-11-11, `gui/language_table.py`): the standard is BCP 47 (IETF), tags
    made of ISO 639 language, ISO 15924 script and ISO 3166 / UN M.49 region subtags, the
    valid ones listed in IANA's Language Subtag Registry, the names in Unicode's CLDR;
    no library ships either in the standard library, and the GUI needs a few dozen rows,
    so a short table by hand: the tag as the registry spells it, the name in the language
    itself as CLDR writes it (the switch shows it), a sentence for the model where the
    tag leaves the variety or register open (zh-Hans and zh-Hant, the written standards
    of the mainland and of Taiwan; es neutral;
    pt-BR and pt-PT), and the direction. The Language page's form offers the table in a
    drop-down and keeps the text box for a language not in it (no script of our own: the
    box is always there, the server takes the table's choice when there is one). A table
    language is filed by its tag in lower case (`zh-hant.po`; `--code TAG`), its name
    in X-Name, the table's sentence as the note when the owner wrote none, and the
    table's direction; a typed name is filed by its slug as before. The browser's
    Accept-Language is matched subtag by subtag against the files (`from_browser`): the
    tag, then with CLDR's likely script for a Chinese region ("zh-HK" is zh-hant),
    then shorter forms down to the language, then a file of the same language and script,
    then of the same language ("pt" finds pt-br). The files written before by name
    (`zh_hk.po`) keep working under their names; renamed to the tag, the browser finds
    them too. The model to ask (owner, 2026-11-10: the drop-down offered each course's
    setup model and all four were the same Sonnet; 2026-11-11): every course's chat
    profiles (`environment.chat_profiles`: the embedding role's left out), the same
    provider and model in several courses listed once under the first, the first
    course's setup model chosen; the task runs in the course that owns the profile, with
    its key (`--profile`). A language is deleted from the same list (owner, 2026-11-11:
    "two clicks"): a required box then the button, as the GUI's other removals; the file
    goes for good (a paid translation with it, hence the box), English is chosen when it
    was the one in use; the row's form is on a line of its own, at the end side. Jinja's
    new-style `_()` marks what gettext returns as safe HTML (so the
    values it formats in are escaped), translated or not, so the callables escape the
    text first: an English string may say `<profile>` and shows as written; a literal `%`
    in a page's English is written `%%` (a test checks every string). The language: the
    owner's choice on the footer's switch, kept for every course in
    `<root>/.tutor-gui-settings.json`, else the browser's Accept-Language when a file of
    that code exists, else English. Tried on Ollama's qwen3 0.6B for the click check
    (59 of 82 strings; the rest stayed English): the path, not the quality. Plural forms:
    two for every language (the same text twice where a language has one). Every
    template is wrapped (2026-11-10: 880 strings), and the Python side too: the form
    problems a module returns are looked up with `gettext_` inside the request, the
    state words a chip shows (task statuses, plan and item states, doctor's statuses,
    the [requires] kinds) and the explanations are `N_()` strings, so the files and the
    comparisons keep the English and the page shows `_(state)`; every abort message is
    `N_()` and the error handler translates it with gettext alone (a command's text may
    carry a `%`). A sentence with a link or `<code>` inside is one string, its pieces
    passed in as captured blocks (`{% set code_x %}<code>…</code>{% endset %}`, which
    escape their content), so a translator sees the whole sentence. Still English:
    the language rows' titles and roles (`tutor.languages`, a core module), a
    throwaway `_` must not be used in a module that imports gettext as `_`. Right to
    left (owner, 2026-11-10 "Arabic also needs the layout mirrored"; 2026-11-11): every
    page is `<html dir="rtl">` for such a language and the stylesheet names no physical
    side (start and end, `margin-inline-start`, `inset-inline-start`; a test refuses
    `left` and `right`), so the browser mirrors the pages from that one attribute;
    code, paths, commands and a task's log are `direction: ltr` and bidi-isolated, as
    they read the same in every language. The direction comes from the file's
    `X-Direction` header (`rtl` or `ltr`, written by `tutor.gui.translate`), else read
    off the translations themselves (the side most of their first strong characters
    are written from, Unicode's bidirectional classes R and AL against L; the name
    decides a tie only, so a name typed as a code, "ar", does not outvote the text), so
    a hand-written file needs no header and nothing new on the form. Checked in Chrome
    with a hand-written Arabic file (nine strings, no model; `notes/gui-checks/
    rtl_click_test.py`): the header, buttons, lists, tables and forms mirrored with no
    horizontal overflow, `<code>` left to right; an arrow in a string ("Lectures →") is
    the translator's to turn, as it is text. The question itself is not mirrored
    (owner, 2026-11-11, the one exception): the article is `dir="ltr"`, as it is the
    model's text in the language it was written in, not the pages'.
  - Light or dark by choice (owner, 2026-11-11 "override the night mode"; `gui/theme.py`).
    The stylesheet followed the system's `prefers-color-scheme` alone, two blocks of the
    same variables. Now each colour is defined once as `light-dark(light, dark)` under
    `color-scheme: light dark`, so the system still decides by default (and the browser's
    own parts, form controls and scrollbars, follow), and the footer's Theme switch
    (System, Light, Dark, beside the language) writes `<html data-theme="light|dark">`,
    which the stylesheet turns into `color-scheme: light` or `dark`: one attribute, no
    script. The choice is kept for every course in the GUI's settings file with the
    language (`i18n.save_setting` keeps the other keys). The footer's switches return to
    the page they were on less its one-shot `notice` (`gui_back`), so a notice is not
    shown twice and a forged one is not echoed. Tick boxes take the accent colour and a
    size that sits on the text's baseline (`accent-color`).
  - Servers on this machine in New model (owner, 2026-11-07 "WSL and Mac llama, those
    local ones, later"; 2026-11-10). models.dev lists only LM Studio of them. The form's
    provider choice now starts with an "On this machine" group (Ollama, LM Studio,
    llama.cpp's llama-server, vLLM; `catalog.LOCAL`), there without the models.dev list,
    each a provider of llm.py's at the port its docs give (11434, 1234, 8080, 8000) with
    the request field its docs give for thinking off (Ollama `reasoning_effort: "none"`;
    llama-server and vLLM `chat_template_kwargs: {enable_thinking: false}`, llama-server
    then `reasoning_budget: 0`). "List its models" asks the server itself, `GET /v1/models`
    with no key (`catalog.installed`): what it has loaded or pulled, which models.dev could
    not know; that call goes only to localhost, so the page never makes the GUI's server
    call out. The server's list does not say whether a model thinks or reads images (the
    table says "not said"). The address beside the choice can be changed (another port,
    say) and the server listed again, and "Use" carries it into the form (owner,
    2026-11-10); a server in WSL is reached from Windows at localhost too (WSL 2 forwards
    it; written from the docs, to be clicked on the owner's PC at the packaging check).
    What makes a local server unlike a cloud one: it ignores a request field it does not
    know instead of refusing it with 400 (Ollama 0.30.2 and `think: false` on its OpenAI
    path, tried 2026-11-10), so chat()'s try-the-next-switch never runs and thinking stays
    on unnoticed: slower, and the tiny `check` reply comes back empty, the whole 32 tokens
    spent thinking. So every reply now counts the thinking it carried
    (`Reply.reasoning_chars`: `reasoning_content` or Ollama's `reasoning` beside the
    content, or a `<think>` block a server left inside it; streamed pieces too; in the
    call log), and Check says "thinking stayed on (N characters of it)" with what to set.
    Tried on Ollama with qwen3 0.6B: the built-in switch answers "ok" in 0.7 s; a switch
    it ignores gives the empty reply and the line. llama-server and vLLM are tested with
    stand-ins only (neither is installed here with a model).
  - The models.dev list is downloaded data (owner, 2026-11-07, "not now"; done
    2026-11-10). A changed base URL or key name in it, filled by "Use" and saved, would
    send that key and the course's text to another host on every call. Three guards: a
    provider this tool knows (`catalog.KNOWN`, `OPENAI_URLS`, LOCAL) always gets this
    tool's own URL, whatever the list says (`Provider.own_url`; z.ai's own URL is a
    constant, as it differs from Zhipu's), and the page marks the others "address from
    models.dev: check the domain before saving"; a fetch keeps which providers' URLs differ
    from the copy before (`url_changes`, shown on the page until the next fetch: a provider
    does move, a list can be tampered with); and saving a profile is refused when its key
    name is used by any profile of any course at another host (`llm_settings.key_elsewhere`:
    one key goes to one provider; the same host at another path passes, a key-less local
    profile is not compared). The kept list is format 3 (one kept before is fetched again).
  - One provider's oracle (owner, 2026-11-07: "not everyone buys several APIs; do not
    forbid it, at least warn"). The oracle at the same provider and address as generate or
    repair was refused, by the Models page and by the graph before any call. It now runs,
    with a note at the run's start and a warning on the page: an oracle call still reads
    the question alone, apart from the exercise, while with no oracle role (always
    allowed) the generating model writes oracle.py in the same reply, the least
    independent; refusing the better of the two pushed one-key users to the worse. Still
    refused: one assistant profile for both, whose requests would mix in one queue. Left
    as it was: when rule 1 fails and rule 4 agrees, repair is told oracle.py was written
    by "another model", which with one model is not quite so; that text is part of a
    calibrated prompt, so it stays.
  - One maker's oracle (owner, 2026-11-07: "a whole family gives no warning"). Saving
    roles refused an oracle at the same provider and address as generate or repair, as
    the graph did; the same maker's model through another site (GLM at bigmodel.cn and
    z.ai, DeepSeek directly and through OpenRouter) passed, though it is no independent
    reading either. The maker comes from models.dev's canonical model id ("zhipuai/...";
    for about 85% of its models), else an aggregator's id that starts with a known maker
    ("deepseek/..."); the kept list now has it (format 2: a list kept before is fetched
    again). One model, one provider and one maker are each a warning, said while the
    roles are being chosen: htmx asks a read-only route for the part above Save whenever
    a role changes (hx-get on each select; no script of our own), with "Save even so" to
    tick. Without htmx, a save of changed roles that have a warning comes back held, the
    roles as chosen in the form; once saved, the page keeps saying so. A model the list
    does not know is not compared by maker. A role set to none is
    not compared, and repair set to none is generate. The graph's own check stays as it
    was: it runs without the list. With no script the page cannot warn while a role is
    being chosen, so each model's provider and maker follow its name in the role choices
    and in the table (owner, 2026-11-07). A save that is refused or held back keeps the
    roles as chosen in the form (they were reset to the file's, which looked wiped). The
    GUI never fetches the list by itself; a list kept in an older format is named as such,
    with its date, and the page asks to fetch it again.
  - The form follows its provider (owner, 2026-11-07: an empty base URL under "deepseek"
    read as a contradiction). A provider llm.py knows shows its own base URL in the field,
    editable for another site of the same provider (z.ai for glm), and its way to turn
    thinking off in grey; saving leaves its own URL out of the file, so a change in llm.py
    reaches it. custom requires a URL; assistant greys out the key, the URL and More. With
    no script, Apply beside the provider sends the whole form back by GET, nothing typed
    lost, and replaces a known provider's own URL with the new provider's. A save whose URL
    is another known provider's own (changed without Apply, or a known one as custom) is
    refused, saying which. With htmx the provider's choice swaps the fields at once (the
    page as Apply would send it, cut to those fields with hx-select), so Apply is only the
    way without it. Base URL moved out of More, as it decides where calls go. Enter
    still saves: an off-screen Save is the form's first button. A new model's time limit
    is filled in as 900 s (owner, 2026-11-07; every cloud profile had been given it by
    hand, as plan and generate calls with thinking run past llm.py's 300 s); editing a
    model shows what its file has.
  - "Check" (owner): `python -m tutor.llm check [PROFILE ...]`, as a task, sends each model
    one tiny request (thinking off, 32 output tokens) and says ok or the provider's reason,
    so a wrong key or model name shows before a run that spends more. An assistant profile
    has nothing to check; a profile only the embedding role uses gets an embedding call.
  - Not in this step: the `[generation]` tunables (their defaults are measured values).
- Environment page:
  - Optional host setup guide (2026-11-09): step 2 links to a packaged, read-only page
    with explicit macOS/Linux/Windows/WSL navigation. It reads course requirements and
    the existing language registry; no probes, lecture reads, model requests or installs.
    Python links back to venv install/check/switch; C links to its existing settings.
    Declared Java/Hadoop get manual guidance derived from docs/setup/bigdata.md, with
    official sources and coverage limits for other versions. No new language support,
    system installer. Doctor connects this guide to measured course checks and repair paths.
    Container practice needs no host JVM/compiler.
    Linux/WSL fresh-install testing is still outstanding. Templates are packaged so the
    page does not depend on a source checkout's docs directory.
  - The page is four numbered steps in the order they are done (owner, 2026-11-06, after
    trying it on a new course: by topic it read unclearly): (1) what the course needs, the
    model's draft then the list; (2) install on this machine, the check below its button;
    (3) where tests run; (4) the test image, dimmed and "not needed" on the host. Each says
    whether it is done; steps 2 and 4 say so in their htmx parts.
  - Environment controls (started in step 7a, 2026-11-06; integrated 2026-11-09), one page
    per course, with deterministic checks and explicit optional model repair:
    - Step 2 automatically loads the same deterministic Doctor rows as `tutor doctor`,
      without smoke or model calls. Check / Result / What to do columns use OK, Needs
      attention, Problem and Skipped, with duplicate rows removed. Doctor's host requirement
      probe uses the course's `.venv`; a missing/broken venv produces one failure and skips
      those requirements. Known failures have their existing fix buttons on the row:
      install, build, Python switch, host guide or runner choice. An uncovered Problem
      alone offers the four-screen model-fix wizard described in "Version conflicts".
      Running tasks disable fix actions. The initial page loads probes through htmx;
      read-only checks never start a model, install or worker task. The GUI server never
      loads credentials into its environment.
    - "Exercises may import" (owner, 2026-11-06): each pip row of the list has a tick and the
      module it is imported as; the ticked ones go into `[safety] allow_imports` in the same
      checked write as the version declarations, an unticked one comes out, and modules no
      row names (an OS course's `subprocess`) stay, listed under the table. Unticked by default: widening
      the static check is the owner's call, package by package. The module box starts with
      what the lectures' code imports it as when the draft saw it (scikit-learn is sklearn),
      else the name with `-` as `_`. The generate and repair prompt names them as importable
      next to the core modules (owner, 2026-11-06), or the model would be told it may not use
      what the check lets through. A course with none gets the same prompt text as before
      (checked by its hash), so its prompt cache holds. The oracle's prompt keeps the core
      modules only, to stay independent of the exercise; an exercise whose answer needs the
      library itself may then get no oracle, to decide when one does.
    - Step 1 has one version cell per package, accepting a prefix such as `3.5` or a
      restricted numeric comparison such as `>=1,<2`. Prefixes stay in `[requires]`,
      comparisons in `[package-bounds]`; changing form moves the entry in one checked
      toml_edit write with a `.bak`, preserving unrelated tables/comments. Package identities
      cannot appear twice across spelling variants or declaration tables. Python, java,
      hadoop and gcc accept prefixes only; Python/C versions remain in the language list.
      Language-version and draft-accept saves preserve unrelated bounds. Import consent is
      saved in that same transaction. "+ Add a row" and "−" change the form using htmx;
      Save applies it. Empty rows are ignored, incomplete/invalid rows return as typed.
      The shared envcheck rules still require a JVM for pyspark/hadoop, and container
      courses require a supported Hadoop recipe. Each pip name links to its PyPI page.
    - "Install into .venv" runs `tutor.workspace init` again as a task, which makes the
      `.venv` if missing and installs this package and its declared dependencies (the same
      command a command-line user runs). The page shows first the pip command, where it installs,
      that it needs no admin rights and changes nothing outside the course folder, that it
      downloads from PyPI, and how to undo it.
    - Where tests run: container or host, written to `[isolation] runner` only when chosen;
      host only with a tick saying generated code then runs with the user's rights. A new
      `[isolation]` table goes after init's commented lines, its header saying the page set it.
    - The test image: Docker missing, not running, image missing or out of date, ready with
      its size. "Build image" runs `tutor.container build` with the course's venv, so the
      image takes its package versions; before it, the page shows the recipe
      (`python -m tutor.container recipe`, in that venv), each download and its source, the
      sizes measured so far for scale, and how to remove the image.
    - Step 4 loads build time and course-venv/image version differences in a separate
      read-only fragment; a course tested on this machine has no image, so none is shown.
      Its times are stored in UTC and shown like every other time on the pages: this
      machine's clock, to the second (`tasks.local_time`; owner, 2026-11-10).
      Differences are information; unmeasured versions stay unknown.
      Old records never produce a current comparison. Missing/old/invalid/unavailable records
      offer the existing metadata-only Refresh action; the full record/history page is
      linked here only. An unbuildable recipe keeps a GUI path back to step 1 instead of
      failing the history page. Legacy images keep unknown build/base facts until rebuilt.
    - Python recovery offers installed compatible interpreters through the existing guarded
      switch. Only when none is compatible does it show "No suitable Python here. Download
      one with uv", linking to the existing explicit-consent download page. A compatible
      current interpreter counts; a package-only repair does not imply a Python search.
      The host guide stays inside step 2. No setup links sit above the four steps, whose
      intro states their order only. System-tool installation remains manual.
    - Original step-7a Chrome check (2026-11-06) on a scratch course: a row added, host refused
      without the tick, container saved, the install (2 s) and the build (8 s, cached layers, 0.18
      GB) run as tasks, the check then ok; the three real courses only opened. The clicks
      made on the Environment/Doctor integration, and those still owed, are listed in
      "Version conflicts".
  - Built in step 7b (owner, 2026-11-06): a model drafts `[requires]` from the lectures
    (`python -m tutor.materials.requires`, a task from the page's button, which names the
    model and the input tokens; only on that click, as it may cost money).
    - The model: the new `setup` role's, else `plan`'s, else `generate`'s, so nothing new has
      to be set up. One request per cached lecture, the fixed instructions first (for the
      provider's prompt cache) and the lecture after; thinking off; a JSON schema: kind
      (pip, python, java, hadoop, other), pip name, module imported, version only if the
      page states one, page, and the page's own words that show it.
    - The answer is stored as given, `cache/requires/<lecture>.json`, keyed by the lecture's
      text and the prompt version: a lecture is asked once, and again only when it changes.
      The checks run each time the draft is read, so a better check needs no new call:
      the quote must be on its page (spaces aside) and name the software (the pip name or
      the module, as a whole word: "six" is not in "sixty"; "other" more loosely, as it is
      only shown); the standard library goes (`sys.stdlib_module_names`); versions keep
      their dotted numbers ("3.5.x" is 3.5, Java "1.8" is 8, as envcheck reads it).
    - A pip name its quotes do not show (`import cv2` for opencv-python) is the model's
      knowledge, flagged "name from the model", with its PyPI link: a made-up name can be
      one somebody registered. One piece of software across lectures is one candidate with
      every source; versions stated differently are all shown.
    - The page lists candidates with a tick, a version box, what [requires] holds now (new,
      listed, listed with another version) and the sources; nothing is written until the
      owner ticks entries and adds them, by 7a's rules (a pyspark without java is refused).
      The box starts at the stated version's major.minor (3.5.1 as 3.5; Java its major),
      the slide's own beside it. Software envcheck cannot check (a database, CUDA) is only
      listed. `[safety] allow_imports` is not drafted: widening the static check is a safety
      decision, and the page says a package exercises import must be added there by hand.
    - A course without models gets no button: the GUI does not read `.env`, so it cannot tell
      whether the LLM_* variables would answer.
    - Prompt version 2 (owner, 2026-11-07): only software that builds, runs or tests the
      course's code on the student's own computer, a compiler, build tool and debugger
      named among them. A C course's first lecture had brought in what its lab machines
      are reached with (PuTTY, WinSCP, X2Go, ssh), its editors (vi, emacs) and commands
      every Unix-like system has (tar, nohup); these are now left out by name. Such
      entries were "other", only listed and never installed, so the change cleans the
      list; the new version asks every lecture again on the next click.
    - Prompt version 3 (owner, 2026-11-07, with C): a kind of its own, "gcc", for a C
      compiler (gcc, cc or clang), so the C compiler is a tickable `gcc` row rather than an
      "other" only listed; its quote must name gcc, cc or clang as a word of its own ("cc" is
      in "access"), and its box starts at the major version, as Java's. Every course's stored
      answers are then out of date and the page offers to ask again; [requires] itself is
      untouched.
    - Ask again (owner, 2026-11-07): once lectures are looked at, the page still offers
      "Look at all N lectures again" (`--again`), with the input tokens that sends, after a
      change of model or to compare; it was offered only for lectures not yet asked.
    - Measured on the private deep learning course, with GLM-5.3-Flash (free): 16 lectures,
      157k tokens in and 715 out, 10 candidates, one dropped (a plural, before "other"
      matched loosely). It found torch, numpy and scikit-learn with the versions the slides
      state (scikit-learn from `sklearn`, so flagged), pandas, torchvision and a newer
      Python than the course's [requires], which held only python; matplotlib, which the
      course's venv holds, is in no slide. Nothing was added: that is the owner's click. The
      page was checked by a click in Chrome on a scratch course, its run button with a
      stand-in command.
  - Infer requirements from course material: an LLM reads lectures and labs for named tools
    and versions and drafts `[requires]`; the owner confirms before `workspace.toml` is
    written.
  - Show `tutor.envcheck` results with a fix hint and a link to `docs/setup/<course>.md`.
  - Installation is not only pip: a course may need a JDK, Hadoop, a database, Node, R or a
    specific Python, and each can be installed several ways (brew, apt, winget, a tarball,
    SDKMAN, conda, a container). The method is chosen per case from what the page detects:
    OS and architecture, WSL or not, which package managers exist, conda vs venv, what is
    already installed (e.g. a JDK 21 next to the wanted 17) and whether admin rights are
    needed. Recipes are curated per component and platform, built from commands verified in
    `docs/setup/`; the LLM only picks among them, and when several fit, the page shows the
    options with their trade-offs instead of deciding silently.
  - Every command is shown before it runs, with a "check before executing" reminder and,
    for each one: what it does, where it installs, whether it needs sudo / admin, what it
    changes outside the project (PATH, JAVA_HOME, shell profile), the download source, and
    how to undo it.
  - Python packages into the course's own venv (one venv per course, owner, 2026-11-05; the
    project venv holds only the tool): one click, after the same review (a broken venv
    is deleted and rebuilt). Everything else, the user runs in their own terminal, then
    clicks "Check again". The GUI never runs system installs, and never runs LLM-written
    commands (arbitrary commands; hallucinated package names can be squatted). A component
    or platform missing from the recipes is reported as needing a new recipe.
  - One click after uploading a new course's slides creates its workspace venv and terminal files
    (owner's wish, 2026-11-04), by calling `python -m tutor.workspace init`.
  - How far this relaxes "Check, don't install" (owner, 2026-11-06): the GUI installs Python
    packages only into a course's own venv (through `tutor.workspace init`) and builds the
    test image (through `tutor.container build`); it never runs a system install, which the
    user runs in their own terminal before "Check again".
  - The test-run container (layer B, "Code execution safety"; owner, 2026-11-05). The GUI
    itself stays on the host, since it opens the IDE and folders and reads keys; only test
    runs go into the container. The page shows, per course: Docker missing, not running,
    image missing or out of date, or ready, with the image's size and "Check again". A "Build
    image" button is allowed, since the recipe comes from our code and not from a model; it
    shows the recipe, download source and expected size first. The user installs Docker.
    Starting it (owner, 2026-11-15, which replaces "the user installs and starts Docker"):
    when a course under the root runs its tests in the container, every `tutor gui` launch
    starts an installed but stopped Docker Desktop (`container.start_docker_desktop`: `open
    -a Docker` on macOS, its exe under Program Files on Windows, the `docker-desktop` user
    service on Linux), in a thread so the page opens meanwhile, and says to install Docker
    Desktop when there is no `docker`. It never installs, and a Linux Docker Engine without
    Desktop, which needs sudo, is left to the user; courses all on the host leave Docker
    alone, since its VM takes memory a local model may need. What stops Docker is told apart
    (`container.docker_state`, after the 2026-11-15/16 walk-throughs), since each asks the
    user for something else: not installed; Docker Desktop installed but not on this
    terminal's PATH (installed after it opened: open a new one, not install again; the app
    is found from `docker.exe` two folders up, or under Program Files, or per user under
    AppData\Local\Programs, which the launcher also starts); permission denied (Linux: the
    docker group and a new login; Windows: docker-users, from Docker's docs, untested);
    not running (Docker Desktop, or on Linux also Docker Engine with `systemctl`); no answer
    in 30 s. `tutor doctor`'s "next:" gives the fix, never the problem again, and for "not
    running" also the way on where Docker cannot start (a VM or Windows Sandbox: run the
    course's tests on this machine). The venv click does not build
    the image, which may be 2 GB: that waits until `[requires]` is confirmed. The user chooses host or container for a new course, nothing
    switched silently, with a warning when it matters: choosing the host (generated code runs
    with their rights), and a course set to the container while Docker is not available.
