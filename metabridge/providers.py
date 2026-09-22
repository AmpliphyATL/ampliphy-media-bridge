"""Lookup providers. Every provider returns a list of Candidate objects.

All three default providers are keyless and use only their public, documented
search APIs (no scraping):

  * iTunes Search API   (Apple Music catalog)
  * Deezer API
  * MusicBrainz + Cover Art Archive

Spotify is optional: set SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET to enable it.
Only the Python standard library is used so this runs on a stock Mac.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from .normalize import search_terms

USER_AGENT = os.environ.get(
    "RESOLVER_USER_AGENT", "AmpliPhyCoverArtResolver/0.1 (ampliphy.net; contact via site)"
)
TIMEOUT = float(os.environ.get("RESOLVER_HTTP_TIMEOUT", "12"))


@dataclass
class Candidate:
    provider: str
    artist: str
    title: str
    album: str
    artwork_url: str
    isrc: str = ""
    duration_ms: int = 0
    explicit: bool | None = None
    provider_url: str = ""
    all_artists: list = field(default_factory=list)   # extra credited artists if the API gives them
    score: float = 0.0
    confidence: float = 0.0
    reasons: list = field(default_factory=list)

    def public(self) -> dict:
        return {
            "provider": self.provider,
            "artist": self.artist,
            "title": self.title,
            "album": self.album,
            "artwork_url": self.artwork_url,
            "isrc": self.isrc,
            "confidence": round(self.confidence, 3),
            "provider_url": self.provider_url,
            "reasons": self.reasons,
        }


class ProviderError(Exception):
    pass


def _get_json(url: str, headers: dict | None = None, data: bytes | None = None) -> dict:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ProviderError(f"{url} -> HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ProviderError(f"{url} -> {e}") from e


def _head_ok(url: str) -> bool:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return 200 <= r.status < 400
    except Exception:
        return False


# ---------------------------------------------------------------- iTunes / Apple Music
def itunes(artist: str, title: str, album: str = "", limit: int = 12) -> list[Candidate]:
    a, t = search_terms(artist, title)
    q = urllib.parse.urlencode({"term": f"{a} {t}", "entity": "song", "limit": limit, "country": "US"})
    data = _get_json(f"https://itunes.apple.com/search?{q}")
    out = []
    for r in data.get("results", []):
        art = (r.get("artworkUrl100") or "").replace("100x100bb", "600x600bb")
        if not art:
            continue
        out.append(Candidate(
            provider="apple_music",
            artist=r.get("artistName", ""),
            title=r.get("trackName", ""),
            album=r.get("collectionName", ""),
            artwork_url=art,
            duration_ms=int(r.get("trackTimeMillis") or 0),
            explicit=(r.get("trackExplicitness") == "explicit") if r.get("trackExplicitness") else None,
            provider_url=r.get("trackViewUrl", ""),
        ))
    return out


# ---------------------------------------------------------------- Deezer
def deezer(artist: str, title: str, album: str = "", limit: int = 12) -> list[Candidate]:
    a, t = search_terms(artist, title)
    out = []
    # Fielded search first (precise), then a loose search as a fallback.
    for q in (f'artist:"{a}" track:"{t}"', f"{a} {t}"):
        qs = urllib.parse.urlencode({"q": q, "limit": limit})
        data = _get_json(f"https://api.deezer.com/search?{qs}")
        for r in data.get("data", []):
            alb = r.get("album") or {}
            art = alb.get("cover_xl") or alb.get("cover_big") or alb.get("cover_medium") or ""
            if not art:
                continue
            out.append(Candidate(
                provider="deezer",
                artist=(r.get("artist") or {}).get("name", ""),
                title=r.get("title", ""),
                album=alb.get("title", ""),
                artwork_url=art,
                duration_ms=int(r.get("duration") or 0) * 1000,
                explicit=bool(r.get("explicit_lyrics")) if "explicit_lyrics" in r else None,
                provider_url=r.get("link", ""),
            ))
        if out:
            break
    return out


# ---------------------------------------------------------------- MusicBrainz + Cover Art Archive
_last_mb_call = 0.0


def _mb_throttle():
    """MusicBrainz asks for max 1 request/second."""
    global _last_mb_call
    wait = 1.05 - (time.time() - _last_mb_call)
    if wait > 0:
        time.sleep(wait)
    _last_mb_call = time.time()


def musicbrainz(artist: str, title: str, album: str = "", limit: int = 8) -> list[Candidate]:
    a, t = search_terms(artist, title)
    lucene = f'recording:"{t}" AND artist:"{a}"'
    qs = urllib.parse.urlencode({"query": lucene, "fmt": "json", "limit": limit})
    _mb_throttle()
    data = _get_json(f"https://musicbrainz.org/ws/2/recording?{qs}")
    out = []
    seen_releases = set()
    for rec in data.get("recordings", []):
        credit = rec.get("artist-credit") or []
        names = [c.get("name") or (c.get("artist") or {}).get("name", "") for c in credit if isinstance(c, dict)]
        artist_name = names[0] if names else ""
        for rel in rec.get("releases", [])[:3]:
            rid = rel.get("id")
            if not rid or rid in seen_releases:
                continue
            seen_releases.add(rid)
            art = f"https://coverartarchive.org/release/{rid}/front-500"
            # Only offer releases that actually have front art in the archive.
            if not _head_ok(art):
                continue
            out.append(Candidate(
                provider="musicbrainz",
                artist=artist_name,
                title=rec.get("title", ""),
                album=rel.get("title", ""),
                artwork_url=art,
                isrc=(rec.get("isrcs") or [""])[0] if rec.get("isrcs") else "",
                duration_ms=int(rec.get("length") or 0),
                provider_url=f"https://musicbrainz.org/recording/{rec.get('id')}",
                all_artists=names,
            ))
    return out


# ---------------------------------------------------------------- Spotify (optional)
_spotify_token: dict = {"value": None, "exp": 0.0}


def _spotify_auth() -> str | None:
    cid, sec = os.environ.get("SPOTIFY_CLIENT_ID"), os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not cid or not sec:
        return None
    if _spotify_token["value"] and time.time() < _spotify_token["exp"] - 30:
        return _spotify_token["value"]
    basic_auth = base64.b64encode(f"{cid}:{sec}".encode()).decode()
    data = _get_json(
        "https://accounts.spotify.com/api/token",
        headers={"Authorization": f"Basic {basic_auth}", "Content-Type": "application/x-www-form-urlencoded"},
        data=b"grant_type=client_credentials",
    )
    _spotify_token["value"] = data["access_token"]
    _spotify_token["exp"] = time.time() + int(data.get("expires_in", 3600))
    return _spotify_token["value"]


def spotify(artist: str, title: str, album: str = "", limit: int = 10) -> list[Candidate]:
    tok = _spotify_auth()
    if not tok:
        return []
    a, t = search_terms(artist, title)
    qs = urllib.parse.urlencode({"q": f"artist:{a} track:{t}", "type": "track", "limit": limit})
    data = _get_json(f"https://api.spotify.com/v1/search?{qs}", headers={"Authorization": f"Bearer {tok}"})
    out = []
    for r in (data.get("tracks") or {}).get("items", []):
        imgs = (r.get("album") or {}).get("images") or []
        if not imgs:
            continue
        names = [x.get("name", "") for x in r.get("artists", [])]
        out.append(Candidate(
            provider="spotify",
            artist=names[0] if names else "",
            title=r.get("name", ""),
            album=(r.get("album") or {}).get("name", ""),
            artwork_url=imgs[0]["url"],
            isrc=(r.get("external_ids") or {}).get("isrc", ""),
            duration_ms=int(r.get("duration_ms") or 0),
            explicit=r.get("explicit"),
            provider_url=(r.get("external_urls") or {}).get("spotify", ""),
            all_artists=names,
        ))
    return out


# Order matters: this is the fallback hierarchy. Spotify only runs when keys are set.
PROVIDERS = [
    ("apple_music", itunes),
    ("deezer", deezer),
    ("spotify", spotify),
    ("musicbrainz", musicbrainz),
]


# ---------------------------------------------------------------- offline replay (testing only)
# METABRIDGE_REPLAY=1 swaps the live providers for the captured Apple Music / Deezer
# results in data/raw_itunes.json + data/raw_deezer.json, so the whole pipeline
# (intake -> resolve -> cache -> outputs -> timing) can be exercised with no network.
def _replay_index():
    import pathlib
    from .normalize import cache_key
    root = pathlib.Path(__file__).resolve().parent.parent / "data"
    ds = json.loads((root / "test_dataset.json").read_text())
    it = json.loads((root / "raw_itunes.json").read_text())
    dz = json.loads((root / "raw_deezer.json").read_text())
    idx = {}
    for g in ("failures", "controls"):
        for t in ds[g]:
            k = f"{g[:4]}{t['n']}"
            key = cache_key(t["artist"], t["title"]).rsplit("|", 1)[0]
            apple = [Candidate("apple_music", r["artist"], r["title"], r["album"], r["art"], provider_url=r.get("url", ""),
                               duration_ms=int(r.get("ms") or 0), explicit=(r.get("exp") == "explicit") if r.get("exp") else None)
                     for r in (it.get(k) or []) if isinstance(r, dict)]
            deez = [Candidate("deezer", r[0], r[1], r[2], f"https://cdn-images.dzcdn.net/images/cover/{r[3]}/1000x1000-000000-80-0-0.jpg",
                              provider_url=f"https://www.deezer.com/track/{r[4]}") for r in (dz.get(k) or [])]
            idx[key] = {"apple_music": apple, "deezer": deez}
    return idx


if os.environ.get("METABRIDGE_REPLAY"):
    _IDX = _replay_index()

    def _replay(name):
        def fn(artist, title, album="", limit=12):
            from .normalize import cache_key
            time.sleep(0.05)  # pretend to be a network call
            key = cache_key(artist, title).rsplit("|", 1)[0]
            return [Candidate(**{**c.__dict__}) for c in _IDX.get(key, {}).get(name, [])]
        return fn

    PROVIDERS[:] = [("apple_music", _replay("apple_music")), ("deezer", _replay("deezer"))]
