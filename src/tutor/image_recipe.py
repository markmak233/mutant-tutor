"""The recipe of a course's test image: the Dockerfile made from its [requires], and the
image's name, a hash of that recipe. Nothing here calls Docker; tutor.container builds the image
from this recipe and runs tests in it.

Isolation layer B (docs/design.md, "Code execution safety"): what goes into the image is fixed
here, from the course's [requires] and nothing of this machine but the versions it has.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import sys
from collections.abc import Callable, Mapping
from importlib import metadata
from pathlib import Path

from tutor import compat
from tutor.envcheck import NOT_PIP, requires_problems, version_matches
from tutor.package_bounds import VERSION

IMAGE_PREFIX = "tutor-run-"
# This package, mounted read-only: exercise tests import tutor.testkit. PYTHONPATH in the image
# points at the parent. A mount and not a copy, so an image never holds an older testkit.
TUTOR_DIR = Path(__file__).resolve().parent
TUTOR_MOUNT = "/opt/tutor/tutor"

# Fixed in the code: only these releases can be built, each checked against Apache's own
# checksum. The documentation is half a gigabyte no run reads, and is removed after unpacking.
HADOOP_RELEASES = {
    "3.3.6": "de3eaca2e0517e4b569a88b63c89fae19cb8ac6c01ff990f1ff8f0cc0f3128c8"
             "e8a23db01577ca562a0e0bb1b4a3889f8c74384e609cd55e537aada3dcaa9f8a",
}
# Measured 2026-11-05: 3.3 MB/s and 450 KB/s for the same 730 MB. The first drops old releases
# in time and the second keeps them all; the checksum covers both.
HADOOP_MIRRORS = ("https://downloads.apache.org/hadoop/common",
                  "https://archive.apache.org/dist/hadoop/common")

# Every image has these: the validator's rules run pytest, and oracle tests use hypothesis.
_TEST_PACKAGES = ("pytest", "hypothesis")


class ContainerError(Exception):
    """Docker or the image cannot be used. The message says how to fix it. Defined here, as
    the recipe is the first that can refuse ([requires] it cannot build); callers name it
    from tutor.container."""


def runner_uid() -> int:
    """The uid the image's `runner` user gets: the host user's, so the host can delete what
    a run wrote in its folder (on native Linux the numbers must match). Never root, and 1000
    where there are no uids (Windows)."""
    getuid = getattr(os, "getuid", None)
    return (getuid() if getuid else 0) or 1000


def dockerfile(requires: Mapping[str, str], uid: int | None = None,
               installed: Callable[[str], str | None] = lambda name: None) -> str:
    """The image's recipe, from a course's [requires]: `python` picks the base image, `java`
    the JDK, `gcc` Debian's C compiler (C exercises), `hadoop` Apache's tarball, and any other
    name is a pip package.

    `installed` gives a package's version on this machine, or None. A build passes the real
    one so that the image tests with what the host tests with, as long as it fits [requires].
    Leaving it out gives the recipe image_name hashes, which must not depend on the venv.

    Slow, rarely changing steps come first, so a changed package does not download Hadoop
    again.
    """
    _check_requires(requires)
    python = requires.get("python") or f"{sys.version_info.major}.{sys.version_info.minor}"
    # requires_problems checks this only when [requires] names python; the image's own does.
    if found := compat.conflicts(requires, python):
        raise ContainerError(found[0])
    steps = [f"FROM python:{python}-slim-bookworm"]
    env = {
        # The root file system is read-only and /tmp is the only writable place besides a
        # run's own folder, so Python, Spark and MRJob all keep their files there.
        "HOME": "/tmp",
        "TMPDIR": "/tmp",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(Path(TUTOR_MOUNT).parent.as_posix()),
        # A pipe's encoding, so the host can decode what the run prints.
        "PYTHONIOENCODING": "utf-8",
        # Spark workers otherwise start the `python3` on PATH.
        "PYSPARK_PYTHON": "/usr/local/bin/python",
        "SPARK_LOCAL_IP": "127.0.0.1",
    }
    if java := requires.get("java"):
        major = java.split(".")[0]
        # procps: Hadoop's and Spark's launch scripts call `ps`.
        steps.append("RUN apt-get update \\\n"
                     f" && apt-get install -y --no-install-recommends openjdk-{major}-jre-headless"
                     " procps \\\n"
                     " && rm -rf /var/lib/apt/lists/*")
        # The JDK's folder name holds the architecture; one JAVA_HOME for arm64 and amd64.
        steps.append(f'RUN ln -s "/usr/lib/jvm/java-{major}-openjdk-'
                     '$(dpkg --print-architecture)" /opt/java')
        env["JAVA_HOME"] = "/opt/java"
    if "gcc" in requires:
        # A step of its own, so the recipes (and image names) of courses without C stay as
        # they were. Debian's gcc package is the release's one (12 on bookworm); the check
        # after the build compares it with [requires], so another major is refused there.
        # libc6-dev: the C library's headers. The sanitizers' libraries come with gcc.
        steps.append("RUN apt-get update \\\n"
                     " && apt-get install -y --no-install-recommends gcc libc6-dev \\\n"
                     " && rm -rf /var/lib/apt/lists/*")
    if hadoop := requires.get("hadoop"):
        steps.append(_hadoop_step(hadoop))
        env["HADOOP_HOME"] = "/opt/hadoop"
    packages = [_pip_spec(name, None, installed) for name in _TEST_PACKAGES
                if name not in requires]
    packages += [_pip_spec(name, prefix, installed) for name, prefix in requires.items()
                 if name not in NOT_PIP]
    # Companions only where the image's Python needs them (tutor.compat), so the recipe, and
    # the image name, of a course that needs none stays as it was.
    packages += [_pip_spec(name, None, installed) for name in compat.companions(requires, python)
                 if name not in requires]
    steps.append("RUN pip install --no-cache-dir " + " ".join(shlex.quote(p) for p in packages))
    # --no-log-init: useradd's log file grows with the uid, and a large one fills the disk.
    steps.append(f"RUN useradd --create-home --no-log-init --uid {_uid(uid)} runner")
    steps.append("ENV " + " ".join(f"{key}={value}" for key, value in env.items()))
    steps.append("USER runner")
    return "\n".join(steps) + "\n"


def _uid(uid: int | None) -> int:
    return runner_uid() if uid is None else uid


def _check_requires(requires: Mapping[str, str]) -> None:
    """Refuse what would fail late, or change the recipe's meaning, inside a build. [requires]
    is the owner's and generation never writes it, so this catches typing errors. The rules
    are envcheck's, shared with the venv install and the GUI; a build adds a known Hadoop."""
    if problems := requires_problems(requires, allow_bounds=True):
        raise ContainerError(problems[0])
    if "hadoop" in requires:
        hadoop_release(requires["hadoop"])


