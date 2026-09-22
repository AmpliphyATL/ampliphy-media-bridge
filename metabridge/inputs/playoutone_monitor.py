"""Input adapter: PlayoutONE Monitor.

Monitor sends the current track to configured outputs using a format string made of
tokens such as %%Artist%%, %%Title%%, %%Album%%, %%ISRC%%, %%Duration%%. It can deliver
that string by HTTP (GET a URL that contains the tokens), or by TCP/UDP as a line of text.

This adapter accepts all of those shapes and turns them into one TrackEvent:

  * HTTP GET  /inbound/playoutone?artist=%%Artist%%&title=%%Title%%&album=%%Album%%&isrc=%%ISRC%%
              (recommended — fields arrive separately, nothing to guess)
  * HTTP GET  /inbound/playoutone?data=%%Artist%% - %%Title%%
              (what Monitor sends SecureNet today; split on the first " - ")
  * HTTP POST /inbound/playoutone   with the same fields as JSON or form data
  * TCP line  "%%Artist%% - %%Title%%"  or  "artist=...|title=...|album=..."

Optional %%NextArtist%%/%%NextTitle%% fields are accepted and stored for the
Phase 2B prefetch, but nothing acts on them yet.
"""
from __future__ import annotations

import re
import time

from ..events import TrackEvent

NAME = "playoutone_monitor"
SEP_RE = re.compile(r"\s+[-–—|]\s+")   # "Artist - Title", "Artist – Title", "Artist | Title"

# Accept a few spellings for each field so the Monitor format string can be forgiving.
FIELD_ALIASES = {
    "artist": ("artist", "Artist", "ARTIST", "a"),
    "title": ("title", "Title", "TITLE", "t", "song"),
    "album": ("album", "Album", "ALBUM"),
    "isrc": ("isrc", "ISRC"),
    "duration": ("duration", "Duration", "length"),
    "data": ("data", "text", "np", "nowplaying", "metadata", "StreamTitle"),
    "ts": ("ts", "time", "timestamp", "airtime"),
    "category": ("category", "Category", "cat"),
    "next_artist": ("next_artist", "NextArtist"),
    "next_title": ("next_title", "NextTitle"),
}


def parse_query(raw_query: str) -> dict:
    """Parse a query string the way PlayoutONE Monitor's HTTP Get actually builds it.

    Monitor appends the *Parameters* box to the URL verbatim, so what arrives can be:
        artist=X&title=Y&duration=Z            (normal)
        artist=X%0Atitle=Y%0Aduration=Z        (one parameter per line -> newline-joined)
        ?artist=X&title=Y                      (a stray leading '?')
        ...&duration=%%Duration%%artist=X...   (URL-box tokens, unsubstituted, glued to Parameters)
    Newlines and '?' are treated as separators; keys are stripped of leading '?'/'&'.
    Later values win, so substituted Parameters override unsubstituted URL-box tokens."""
    from urllib.parse import unquote_plus
    text = unquote_plus(raw_query or "")
    text = text.replace("\r", "\n").replace("\n", "&").replace("?", "&")
    # "duration=%%Duration%%artist=Jaydon" -> split the glued key off the unsubstituted token
    text = re.sub(r"(%%[A-Za-z]+%%)(?=[A-Za-z_]+=)", r"\1&", text)
    out: dict = {}
    for part in text.split("&"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip().lstrip("?&").strip()
        v = v.strip()
        if not k:
            continue
        if v and "%%" in v and k in out and "%%" not in out[k]:
            continue  # don't let an unsubstituted token overwrite a real value
        out[k] = v
    return out


def _pick(params: dict, field: str) -> str:
    for k in FIELD_ALIASES[field]:
        v = params.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def split_line(line: str) -> tuple[str, str]:
    """'Artist - Title' -> (artist, title). Splits on the FIRST separator so titles
    containing ' - ' (e.g. 'Omnivert - Remix') stay intact."""
    line = (line or "").strip()
    if "artist=" in line.lower():
        kv = dict(p.split("=", 1) for p in line.split("|") if "=" in p)
        low = {k.strip().lower(): v.strip() for k, v in kv.items()}
        return low.get("artist", ""), low.get("title", "")
    parts = SEP_RE.split(line, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return "", line


def parse_duration_ms(s: str) -> int:
    """Monitor's %%Duration%% may be seconds ("225"), seconds with decimals ("225.4"),
    milliseconds ("225400"), or a clock ("3:45" / "0:03:45"). Returns ms, or 0 if unknown.
    Never guesses: anything unparseable is 0 and the Cirrus adapter will refuse to invent it."""
    s = (s or "").strip()
    if not s:
        return 0
    if ":" in s:
        try:
            parts = [float(x) for x in s.split(":")]
        except ValueError:
            return 0
        secs = 0.0
        for x in parts:
            secs = secs * 60 + x
        return int(secs * 1000)
    try:
        v = float(s)
    except ValueError:
        return 0
    if v <= 0:
        return 0
    return int(v) if v >= 10000 else int(v * 1000)   # >= 10000 can't be seconds for a song; treat as ms


def parse_timestamp(s: str) -> float | None:
    """Accept epoch seconds, epoch ms, or HH:MM:SS today (Monitor's %%AirTime%% style)."""
    if not s:
        return None
    try:
        f = float(s)
        return f / 1000.0 if f > 1e11 else f
    except ValueError:
        pass
    m = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", s)
    if m:
        lt = time.localtime()
        h, mi, se = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
        return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, mi, se, 0, 0, -1))
    return None


def from_params(params: dict, raw: str = "") -> TrackEvent | None:
    """Build a TrackEvent from query/form/JSON parameters. Returns None if there is no track in it."""
    artist, title = _pick(params, "artist"), _pick(params, "title")
    if not (artist and title):
        data = _pick(params, "data")
        if data:
            artist, title = split_line(data)
            raw = raw or data
    if not title:
        return None
    ev = TrackEvent(
        artist=artist, title=title, album=_pick(params, "album"), isrc=_pick(params, "isrc"),
        duration_ms=parse_duration_ms(_pick(params, "duration")), category=_pick(params, "category"),
        source=NAME, raw=raw or str(params),
        source_ts=parse_timestamp(_pick(params, "ts")),
    )
    return ev


def from_line(line: str) -> TrackEvent | None:
    """A TCP/UDP line from Monitor. Either
         artist=%%Artist%%|title=%%Title%%|duration=%%DurationSE%%|album=%%Album%%   (recommended)
       or the plain  %%Artist%% - %%Title%%  string."""
    line = (line or "").strip()
    if not line:
        return None
    if "=" in line and ("|" in line or line.lower().startswith(("artist=", "title="))):
        kv = {}
        for part in line.split("|"):
            if "=" in part:
                k, v = part.split("=", 1)
                kv[k.strip()] = v.strip()
        ev = from_params(kv, raw=line)
        if ev:
            ev.source = NAME + "_tcp"
        return ev
    artist, title = split_line(line)
    if not title:
        return None
    return TrackEvent(artist=artist, title=title, source=NAME + "_tcp", raw=line)
