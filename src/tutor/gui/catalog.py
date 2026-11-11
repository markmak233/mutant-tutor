"""The models.dev catalog, to fill the New model form (owner, 2026-11-07).

models.dev (https://models.dev, MIT licence) publishes one JSON file of AI providers and their
models, readable with no key: per provider its base URL, the name of its key's variable and its
docs; per model its release date, whether it thinks and how far that can be turned down, what
it reads, its limits and its price. The GUI downloads it only when the owner clicks (the
request carries nothing of the courses), keeps the part it uses in `<root>/.cache/`, and only
fills the form with it: nothing is saved until the owner saves the form, through the usual
checks (llm_settings.check_profile). The hand-written form works without it.

The file is downloaded data, so each value is checked before it is kept: a base URL on https
(or http on this machine), a key name in capitals, a model id of plain characters, short
names; anything else is left out. Only providers this tool can call are kept: those models.dev
marks as taking OpenAI's requests, and those it lists with their own SDK whose OpenAI URL
is in OPENAI_URLS; the others are counted, and the page says how many.

The list is downloaded data, and a changed base URL or key name in it, filled by "Use" and
saved, would send that key and the course's text to another host on every call (owner,
2026-11-07; guarded 2026-11-10). So a provider this tool knows (KNOWN, OPENAI_URLS) always
gets this tool's own URL, whatever the list says (`own_url`); the page marks the others as
"URL from models.dev"; and a fetch keeps which providers' URLs changed since the fetch
before, for the page to show. The Models page refuses a key name used at another host.

Servers on this machine (LOCAL: Ollama, LM Studio, llama.cpp, vLLM; owner, 2026-11-10) are
not read from the list: their models are what the server has, which it tells at GET
/v1/models with no key (`installed`), and that call goes only to localhost.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from tutor import llm
from tutor.gui.i18n import N_

SOURCE = "https://models.dev/api.json"
CACHE_FORMAT = 3  # what the kept file holds; a file of another format is fetched again
CACHE = Path(".cache") / "models-dev.json"  # under the GUI's root, beside the course folders
TIMEOUT_S = 30
MAX_BYTES = 40 * 1024 * 1024  # the file was 6 MB on 2026-11-07
SHOWN = 40  # models listed before "Show all"

# models.dev lists these with their own SDK (and mostly no base URL); each one's
# OpenAI-compatible URL, from the provider's own docs (checked 2026-11-07). MiniMax is listed
# there with its Anthropic-style URL, which this tool's client speaks only for Anthropic
# itself (KNOWN below: its own provider in llm.py since 2026-11-10). These URLs are this
# tool's own: the list's are not used for them.
OPENAI_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai",
    "xai": "https://api.x.ai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "groq": "https://api.groq.com/openai/v1",
    "togetherai": "https://api.together.ai/v1",
    "minimax": "https://api.minimax.io/v1",
    "minimax-cn": "https://api.minimaxi.com/v1",
}
# Providers llm.py knows by name: their own way to turn thinking off, and their base URL
# (z.ai is Zhipu's international site: GLM's rules at its own URL, ZAI_URL). The local
# servers (LOCAL) are their own providers in llm.py under the same ids.
KNOWN = {"deepseek": "deepseek", "zhipuai": "glm", "zai": "glm", "openai": "openai",
         "anthropic": "anthropic", "lmstudio": "lmstudio", "ollama": "ollama",
         "llamacpp": "llamacpp", "vllm": "vllm"}
ZAI_URL = "https://api.z.ai/api/paas/v4"  # from z.ai's docs (checked 2026-11-07)
# Servers on this machine, as the form names them: their models come from the server
# (`installed`), at llm.py's default URL for each; models.dev's entry for LM Studio (a
# sample of models) is left out, so the two lists do not disagree.
LOCAL = {"ollama": "Ollama", "lmstudio": "LM Studio", "llamacpp": "llama.cpp (llama-server)",
         "vllm": "vLLM"}
LOCAL_DOCS = {
    "ollama": "https://docs.ollama.com/api/openai-compatibility",
    "lmstudio": "https://lmstudio.ai/docs/app/api/endpoints/openai",
    "llamacpp": "https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md",
    "vllm": "https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html",
}
LOCAL_TIMEOUT_S = 5  # a server on this machine answers at once, or is not running
LOCAL_MAX_BYTES = 1024 * 1024  # a list of models is a few kB
# Listed first, in this order; the rest follow by name.
COMMON = ("deepseek", "zhipuai", "zai", "moonshotai-cn", "moonshotai", "alibaba-cn", "alibaba",
          "minimax-cn", "minimax", "volcengine", "siliconflow-cn", "siliconflow", "openai",
          "anthropic", "google", "xai", "mistral", "openrouter", "groq", "togetherai")
NOTES: dict[str, str] = {}  # a line the New model form shows for a provider, by id
# How models.dev marks a provider that takes OpenAI's requests at its own base URL.
_OPENAI_SHAPED = ("@ai-sdk/openai-compatible", "@ai-sdk/openai")
_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,119}$")
_PROVIDER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_EFFORT = re.compile(r"^[a-z]{1,20}$")
_MAKER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,40}$")
_DAY = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")
_PROFILE_CHARS = re.compile(r"[^a-z0-9._-]+")
# The lowest settings that keep a thinking model's calls short, in the order tried.
_LOWEST = ("none", "minimal", "low")


@dataclass(frozen=True)
class Model:
    id: str
    name: str
    released: str  # "2026-10-17"; "" when not given
    reasoning: bool | None  # it thinks; None: not said (a local server's list)
    efforts: tuple[str, ...]  # the reasoning_effort values it takes, e.g. ("none", "high")
    image: bool  # it reads images (the vision role needs that)
    context: int  # tokens; 0 when not given
    price_in: float | None  # USD per million input tokens
    price_out: float | None
    maker: str = ""  # who made it, e.g. "deepseek", "zhipuai"; "" when the list does not say

    @property
    def thinking(self) -> str:
        """How it thinks, as the table says it (the fixed words marked N_(): the page
        translates them with _(); "lowest <effort>" names a value and stays as it is)."""
        if self.reasoning is None:
            return N_("not said")
        if not self.reasoning:
            return N_("no")
        return N_("can be off") if "none" in self.efforts else (
            f"lowest {low}" if (low := self.lowest) else N_("always"))

    @property
    def context_text(self) -> str:
        """1048576 -> "1M", 1000000 -> "1M", 131072 -> "128k", 128000 -> "128k"; "" when
        not given. Providers count in powers of 2 or of 10; either reads as written."""
        n = self.context
        if n >= 1_000_000:
            return f"{n >> 20}M" if n % (1 << 20) == 0 else f"{round(n / 1e6, 1):g}M"
        if n >= 1000:
            return f"{n >> 10}k" if n % 1024 == 0 else f"{round(n / 1000)}k"
        return str(n) if n else ""

    @property
    def price_text(self) -> str:
        """"0.15 · 3.89": USD per million tokens in and out, cents from 1 USD up (some
        prices are converted from another currency, with many decimals); "" when not given."""
        if self.price_in is None or self.price_out is None:
            return ""
        return f"{_usd(self.price_in)} · {_usd(self.price_out)}"

    @property
    def lowest(self) -> str:
        """The lowest reasoning_effort it takes among _LOWEST; "" if none of them."""
        return next((e for e in _LOWEST if e in self.efforts), "")


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    base_url: str
    key_env: str  # the variable its key goes in; "" for a server on this machine
    doc: str  # its docs page, https; "" when not given
    models: tuple[Model, ...]  # newest first; deprecated ones left out
    # The base URL is this tool's own (OPENAI_URLS, KNOWN, LOCAL), not the list's.
    own_url: bool = False


@dataclass(frozen=True)
class Catalog:
    fetched: str  # the day it was downloaded
    providers: tuple[Provider, ...]  # COMMON first, then by name
    left_out: int  # providers reachable only through their own SDK
    # (provider id, URL before, URL now) for providers whose URL differed from the copy
    # kept before this fetch: a change to look at before using one of them.
    url_changes: tuple[tuple[str, str, str], ...] = ()

    def provider(self, provider_id: str) -> Provider | None:
        return next((p for p in self.providers if p.id == provider_id), None)

    def maker(self, base_url: str, model_id: str) -> str:
        """Who made the model a profile calls: looked up at the provider with its base URL,
        else at any provider with a model of that id; "" when the list does not say, or
        two makers claim it."""
        here = [p for p in self.providers if same_url(p.base_url, base_url)]
        for group in (here, self.providers):
            makers = {m.maker for p in group for m in p.models if m.id == model_id and m.maker}
            if makers:
                return makers.pop() if len(makers) == 1 else ""
        return ""


def refresh(root: Path, opener=urllib.request.urlopen) -> str | None:
    """Download the catalog and keep the part this tool uses; the problem if it failed (the
    copy before stays). `opener`: tests pass a stand-in, so no test uses the network."""
    request = urllib.request.Request(SOURCE, headers={"User-Agent": "mutant-tutor",
                                                      "Accept": "application/json"})
    try:
        with opener(request, timeout=TIMEOUT_S) as response:
            raw = response.read(MAX_BYTES + 1)
    except (urllib.error.URLError, OSError) as e:
        return f"models.dev could not be reached ({getattr(e, 'reason', e)})."
    if len(raw) > MAX_BYTES:
        return "models.dev sent more than expected; nothing was kept."
    try:
        data = json.loads(raw)
    except ValueError:
        return "models.dev sent something that is not its catalog; nothing was kept."
    if not isinstance(data, dict):
        return "models.dev sent something that is not its catalog; nothing was kept."
    kept = compact(data, date.today().isoformat())
    path = root / CACHE
    kept["url_changes"] = _url_changes(path, kept["providers"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps(kept, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)  # whole or not at all
    _loaded.clear()
    return None


def _url_changes(path: Path, providers: list[dict]) -> list[list[str]]:
    """[id, URL before, URL now] for each provider the copy at `path` (the fetch before)
    also had, at another URL; [] with no copy before, or one that cannot be read."""
    try:
        before = json.loads(path.read_text(encoding="utf-8")).get("providers") or []
        old = {p["id"]: p["base_url"] for p in before if isinstance(p, dict)}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []
    return [[p["id"], old[p["id"]], p["base_url"]] for p in providers
            if p["id"] in old and not same_url(old[p["id"]], p["base_url"])]


def compact(data: dict, fetched: str) -> dict:
    """The part of models.dev's file this tool uses, each value checked."""
    providers, left_out = [], 0
    makers = _makers(data)
    for key, raw in data.items():
        if not isinstance(raw, dict) or not _PROVIDER.match(str(key)) or key in LOCAL:
            continue  # a local server's models come from the server, not the list
        provider = _provider(str(key), raw, makers)
        if provider is None:
            left_out += 1
        else:
            providers.append(asdict(provider))
    order = {p: i for i, p in enumerate(COMMON)}
    providers.sort(key=lambda p: (order.get(p["id"], len(order)), p["name"].casefold()))
    return {"format": CACHE_FORMAT, "source": SOURCE, "fetched": fetched,
            "left_out": left_out, "providers": providers}


