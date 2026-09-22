"""Metadata normalization for the AmpliPhy cover-art resolver.

Turns messy station metadata ("Compton AV feat. Steelz, Blueface", "Omnivert - Remix",
"Beyoncé") into comparable, search-friendly forms without throwing away the
information (remix / extended / explicit) that distinguishes one release from another.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# Words that mark a *version* of a song. If the station says "Remix" and a
# candidate does not (or vice versa) that is a real mismatch, not noise.
VERSION_TAGS = {
    "remix", "extended", "extended mix", "club mix", "radio edit", "edit",
    "acoustic", "live", "instrumental", "sped up", "slowed", "remastered",
    "remaster", "demo", "mix", "version", "dub", "vip",
}
# Tags that are pure packaging noise and never change which record it is.
NOISE_TAGS = {"clean", "explicit", "dirty", "single", "album version", "original", "original mix"}

FEAT_RE = re.compile(r"\s*[\(\[]?\s*(?:feat\.?|ft\.?|featuring)\s+([^\)\]]+)[\)\]]?\s*$", re.I)
FEAT_ANY_RE = re.compile(r"\s*[\(\[]?\s*(?:feat\.?|ft\.?|featuring)\s+", re.I)
BRACKET_RE = re.compile(r"\s*[\(\[]([^\)\]]*)[\)\]]")
DASH_SUFFIX_RE = re.compile(r"\s+[-–—]\s+(.+)$")
ARTIST_SPLIT_RE = re.compile(r"\s*(?:,|&|\+|\bx\b|\band\b|/|;)\s*", re.I)
PUNCT_RE = re.compile(r"[^\w\s]")
WS_RE = re.compile(r"\s+")


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def basic(s: str) -> str:
    """Lowercase, accent-free, punctuation-free, single-spaced."""
    s = strip_accents(s or "").lower()
    s = re.sub(r"['’]", "", s)  # don't -> dont, vibin' -> vibin
    s = s.replace("$", "s").replace("&", " and ")
    s = PUNCT_RE.sub(" ", s)
    return WS_RE.sub(" ", s).strip()


@dataclass
class Title:
    raw: str
    core: str                      # "omnivert"
    version_tags: set = field(default_factory=set)   # {"remix"}
    features: list = field(default_factory=list)     # ["james fauntleroy"]

    @property
    def key(self) -> str:
        return basic(self.core)


@dataclass
class Artist:
    raw: str
    primary: str                   # "compton av"
    all: list                      # ["compton av", "steelz", "blueface", ...]

    @property
    def key(self) -> str:
        return basic(self.primary)


def parse_title(raw: str) -> Title:
    t = raw or ""
    features: list[str] = []
    tags: set[str] = set()

    # "Anytime feat. James Fauntleroy" / "Yaya (feat. X)"
    m = FEAT_RE.search(t)
    if m:
        features = [basic(x) for x in ARTIST_SPLIT_RE.split(m.group(1)) if x.strip()]
        t = t[: m.start()]

    # "(Radio Edit)" "[Clean]" "(Remastered 2011)"
    def _bracket(mm: re.Match) -> str:
        inner = basic(mm.group(1))
        if not inner:
            return ""
        if FEAT_ANY_RE.match(mm.group(0)):
            return ""
        if inner in NOISE_TAGS:
            return ""
        for v in VERSION_TAGS:
            if v in inner:
                tags.add(_canon_tag(inner))
                return ""
        # unknown parenthetical (e.g. "(Part 2)") stays part of the core title
        return " " + mm.group(1)

    t = BRACKET_RE.sub(_bracket, t)

    # "Omnivert - Remix" / "Song - Explicit"
    m = DASH_SUFFIX_RE.search(t)
    if m:
        suffix = basic(m.group(1))
        if suffix in NOISE_TAGS:
            t = t[: m.start()]
        elif any(v in suffix for v in VERSION_TAGS):
            tags.add(_canon_tag(suffix))
            t = t[: m.start()]

    # trailing bare tags: "Get Me Bodied Extended Mix"
    words = basic(t).split()
    for n in (2, 1):
        if len(words) > n:
            tail = " ".join(words[-n:])
            if tail in VERSION_TAGS:
                tags.add(_canon_tag(tail))
                words = words[:-n]
                break
            if tail in NOISE_TAGS:
                words = words[:-n]
                break
    core = " ".join(words) if words else basic(t)
    return Title(raw=raw, core=core, version_tags=tags, features=features)


def _canon_tag(s: str) -> str:
    s = basic(s)
    if "extended" in s:
        return "extended"
    if "remix" in s:
        return "remix"
    if "radio" in s or s == "edit":
        return "edit"
    if "remaster" in s:
        return "remastered"
    if "acoustic" in s:
        return "acoustic"
    if "live" in s:
        return "live"
    if "instrumental" in s:
        return "instrumental"
    if "sped" in s:
        return "sped up"
    if "slowed" in s:
        return "slowed"
    return s


def parse_artist(raw: str) -> Artist:
    a = raw or ""
    a = FEAT_ANY_RE.split(a)[0] if FEAT_ANY_RE.search(a) else a
    # also pull features hidden in the artist field into the list
    feats = FEAT_RE.search(raw or "")
    parts = [basic(p) for p in ARTIST_SPLIT_RE.split(a) if p.strip()]
    if feats:
        parts += [basic(x) for x in ARTIST_SPLIT_RE.split(feats.group(1)) if x.strip()]
    parts = [p for p in parts if p]
    primary = parts[0] if parts else basic(raw)
    return Artist(raw=raw, primary=primary, all=parts or [primary])


def cache_key(artist: str, title: str, album: str | None = None) -> str:
    """Stable key for the cache/override tables. Album is included only when supplied."""
    a = parse_artist(artist)
    t = parse_title(title)
    tags = ",".join(sorted(t.version_tags))
    return "|".join([a.key, t.key, tags, basic(album or "")])


def search_terms(artist: str, title: str) -> tuple[str, str]:
    """Best free-text pair to send to a search API: primary artist + core title."""
    return parse_artist(artist).primary, parse_title(title).core
