"""The Python prompts and schemas the generation pipeline sends, pinned by hash.

They are calibrated (docs/design.md, "Prompt layout for caching") and are the cached prefix of
every Python course's calls, so a change to them must be deliberate. Pinned on 2026-11-07,
before the prompts became one template filled per language (Python and C): Python's fill
must stay these exact bytes. A failure names what changed; if the change is meant, update
the hash and say so in design.md.
"""

import hashlib
import json

from tutor.graph import nodes, prompts
from tutor.graph.state import PlanItem

CONTEXT = "## p.3 · slide 1.3\nMade-up page text.\n\n## p.4 · slide 1.4\nMore made-up text."
FAILURE = ("rule 1 (reference passes) FAIL: reference FAILED: 1 failed\n"
           "rule 3 (mutants killed) FAIL: 2/3 killed; survived (tests miss this bug): m02_x")


def _item(framework: str, interface: str) -> PlanItem:
    return PlanItem(id="count-per-group", knowledge_point="a made-up knowledge point",
                    lecture="L1", slides="1.3-1.4", type="implementation",
                    interface=interface, framework=framework, notes="check the edge case")


def outputs() -> dict[str, str]:
    """Every Python prompt and schema the pipeline sends, by name."""
    found = {
        "draft_schema": json.dumps(nodes.DRAFT_SCHEMA, sort_keys=True),
        "plan_schema": json.dumps(nodes.PLAN_SCHEMA, sort_keys=True),
        "oracle_schema": json.dumps(nodes.ORACLE_SCHEMA, sort_keys=True),
        "plan_spec": prompts.PLAN_SPEC,
        "oracle_spec": prompts.oracle_spec(),
        "oracle_prompt": prompts.oracle_prompt("The question.", "def f(x):\n    ...\n",
                                               {"sample_input/a.csv": "a,1\n" * 900}),
    }
    for interface in (False, True):
        for lines in (50, 20):
            for extra in ((), ("nltk",)):
                found[f"system/{interface}/{lines}/{','.join(extra)}"] = prompts.system_prompt(
                    "Course rule one.", oracle_interface=interface, concept_lines=lines,
                    extra_imports=extra)
    found["system/empty"] = prompts.system_prompt("")
    for framework, interface in (("python", "function"), ("python", "file"),
                                 ("pyspark-rdd", "function"), ("pyspark-rdd", "file"),
                                 ("pyspark-df", "file"), ("mrjob", "file"),
                                 ("pyspark-df", "function")):
        item = _item(framework, interface)
        for mode in ("learn", "review"):
            found[f"item/{framework}/{interface}/{mode}"] = prompts.item_prompt(item, CONTEXT,
                                                                                mode)
    item = _item("python", "function")
    found["repair"] = prompts.repair_prompt(item, CONTEXT, '{"draft": 1}', FAILURE, "learn")
    found["repair/unchanged"] = prompts.repair_prompt(item, CONTEXT, "{}", "rule 2 x", "review",
                                                      unchanged=True)
    found["retry"] = prompts.retry_prompt(item, CONTEXT, "cut off", "learn")
    for mode in ("learn", "review"):
        found[f"plan/{mode}"] = prompts.plan_prompt(
            ["L1", "L2"], "| row | gap-code |", "p.1 1.1 Title", ["old-id: Old [topic]"],
            "short exercises", None, max_count=5, mode=mode)
    found["plan/back"] = prompts.plan_prompt(
        ["L1"], "cov", "pages", [], "", 3, feedback="fewer", previous="[{}]",
        problems=["2 items, 3 asked for"])
    return found


def digests() -> dict[str, str]:
    return {name: hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
            for name, text in outputs().items()}


PINNED = {
    "draft_schema": "e3dfa303138e6528",
    "item/mrjob/file/learn": "4c44b12596a7c854",
    "item/mrjob/file/review": "4bdd57d56d085870",
    "item/pyspark-df/file/learn": "1b16589e388f49b4",
    "item/pyspark-df/file/review": "20276304e1fd39fb",
    "item/pyspark-df/function/learn": "b0f0566de9ad2fd6",
    "item/pyspark-df/function/review": "00d23525da7f43c7",
    "item/pyspark-rdd/file/learn": "0cc8af96849d46fb",
    "item/pyspark-rdd/file/review": "ffd1ae40469dd5a0",
    "item/pyspark-rdd/function/learn": "15b624521a2e5797",
    "item/pyspark-rdd/function/review": "a56ed246c9855307",
    "item/python/file/learn": "54b3c1c2d68c6509",
    "item/python/file/review": "0a1ff4e84c97e105",
    "item/python/function/learn": "25b9f31f37728e40",
    "item/python/function/review": "be4fbdcba8f449d5",
    "oracle_prompt": "ebb868fc9bfea53b",
    "oracle_schema": "1ba1452b6d888a17",
    "oracle_spec": "ef515a6cd46357ec",
    "plan/back": "57e6d0d80254f103",
    "plan/learn": "944fefe2dbec59ab",
    "plan/review": "a1ffc8b343a5d093",
    "plan_schema": "d6fd45df21185820",
    "plan_spec": "49f92266fee1996d",
    "repair": "37aa9e216a6f389a",
    "repair/unchanged": "8b251ba1fd5f3875",
    "retry": "6718dbf2b19835e5",
    "system/False/20/": "c29b0f4c0dc0bcdd",
    "system/False/20/nltk": "7cd8c5d52822ca05",
    "system/False/50/": "e11ba358ee874e9d",
    "system/False/50/nltk": "172ab6c2bfb7bf1e",
    "system/True/20/": "362b76bb6a0ae67c",
    "system/True/20/nltk": "29bc041c4642b1f6",
    "system/True/50/": "aaadcf9e16a3f893",
    "system/True/50/nltk": "2d087bbc38390372",
    "system/empty": "f234fec9389ba031",
}


def test_the_python_prompts_and_schemas_are_byte_for_byte_as_pinned():
    now = digests()
    changed = sorted(name for name in PINNED if now.get(name) != PINNED[name])
    assert not changed, f"changed: {changed}"
    assert set(now) == set(PINNED)