_loaded: dict[Path, tuple[float, Catalog]] = {}


def load(root: Path) -> Catalog | None:
    """The kept catalog; None if there is none yet (or it cannot be read). Read again only
    when the file changed."""
    path = root / CACHE
    try:
        mtime = path.stat().st_mtime
        if path in _loaded and _loaded[path][0] == mtime:
            return _loaded[path][1]
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("format") != CACHE_FORMAT:
            return None  # kept by an older version: the page offers to fetch it again
        catalog = Catalog(
            str(data["fetched"]),
            tuple(Provider(**{**p, "models": tuple(
                Model(**{**m, "efforts": tuple(m["efforts"])}) for m in p["models"])})
                for p in data["providers"]),
            int(data["left_out"]),
            tuple((str(a), str(b), str(c)) for a, b, c in data.get("url_changes") or []))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    _loaded[path] = (mtime, catalog)
    return catalog


def stale(root: Path) -> str:
    """The day of a kept list that this version cannot use (an older format), so the page
    can say why it asks to fetch again; "" when there is none, or it is usable."""
    try:
        data = json.loads((root / CACHE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict) or data.get("format") == CACHE_FORMAT:
        return ""
    fetched = data.get("fetched")
    return fetched if isinstance(fetched, str) and _DAY.match(fetched) else "earlier"


def fill(provider: Provider, model: Model, taken: set[str]) -> tuple[dict[str, str],
                                                                       list[str]]:
    """The New model form's values for this model, and what the page should say about it.
    A provider llm.py knows keeps its own way to turn thinking off; another one is `custom`
    with its base URL, and a thinking model there gets the lowest reasoning_effort it takes."""
    notes = list(filter(None, [NOTES.get(provider.id)]))
    if provider.id in LOCAL:
        notes.append("The server's list does not say whether the model thinks or reads "
                     "images: Check on the Models page tells whether its thinking stayed on.")
    known = KNOWN.get(provider.id)
    if known:
        default = llm.PROVIDERS[known][0] or ""
        # Shown as it is used; saving leaves a provider's own URL out of the file.
        base_url = default if same_url(provider.base_url, default) else provider.base_url
        effort = ""
    else:
        base_url, effort = provider.base_url, model.lowest if model.reasoning else ""
        if model.reasoning and not effort:
            notes.append("Its thinking cannot be turned down, so each call thinks: slower, "
                         "and the thinking is paid.")
    values = {"name": _profile_name(model.id, taken), "provider": known or "custom",
              "model": model.id, "api_key_env": provider.key_env, "base_url": base_url,
              "reasoning_effort": effort, "reasoning_effort_on": "", "timeout_s": "",
              "temperature": ""}
    return values, notes


def _makers(data: dict) -> set[str]:
    """Every maker named by a canonical model id ("deepseek/deepseek-v4-pro-0813" ->
    "deepseek"): models.dev's own names for the companies that make models."""
    found = set()
    for raw in data.values():
        models = raw.get("models") if isinstance(raw, dict) else None
        for model in models.values() if isinstance(models, dict) else []:
            canonical = model.get("canonical_model_id") if isinstance(model, dict) else None
            if isinstance(canonical, str) and _MAKER.match(head := canonical.split("/")[0]):
                found.add(head)
    return found


def _provider(key: str, raw: dict, makers: set[str]) -> Provider | None:
    """One provider as kept, or None when this tool cannot call it."""
    api = raw.get("api") if isinstance(raw.get("api"), str) else ""
    own = True  # the URL is this tool's own, whatever the list says
    if key in OPENAI_URLS:
        base_url = OPENAI_URLS[key]
    elif key in KNOWN:
        base_url = ZAI_URL if key == "zai" else llm.PROVIDERS[KNOWN[key]][0] or ""
    elif raw.get("npm") in _OPENAI_SHAPED:
        base_url, own = api, False
    else:
        return None
    base_url = base_url.rstrip("/")
    if not _url_ok(base_url):
        return None
    local = urlsplit(base_url).scheme == "http"
    envs = raw.get("env") if isinstance(raw.get("env"), list) else []
    key_env = "" if local else next((e for e in envs if isinstance(e, str) and _KEY.match(e)),
                                    "")
    doc = raw.get("doc") if isinstance(raw.get("doc"), str) else ""
    models = [m for m in (_model(str(k), v, makers)
                          for k, v in (raw.get("models") or {}).items() if isinstance(v, dict))
              if m is not None]
    models.sort(key=lambda m: (m.released, m.id), reverse=True)
    return Provider(key, _text(raw.get("name"), 80) or key, base_url, key_env,
                    doc if doc.startswith("https://") and len(doc) <= 300 else "",
                    tuple(models), own)


# --- servers on this machine --------------------------------------------------------------------


def local_provider(provider_id: str) -> Provider | None:
    """One of LOCAL as a Provider, at llm.py's URL for it, with no models yet (they come
    from the server: `installed`); None for any other id."""
    if provider_id not in LOCAL:
        return None
    return Provider(provider_id, LOCAL[provider_id], llm.PROVIDERS[provider_id][0] or "", "",
                    LOCAL_DOCS[provider_id], (), own_url=True)


def installed(base_url: str, opener=urllib.request.urlopen
              ) -> tuple[tuple[Model, ...], str]:
    """The models a server on this machine has, from its GET /v1/models (no key; Ollama,
    LM Studio, llama-server and vLLM all answer it), and the problem when it gave none.
    Only localhost is asked: the page never makes the GUI's server call out. A model's
    thinking, what it reads and its limits are not in that list (None, "" and 0)."""
    if urlsplit(base_url).hostname not in ("localhost", "127.0.0.1"):
        return (), f"{base_url} is not on this machine: fill the form by hand."
    request = urllib.request.Request(f"{base_url.rstrip('/')}/models",
                                     headers={"Accept": "application/json"})
    try:
        with opener(request, timeout=LOCAL_TIMEOUT_S) as response:
            raw = response.read(LOCAL_MAX_BYTES + 1)
        data = json.loads(raw) if len(raw) <= LOCAL_MAX_BYTES else None
    except (urllib.error.URLError, OSError, ValueError):
        return (), f"Nothing answers at {base_url}: is the server running?"
    # {"data": [{"id": ...}, ...]}; Ollama sends "data": null with no model pulled.
    rows = data.get("data") if isinstance(data, dict) else None
    if rows is None and isinstance(data, dict):
        rows = []
    if not isinstance(rows, list):
        return (), f"{base_url} answered with something that is not a list of models."
    ids = sorted({r["id"] for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str)
                  and _MODEL.match(r["id"])})
    if not ids:
        return (), f"{base_url} lists no model: load or pull one there first."
    return tuple(Model(i, i, "", None, (), False, 0, None, None) for i in ids), ""


