"""Load metabridge.env (KEY=VALUE per line) from the project folder into the environment.

Secrets such as the Cirrus authToken live here, never in code. The file is git-ignored.
Existing environment variables win over the file.
"""
from __future__ import annotations

import os
import pathlib

ENV_FILE = pathlib.Path(__file__).resolve().parent.parent / "metabridge.env"


def load() -> list[str]:
    loaded = []
    if not ENV_FILE.exists():
        return loaded
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v
            loaded.append(k)
    return loaded