def hadoop_release(required: str) -> str:
    """The newest release in HADOOP_RELEASES that fits, or ContainerError."""
    releases = [r for r in HADOOP_RELEASES if version_matches(r, required)]
    if not releases:
        raise ContainerError(f"[requires] hadoop = {required!r}: no known release. Known: "
                             f"{', '.join(HADOOP_RELEASES)} (image_recipe.HADOOP_RELEASES)")
    return releases[-1]


def _hadoop_step(required: str) -> str:
    release = hadoop_release(required)
    urls = " ".join(f"{mirror}/hadoop-{release}/hadoop-{release}.tar.gz"
                    for mirror in HADOOP_MIRRORS)
    # Python downloads, as the slim image has no curl. A failed mirror leaves no file, or a
    # partial one, and the checksum stops the build either way.
    fetch = ("python -c \"import sys, urllib.request; "
             "urllib.request.urlretrieve(sys.argv[1], '/tmp/hadoop.tgz')\" \"$url\"")
    return (f"RUN for url in {urls}; do {fetch} && break; done \\\n"
            f' && echo "{HADOOP_RELEASES[release]}  /tmp/hadoop.tgz" | sha512sum -c - \\\n'
            " && mkdir /opt/hadoop \\\n"
            " && tar -xzf /tmp/hadoop.tgz -C /opt/hadoop --strip-components=1 \\\n"
            " && rm -rf /tmp/hadoop.tgz /opt/hadoop/share/doc")


def _pip_spec(name: str, prefix: str | None, installed: Callable[[str], str | None]) -> str:
    """`pyspark==3.5.9` when this machine has a version that fits, else `pyspark==3.5.*`."""
    found = installed(name)
    if found and (prefix is None or version_matches(found, prefix)):
        return f"{name}=={found}"
    if prefix:
        return f"{name}=={prefix}.*" if VERSION.fullmatch(prefix) else name + prefix
    return name


def installed_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def image_name(course: str, requires: Mapping[str, str], uid: int | None = None) -> str:
    """`tutor-run-<course>:<hash of the recipe>`, so a changed [requires] or a changed recipe
    means a missing image and a clear "rebuild". The hash leaves out the versions on this
    machine: two venvs of one course hold different pytest versions (measured), and each
    would otherwise call the image out of date."""
    digest = hashlib.sha256(dockerfile(requires, uid).encode("utf-8")).hexdigest()[:12]
    # Docker takes lower case only, and a separator must stand between two alphanumerics.
    return f"{IMAGE_PREFIX}{re.sub(r'[^a-z0-9]+', '-', course.lower()).strip('-')}:{digest}"
