"""Output adapter: POST the enriched record as JSON to any URL.

Useful for testing (point it at a request-catcher), for the AmpliPhy website /
app "now playing" feed, and as the template for provider-specific adapters.

    METABRIDGE_WEBHOOK_URL=https://example.com/nowplaying
    METABRIDGE_WEBHOOK_TOKEN=optional-bearer-token
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from ..events import EnrichedTrackEvent, OutputResult, now

NAME = "webhook"
TIMEOUT = float(os.environ.get("METABRIDGE_OUTPUT_TIMEOUT", "5"))


def send(enriched: EnrichedTrackEvent) -> OutputResult:
    url = os.environ.get("METABRIDGE_WEBHOOK_URL")
    payload = enriched.payload()
    if not url:
        return OutputResult(NAME, False, now(), "skipped: METABRIDGE_WEBHOOK_URL not set", payload)
    headers = {"Content-Type": "application/json", "User-Agent": "AmpliPhy-MetaBridge/0.2"}
    tok = os.environ.get("METABRIDGE_WEBHOOK_TOKEN")
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return OutputResult(NAME, 200 <= r.status < 300, now(), str(r.status), payload)
    except urllib.error.HTTPError as e:
        return OutputResult(NAME, False, now(), f"HTTP {e.code}", payload)
    except Exception as e:  # network errors must never take the pipeline down
        return OutputResult(NAME, False, now(), f"error: {e}", payload)
