"""AmpliPhy MetaBridge — real-time music metadata enrichment middleware (Phase 2A + Cirrus adapter).

    Input Adapter -> TrackEvent -> Resolver Core -> EnrichedTrackEvent -> Output Adapter(s)

Run:   pip3 install fastapi uvicorn
       python3 -m uvicorn app:app --host 0.0.0.0 --port 8765

Intake (PlayoutONE Monitor points here):
  GET/POST /inbound/playoutone?artist=%%Artist%%&title=%%Title%%&album=%%Album%%&isrc=%%ISRC%%
  GET      /inbound/playoutone?data=%%Artist%% - %%Title%%
  (responds 200 immediately; resolving + forwarding happens in the background so
   Monitor never waits on us)

Generic:
  POST /resolve                {"artist","title","album"}  -> enriched JSON (synchronous, no forwarding)
  GET  /events  /events.csv    timed event log (instrumentation)
  GET  /stats                  resolve rate + latency summary
  GET  /                       dashboard
"""
from __future__ import annotations

import csv
import html
import io
import json
import logging
import os
import time

from fastapi import BackgroundTasks, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel

from metabridge import config, db, outputs

config.load()
from metabridge.core import resolve
from metabridge.inputs import playoutone_http_poll, playoutone_monitor, tcp_listener
from metabridge.pipeline import handle, is_duplicate