def _model(key: str, raw: dict, makers: set[str]) -> Model | None:
    model_id = raw.get("id") if isinstance(raw.get("id"), str) else key
    if not _MODEL.match(model_id) or raw.get("status") == "deprecated":
        return None
    modalities = raw.get("modalities") if isinstance(raw.get("modalities"), dict) else {}
    inputs = modalities.get("input") if isinstance(modalities.get("input"), list) else []
    outputs = modalities.get("output") if isinstance(modalities.get("output"), list) else []
    if outputs and "text" not in outputs:  # one that only makes images, speech or video
        return None
    efforts: list[str] = []
    for option in raw.get("reasoning_options") or []:
        if isinstance(option, dict) and option.get("type") == "effort":
            efforts += [v for v in option.get("values") or []
                        if isinstance(v, str) and _EFFORT.match(v) and v not in efforts]
    released = raw.get("release_date") if isinstance(raw.get("release_date"), str) else ""
    limit = raw.get("limit") if isinstance(raw.get("limit"), dict) else {}
    cost = raw.get("cost") if isinstance(raw.get("cost"), dict) else {}
    # The maker: models.dev's canonical id names it; else an aggregator's id often starts
    # with it ("deepseek/deepseek-flash-latest" at OpenRouter), when it is a known maker.
    canonical = raw.get("canonical_model_id")
    maker = canonical.split("/")[0] if isinstance(canonical, str) and "/" in canonical else ""
    if not _MAKER.match(maker):
        head = model_id.lstrip("~").split("/")[0].lower() if "/" in model_id else ""
        maker = head if head in makers else ""
    return Model(model_id, _text(raw.get("name"), 80) or model_id,
                 released if _DAY.match(released) else "", raw.get("reasoning") is True,
                 tuple(efforts), "image" in inputs, _number(limit.get("context"), int) or 0,
                 _number(cost.get("input"), float), _number(cost.get("output"), float), maker)


