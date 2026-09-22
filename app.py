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
app = FastAPI(title="AmpliPhy MetaBridge", version="0.4.2")


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
STYLE = """<style>body{font-family:-apple-system,Helvetica,Arial,sans-serif;margin:24px;max-width:1200px;color:#222}
h1{font-size:22px} h2{font-size:16px;margin-top:32px} input{padding:6px;margin:2px;width:220px} button{padding:6px 12px}
table{border-collapse:collapse;width:100%} td,th{border:1px solid #ddd;padding:6px;font-size:12px;vertical-align:top}
th{background:#f4f4f4;text-align:left} img{border:1px solid #ccc;border-radius:4px} .res{background:#f7f7f7;padding:12px;border-radius:6px}
.kpi{display:inline-block;background:#f4f4f4;border-radius:6px;padding:10px 14px;margin:4px 8px 4px 0;font-size:13px}.kpi b{font-size:20px;display:block}
.ok{color:#2e9e5b}.no{color:#c33}.small{color:#777;font-size:11px}</style>"""


@app.get("/", response_class=HTMLResponse)
def dashboard(artist: str = "", title: str = "", album: str = ""):
    e = html.escape
    st = db.event_stats()
    result_html = ""
    if artist and title:
        r = resolve(artist, title, album, debug=True)
        m = r.get("match") or {}
        art = r.get("artwork_url")
        result_html = f"""<div class="res"><b>{'RESOLVED' if art else 'UNRESOLVED'}</b> — source {e(str(r.get('source')))},
        confidence {r.get('confidence')}{' (cached)' if r.get('cached') else ''} · {r.get('ms')} ms<br>
        {'<img src="' + e(art) + '" width="140">' if art else ''}
        <div>Matched: {e(str(m.get('artist')))} — {e(str(m.get('title')))} <i>{e(str(m.get('album')))}</i></div>
        <div class="small">{'; '.join(e(x) for x in r.get('reasons', []))}</div>
        <form method="post" action="/ui/override" style="margin-top:8px">
          <input type="hidden" name="artist" value="{e(artist)}"><input type="hidden" name="title" value="{e(title)}"><input type="hidden" name="album" value="{e(album)}">
          <input name="artwork_url" placeholder="artwork URL to pin for this song" value="{e(art or '')}" style="width:420px"><button>Pin as AmpliPhy override</button></form></div>"""

    def fmt_ms(v):
        return "—" if v is None else f"{int(v)} ms"

    ev_rows = ""
    for r in db.list_events(50):
        outs = json.loads(r["outputs_json"] or "[]")
        ev_rows += (f"<tr><td class='small'>{time.strftime('%H:%M:%S', time.localtime(r['received_ts']))}<br>{e(r['input_source'])}</td>"
                    f"<td>{'<img src=' + chr(34) + e(r['artwork_url']) + chr(34) + ' width=44>' if r['artwork_url'] else ''}</td>"
                    f"<td><b>{e(r['artist'])}</b><br>{e(r['title'])}</td>"
                    f"<td class='{'ok' if r['artwork_url'] else 'no'}'>{e(r['artwork_source'] or 'unresolved')}<br><span class='small'>{r['confidence']:.2f}{' · cache' if r['cache_hit'] else ''}</span></td>"
                    f"<td>{fmt_ms(r['intake_ms'])}</td><td>{fmt_ms(r['resolve_ms'])}</td><td>{fmt_ms(r['total_ms'])}</td>"
                    f"<td class='small'>{'<br>'.join(e(o['adapter']) + ': ' + e(o['status']) for o in outs)}</td></tr>")

    ov_rows = "".join(
        f"<tr><td><img src='{e(o['artwork_url'])}' width='44'></td><td>{e(o['artist'])}</td><td>{e(o['title'])}</td><td>{e(o['album'] or '')}</td>"
        f"<td class='small'>{e(o['artwork_url'])}</td><td><form method='post' action='/ui/override/delete'><input type='hidden' name='oid' value='{o['id']}'><button>remove</button></form></td></tr>"
        for o in db.list_overrides())

    return f"""<!doctype html><html><head><meta charset="utf-8"><title>AmpliPhy MetaBridge</title>{STYLE}</head><body>
<h1>AmpliPhy MetaBridge <small style="font-weight:400;color:#777">Phase 2A · outputs: {e(os.environ.get('METABRIDGE_OUTPUTS', 'log'))}</small></h1>
<div>
 <span class="kpi"><b>{st['n'] or 0}</b>events</span>
 <span class="kpi"><b>{st['resolved_pct']}%</b>resolved</span>
 <span class="kpi"><b>{st['cache_hits'] or 0}</b>cache hits</span>
 <span class="kpi"><b>{fmt_ms(st['avg_resolve_ms'])}</b>avg resolve</span>
 <span class="kpi"><b>{fmt_ms(st['max_resolve_ms'])}</b>max resolve</span>
 <span class="kpi"><b>{fmt_ms(st['avg_total_ms'])}</b>avg received→sent</span>
 <span class="kpi"><b>{fmt_ms(st['avg_intake_ms'])}</b>avg source→received</span>
</div>
<h2>Try a lookup</h2>
<form method="get" action="/"><input name="artist" placeholder="Artist" value="{e(artist)}"><input name="title" placeholder="Title" value="{e(title)}">
<input name="album" placeholder="Album (optional)" value="{e(album)}"><button>Resolve</button></form>
{result_html}
<h2>Live events (latest 50) — <a href="/events.csv">download CSV</a> · <a href="/events">JSON</a></h2>
<table><tr><th>When / source</th><th></th><th>Track</th><th>Result</th><th>source→received</th><th>resolve</th><th>received→sent</th><th>Outputs</th></tr>{ev_rows}</table>
<h2>AmpliPhy override library ({len(db.list_overrides())})</h2>
<form method="post" action="/ui/override"><input name="artist" placeholder="Artist"><input name="title" placeholder="Title"><input name="album" placeholder="Album (optional)">
<input name="artwork_url" placeholder="Artwork URL" style="width:360px"><button>Add</button></form>
<table><tr><th></th><th>Artist</th><th>Title</th><th>Album</th><th>Artwork URL</th><th></th></tr>{ov_rows}</table>
</body></html>"""


@app.post("/ui/override")
def ui_override(artist: str = Form(...), title: str = Form(...), artwork_url: str = Form(...), album: str = Form("")):
    db.set_override(artist, title, artwork_url, album)
    return RedirectResponse("/", status_code=303)


@app.post("/ui/override/delete")
def ui_override_delete(oid: int = Form(...)):
    db.delete_override(oid)
    return RedirectResponse("/", status_code=303)
