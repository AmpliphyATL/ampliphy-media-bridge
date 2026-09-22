"""Output adapter: record the payload, send nothing. Always safe; the default."""
from __future__ import annotations

from ..events import EnrichedTrackEvent, OutputResult, now

NAME = "log"


def send(enriched: EnrichedTrackEvent) -> OutputResult:
    return OutputResult(adapter=NAME, ok=True, sent_ts=now(), status="logged (no downstream configured)", payload=enriched.payload())
