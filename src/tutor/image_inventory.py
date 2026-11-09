"""Read fixed stdlib/tool version facts from an immutable image under container limits."""

from __future__ import annotations

import json
import re
import uuid

from tutor import image_build, package_bounds
from tutor.envcheck import parse_java_version
from tutor.image_recipe import ContainerError

# No course, exercise, package framework or model-supplied code is imported. Tools are
# invoked by fixed argv and bounded individually, with JSON output rather than shell text.
PROBE = '''# tutor-image-inventory-v1
import glob, json, os, re, subprocess, sys
from importlib import metadata
def ask(argv):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=10)
        return (p.stdout + p.stderr)[:2000] if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None
hadoop = None
for name in glob.glob(os.path.join(os.environ.get("HADOOP_HOME", "/nonexistent"),
                                  "share", "hadoop", "common", "hadoop-common-*.jar")):
    match = re.fullmatch(r"hadoop-common-([0-9.]+)\\.jar", os.path.basename(name))
    if match:
        hadoop = match.group(1)
print(json.dumps(dict(python=".".join(map(str, sys.version_info[:3])),
    packages=[[d.metadata["Name"], d.version] for d in metadata.distributions()],
    java=ask(["java", "-version"]), gcc=ask(["gcc", "-dumpfullversion"]), hadoop=hadoop)))
'''
_VERSION = re.compile(r"[0-9][A-Za-z0-9.!+_-]{0,127}")


def collect(image: str, run=None) -> dict:
    """No mount/network/install; own name, outer deadline and inner kill bound."""
    from tutor import container

    container_name = "tutor-image-record-" + uuid.uuid4().hex
    try:
        done = image_build.command(run, ["docker", "run", "--rm", "--pull", "never",
                                        "--name", container_name,
                                        *container.limits(), image, "timeout", "-s", "KILL", "45",
                                        "python", "-I", "-B", "-c", PROBE], timeout=60)
        if done.returncode or len(done.stdout.encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError
        data = json.loads(done.stdout)
        if set(data) != {"python", "packages", "java", "gcc", "hadoop"}:
            raise ValueError
        return validate(data)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ContainerError("image inventory failed or returned invalid version facts") from None
    finally:
        # A successful --rm probe needs no kill; a disconnected client may have left one.
        # Kill our unique name only. Not-found is the normal successful cleanup case.
        image_build.command(run, ["docker", "kill", container_name], timeout=10)


def validate(data) -> dict:
    """Normalize measured raw versions; no arbitrary paths/control text enters records."""
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}", data["python"]):
        raise ValueError
    packages = {}
    if not isinstance(data["packages"], list):
        raise ValueError
    for entry in data["packages"]:
        if not isinstance(entry, list) or len(entry) != 2:
            raise ValueError
        raw, version = entry
        name = package_bounds.identity(raw)
        if not isinstance(version, str) or not _VERSION.fullmatch(version):
            raise ValueError
        if name in packages and packages[name] != version:
            raise ValueError
        packages[name] = version
    java = parse_java_version(data["java"]) if isinstance(data["java"], str) else None
    tools = {"java": java, "gcc": data["gcc"].strip() if data["gcc"] else None,
             "hadoop": data["hadoop"]}
    if any(v is not None and not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", v)
           for v in tools.values()):
        raise ValueError
    return {"python": data["python"], "packages": dict(sorted(packages.items())), "tools": tools}
