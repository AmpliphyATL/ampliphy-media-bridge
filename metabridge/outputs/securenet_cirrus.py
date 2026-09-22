"""Output adapter: SecureNet Systems / Cirrus — Direct Metadata Posting.

Cirrus supports third-party metadata capture solutions posting directly whenever a
program/song change is detected (Cirrus Metadata Hub, "Direct Metadata Posting").

    endpoint  https://streamdb9.securenetsystems.net/dx/media_info_update_v2.cfm
    method    GET or POST
    params    stationCallSign, authToken,
              title (required), artist (required), duration (required, seconds),
              album, category, isrc, copyright, label,
              cover        absolute URL to album art — "will be matched by Cirrus otherwise"
              stationFlag  0 = program, 1 = commercials
              responseType xml | head

Behaviour:
  * `cover` is sent ONLY when MetaBridge has a trusted artwork match. Otherwise it is
    omitted so Cirrus performs its own normal artwork matching.
  * `duration` is required and is never invented. It comes from the automation event
    (PlayoutONE %%Duration%%), else from the matched provider record. If neither exists
    the post is skipped and logged loudly.
  * Credentials are configuration only. They are read from the environment, never
    written to code, logs, the event log, reports, or the dashboard.

Configuration:
    METABRIDGE_CIRRUS_CALLSIGN=...          stationCallSign
    METABRIDGE_CIRRUS_TOKEN=...             authToken (secret — rotate the one that was pasted into a chat)
    METABRIDGE_CIRRUS_URL=...               optional; defaults to the documented endpoint
    METABRIDGE_CIRRUS_METHOD=POST|GET       default POST
    METABRIDGE_CIRRUS_RESPONSE=xml|head     default xml (so the response is captured for instrumentation)
    METABRIDGE_CIRRUS_STATIONFLAG=0|1       default 0
    METABRIDGE_CIRRUS_DRY_RUN=1             build everything, send nothing (for rehearsal)
"""
from __future__ import annotations

import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from ..events import EnrichedTrackEvent, OutputResult, now

NAME = "securenet_cirrus"
# Cirrus stores the cover URL in a field of this many characters and silently truncates
# longer ones (observed 2026-09-12: a 135-char Apple URL came back cut to 128 -> broken image).
COVER_MAXLEN = int(os.environ.get("METABRIDGE_CIRRUS_COVER_MAXLEN", "128"))
_APPLE_THUMB = re.compile(r"^https://is\d(?:-ssl)?\.mzstatic\.com/image/thumb/(.+?)/\d+x\d+[a-z]*\.(?:jpg|jpeg|png|webp)$", re.I)


def fit_cover(url: str, alternates: list | None = None, maxlen: int = COVER_MAXLEN) -> tuple[str | None, str]:
    """Return (url_to_send, note). Never send a URL Cirrus would truncate."""
    if not url:
        return None, "none"
    if len(url) <= maxlen:
        return url, "as-is"
    m = _APPLE_THUMB.match(url)
    if m:
        path = m.group(1)
        if path.lower().endswith((".jpg", ".jpeg", ".png")):
            alt = f"https://a1.mzstatic.com/us/r1000/0/{path}"   # Apple's original-file route, same image
            if len(alt) <= maxlen:
                return alt, "apple-original"
    for a in alternates or []:
        if a and len(a) <= maxlen:
            return a, "alternate-provider"
        fa, _ = fit_cover(a, None, maxlen) if a and _APPLE_THUMB.match(a) else (None, "")
        if fa:
            return fa, "alternate-apple-original"
    return None, f"omitted: {len(url)} chars > {maxlen} (Cirrus would truncate it)"
DEFAULT_URL = "https://streamdb9.securenetsystems.net/dx/media_info_update_v2.cfm"
TIMEOUT = float(os.environ.get("METABRIDGE_OUTPUT_TIMEOUT", "6"))
log = logging.getLogger("metabridge.cirrus")


def _cfg() -> dict:
    return {
        "url": os.environ.get("METABRIDGE_CIRRUS_URL", DEFAULT_URL),
        "callsign": os.environ.get("METABRIDGE_CIRRUS_CALLSIGN", ""),
        "token": os.environ.get("METABRIDGE_CIRRUS_TOKEN", ""),
        "method": os.environ.get("METABRIDGE_CIRRUS_METHOD", "POST").upper(),
        "response": os.environ.get("METABRIDGE_CIRRUS_RESPONSE", "xml"),
        "stationflag": os.environ.get("METABRIDGE_CIRRUS_STATIONFLAG", "0"),
        "dry_run": os.environ.get("METABRIDGE_CIRRUS_DRY_RUN", "") not in ("", "0", "false"),
    }


