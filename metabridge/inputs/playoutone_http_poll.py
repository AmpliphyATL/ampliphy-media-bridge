"""Input adapter: PlayoutONE HTTP API (pull, not push).

PlayoutONE itself (not Monitor) has a built-in HTTP server: Settings > Ports > "Enable HTTP
Server" (default port 81). Documented commands (support.aiir.com/article/182-http-api):

    http://<playout-pc>:81/?c=GET CURRENT PLAYER NOW_PLAYING   -> "player<TAB>title<TAB>artist"
    http://<playout-pc>:81/?c=GET CURRENT PLAYER POSITION      -> "player<TAB>position_ms<TAB>title<TAB>artist"
    http://<playout-pc>:81/?c=GET PLAYER CURRENT <id>          -> JSON with the player's settings/status

MetaBridge asks every few seconds and treats a change of artist/title as a track event.
No Monitor output configuration is involved at all, which makes this the most deterministic
path: it uses PlayoutONE's own documented API and needs one checkbox in PlayoutONE.

    METABRIDGE_P1_API_URL=http://192.168.1.113:81     (the machine running PlayoutONE)
    METABRIDGE_P1_POLL_SECONDS=2
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request

from ..events import TrackEvent

NAME = "playoutone_http_poll"
log = logging.getLogger("metabridge.p1poll")
TIMEOUT = 4.0


def _get(base: str, command: str) -> str:
    url = base.rstrip("/") + "/?c=" + urllib.parse.quote(command)
    req = urllib.request.Request(url, headers={"User-Agent": "AmpliPhy-MetaBridge"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def now_playing(base: str) -> tuple[str, str, str] | None:
    """Returns (player_index, title, artist) or None if nothing/unknown."""
    text = _get(base, "GET CURRENT PLAYER NOW_PLAYING").strip("\r\n")
    parts = text.split("\t")
    if len(parts) >= 3:
        return parts[0].strip(), parts[1].strip(), parts[2].strip()
    # some builds use " - " text; be forgiving
    if " - " in text:
        a, t = text.split(" - ", 1)
        return "", t.strip(), a.strip()
    return None


def player_json(base: str, player: str) -> dict:
    try:
        text = _get(base, f'GET PLAYER CURRENT "{player}"' if player else "GET PLAYER CURRENT")
        return json.loads(text)
    except Exception:
        return {}


def _find_key(d, names: tuple) -> str:
    """Find a value in nested JSON by any of the given key names (case-insensitive)."""
    if isinstance(d, dict):
        for k, v in d.items():
            if k.lower() in names and isinstance(v, (str, int, float)):
                return str(v)
        for v in d.values():
            r = _find_key(v, names)
            if r:
                return r
    elif isinstance(d, list):
        for v in d:
            r = _find_key(v, names)
            if r:
                return r
    return ""


def build_event(base: str, player: str, title: str, artist: str) -> TrackEvent:
    ev = TrackEvent(artist=artist, title=title, source=NAME, raw=f"{player}\t{title}\t{artist}", source_ts=time.time())
    pj = player_json(base, player)
    if pj:
        dur = _find_key(pj, ("duration", "durationms", "length", "lengthms", "duration_ms"))
        if dur:
            try:
                v = float(re.sub(r"[^\d.]", "", dur) or 0)
                ev.duration_ms = int(v) if v >= 10000 else int(v * 1000)
            except ValueError:
                pass
        ev.album = _find_key(pj, ("album",)) or ""
        ev.isrc = _find_key(pj, ("isrc",)) or ""
        ev.category = _find_key(pj, ("category",)) or ""
    return ev


def _loop(base: str, every: float):
    from ..pipeline import handle, is_duplicate, looks_like_template
    last: tuple | None = None
    failures = 0
    log.info("polling PlayoutONE HTTP API at %s every %.1fs", base, every)
    while True:
        try:
            np = now_playing(base)
            failures = 0
            if np and np[1]:
                key = (np[1].lower(), np[2].lower())
                if key != last:
                    last = key
                    ev = build_event(base, np[0], np[1], np[2])
                    if looks_like_template(ev):
                        log.warning("ignoring odd now-playing %r", np)
                    elif not is_duplicate(ev):
                        threading.Thread(target=handle, args=(ev,), daemon=True).start()
        except Exception as e:
            failures += 1
            if failures in (1, 10, 100) or failures % 300 == 0:
                log.warning("PlayoutONE API unreachable (%s) — attempt %d", e, failures)
        time.sleep(every)


def start_if_configured() -> bool:
    base = os.environ.get("METABRIDGE_P1_API_URL")
    if not base:
        return False
    every = float(os.environ.get("METABRIDGE_P1_POLL_SECONDS", "2"))
    threading.Thread(target=_loop, args=(base, every), daemon=True).start()
    return True
