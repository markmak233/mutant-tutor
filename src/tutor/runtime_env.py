"""Credential-free runtime maps for diagnostic Python/Docker subprocesses.

Docker alone retains CLI context/transport locations. Never forward model credentials,
registry auth blobs or inherited pip configuration into diagnostic children.
"""

import os

_RUNTIME = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR",
            "LANG", "LC_ALL", "LC_CTYPE"}
_DOCKER = {"HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME",
           "DOCKER_CONFIG", "DOCKER_CONTEXT", "DOCKER_HOST", "DOCKER_TLS_VERIFY",
           "DOCKER_CERT_PATH", "SSH_AUTH_SOCK"}


def environment(*, docker: bool = False) -> dict[str, str]:
    """Names are compared case-insensitively for native Windows environments."""
    allowed = _RUNTIME | (_DOCKER if docker else set())
    return {k: v for k, v in os.environ.items() if k.upper() in allowed}