def _url_ok(url: str) -> bool:
    """https anywhere, or http on this machine (a local server); nothing odd inside."""
    if len(url) > 200 or any(c.isspace() or c in "\"'<>\\" for c in url):
        return False
    parts = urlsplit(url)
    return (parts.scheme == "https" and bool(parts.hostname)) or (
        parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1"))


def same_url(a: str, b: str) -> bool:
    """One server: equal but for a trailing slash and localhost written as 127.0.0.1."""
    def norm(url: str) -> str:
        return url.rstrip("/").replace("://127.0.0.1", "://localhost")
    return norm(a) == norm(b)


def _profile_name(model_id: str, taken: set[str]) -> str:
    """A profile name from the model's id: its last part, in the form llm_settings takes,
    with -2, -3 ... when the course has one of that name."""
    base = _PROFILE_CHARS.sub("-", model_id.rsplit("/", 1)[-1].lower()).strip("-._")[:36]
    base = base or "model"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    return name


def _usd(amount: float) -> str:
    """3.8874 -> "3.89", 1.5 -> "1.5", 0.15 -> "0.15", 0.003 -> "0.003"."""
    if amount >= 1:
        return f"{amount:.2f}".rstrip("0").rstrip(".")
    return f"{amount:.3g}"


def _text(value, limit: int) -> str:
    """A short display text: no control characters, at most `limit` characters."""
    if not isinstance(value, str):
        return ""
    return "".join(c for c in value if c.isprintable())[:limit].strip()


def _number(value, kind):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return kind(value)
