"""Normalized track events — the only thing the MetaBridge core ever sees.

    Input Adapter -> TrackEvent -> Resolver Core -> EnrichedTrackEvent -> Output Adapter(s)

Every input adapter (PlayoutONE Monitor, a webhook, a text file, a TCP line...)
produces a TrackEvent. Every output adapter (SecureNet/Cirrus, Live365, a webhook...)
consumes an EnrichedTrackEvent. Neither side leaks into the core.

Timing instrumentation lives here too, so it is identical for every adapter:

    source_ts     when the automation system says the track changed (if it tells us)
    received_ts   when MetaBridge received the event
    resolve_start / resolve_end   the core's own work
    sent_ts       per output adapter, when the downstream payload went out
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field


def now() -> float:
    return time.time()


@dataclass
class TrackEvent:
    artist: str
    title: str
    album: str = ""
    isrc: str = ""
    duration_ms: int = 0
    category: str = ""               # automation category, if the source sends one
    source: str = "unknown"          # input adapter name, e.g. "playoutone_monitor"
    raw: str = ""                    # what the adapter actually received (for debugging)
    source_ts: float | None = None   # automation-side timestamp, if supplied
    received_ts: float = field(default_factory=now)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def key(self) -> str:
        return f"{self.artist.strip().lower()}|{self.title.strip().lower()}"


@dataclass
class OutputResult:
    adapter: str
    ok: bool
    sent_ts: float
    status: str = ""                 # e.g. "200", "skipped: not configured", "error: ..."
    payload: dict | None = None      # what we sent (or would have sent) — never contains secrets
    roundtrip_ms: int = 0            # downstream request -> response


@dataclass
class EnrichedTrackEvent:
    event: TrackEvent
    artwork_url: str | None
    source: str | None               # "apple_music", "deezer", "ampliphy_override", ...
    confidence: float
    match: dict | None
    reasons: list
    cache_hit: bool
    resolve_start: float
    resolve_end: float
    outputs: list = field(default_factory=list)   # list[OutputResult]
    alt_artwork_urls: list = field(default_factory=list)   # other trusted candidates' art, best first

    # -- timings (ms) -----------------------------------------------------
    @property
    def intake_ms(self) -> int | None:
        """source -> MetaBridge received (only if the source stamped a time)."""
        if self.event.source_ts is None:
            return None
        return int((self.event.received_ts - self.event.source_ts) * 1000)

    @property
    def resolve_ms(self) -> int:
        return int((self.resolve_end - self.resolve_start) * 1000)

    @property
    def total_ms(self) -> int:
        """received -> last output sent (or resolve_end if nothing was sent)."""
        end = max([o.sent_ts for o in self.outputs] + [self.resolve_end])
        return int((end - self.event.received_ts) * 1000)

    def payload(self) -> dict:
        """The neutral enriched record every output adapter starts from."""
        e = self.event
        m = self.match or {}
        return {
            "artist": e.artist,
            "title": e.title,
            "album": e.album or m.get("album") or "",
            "isrc": e.isrc or m.get("isrc") or "",
            "artwork_url": self.artwork_url,
            "artwork_source": self.source,
            "confidence": round(self.confidence, 3),
            "event_id": e.event_id,
        }

    def to_dict(self) -> dict:
        return {
            **self.payload(),
            "input_source": self.event.source,
            "raw": self.event.raw,
            "cache_hit": self.cache_hit,
            "match": self.match,
            "reasons": self.reasons,
            "timing": {
                "source_ts": self.event.source_ts,
                "received_ts": self.event.received_ts,
                "resolve_start": self.resolve_start,
                "resolve_end": self.resolve_end,
                "intake_ms": self.intake_ms,
                "resolve_ms": self.resolve_ms,
                "total_ms": self.total_ms,
            },
            "outputs": [asdict(o) for o in self.outputs],
        }
