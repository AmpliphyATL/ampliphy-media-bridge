"""Confidence scoring. "Wrong artwork is worse than missing artwork."

Each candidate gets points for how well it matches what the station sent.
The score is then divided by the maximum score *available for this input*
(album points only count when the station supplied an album), giving a
0.0 - 1.0 confidence. Only candidates above ACCEPT_THRESHOLD are used.
"""
from __future__ import annotations

import os
import re
from difflib import SequenceMatcher

from .normalize import basic, parse_artist, parse_title
from .providers import Candidate

ACCEPT_THRESHOLD = float(os.environ.get("RESOLVER_ACCEPT_THRESHOLD", "0.80"))
# Above this we stop querying further providers (fast path).
CONFIDENT_THRESHOLD = float(os.environ.get("RESOLVER_CONFIDENT_THRESHOLD", "0.93"))

W_ARTIST, W_TITLE, W_ALBUM = 50, 40, 20
W_ISRC = 100          # reserved: only usable when the station starts sending ISRCs
VERSION_PENALTY = 25  # "Remix" vs not-remix
FEATURE_PENALTY = 6   # station lists a feature the candidate doesn't credit (soft)
FEATURE_BONUS = 4     # station-listed feature that the candidate does credit


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def score_candidate(c: Candidate, artist: str, title: str, album: str = "") -> Candidate:
    in_a, in_t = parse_artist(artist), parse_title(title)
    c_a, c_t = parse_artist(c.artist), parse_title(c.title)
    reasons: list[str] = []
    pts = 0.0
    max_pts = W_ARTIST + W_TITLE

    # ---- artist ---------------------------------------------------------
    cand_names = {basic(x) for x in ([c.artist] + list(c.all_artists))} | set(c_a.all)
    if in_a.key == c_a.key:
        pts += W_ARTIST
        reasons.append("artist exact")
    elif in_a.key in cand_names or any(in_a.key == n for n in cand_names):
        pts += W_ARTIST * 0.95
        reasons.append("artist credited")
    else:
        r = max(_ratio(in_a.key, n) for n in cand_names) if cand_names else 0.0
        # containment: "Nor Kin 4 Life" vs "Nor Kin 4 Life & RJC Productions"
        if in_a.key and any(in_a.key in n or n in in_a.key for n in cand_names if n):
            r = max(r, 0.9)
        if r >= 0.85:
            pts += W_ARTIST * r
            reasons.append(f"artist fuzzy {r:.2f}")
        else:
            reasons.append(f"artist mismatch ({c.artist!r})")

    # ---- title ----------------------------------------------------------
    if in_t.key == c_t.key:
        pts += W_TITLE
        reasons.append("title exact")
    else:
        r = _ratio(in_t.key, c_t.key)
        if r >= 0.85:
            pts += W_TITLE * r
            reasons.append(f"title fuzzy {r:.2f}")
        else:
            reasons.append(f"title mismatch ({c.title!r})")

    # features the station lists vs. what the candidate credits
    station_feats = set(in_t.features) | set(in_a.all[1:])
    cand_feats = set(c_t.features) | set(c_a.all[1:]) | {basic(x) for x in c.all_artists}
    cand_blob = basic(c.artist + " " + c.title + " " + c.album)
    missing = [f for f in station_feats if f not in cand_feats and f not in cand_blob]
    credited = [f for f in station_feats if f not in missing]

    # version tags: remix / extended / live etc. must agree ...
    if in_t.version_tags != c_t.version_tags:
        # ... unless the station names features that only the remix credits
        # ("Champagne Shit feat. Quavo & Latto" *is* the remix even without the word).
        if (not in_t.version_tags and c_t.version_tags == {"remix"} and station_feats and not missing):
            reasons.append("remix implied by credited features")
        else:
            pts -= VERSION_PENALTY
            reasons.append(f"version tags differ {sorted(in_t.version_tags)} vs {sorted(c_t.version_tags)}")

    if missing:
        pts -= FEATURE_PENALTY * min(len(missing), 2)
        reasons.append(f"features not credited: {missing}")
    if credited:
        pts += FEATURE_BONUS * min(len(credited), 2)
        reasons.append(f"features credited: {credited}")

    # ---- album (only when the station sent one) -------------------------
    if album and basic(album):
        max_pts += W_ALBUM
        in_alb, c_alb = basic(album), basic(c.album)
        if in_alb == c_alb:
            pts += W_ALBUM
            reasons.append("album exact")
        else:
            r = _ratio(in_alb, c_alb)
            if r >= 0.8 or in_alb in c_alb or c_alb in in_alb:
                pts += W_ALBUM * max(r, 0.8)
                reasons.append(f"album fuzzy {r:.2f}")
            else:
                reasons.append(f"album differs ({c.album!r})")

    c.score = pts
    c.confidence = max(0.0, min(1.0, pts / max_pts))
    c.reasons = reasons
    return c


_COMPILATION_WORDS = re.compile(
    r"\b(various|compilation|mix|mixes|megamix|hits|greatest|best of|collection|anthology|"
    r"presents|now that'?s|now \d+|party|vol\.?|volume|soundtrack|ost|sampler|jams|throwback|"
    r"classics|essentials|top \d+|\d{2}s? (hits|jams)|'\d\d\b)", re.I)
_PROVIDER_ORDER = {"apple_music": 0, "deezer": 1, "spotify": 2, "musicbrainz": 3}


def album_kind(c: Candidate) -> int:
    """0 = studio album, 1 = single/EP, 2 = compilation. Used only to break ties."""
    alb = (c.album or "").strip()
    if (c.album_artist or "").strip().lower() in ("various artists", "various", "verschiedene interpreten"):
        return 2
    if _COMPILATION_WORDS.search(alb):
        return 2
    low = alb.lower()
    if low.endswith(" - single") or low.endswith(" - ep") or basic(alb) == basic(c.title):
        return 1
    return 0


def best(cands: list[Candidate]) -> Candidate | None:
    """Highest confidence wins. Among equals (the common case: an artist's own album AND a
    compilation both match artist+title exactly) the pecking order is studio album, then
    single, then compilation, then provider order - so the same song always gets the same,
    sensible artwork instead of whichever record the API happened to list first."""
    if not cands:
        return None
    return max(cands, key=lambda c: (round(c.confidence, 2), -album_kind(c), -_PROVIDER_ORDER.get(c.provider, 9)))