logging.basicConfig(level=os.environ.get("METABRIDGE_LOG", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = FastAPI(title="AmpliPhy MetaBridge", version="0.4.3")


class _GluedPathRewrite:
    """PlayoutONE Monitor appends its Parameters box to the URL verbatim. If the URL box
    has no trailing '?', the request path becomes /inbound/playoutoneartist=...  This
    moves that glued tail into the query string so the normal intake handles it."""
    PREFIX = b"/inbound/playoutone"

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            rp = scope.get("raw_path") or scope.get("path", "").encode()
            if rp.startswith(self.PREFIX) and len(rp) > len(self.PREFIX) and rp[len(self.PREFIX):len(self.PREFIX) + 1] not in (b"/",):
                tail = rp[len(self.PREFIX):].lstrip(b"?&")
                qs = scope.get("query_string", b"")
                scope["query_string"] = tail + (b"&" + qs if qs else b"")
                scope["path"] = "/inbound/playoutone"
                scope["raw_path"] = self.PREFIX
        await self.app(scope, receive, send)


app.add_middleware(_GluedPathRewrite)


@app.on_event("startup")
def _startup():
    tcp_listener.start_if_configured()
    playoutone_http_poll.start_if_configured()
    logging.getLogger("metabridge").info("outputs: %s", os.environ.get("METABRIDGE_OUTPUTS", "log"))


# ---------------------------------------------------------------- intake: PlayoutONE Monitor
@app.api_route("/inbound/playoutone", methods=["GET", "POST"], response_class=PlainTextResponse)
async def inbound_playoutone(request: Request, background: BackgroundTasks):
    return await _inbound(request, background, "")


async def _inbound(request: Request, background: BackgroundTasks, glued: str):
    raw = str(request.url.query)
    params = playoutone_monitor.parse_query(raw)
    if request.method == "POST":
        ctype = request.headers.get("content-type", "")
        body = await request.body()
        raw = body.decode("utf-8", "replace")
        if "json" in ctype:
            try:
                params.update(json.loads(raw) or {})
            except json.JSONDecodeError:
                pass
        elif "form" in ctype:
            params.update(dict(await request.form()))
        elif raw.strip():
            params.setdefault("data", raw.strip())
    ev = playoutone_monitor.from_params(params, raw=raw)
    # Replies are single bare words: PlayoutONE Monitor compares the response body
    # against its "Return Value" box (set it to: queued) to decide OK vs error.
    if not ev:
        return PlainTextResponse("ignored", status_code=200)
    from metabridge.pipeline import looks_like_template
    bad = looks_like_template(ev)
    if bad:
        handle(ev)  # records the refusal on the dashboard, sends nothing
        return PlainTextResponse("refused", status_code=200)
    if is_duplicate(ev):
        return PlainTextResponse("queued", status_code=200)   # already handled a moment ago
    background.add_task(handle, ev)          # answer Monitor now, work after
    return PlainTextResponse("queued", status_code=200)


# ---------------------------------------------------------------- generic API
class TrackIn(BaseModel):
    artist: str
    title: str
    album: str = ""
    debug: bool = False


@app.post("/resolve")
def resolve_post(t: TrackIn):
    return resolve(t.artist, t.title, t.album, debug=t.debug)


@app.get("/resolve")
def resolve_get(artist: str, title: str, album: str = "", debug: bool = False):
    return resolve(artist, title, album, debug=debug)


@app.get("/events")
def events(limit: int = 200):
    return [json.loads(e["event_json"]) for e in db.list_events(limit)]


@app.get("/events.csv", response_class=PlainTextResponse)
def events_csv(limit: int = 1000):
    rows = db.list_events(limit)
    buf = io.StringIO()
    cols = ["event_id", "received_ts", "input_source", "artist", "title", "album", "artwork_source", "confidence", "cache_hit",
            "artwork_url", "intake_ms", "resolve_ms", "total_ms", "outputs_json"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        r["received_ts"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["received_ts"]))
        w.writerow(r)
    return buf.getvalue()


@app.get("/stats")
def stats():
    return {**db.event_stats(), "outputs": os.environ.get("METABRIDGE_OUTPUTS", "log")}


@app.get("/overrides")
def overrides_list():
    return db.list_overrides()


@app.post("/overrides")
def overrides_add(o: dict):
    return db.set_override(o["artist"], o["title"], o["artwork_url"], o.get("album", ""), o.get("isrc", ""), o.get("source", "ampliphy"), o.get("notes", ""))


@app.delete("/overrides/{oid}")
def overrides_delete(oid: int):
    db.delete_override(oid)
    return {"deleted": oid}


@app.get("/cache")
def cache_list():
    return db.list_cache()


@app.delete("/cache")
def cache_clear():
    return {"cleared": db.cache_clear()}


# ---------------------------------------------------------------- dashboard
STYLE = """<style>
body{font-family:-apple-system,Helvetica,Arial,sans-serif;margin:0;padding:0;background:#fafafa;color:#222}
header{background:#fff;padding:20px 24px;border-bottom:1px solid #e0e0e0;box-shadow:0 1px 3px rgba(0,0,0,0.05)}
main{margin:24px;max-width:1200px}
h1{font-size:24px;margin:0}
h1 small{font-size:14px;font-weight:400;color:#777}
h2{font-size:16px;margin:32px 0 16px;padding-bottom:8px;border-bottom:1px solid #e0e0e0}
input{padding:8px;margin:2px;border:1px solid #ddd;border-radius:4px;font-size:13px;width:200px}
input:focus{outline:0;border-color:#007aff;box-shadow:0 0 0 2px rgba(0,122,255,0.1)}
button{padding:8px 14px;margin:2px;background:#007aff;color:#fff;border:0;border-radius:4px;font-size:13px;cursor:pointer}
button:hover{background:#0051d5}
button.secondary{background:#e8e8e8;color:#222}
button.secondary:hover{background:#d0d0d0}
table{border-collapse:collapse;width:100%;background:#fff;border:1px solid #e0e0e0;border-radius:4px;overflow:hidden}
td,th{border-bottom:1px solid #e0e0e0;padding:10px;font-size:13px;vertical-align:top;text-align:left}
th{background:#f5f5f5;font-weight:500}
tr:last-child td{border-bottom:0}
img{border:1px solid #ddd;border-radius:4px;background:#fff}
.res{background:#f9f9f9;padding:16px;border-radius:6px;border-left:4px solid #007aff;margin-bottom:16px}
.res.unresolved{border-left-color:#dc3545}
.kpi{display:inline-block;background:#fff;border:1px solid #e0e0e0;border-radius:6px;padding:14px 16px;margin:0 8px 12px 0;font-size:13px;min-width:100px}
.kpi b{font-size:18px;display:block;color:#007aff}
.kpi span{color:#777;font-size:12px}
.ok{color:#2e9e5b;font-weight:500}
.no{color:#dc3545;font-weight:500}
.small{color:#777;font-size:12px}
.form-group{margin-bottom:16px;padding:12px;background:#f9f9f9;border-radius:4px}
.form-group label{display:block;font-size:12px;font-weight:500;margin-bottom:4px;color:#666}
.form-inline{display:flex;gap:8px;align-items:flex-end}
.form-inline input{margin:0}
.form-inline button{margin:0}
.section{background:#fff;padding:16px;border-radius:6px;margin-bottom:16px}
.tab-nav{display:flex;gap:8px;margin-bottom:16px;border-bottom:2px solid #e0e0e0}
.tab-nav a{padding:10px 16px;text-decoration:none;color:#666;border-bottom:3px solid transparent;cursor:pointer}
.tab-nav a.active{color:#007aff;border-bottom-color:#007aff}
.tab-content{display:none}
.tab-content.active{display:block}
.badge{display:inline-block;padding:2px 8px;background:#e8e8e8;border-radius:3px;font-size:11px;margin:0 2px}
.badge.unresolved{background:#ffe8e8;color:#c00}
.unresolved-row{background:#fffbfb}
</style>"""


@app.get("/", response_class=HTMLResponse)
def dashboard(artist: str = "", title: str = "", album: str = "", tab: str = "monitor"):
    e = html.escape
    st = db.event_stats()

    def fmt_ms(v):
        return "—" if v is None else f"{int(v)} ms"

    # Resolution lookup result
    result_html = ""
    if artist and title:
        r = resolve(artist, title, album, debug=True)
        m = r.get("match") or {}
        art = r.get("artwork_url")
        confidence = r.get('confidence', 0)
        res_class = "unresolved" if not art else ""

        result_html = f"""<div class="res {res_class}">
            <b>{'✓ RESOLVED' if art else '✗ UNRESOLVED'}</b>
            {f'<br><img src="{e(art)}" width="120" style="margin-top:8px">' if art else ''}
            <div style="margin-top:8px">
                <b>Matched:</b> {e(str(m.get('artist')))} — {e(str(m.get('title')))} {f'<i>({e(str(m.get("album")))})</i>' if m.get('album') else ''}
                <br><b>Confidence:</b> {confidence:.2f} · <b>Time:</b> {r.get('ms')} ms
                {f'<br><span class="badge">cached</span>' if r.get('cached') else ''}
            </div>
            {f'<div class="small" style="margin-top:8px"><b>Why:</b> {"; ".join(e(x) for x in r.get("reasons", []))}</div>' if r.get('reasons') else ''}
            <div style="margin-top:12px">
                <a href="/deep-dive?artist={e(artist)}&title={e(title)}" target="_blank" style="color:#007aff;text-decoration:none">📋 Deep Dive</a>
                &nbsp;|&nbsp;
                <form method="post" action="/force-resolve" style="display:inline">
                    <input type="hidden" name="artist" value="{e(artist)}"><input type="hidden" name="title" value="{e(title)}"><input type="hidden" name="album" value="{e(album)}">
                    <button type="submit" class="secondary" style="padding:4px 8px;font-size:12px">🔄 Force Resolve</button>
                </form>
            </div>
            <form method="post" action="/ui/override" style="margin-top:12px">
                <input type="hidden" name="artist" value="{e(artist)}"><input type="hidden" name="title" value="{e(title)}"><input type="hidden" name="album" value="{e(album)}">
                <div class="form-inline">
                    <input name="artwork_url" placeholder="artwork URL" value="{e(art or '')}" style="flex:1">
                    <button type="submit">📌 Pin Override</button>
                </div>
            </form>
        </div>"""

    # Live events
    ev_rows = ""
    for r in db.list_events(50):
        outs = json.loads(r["outputs_json"] or "[]")
        has_art = r['artwork_url'] is not None and r['artwork_url'] != ""
        row_class = "" if has_art else "unresolved-row"
        ev_rows += (f"<tr class='{row_class}'>"
                    f"<td class='small'>{time.strftime('%H:%M:%S', time.localtime(r['received_ts']))}</td>"
                    f"<td>{'<img src=' + chr(34) + e(r['artwork_url']) + chr(34) + ' width=36>' if has_art else '—'}</td>"
                    f"<td><b>{e(r['artist'])}</b><br><span class='small'>{e(r['title'])}</span></td>"
                    f"<td class='{'ok' if has_art else 'no'}'><strong>{'✓ Resolved' if has_art else '✗ Unresolved'}</strong><br><span class='small'>{r['confidence']:.2f} {' cache' if r['cache_hit'] else ''}</span></td>"
                    f"<td class='small'>{fmt_ms(r['resolve_ms'])}</td>"
                    f"<td><a href='/deep-dive?artist={e(r['artist'])}&title={e(r['title'])}' style='color:#007aff;text-decoration:none;font-size:12px'>details</a></td>"
                    f"</tr>")

    # Unresolved top tracks
    unresolved = _get_unresolved(20)
    unresolved_rows = ""
    for u in unresolved:
        unresolved_rows += (f"<tr>"
            f"<td><b>{e(u['artist'])}</b><br><span class='small'>{e(u['title'])}</span></td>"
            f"<td class='small'>{u['count']} times</td>"
            f"<td class='small'>{time.strftime('%H:%M:%S', time.localtime(u['last_seen']))}</td>"
            f"<td><form method='post' action='/force-resolve' style='margin:0'>"
            f"<input type='hidden' name='artist' value='{e(u['artist'])}'><input type='hidden' name='title' value='{e(u['title'])}'>"
            f"<button type='submit' class='secondary' style='padding:4px 8px;font-size:11px'>Retry</button></form></td>"
            f"</tr>")

    # Overrides
    ov_rows = "".join(
        f"<tr><td><img src='{e(o['artwork_url'])}' width='40'></td>"
        f"<td><b>{e(o['artist'])}</b><br><span class='small'>{e(o['title'])}</span></td>"
        f"<td class='small'>{e(o['artwork_url'][:60])}...</td>"
        f"<td><form method='post' action='/ui/override/delete' style='margin:0'><input type='hidden' name='oid' value='{o['id']}'><button type='submit' class='secondary' style='padding:4px 8px;font-size:11px'>Remove</button></form></td></tr>"
        for o in db.list_overrides(limit=50))

    return f"""<!doctype html><html><head><meta charset="utf-8"><title>AmpliPhy MetaBridge</title>{STYLE}
    <script>
    function switchTab(name) {{
        document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
        document.querySelectorAll('.tab-nav a').forEach(el => el.classList.remove('active'));
        document.getElementById('tab-' + name).classList.add('active');
        document.querySelector('[data-tab="' + name + '"]').classList.add('active');
    }}
    window.addEventListener('load', () => switchTab('{tab}'));
    </script>
    </head><body>
    <header>
        <h1>AmpliPhy MetaBridge <small>Phase 2A</small></h1>
    </header>
    <main>
        <div style="display:flex;gap:16px;margin-bottom:20px">
            <div class="kpi"><b>{st['n'] or 0}</b><span>events</span></div>
            <div class="kpi"><b>{st['resolved_pct']}%</b><span>resolved</span></div>
            <div class="kpi"><b>{st['cache_hits'] or 0}</b><span>cache hits</span></div>
            <div class="kpi"><b>{fmt_ms(st['avg_resolve_ms'])}</b><span>avg resolve</span></div>
            <div class="kpi"><b>{fmt_ms(st['max_resolve_ms'])}</b><span>max resolve</span></div>
        </div>

        <div class="tab-nav">
            <a data-tab="monitor" onclick="switchTab('monitor')" class="active">📊 Monitor</a>
            <a data-tab="unresolved" onclick="switchTab('unresolved')">⚠️ Unresolved</a>
            <a data-tab="lookup" onclick="switchTab('lookup')">🔍 Lookup</a>
            <a data-tab="overrides" onclick="switchTab('overrides')">📌 Overrides</a>
            <a data-tab="export" onclick="switchTab('export')">📥 Export</a>
        </div>

        <div id="tab-monitor" class="tab-content active">
            <h2>Live Events (latest 50)</h2>
            <table>
                <tr><th>Time</th><th></th><th>Track</th><th>Status</th><th>Resolve Time</th><th></th></tr>
                {ev_rows if ev_rows else '<tr><td colspan="6" style="text-align:center;color:#999">No events yet</td></tr>'}
            </table>
        </div>

        <div id="tab-unresolved" class="tab-content">
            <h2>Unresolved Tracks (Top 20 by frequency)</h2>
            {f'<table><tr><th>Track</th><th>Occurrences</th><th>Last Seen</th><th></th></tr>{unresolved_rows}</table>' if unresolved else '<p>No unresolved tracks!</p>'}
        </div>

        <div id="tab-lookup" class="tab-content">
            <h2>Manual Resolution Lookup</h2>
            <div class="section">
                <form method="get" action="/" style="display:flex;gap:8px">
                    <input type="hidden" name="tab" value="lookup">
                    <input name="artist" placeholder="Artist name" value="{e(artist)}" required>
                    <input name="title" placeholder="Song title" value="{e(title)}" required>
                    <input name="album" placeholder="Album (optional)" value="{e(album)}">
                    <button type="submit">Search</button>
                </form>
            </div>
            {result_html}
        </div>

        <div id="tab-overrides" class="tab-content">
            <h2>Manual Override Library</h2>
            <div class="section">
                <form method="post" action="/ui/override">
                    <div class="form-group">
                        <label>Add New Override</label>
                        <div class="form-inline" style="gap:8px">
                            <input name="artist" placeholder="Artist" required>
                            <input name="title" placeholder="Title" required>
                            <input name="album" placeholder="Album (optional)">
                            <input name="artwork_url" placeholder="Artwork URL" style="flex:1" required>
                            <button type="submit">Add</button>
                        </div>
                    </div>
                </form>
            </div>
            <h3>Active Overrides ({len(db.list_overrides())})</h3>
            {f'<table><tr><th></th><th>Track</th><th>Artwork URL</th><th></th></tr>{ov_rows}</table>' if ov_rows else '<p>No overrides set yet.</p>'}
        </div>

        <div id="tab-export" class="tab-content">
            <h2>Data Export</h2>
            <div class="section">
                <p>Download resolution events for analysis:</p>
                <a href="/events.csv" style="display:inline-block;padding:8px 14px;background:#007aff;color:#fff;text-decoration:none;border-radius:4px">📊 Download Events (CSV)</a>
                &nbsp;
                <a href="/events" style="display:inline-block;padding:8px 14px;background:#007aff;color:#fff;text-decoration:none;border-radius:4px">📋 View Events (JSON)</a>
            </div>
        </div>

        <div style="margin-top:32px;padding-top:16px;border-top:1px solid #e0e0e0;color:#777;font-size:12px">
            <p>MetaBridge resolves track metadata to artwork. Covers are displayed in the AmpliPhy iOS app's now-playing screen.</p>
            <p>Outputs: {e(os.environ.get('METABRIDGE_OUTPUTS', 'log'))} · <a href="/stats" style="color:#007aff">API stats</a></p>
        </div>
    </main>
    </body></html>"""


@app.post("/ui/override")
def ui_override(artist: str = Form(...), title: str = Form(...), artwork_url: str = Form(...), album: str = Form("")):
    db.set_override(artist, title, artwork_url, album)
    return RedirectResponse("/", status_code=303)


@app.post("/ui/override/delete")
def ui_override_delete(oid: int = Form(...)):
    db.delete_override(oid)
    return RedirectResponse("/", status_code=303)


# ---------------------------------------------------------------- dashboard enhancements: unresolved, force resolve, deep dive
def _get_unresolved(limit: int = 100):
    """Get top unresolved tracks by frequency."""
    with db.conn() as c:
        rows = c.execute("""
            SELECT artist, title, album, COUNT(*) as count, MAX(received_ts) as last_seen
            FROM events WHERE artwork_url IS NULL
            GROUP BY artist, title, album
            ORDER BY count DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


@app.get("/unresolved")
def unresolved_list(limit: int = 100):
    """API endpoint: top unresolved tracks by frequency."""
    return _get_unresolved(limit)


@app.post("/force-resolve")
def force_resolve_post(artist: str = Form(...), title: str = Form(...), album: str = Form(""), background: BackgroundTasks = None):
    """Manually force resolution (bypass cache) and re-post to Cirrus."""
    # Clear from cache to force fresh lookup
    cache_key_val = db.cache_key(artist, title, album)
    with db.conn() as c:
        c.execute("DELETE FROM cache WHERE key=?", (cache_key_val,))

    # Resolve fresh
    result = resolve(artist, title, album, debug=True)

    # If resolved and Cirrus is configured, re-post
    if result.get("artwork_url") and "cirrus" in os.environ.get("METABRIDGE_OUTPUTS", "log"):
        from metabridge.outputs.securenet_cirrus import post_to_cirrus
        from metabridge.pipeline import EnrichedTrackEvent
        # Create minimal enriched event for re-posting
        enriched = type('', (), {
            'artist': artist, 'title': title, 'album': album,
            'artwork_url': result.get('artwork_url'),
            'artwork_source': result.get('source'),
            'confidence': result.get('confidence', 0.0),
            'event_id': f"force-resolve-{int(time.time()*1000)}"
        })()
        try:
            post_to_cirrus(enriched)
        except Exception as ex:
            logging.getLogger("metabridge").warning(f"Force resolve Cirrus post failed: {ex}")

    return result


@app.get("/deep-dive", response_class=HTMLResponse)
def deep_dive(artist: str = "", title: str = ""):
    """Debug view: what was searched, what was found, why rejected (provider sources hidden per policy)."""
    e = html.escape
    if not artist or not title:
        return f"""<!doctype html><html><head><meta charset="utf-8"><title>Deep Dive</title>{STYLE}</head><body>
        <header><h1>Deep Dive Analysis</h1></header><main>
        <p>Provide artist and title as URL parameters, e.g.: <code>?artist=Beatles&title=Let+It+Be</code></p>
        <p><a href="/">&larr; back to dashboard</a></p></main></body></html>"""

    result = resolve(artist, title, "", debug=True)
    art = result.get("artwork_url")
    match = result.get("match", {})
    candidates = result.get("candidates", [])
    reasons = result.get("reasons", [])

    candidates_html = ""
    for i, c in enumerate(candidates[:10], 1):
        conf = c.get("confidence", 0)
        status = "✓ used" if (art and c.get("artwork_url") == art) else "⊘ not used"
        why = c.get("rejection_reason", "metadata didn't match well enough")
        candidates_html += f"""<tr>
            <td>{i}.</td>
            <td><strong>{e(c.get('artist', '—'))}</strong><br><span class='small'>{e(c.get('title', '—'))}</span></td>
            <td class='small'>{e(c.get('album', '—'))}</td>
            <td style="text-align:center"><strong>{conf:.0%}</strong></td>
            <td class='small {"ok" if status.startswith("✓") else "no"}'>{status}</td>
            <td class='small'>{e(why)}</td>
        </tr>"""

    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Deep Dive</title>{STYLE}</head><body>
    <header><h1>Deep Dive Analysis</h1></header>
    <main>
        <div class="section">
            <h2>Query</h2>
            <p><strong>You searched for:</strong> "{e(artist)}" × "{e(title)}"</p>
            <p><strong>Result:</strong> <span class="{'ok' if art else 'no'}">{'✓ RESOLVED' if art else '✗ UNRESOLVED'}</span></p>
            {f'<p><strong>Artwork found:</strong> <a href="{e(art)}" target="_blank" style="word-break:break-all">{e(art[:100])}</a></p>' if art else ''}
        </div>

        <div class="section">
            <h2>Matched Track</h2>
            {f'<p><strong>Artist:</strong> {e(match.get("artist", "—"))}<br><strong>Title:</strong> {e(match.get("title", "—"))}<br><strong>Album:</strong> {e(match.get("album", "—"))}<br><strong>Confidence:</strong> {result.get("confidence", 0):.0%}</p>' if match else '<p>No match found.</p>'}
            <p class='small'><strong>Lookup time:</strong> {result.get('ms')} ms</p>
        </div>

        <div class="section">
            <h2>How We Decided</h2>
            <p>We searched across available music databases and found {len(candidates)} possible matches. Here's what we evaluated:</p>
            {f'<table><tr><th>#</th><th>Track</th><th>Album</th><th>Match Quality</th><th>Status</th><th>Why</th></tr>{candidates_html}</table>' if candidates else '<p><em>No candidates from any database.</em></p>'}
        </div>

        <div class="section">
            <h2>Explanation</h2>
            <ul>{"".join(f"<li>{e(r)}</li>" for r in reasons) if reasons else '<li>Unable to resolve.</li>'}</ul>
        </div>

        <div style="margin-top:24px;text-align:center">
            <a href="/?tab=lookup&artist={e(artist)}&title={e(title)}" style="color:#007aff;text-decoration:none">🔄 Try Again</a>
            &nbsp;|&nbsp;
            <a href="/" style="color:#007aff;text-decoration:none">← Back to Dashboard</a>
        </div>
    </main>
    </body></html>"""
