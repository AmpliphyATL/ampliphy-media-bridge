"""SQLite storage: resolved-track cache, manual override library, lookup log, and the timed event log."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager

from .normalize import cache_key, parse_artist, parse_title

DB_PATH = os.environ.get("RESOLVER_DB", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "resolver.sqlite"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    key            TEXT PRIMARY KEY,
    artist         TEXT, title TEXT, album TEXT,
    artwork_url    TEXT, source TEXT, confidence REAL,
    matched_artist TEXT, matched_title TEXT, matched_album TEXT, isrc TEXT,
    result_json    TEXT,
    hits           INTEGER DEFAULT 0,
    created_at     REAL, last_verified REAL
);
CREATE TABLE IF NOT EXISTS overrides (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    artist_key   TEXT NOT NULL,
    title_key    TEXT NOT NULL,
    artist       TEXT, title TEXT, album TEXT, isrc TEXT,
    artwork_url  TEXT NOT NULL,
    source       TEXT DEFAULT 'ampliphy',
    verified     INTEGER DEFAULT 1,
    notes        TEXT,
    created_at   REAL,
    UNIQUE(artist_key, title_key)
);
CREATE TABLE IF NOT EXISTS events (
    event_id      TEXT PRIMARY KEY,
    input_source  TEXT, raw TEXT,
    artist TEXT, title TEXT, album TEXT,
    artwork_url TEXT, artwork_source TEXT, confidence REAL, cache_hit INTEGER,
    source_ts REAL, received_ts REAL, resolve_start REAL, resolve_end REAL,
    intake_ms INTEGER, resolve_ms INTEGER, total_ms INTEGER,
    outputs_json TEXT, event_json TEXT
);
CREATE TABLE IF NOT EXISTS lookups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL, artist TEXT, title TEXT, album TEXT,
    outcome TEXT, source TEXT, confidence REAL, artwork_url TEXT
);
"""


@contextmanager
def conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        c.executescript(SCHEMA)
        yield c
        c.commit()
    finally:
        c.close()


# ---------------------------------------------------------------- overrides
def get_override(artist: str, title: str) -> dict | None:
    ak, tk = parse_artist(artist).key, parse_title(title).key
    with conn() as c:
        row = c.execute("SELECT * FROM overrides WHERE artist_key=? AND title_key=?", (ak, tk)).fetchone()
        return dict(row) if row else None


def set_override(artist: str, title: str, artwork_url: str, album: str = "", isrc: str = "",
                 source: str = "ampliphy", notes: str = "", verified: bool = True) -> dict:
    ak, tk = parse_artist(artist).key, parse_title(title).key
    with conn() as c:
        c.execute(
            """INSERT INTO overrides(artist_key,title_key,artist,title,album,isrc,artwork_url,source,verified,notes,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(artist_key,title_key) DO UPDATE SET
                 artist=excluded.artist, title=excluded.title, album=excluded.album, isrc=excluded.isrc,
                 artwork_url=excluded.artwork_url, source=excluded.source, verified=excluded.verified, notes=excluded.notes""",
            (ak, tk, artist, title, album, isrc, artwork_url, source, int(verified), notes, time.time()),
        )
        # an override supersedes anything cached for that song
        c.execute("DELETE FROM cache WHERE key LIKE ?", (f"{ak}|{tk}|%",))
    return get_override(artist, title)


def delete_override(override_id: int) -> None:
    with conn() as c:
        c.execute("DELETE FROM overrides WHERE id=?", (override_id,))


def list_overrides() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM overrides ORDER BY artist, title")]


# ---------------------------------------------------------------- cache
def cache_get(artist: str, title: str, album: str = "") -> dict | None:
    key = cache_key(artist, title, album)
    with conn() as c:
        row = c.execute("SELECT * FROM cache WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        c.execute("UPDATE cache SET hits=hits+1 WHERE key=?", (key,))
        return json.loads(row["result_json"])


def cache_put(artist: str, title: str, album: str, result: dict) -> None:
    key = cache_key(artist, title, album)
    m = result.get("match") or {}
    with conn() as c:
        c.execute(
            """INSERT OR REPLACE INTO cache(key,artist,title,album,artwork_url,source,confidence,
               matched_artist,matched_title,matched_album,isrc,result_json,hits,created_at,last_verified)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)""",
            (key, artist, title, album, result.get("artwork_url"), result.get("source"), result.get("confidence"),
             m.get("artist"), m.get("title"), m.get("album"), m.get("isrc"), json.dumps(result), time.time(), time.time()),
        )


def cache_clear() -> int:
    with conn() as c:
        return c.execute("DELETE FROM cache").rowcount


def list_cache(limit: int = 500) -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM cache ORDER BY last_verified DESC LIMIT ?", (limit,))]


def log_lookup(artist: str, title: str, album: str, outcome: str, source: str | None, confidence: float | None, artwork_url: str | None):
    with conn() as c:
        c.execute("INSERT INTO lookups(ts,artist,title,album,outcome,source,confidence,artwork_url) VALUES(?,?,?,?,?,?,?,?)",
                  (time.time(), artist, title, album, outcome, source, confidence, artwork_url))


# ---------------------------------------------------------------- timed event log (instrumentation)
def log_event(enriched) -> None:
    d = enriched.to_dict()
    t = d["timing"]
    with conn() as c:
        c.execute(
            """INSERT OR REPLACE INTO events(event_id,input_source,raw,artist,title,album,artwork_url,artwork_source,confidence,cache_hit,
               source_ts,received_ts,resolve_start,resolve_end,intake_ms,resolve_ms,total_ms,outputs_json,event_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (d["event_id"], d["input_source"], d["raw"], d["artist"], d["title"], d["album"], d["artwork_url"], d["artwork_source"],
             d["confidence"], int(d["cache_hit"]), t["source_ts"], t["received_ts"], t["resolve_start"], t["resolve_end"],
             t["intake_ms"], t["resolve_ms"], t["total_ms"], json.dumps(d["outputs"]), json.dumps(d)),
        )


def list_events(limit: int = 200) -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM events ORDER BY received_ts DESC LIMIT ?", (limit,))]


def event_stats() -> dict:
    with conn() as c:
        row = c.execute(
            """SELECT COUNT(*) n, SUM(artwork_url IS NOT NULL) resolved, SUM(cache_hit) cache_hits,
                      AVG(resolve_ms) avg_resolve_ms, MAX(resolve_ms) max_resolve_ms,
                      AVG(total_ms) avg_total_ms, MAX(total_ms) max_total_ms, AVG(intake_ms) avg_intake_ms
               FROM events"""
        ).fetchone()
        d = dict(row)
        n = d["n"] or 0
        d["resolved_pct"] = round(100.0 * (d["resolved"] or 0) / n, 1) if n else 0.0
        return d


def last_event_for(artist: str, title: str, within_seconds: float) -> dict | None:
    """Used by input adapters to drop duplicate notifications for the same track."""
    with conn() as c:
        row = c.execute(
            "SELECT event_id, received_ts FROM events WHERE lower(artist)=lower(?) AND lower(title)=lower(?) AND received_ts > ? ORDER BY received_ts DESC LIMIT 1",
            (artist, title, time.time() - within_seconds),
        ).fetchone()
        return dict(row) if row else None
