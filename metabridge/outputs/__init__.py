"""Output adapters: take an EnrichedTrackEvent and deliver it somewhere.

Each adapter is a module exposing:

    NAME = "..."
    def send(enriched) -> OutputResult

Which adapters run is chosen by the METABRIDGE_OUTPUTS environment variable
(comma-separated), e.g.  METABRIDGE_OUTPUTS=log,securenet_cirrus
Default is "log" — record what *would* be sent, send nothing.
"""
from __future__ import annotations

import importlib
import os

AVAILABLE = ["log", "webhook", "securenet_cirrus"]


def active_adapters() -> list:
    names = [n.strip().lower() for n in os.environ.get("METABRIDGE_OUTPUTS", "log").split(",") if n.strip()]
    mods = []
    for n in names:
        if n not in AVAILABLE:
            raise ValueError(f"unknown output adapter {n!r}; available: {AVAILABLE}")
        mods.append(importlib.import_module(f"metabridge.outputs.{n}"))
    return mods
