"""The MetaBridge pipeline: TrackEvent -> resolve -> EnrichedTrackEvent -> output adapters.

This is the only place inputs and outputs meet, and it knows nothing about
PlayoutONE or SecureNet — it just runs the neutral core and hands the result
to whichever output adapters are configured.
"""
from __future__ import annotations

import logging
import os
import threading
import time

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


# Cirrus shows whichever update it heard LAST. Its own in-stream (ICY) update lands a
# few seconds after the song starts, so a single post at second 0 can be overwritten.
# MetaBridge therefore re-posts the same package at these offsets (seconds after the
# song started) so its artwork has the last word. No station-side configuration needed.
REINFORCE_AT = [float(x) for x in os.environ.get("METABRIDGE_CIRRUS_REINFORCE_AT", "15,45").split(",") if x.strip()]


def _cirrus_adapter():
    for mod in outputs.active_adapters():
        if getattr(mod, "NAME", None) == "securenet_cirrus":
            return mod
    return None


def _cirrus_followups(enriched: EnrichedTrackEvent, duration_ms: int, first_ok: bool):
    """Re-post to Cirrus while the song is still playing.
    - If the first post failed: retry every 20 s until it succeeds (song must be >= 30 s).
    - Once a post has succeeded: reinforce at REINFORCE_AT offsets so Cirrus's own
      in-stream update cannot overwrite MetaBridge's artwork."""
    started = enriched.resolve_end
    deadline = started + (duration_ms / 1000.0) - 10 if duration_ms else started + 60
    ok = first_ok
    attempt = 1
    while not ok and duration_ms >= 30000 and time.time() < deadline:
        time.sleep(20)
        attempt += 1
        try:
            mod = _cirrus_adapter()
            if not mod:
                return
            result = mod.send(enriched)
            ok = bool(result.ok)
            if ok:
                log.info("%s — %s (retry %d): Cirrus post succeeded", enriched.event.artist, enriched.event.title, attempt)
            else:
                log.warning("%s — %s (retry %d): Cirrus post failed: %s", enriched.event.artist, enriched.event.title, attempt, result.status)
        except Exception:
            log.exception("retry send failed for %s - %s", enriched.event.artist, enriched.event.title)
    if not ok:
        return
    for offset in sorted(REINFORCE_AT):
        at = started + offset
        if at >= deadline:
            break
        delay = at - time.time()
        if delay > 0:
            time.sleep(delay)
        try:
            mod = _cirrus_adapter()
            if not mod:
                return
            result = mod.send(enriched)
            log.info("%s — %s: Cirrus reinforced at +%ds (%s)", enriched.event.artist, enriched.event.title,
                     int(offset), "ok" if result.ok else result.status)
        except Exception:
            log.exception("reinforce send failed for %s - %s", enriched.event.artist, enriched.event.title)


def handle(ev: TrackEvent, send: bool = True) -> EnrichedTrackEvent:
    """Resolve one track event and deliver it. Never raises — a bad lookup must not stop the station."""
    t0 = now()
    bad = looks_like_template(ev)
    if bad:
        if bad == "empty artist":
            # Ad breaks, sweepers and station IDs arrive with no artist. Expected — not an error.
            log.info("Skipped non-music item from %s (no artist): %r — not sent to Cirrus.", ev.source, (ev.title or "").strip())
        else:
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
        try:
            adapters = outputs.active_adapters()
        except Exception as e:  # bad METABRIDGE_OUTPUTS must not lose the event
            log.exception("invalid outputs setting; event logged but not sent")
            from .events import OutputResult
            enriched.outputs.append(OutputResult("config", False, now(), f"bad outputs setting: {e}"))
            adapters = []
        for mod in adapters:
            try:
                enriched.outputs.append(mod.send(enriched))
            except Exception as e:
                log.exception("output adapter %s failed", getattr(mod, "NAME", mod))
                from .events import OutputResult
                enriched.outputs.append(OutputResult(getattr(mod, "NAME", "?"), False, now(), f"error: {e}"))

        # Follow-up posts to Cirrus: retry if the first one failed, then reinforce so
        # MetaBridge's artwork is the last update Cirrus hears for this song.
        cirrus_results = [r for r in enriched.outputs if getattr(r, "adapter", None) == "securenet_cirrus"]
        if cirrus_results and ev.duration_ms:
            first_ok = any(getattr(r, "ok", False) for r in cirrus_results)
            threading.Thread(
                target=_cirrus_followups,
                args=(enriched, ev.duration_ms, first_ok),
                daemon=True
            ).start()

    try:
        db.log_event(enriched)
    except Exception:
        log.exception("could not log event")

    log.info("%s — %s => %s (%.2f) cache=%s resolve=%dms total=%dms outputs=%s",
             ev.artist, ev.title, enriched.source or "UNRESOLVED", enriched.confidence, enriched.cache_hit,
             enriched.resolve_ms, enriched.total_ms, [(o.adapter, o.status) for o in enriched.outputs])
    return enriched
