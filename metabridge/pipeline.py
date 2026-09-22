"""The MetaBridge pipeline: TrackEvent -> resolve -> EnrichedTrackEvent -> output adapters.

This is the only place inputs and outputs meet, and it knows nothing about
PlayoutONE or SecureNet — it just runs the neutral core and hands the result
to whichever output adapters are configured.
"""
from __future__ import annotations

import logging
import os
import threading

from . import db, outputs
from .core import resolve
from .events import EnrichedTrackEvent, TrackEvent, now

log = logging.getLogger("metabridge")

# Automation systems sometimes notify twice for the same track (retries, two outputs
# pointed at us, a stop/start). Ignore a repeat of the same artist+title inside this window.
DEDUPE_SECONDS = float(os.environ.get("METABRIDGE_DEDUPE_SECONDS", "20"))

_lock = threading.Lock()
_last: dict = {"key": None, "ts": 0.0}


def is_duplicate(ev: TrackEvent) -> bool:
    with _lock:
        if _last["key"] == ev.key() and (ev.received_ts - _last["ts"]) < DEDUPE_SECONDS:
            return True
        _last["key"], _last["ts"] = ev.key(), ev.received_ts
        return False


def looks_like_template(ev: TrackEvent) -> str | None:
    """Catch automation misconfiguration before it reaches the stream: unfilled tokens
    ("%%Artist%%", "{Title}"), empty artist, or placeholder text. Returns a reason or None."""
    a, t = (ev.artist or "").strip(), (ev.title or "").strip()
    for field, val in (("artist", a), ("title", t)):
        if "%%" in val or val.startswith("%") and val.endswith("%"):
            return f"unfilled token in {field}: {val!r}"
        if val.startswith("{") and val.endswith("}"):
            return f"unfilled token in {field}: {val!r}"
    if not t:
        return "empty title"
    if not a:
        return "empty artist"
    return None


def handle(ev: TrackEvent, send: bool = True) -> EnrichedTrackEvent:
    """Resolve one track event and deliver it. Never raises — a bad lookup must not stop the station."""
    t0 = now()
    bad = looks_like_template(ev)
    if bad:
        log.error("REFUSED event from %s — %s (raw=%r). Nothing resolved, nothing sent downstream.", ev.source, bad, ev.raw)
        enriched = EnrichedTrackEvent(event=ev, artwork_url=None, source=None, confidence=0.0, match=None,
                                      reasons=[f"refused: {bad}"], cache_hit=False, resolve_start=t0, resolve_end=now())
        from .events import OutputResult
        enriched.outputs.append(OutputResult("guard", False, now(), f"refused: {bad} — not sent to any output"))
        try:
            db.log_event(enriched)
        except Exception:
            log.exception("could not log refused event")
        return enriched
    try:
        r = resolve(ev.artist, ev.title, ev.album)
    except Exception as e:  # pragma: no cover - belt and braces
        log.exception("resolve failed for %s - %s", ev.artist, ev.title)
        r = {"artwork_url": None, "source": None, "confidence": 0.0, "match": None, "reasons": [f"resolver error: {e}"], "cached": False}
    t1 = now()

    enriched = EnrichedTrackEvent(
        event=ev,
        artwork_url=r.get("artwork_url"),
        source=r.get("source"),
        confidence=float(r.get("confidence") or 0.0),
        match=r.get("match"),
        reasons=list(r.get("reasons") or []),
        cache_hit=bool(r.get("cached")) or r.get("source") == "ampliphy_override",
        resolve_start=t0,
        resolve_end=t1,
        alt_artwork_urls=list(r.get("alt_artwork_urls") or []),
    )

    if send:
        for mod in outputs.active_adapters():
            try:
                enriched.outputs.append(mod.send(enriched))
            except Exception as e:
                log.exception("output adapter %s failed", getattr(mod, "NAME", mod))
                from .events import OutputResult
                enriched.outputs.append(OutputResult(getattr(mod, "NAME", "?"), False, now(), f"error: {e}"))

    try:
        db.log_event(enriched)
    except Exception:
        log.exception("could not log event")

    log.info("%s — %s => %s (%.2f) cache=%s resolve=%dms total=%dms outputs=%s",
             ev.artist, ev.title, enriched.source or "UNRESOLVED", enriched.confidence, enriched.cache_hit,
             enriched.resolve_ms, enriched.total_ms, [(o.adapter, o.status) for o in enriched.outputs])
    return enriched
