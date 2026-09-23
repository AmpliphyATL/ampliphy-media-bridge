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


# What Cirrus actually displayed vs. what MetaBridge sent, per event (shown on the Monitor tab).
WATCH_EVERY = float(os.environ.get("METABRIDGE_CIRRUS_WATCH_SECONDS", "8"))
cirrus_watch: dict = {}   # event_id -> {"checks": n, "restored": n, "last": "ok"|"overwritten"|"unreachable"}


def _same_text(a: str, b: str) -> bool:
    from .normalize import basic
    return basic(a or "") == basic(b or "")


def _cirrus_watchdog(enriched: EnrichedTrackEvent, duration_ms: int):
    """While the song plays, keep reading Cirrus's public now-playing feed. If Cirrus is
    showing something other than what MetaBridge sent (its own in-stream update, a
    different cover), post ours again immediately. Self-healing; nothing to configure."""
    try:
        from .outputs import securenet_cirrus as sc
    except Exception:
        return
    ev = enriched.event
    started = enriched.resolve_end
    deadline = started + (duration_ms / 1000.0) - 8
    state = cirrus_watch.setdefault(ev.event_id, {"checks": 0, "restored": 0, "last": "pending"})
    time.sleep(6)   # let the first post settle
    while time.time() < deadline:
        st = sc.fetch_status()
        state["checks"] += 1
        if st is None:
            state["last"] = "unreachable"
        else:
            wrong_song = not (_same_text(st["title"], ev.title) and _same_text(st["artist"], ev.artist))
            wrong_cover = bool(enriched.artwork_url) and st["cover"] and not _same_text(st["cover"], enriched.artwork_url) \
                          and st["cover"].split("?")[0] != (enriched.artwork_url or "").split("?")[0]
            if wrong_song or wrong_cover:
                state["last"] = "overwritten"
                mod = _cirrus_adapter()
                if mod:
                    try:
                        res = mod.send(enriched)
                        if res.ok:
                            state["restored"] += 1
                            log.info("%s — %s: Cirrus showed %r / %r%s → restored MetaBridge artwork",
                                     ev.artist, ev.title, st["artist"], st["title"],
                                     " (different cover)" if wrong_cover and not wrong_song else "")
                    except Exception:
                        log.exception("restore send failed")
            else:
                state["last"] = "ok"
        time.sleep(WATCH_EVERY)
    # keep the dict small
    if len(cirrus_watch) > 500:
        for k in list(cirrus_watch)[:-300]:
            cirrus_watch.pop(k, None)


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
            if first_ok and ev.duration_ms >= 30000:
                threading.Thread(target=_cirrus_watchdog, args=(enriched, ev.duration_ms), daemon=True).start()

    try:
        db.log_event(enriched)
    except Exception:
        log.exception("could not log event")

    log.info("%s — %s => %s (%.2f) cache=%s resolve=%dms total=%dms outputs=%s",
             ev.artist, ev.title, enriched.source or "UNRESOLVED", enriched.confidence, enriched.cache_hit,
             enriched.resolve_ms, enriched.total_ms, [(o.adapter, o.status) for o in enriched.outputs])
    return enriched
