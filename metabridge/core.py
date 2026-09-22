"""The resolver: override -> cache -> providers (in fallback order) -> scored best match."""
from __future__ import annotations

import time

from . import db
from .providers import PROVIDERS, ProviderError
from .scoring import ACCEPT_THRESHOLD, CONFIDENT_THRESHOLD, best, score_candidate


def resolve(artist: str, title: str, album: str = "", use_cache: bool = True, debug: bool = False) -> dict:
    artist, title, album = (artist or "").strip(), (title or "").strip(), (album or "").strip()
    started = time.time()
    base = {"artist": artist, "title": title, "album": album}

    # 1. AmpliPhy override library always wins.
    try:
        ov = db.get_override(artist, title)
    except Exception as e:  # a storage hiccup must never block artwork
        ov, db_error = None, str(e)
    else:
        db_error = None
    if ov:
        res = {**base, "artwork_url": ov["artwork_url"], "source": "ampliphy_override", "confidence": 1.0,
               "match": {"artist": ov["artist"], "title": ov["title"], "album": ov["album"], "isrc": ov["isrc"]},
               "cached": False, "ms": int((time.time() - started) * 1000)}
        try:
            db.log_lookup(artist, title, album, "override", res["source"], 1.0, res["artwork_url"])
        except Exception:
            pass
        return res

    # 2. Cache.
    if use_cache and not db_error:
        try:
            hit = db.cache_get(artist, title, album)
        except Exception as e:
            hit, db_error = None, str(e)
        if hit:
            hit = {**hit, "cached": True, "ms": int((time.time() - started) * 1000)}
            try:
                db.log_lookup(artist, title, album, "cache", hit.get("source"), hit.get("confidence"), hit.get("artwork_url"))
            except Exception:
                pass
            return hit

    # 3. Providers, in fallback order. Stop early on a confident match.
    all_cands, errors, per_provider = [], {}, {}
    for name, fn in PROVIDERS:
        try:
            cands = fn(artist, title, album)
        except ProviderError as e:
            errors[name] = str(e)
            continue
        cands = [score_candidate(c, artist, title, album) for c in cands]
        all_cands.extend(cands)
        top = best(cands)
        per_provider[name] = top.public() if top else None
        if top and top.confidence >= CONFIDENT_THRESHOLD:
            break

    winner = best(all_cands)
    if winner and winner.confidence >= ACCEPT_THRESHOLD:
        res = {**base, "artwork_url": winner.artwork_url, "source": winner.provider,
               "confidence": round(winner.confidence, 3),
               "match": {"artist": winner.artist, "title": winner.title, "album": winner.album,
                         "isrc": winner.isrc, "provider_url": winner.provider_url,
                         "duration_ms": winner.duration_ms, "explicit": winner.explicit},
               "reasons": winner.reasons,
               "alt_artwork_urls": [c.artwork_url for c in sorted(all_cands, key=lambda c: -c.confidence)
                                    if c.confidence >= ACCEPT_THRESHOLD and c.artwork_url != winner.artwork_url][:5]}
        outcome = "resolved"
        if not db_error:
            try:
                db.cache_put(artist, title, album, res)
            except Exception as e:
                db_error = str(e)
    else:
        res = {**base, "artwork_url": None, "source": None,
               "confidence": round(winner.confidence, 3) if winner else 0.0,
               "match": None,
               "reasons": (winner.reasons if winner else ["no candidates from any provider"]),
               "rejected_best": winner.public() if winner else None}
        outcome = "unresolved"

    if debug:
        res["providers"] = per_provider
        res["candidates"] = [c.public() for c in sorted(all_cands, key=lambda c: -c.confidence)[:8]]
    if errors:
        res["provider_errors"] = errors
    if db_error:
        res["db_error"] = db_error
    res["cached"] = False
    res["ms"] = int((time.time() - started) * 1000)
    try:
        db.log_lookup(artist, title, album, outcome, res.get("source"), res.get("confidence"), res.get("artwork_url"))
    except Exception:
        pass
    return res
