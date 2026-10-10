"""Output adapter: Get Me Radio! directory (getmeradio.com) song updates.

Get Me Radio shows "Now Playing" on the station's directory page. When a
station's Metadata Source is set to "API" in Get Me Radio's Edit Station page,
it accepts song updates pushed to:

    https://services.getmeradio.com/api/song_update/
        token=<station token>  station_id=<id>  artist=<...>  title=<...>

Their guidance: send at song changes, never more often than every 20 seconds.
MetaBridge only calls this once per song (never on the Cirrus reinforce/watchdog
re-posts), and refuses to send twice within 20 s.

    METABRIDGE_GETMERADIO_STATION_ID=12442
    METABRIDGE_GETMERADIO_TOKEN=...      (secret; stored encrypted by the dashboard)
"""
from __future__ import annotations

import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from ..events import EnrichedTrackEvent, OutputResult, now

NAME = "getmeradio"
URL = os.environ.get("METABRIDGE_GETMERADIO_URL", "https://services.getmeradio.com/api/song_update/")
TIMEOUT = float(os.environ.get("METABRIDGE_OUTPUT_TIMEOUT", "6"))
MIN_INTERVAL = 20.0

_lock = threading.Lock()
_last = {"ts": 0.0, "key": None}


def configured() -> bool:
    return bool(os.environ.get("METABRIDGE_GETMERADIO_TOKEN") and os.environ.get("METABRIDGE_GETMERADIO_STATION_ID"))


def send(enriched: EnrichedTrackEvent) -> OutputResult:
    token = os.environ.get("METABRIDGE_GETMERADIO_TOKEN", "")
    station = os.environ.get("METABRIDGE_GETMERADIO_STATION_ID", "")
    ev = enriched.event
    safe = {"station_id": station, "artist": ev.artist, "title": ev.title, "token": "***" if token else "(not set)"}
    if not (token and station):
        return OutputResult(NAME, False, now(), "skipped: Get Me Radio station ID / token not set", safe)
    key = (ev.artist or "").lower() + "|" + (ev.title or "").lower()
    with _lock:
        since = time.time() - _last["ts"]
        if key == _last["key"] and since < 600:
            return OutputResult(NAME, True, now(), "skipped: same song already sent", safe)
        if since < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - since)   # respect their 20-second guidance
        _last["ts"], _last["key"] = time.time(), key
    params = {"token": token, "station_id": station, "artist": ev.artist or "", "title": ev.title or ""}
    data = urllib.parse.urlencode(params).encode()
    headers = {"User-Agent": "AmpliPhy-MetaBridge/0.4", "Content-Type": "application/x-www-form-urlencoded"}
    t0 = time.time()
    try:
        req = urllib.request.Request(URL, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read(400).decode("utf-8", "replace").strip()
            ok = 200 <= r.status < 300 and "error" not in body.lower()
            res = OutputResult(NAME, ok, now(), f"HTTP {r.status} · {body[:120]}", safe)
    except urllib.error.HTTPError as e:
        if e.code in (404, 405):
            # Some deployments only take query-string GETs; try once that way.
            try:
                req = urllib.request.Request(URL + "?" + data.decode(), headers={"User-Agent": headers["User-Agent"]})
                with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                    body = r.read(400).decode("utf-8", "replace").strip()
                    res = OutputResult(NAME, 200 <= r.status < 300 and "error" not in body.lower(), now(),
                                       f"HTTP {r.status} (GET) · {body[:120]}", safe)
            except Exception as e2:
                res = OutputResult(NAME, False, now(), f"error: {e2}", safe)
        else:
            res = OutputResult(NAME, False, now(), f"HTTP {e.code}", safe)
    except Exception as e:  # network errors must never take the pipeline down
        res = OutputResult(NAME, False, now(), f"error: {e}", safe)
    res.roundtrip_ms = int((time.time() - t0) * 1000)
    return res
