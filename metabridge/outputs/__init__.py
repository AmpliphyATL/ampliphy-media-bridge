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

AVAILABLE = ["log", "webhook", "securenet_cirrus", "getmeradio"]


def active_adapters() -> list:
    names = [n.strip().lower() for n in os.environ.get("METABRIDGE_OUTPUTS", "log").split(",") if n.strip()]
    mods = []
    for n in names:
        if n not in AVAILABLE:
            raise ValueError(f"unknown output adapter {n!r}; available: {AVAILABLE}")
        mods.append(importlib.import_module(f"metabridge.outputs.{n}"))
    # Directory listings turn on by themselves once their credentials are saved -
    # nothing to add to the outputs list by hand. Always last: they are slowest.
    if "getmeradio" not in names and os.environ.get("METABRIDGE_GETMERADIO_TOKEN") \
            and os.environ.get("METABRIDGE_GETMERADIO_STATION_ID"):
        mods.append(importlib.import_module("metabridge.outputs.getmeradio"))
    return mods