def duration_seconds(enriched: EnrichedTrackEvent) -> tuple[int | None, str]:
    """(seconds, where it came from). Never invented."""
    ev = enriched.event
    if ev.duration_ms and ev.duration_ms > 0:
        return int(round(ev.duration_ms / 1000)), "automation"
    m = enriched.match or {}
    if m.get("duration_ms"):
        return int(round(int(m["duration_ms"]) / 1000)), f"provider:{enriched.source}"
    return None, "missing"


def build_params(enriched: EnrichedTrackEvent, cfg: dict | None = None) -> tuple[dict, dict]:
    """Returns (params_to_send, params_safe_to_log). The second never contains the token."""
    cfg = cfg or _cfg()
    p = enriched.payload()
    secs, dur_src = duration_seconds(enriched)
    params = {
        "stationCallSign": cfg["callsign"],
        "authToken": cfg["token"],
        "title": p["title"],
        "artist": p["artist"],
        "stationFlag": cfg["stationflag"],
        "responseType": cfg["response"],
    }
    if secs is not None:
        params["duration"] = str(secs)
    if p.get("album"):
        params["album"] = p["album"]
    if p.get("isrc"):
        params["isrc"] = p["isrc"]
    if enriched.event.category:
        params["category"] = enriched.event.category
    cover, cover_note = fit_cover(p.get("artwork_url") or "", getattr(enriched, "alt_artwork_urls", []))
    if cover:
        params["cover"] = cover                     # trusted match only; omitted otherwise
    elif p.get("artwork_url"):
        log.warning("cover %s for %s - %s", cover_note, p["artist"], p["title"])
    safe = {k: v for k, v in params.items() if k != "authToken"}
    safe["authToken"] = "***" if cfg["token"] else "(not set)"
    safe["_duration_source"] = dur_src
    safe["_cover_sent"] = "cover" in params
    safe["_cover_note"] = cover_note
    return params, safe


def interpret_response(http_status: int, headers: dict, body: str, response_type: str) -> tuple[bool, str]:
    """Cirrus reports success/error in XML (responseType=xml) or as updateStatus true|false
    (responseType=head). Be strict: only call it OK when Cirrus itself says so."""
    if not (200 <= http_status < 300):
        return False, f"http {http_status}"
    hdr = {k.lower(): v for k, v in headers.items()}
    if "updatestatus" in hdr:
        v = str(hdr["updatestatus"]).strip().lower()
        return v == "true", f"updateStatus={v}"
    low = body.lower()
    if "updatestatus" in low:
        return ("true" in low.split("updatestatus", 1)[1][:20]), "updateStatus in body"
    if not body:
        return True, "2xx, empty body"
    if "<error" in low or "error>" in low or "invalid" in low or "fail" in low or ">false<" in low:
        return False, "cirrus reported error"
    if "success" in low or ">true<" in low or "ok" in low:
        return True, "cirrus reported success"
    return True, "2xx, unrecognised body (check)"


def send(enriched: EnrichedTrackEvent) -> OutputResult:
    cfg = _cfg()
    params, safe = build_params(enriched, cfg)

    if not cfg["callsign"] or not cfg["token"]:
        return OutputResult(NAME, False, now(), "skipped: METABRIDGE_CIRRUS_CALLSIGN / METABRIDGE_CIRRUS_TOKEN not set", safe)
    if "duration" not in params:
        log.error("Cirrus post skipped — no duration for %s - %s (Monitor must send %%%%Duration%%%%, or no provider match had one)",
                  enriched.event.artist, enriched.event.title)
        return OutputResult(NAME, False, now(), "skipped: duration missing (required by Cirrus; not invented)", safe)
    if cfg["dry_run"]:
        return OutputResult(NAME, True, now(), "dry-run: built, not sent", safe)

    data = urllib.parse.urlencode(params).encode()
    headers = {"User-Agent": "AmpliPhy-MetaBridge/0.3", "Content-Type": "application/x-www-form-urlencoded"}
    if cfg["method"] == "GET":
        req = urllib.request.Request(cfg["url"] + "?" + urllib.parse.urlencode(params), headers=headers, method="GET")
    else:
        req = urllib.request.Request(cfg["url"], data=data, headers=headers, method="POST")

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read(2000).decode("utf-8", "replace").strip()
            rt = int((time.time() - t0) * 1000)
            ok, verdict = interpret_response(r.status, dict(r.headers), body, cfg["response"])
            status = f"HTTP {r.status} · {verdict} · {rt} ms" + (f" · {body[:300]}" if body else "")
            return OutputResult(NAME, ok, now(), status, safe, roundtrip_ms=rt)
    except urllib.error.HTTPError as e:
        body = e.read(500).decode("utf-8", "replace").strip() if e.fp else ""
        return OutputResult(NAME, False, now(), f"HTTP {e.code} · {body[:300]}", safe, roundtrip_ms=int((time.time() - t0) * 1000))
    except Exception as e:  # network errors must never take the pipeline down
        return OutputResult(NAME, False, now(), f"error: {e}", safe, roundtrip_ms=int((time.time() - t0) * 1000))
