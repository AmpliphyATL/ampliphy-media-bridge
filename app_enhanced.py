"""AmpliPhy MetaBridge — real-time music metadata enrichment middleware with Settings UI.

    Input Adapter -> TrackEvent -> Resolver Core -> EnrichedTrackEvent -> Output Adapter(s)

Run:   pip3 install fastapi uvicorn
       python3 -m uvicorn app_enhanced:app --host 0.0.0.0 --port 8765

Includes built-in Settings page for configuring Cirrus credentials directly in the dashboard.
"""
from __future__ import annotations

import csv
import html
import io
import json
import logging
import os
import pathlib
import time

from fastapi import BackgroundTasks, FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel

from metabridge import autostart, config, db, outputs, secrets

config.load()
from metabridge.core import resolve
from metabridge.inputs import playoutone_http_poll, playoutone_monitor, tcp_listener
from metabridge.pipeline import handle, is_duplicate

logging.basicConfig(level=os.environ.get("METABRIDGE_LOG", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
try:
    from build_info import BUILD_NUMBER
except Exception:
    BUILD_NUMBER = "dev"
VERSION = f"v.{BUILD_NUMBER}"

app = FastAPI(title="AmpliPhy MetaBridge", version=VERSION)

# Bundled static assets (logo). Inside the PyInstaller exe they live under sys._MEIPASS.
import sys as _sys
ASSETS_DIR = pathlib.Path(getattr(_sys, "_MEIPASS", pathlib.Path(__file__).resolve().parent)) / "assets"


@app.get("/assets/{name}")
def asset(name: str):
    f = ASSETS_DIR / pathlib.Path(name).name  # no path traversal
    if not f.is_file():
        return PlainTextResponse("not found", status_code=404)
    return FileResponse(str(f), headers={"Cache-Control": "public, max-age=86400"})


SETUP_MD = pathlib.Path(getattr(_sys, "_MEIPASS", pathlib.Path(__file__).resolve().parent)) / "SETUP.md"


def _md_to_html(md: str) -> str:
    """Tiny Markdown renderer for the bundled setup guide (headings, lists, tables, code, bold)."""
    import re
    e = html.escape
    out, in_code, in_list, table = [], False, False, []

    def flush_table():
        nonlocal table
        if not table:
            return
        rows = [r for r in table if not re.match(r"^\s*\|?\s*-{2,}", r)]
        cells = [[c.strip() for c in re.split(r"(?<!\\)\|", r.strip().strip("|"))] for r in rows]
        h = "<table><tr>" + "".join(f"<th>{inline(c)}</th>" for c in cells[0]) + "</tr>"
        for r in cells[1:]:
            h += "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>"
        out.append(h + "</table>")
        table = []

    def inline(t):
        t = e(t).replace("\\|", "|")
        t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
        t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"\*([^*]+)\*", r"<i>\1</i>", t)
        return t

    # Join wrapped lines: a line that does not start a new block continues the previous one.
    joined, code_block = [], False
    for raw in md.splitlines():
        if raw.startswith("```"):
            code_block = not code_block
            joined.append(raw); continue
        starts_block = code_block or not raw.strip() or re.match(r"^(#{1,6}\s|\s*(-|\d+\.)\s|\s*\||---$)", raw)
        if joined and not starts_block and joined[-1].strip() and not joined[-1].startswith("```") \
                and not joined[-1].lstrip().startswith("|") and joined[-1].strip() != "---" and not re.match(r"^#{1,6}\s", joined[-1]):
            joined[-1] = joined[-1].rstrip() + " " + raw.strip()
        else:
            joined.append(raw)
    for line in joined:
        if line.startswith("```"):
            if in_code:
                out.append("</pre>")
            else:
                flush_table(); out.append("<pre>")
            in_code = not in_code
            continue
        if in_code:
            out.append(e(line)); continue
        if line.lstrip().startswith("|"):
            table.append(line); continue
        flush_table()
        if in_list and not re.match(r"^\s*(-|\d+\.)\s", line):
            out.append("</ul>"); in_list = False
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            out.append(f"<h{len(m.group(1))}>{inline(m.group(2))}</h{len(m.group(1))}>"); continue
        if line.strip() == "---":
            out.append("<hr>"); continue
        m = re.match(r"^\s*(-|\d+\.)\s+(.*)", line)
        if m:
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{inline(m.group(2))}</li>"); continue
        if line.strip():
            out.append(f"<p>{inline(line)}</p>")
    flush_table()
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


@app.get("/setup", response_class=HTMLResponse)
def setup_guide():
    try:
        md = SETUP_MD.read_text(encoding="utf-8")
    except Exception:
        md = "# Setup guide\n\nSETUP.md was not bundled with this build."
    body = _md_to_html(md)
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AmpliPhy MetaBridge {VERSION} — Setup Guide</title>
    <script>(function(){{try{{var t=localStorage.getItem('mb-theme');if(!t)t=(window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches)?'dark':'light';document.documentElement.setAttribute('data-theme',t);}}catch(e){{}}}})();</script>
    {STYLE}<style>main{{max-width:900px;padding:0 24px}} pre{{background:var(--panel-3);border:1px solid var(--line);padding:12px;border-radius:4px;overflow:auto;font-size:12px}} code{{background:var(--panel-3);padding:1px 4px;border-radius:3px;font-size:12px}} hr{{border:0;border-top:1px solid var(--line);margin:24px 0}} h1{{font-size:22px;margin:16px 0}} h2{{margin-top:28px}} h3{{font-size:14px;margin:18px 0 8px}} li{{margin:4px 0}} table{{margin:8px 0 16px}}</style></head><body>
    <header><div style="display:flex;align-items:center;gap:12px"><div class="brand"><div class="brand-text"><span class="app">MetaBridge</span><span class="ver">{VERSION}</span> <span class="ver">· Setup Guide</span></div></div><div style="flex:1"></div><a href="/" style="color:#8a8aa8;font-size:12px">← Back to dashboard</a></div></header>
    <main>{body}</main></body></html>"""


# Settings file path (persists credentials entered via dashboard)
SETTINGS_FILE = pathlib.Path(os.environ.get("METABRIDGE_SETTINGS_FILE") or (pathlib.Path(__file__).resolve().parent / "metabridge.env.json"))


def load_settings() -> dict:
    """Load settings from JSON file. Merge with environment variables (env vars take precedence)."""
    settings = {
        "cirrus_callsign": os.environ.get("METABRIDGE_CIRRUS_CALLSIGN", ""),
        "cirrus_token": os.environ.get("METABRIDGE_CIRRUS_TOKEN", ""),
        "outputs": os.environ.get("METABRIDGE_OUTPUTS", "log"),
        "tcp_port": os.environ.get("METABRIDGE_TCP_PORT", "8766"),
        "autostart": True,
    }

    # Try to load from JSON file, but env vars override it
    if SETTINGS_FILE.exists():
        try:
            file_data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            settings["autostart"] = bool(file_data.get("autostart", True))
            # Only use file data if not set in environment
            if not os.environ.get("METABRIDGE_CIRRUS_CALLSIGN"):
                settings["cirrus_callsign"] = file_data.get("cirrus_callsign", "")
            if not os.environ.get("METABRIDGE_CIRRUS_TOKEN"):
                settings["cirrus_token"] = secrets.reveal(file_data.get("cirrus_token", ""))
            if not os.environ.get("METABRIDGE_OUTPUTS"):
                settings["outputs"] = file_data.get("outputs", "log")
            if not os.environ.get("METABRIDGE_TCP_PORT"):
                settings["tcp_port"] = file_data.get("tcp_port", "8766")
        except Exception as e:
            logging.getLogger("metabridge").warning(f"Could not load settings file: {e}")

    return settings


def save_settings(settings: dict):
    """Save settings to JSON file."""
    try:
        on_disk = dict(settings)
        on_disk["cirrus_token"] = secrets.protect(settings.get("cirrus_token", ""))  # never plain text on disk
        SETTINGS_FILE.write_text(json.dumps(on_disk, indent=2), encoding="utf-8")
        # Also update environment variables
        os.environ["METABRIDGE_CIRRUS_CALLSIGN"] = settings.get("cirrus_callsign", "")
        os.environ["METABRIDGE_CIRRUS_TOKEN"] = settings.get("cirrus_token", "")
        os.environ["METABRIDGE_OUTPUTS"] = settings.get("outputs", "log")
        os.environ["METABRIDGE_TCP_PORT"] = settings.get("tcp_port", "8766")
    except Exception as e:
        logging.getLogger("metabridge").error(f"Could not save settings: {e}")


def _apply_settings_to_env(settings: dict):
    """Make the saved dashboard settings visible to the adapters/listeners,
    which read os.environ. Values already set in the environment win."""
    for env_key, key in (
        ("METABRIDGE_CIRRUS_CALLSIGN", "cirrus_callsign"),
        ("METABRIDGE_CIRRUS_TOKEN", "cirrus_token"),
        ("METABRIDGE_OUTPUTS", "outputs"),
        ("METABRIDGE_TCP_PORT", "tcp_port"),
    ):
        if settings.get(key) and not os.environ.get(env_key):
            os.environ[env_key] = str(settings[key])


# Apply whatever was saved from the dashboard on a previous run, BEFORE the
# startup hook starts the TCP listener - otherwise a fresh launch listens on nothing.
_apply_settings_to_env(load_settings())

# One-time upgrade: if an older version left the token in plain text, re-save it encrypted.
try:
    if SETTINGS_FILE.exists():
        _raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if _raw.get("cirrus_token") and not secrets.is_protected(_raw["cirrus_token"]):
            save_settings(load_settings())
            logging.getLogger("metabridge").info("settings file upgraded: token now encrypted at rest")
except Exception:
    logging.getLogger("metabridge").exception("could not upgrade settings file")


class _GluedPathRewrite:
    """PlayoutONE Monitor appends its Parameters box to the URL verbatim."""
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


HISTORY_DAYS = int(os.environ.get("METABRIDGE_HISTORY_DAYS", "30"))


def _prune_loop():
    """Trim old events/lookups on startup and then once a day."""
    import threading
    while True:
        try:
            gone = db.prune_history(HISTORY_DAYS)
            if gone["events"] or gone["lookups"]:
                logging.getLogger("metabridge").info("pruned history older than %d days: %s", HISTORY_DAYS, gone)
        except Exception:
            logging.getLogger("metabridge").exception("history prune failed")
        time.sleep(86400)


@app.on_event("startup")
def _startup():
    tcp_listener.start_if_configured()
    playoutone_http_poll.start_if_configured()
    import threading
    threading.Thread(target=_prune_loop, daemon=True).start()
    logging.getLogger("metabridge").info("outputs: %s", os.environ.get("METABRIDGE_OUTPUTS", "log"))


# ============================================================ API: Settings
@app.get("/api/settings")
def api_get_settings():
    """Get current settings."""
    s = load_settings()
    # Don't send token in API response (security)
    return {
        "cirrus_callsign": s["cirrus_callsign"],
        "cirrus_token_set": bool(s["cirrus_token"]),
        "outputs": s["outputs"],
        "tcp_port": s["tcp_port"],
    }


@app.post("/api/settings")
def api_save_settings(callsign: str = Form(""), token: str = Form(""), outputs: str = Form(""), tcp_port: str = Form(""), autostart_on: str = Form("")):
    """Save settings from dashboard form."""
    s = load_settings()
    want_autostart = autostart_on == "1"
    s["autostart"] = want_autostart
    autostart.set_enabled(want_autostart)
    if callsign:
        s["cirrus_callsign"] = callsign
    if token and token != "***":  # Don't overwrite if they sent the masked version
        s["cirrus_token"] = token
    if outputs:
        s["outputs"] = outputs
    if tcp_port:
        s["tcp_port"] = tcp_port

    save_settings(s)
    tcp_listener.start_if_configured()  # bring the listener up if it wasn't already
    return {"status": "saved", "cirrus_callsign": s["cirrus_callsign"], "outputs": s["outputs"]}


@app.post("/api/shutdown")
def api_shutdown(request: Request):
    """Used by a newer build to close this one during an in-place upgrade. Local only."""
    client = (request.client.host if request.client else "") or ""
    if client not in ("127.0.0.1", "::1"):
        return JSONResponse({"error": "local only"}, status_code=403)
    import threading
    logging.getLogger("metabridge").info("shutdown requested (upgrade in progress)")

    def _die():
        time.sleep(0.5)
        os._exit(0)
    threading.Thread(target=_die, daemon=True).start()
    return {"status": "shutting down"}


# ============================================================ Intake: PlayoutONE Monitor
@app.api_route("/inbound/playoutone", methods=["GET", "POST"], response_class=PlainTextResponse)
async def inbound_playoutone(request: Request, background: BackgroundTasks):
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
    if not ev:
        return PlainTextResponse("ignored", status_code=200)

    from metabridge.pipeline import looks_like_template
    bad = looks_like_template(ev)
    if bad:
        handle(ev)
        return PlainTextResponse("refused", status_code=200)
    if is_duplicate(ev):
        return PlainTextResponse("queued", status_code=200)
    background.add_task(handle, ev)
    return PlainTextResponse("queued", status_code=200)


# ============================================================ Generic API
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
    return db.set_override(o["artist"], o["title"], o["artwork_url"], o.get("album", ""), o.get("isrc", ""),
                          o.get("source", "ampliphy"), o.get("notes", ""))


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


# ============================================================ Dashboard UI
STYLE = """<style>
:root { color-scheme: light;
  --bg:#fafafa; --panel:#fff; --panel-2:#f9f9f9; --panel-3:#f5f5f5; --text:#222; --muted:#777; --muted-2:#999; --line:#e0e0e0; --line-2:#ddd;
  --accent:#007aff; --accent-hover:#0051d5; --btn2:#e8e8e8; --btn2-hover:#d0d0d0; --btn2-text:#222;
  --warn-bg:#fff8e6; --warn-text:#e65100; --ok-bg:#e8f5e9; --ok-text:#2e7d32; --err-bg:#ffebee; --err-text:#c62828; --unres:#fffbfb;
  --header:#0b0b14; --header-line:#1f1f33; --focus:rgba(0,122,255,0.1); --note:#f0f0f0; }
[data-theme="dark"] { color-scheme: dark;
  --bg:#0f0f17; --panel:#171724; --panel-2:#1d1d2c; --panel-3:#1a1a28; --text:#e8e8f0; --muted:#9a9ab0; --muted-2:#7c7c94; --line:#2a2a3d; --line-2:#33334a;
  --accent:#4da3ff; --accent-hover:#7bbaff; --btn2:#2a2a3d; --btn2-hover:#3a3a52; --btn2-text:#e8e8f0;
  --warn-bg:#3a2a10; --warn-text:#ffb74d; --ok-bg:#12301a; --ok-text:#7ad68e; --err-bg:#3a1416; --err-text:#ff8a80; --unres:#1f1519;
  --header:#07070d; --header-line:#1f1f33; --focus:rgba(77,163,255,0.2); --note:#1d1d2c; }

* { box-sizing: border-box; }
body { font-family: -apple-system, Helvetica, Arial, sans-serif; margin: 0; padding: 0; background: var(--bg); color: var(--text); }
header { background: var(--header); padding: 10px 24px; border-bottom: 2px solid var(--header-line); box-shadow: 0 2px 6px rgba(0,0,0,0.35); }
main { margin: 24px auto; max-width: 1200px; }
h1 { font-size: 24px; margin: 0; }
h1 small { font-size: 14px; font-weight: 400; color: var(--muted); }
h2 { font-size: 16px; margin: 24px 0 16px; padding-bottom: 8px; border-bottom: 1px solid var(--line); }
input, textarea, select { padding: 8px; margin: 4px 0; border: 1px solid var(--line-2); border-radius: 4px; font-size: 13px; font-family: inherit; background: var(--panel); color: var(--text); }
input:focus, textarea:focus, select:focus { outline: 0; border-color: var(--accent); box-shadow: 0 0 0 2px var(--focus); }
button { padding: 8px 14px; margin: 4px 4px 4px 0; background: var(--accent); color: #fff; border: 0; border-radius: 4px; font-size: 13px; cursor: pointer; transition: background 0.2s; }
button:hover { background: var(--accent-hover); }
button.secondary { background: var(--btn2); color: var(--btn2-text); }
button.secondary:hover { background: var(--btn2-hover); }
button.success { background: #2e9e5b; }
button.success:hover { background: #237d48; }
button.danger { background: #dc3545; }
button.danger:hover { background: #bb2d3b; }
table { border-collapse: collapse; width: 100%; background: var(--panel); border: 1px solid var(--line); border-radius: 4px; overflow: hidden; }
td, th { border-bottom: 1px solid var(--line); padding: 10px; font-size: 13px; vertical-align: top; text-align: left; }
th { background: var(--panel-3); font-weight: 500; }
tr:last-child td { border-bottom: 0; }
img { border: 1px solid var(--line-2); border-radius: 4px; background: var(--panel); }
.alert { padding: 12px; border-radius: 4px; margin-bottom: 16px; }
.alert.warning { background: var(--warn-bg); border-left: 4px solid #ff9800; color: var(--warn-text); }
.alert.success { background: var(--ok-bg); border-left: 4px solid #4caf50; color: var(--ok-text); }
.alert.error { background: var(--err-bg); border-left: 4px solid #dc3545; color: var(--err-text); }
.kpi { display: inline-block; background: var(--panel); border: 1px solid var(--line); border-radius: 6px; padding: 14px 16px; margin: 0 8px 12px 0; font-size: 13px; min-width: 100px; }
.kpi b { font-size: 18px; display: block; color: var(--accent); }
.kpi span { color: var(--muted); font-size: 12px; }
.ok { color: #2e9e5b; font-weight: 500; }
.no { color: #dc3545; font-weight: 500; }
.small { color: var(--muted); font-size: 12px; }
.form-group { margin-bottom: 16px; padding: 12px; background: var(--panel-2); border-radius: 4px; border-left: 3px solid var(--line-2); }
.form-group label { display: block; font-size: 12px; font-weight: 500; margin-bottom: 4px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }
.form-group label .note { font-weight: 400; color: var(--muted-2); font-size: 11px; display: block; margin-top: 2px; }
.form-inline { display: flex; gap: 8px; align-items: flex-end; flex-wrap: wrap; }
.form-inline input { margin: 0; flex: 1; min-width: 200px; }
.form-inline button { margin: 0; white-space: nowrap; }
.section { background: var(--panel); padding: 16px; border-radius: 6px; margin-bottom: 16px; border: 1px solid var(--line); }
.tab-nav { display: flex; gap: 8px; margin-bottom: 16px; border-bottom: 2px solid var(--line); flex-wrap: wrap; }
.tab-nav a { padding: 10px 16px; text-decoration: none; color: var(--muted); border-bottom: 3px solid transparent; cursor: pointer; transition: all 0.2s; }
.tab-nav a:hover { color: var(--accent); }
.tab-nav a.active { color: var(--accent); border-bottom-color: var(--accent); }
.tab-content { display: none; }
.tab-content.active { display: block; }
.badge { display: inline-block; padding: 2px 8px; background: var(--btn2); color: var(--btn2-text); border-radius: 3px; font-size: 11px; margin: 0 2px; }
.badge.ok { background: var(--ok-bg); color: var(--ok-text); }
.badge.warn { background: var(--warn-bg); color: var(--warn-text); }
.unresolved-row { background: var(--unres); }
.settings-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 768px) { .settings-grid { grid-template-columns: 1fr; } }
.status-indicator { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }
.status-indicator.ok { background: #4caf50; }
.status-indicator.warn { background: #ff9800; }
.brand { display:flex; align-items:center; gap:16px; }
.brand video, .brand img { height: 64px; width: auto; display:block; border:0; border-radius:6px; background:#000; }
.brand-text { color:#e8e8f0; }
.brand-text .app { font-size: 20px; font-weight: 600; letter-spacing: .3px; }
.brand-text .ver { font-size: 12px; font-weight: 500; color: #8a8aa8; margin-left: 6px; }
.header-right { color:#8a8aa8; font-size:11px; text-align:right; display:flex; align-items:center; gap:14px; }
.theme-btn { margin:0; padding:6px 10px; font-size:12px; background:#1f1f33; color:#e8e8f0; border:1px solid #33334a; }
.theme-btn:hover { background:#2a2a44; }
</style>"""


def _get_unresolved(limit=20):
    """Get most frequently unresolved tracks."""
    with db.conn() as c:
        rows = c.execute("""
            SELECT artist, title, COUNT(*) as count, MAX(received_ts) as last_seen
            FROM events WHERE artwork_url IS NULL OR artwork_url = ''
            GROUP BY artist, title
            ORDER BY count DESC LIMIT ?
        """, [limit]).fetchall()
    return [dict(r) for r in rows]


@app.get("/", response_class=HTMLResponse)
def dashboard(artist: str = "", title: str = "", album: str = "", tab: str = "monitor"):
    """Main dashboard page."""
    e = html.escape
    st = db.event_stats()
    settings = load_settings()

    # Check if Cirrus is configured
    cirrus_ready = bool(settings.get("cirrus_callsign") and settings.get("cirrus_token"))
    cirrus_in_outputs = "cirrus" in settings.get("outputs", "").lower()

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

        result_html = f"""<div class="section"><div class="alert {'success' if art else 'error'}">
            <b>{'✓ RESOLVED' if art else '✗ UNRESOLVED'}</b>
        </div>
        {f'<img src="{e(art)}" width="120" style="margin:12px 0;border-radius:6px">' if art else ''}
        <div style="margin:12px 0">
            <b>Matched:</b> {e(str(m.get('artist')))} — {e(str(m.get('title')))}
            {f'<i>({e(str(m.get("album")))})</i>' if m.get('album') else ''}
            <br><b>Confidence:</b> {confidence:.2f} · <b>Time:</b> {r.get('ms')} ms
            {f'<span class="badge ok">cached</span>' if r.get('cached') else ''}
        </div>
        {f'<div class="small" style="margin:12px 0"><b>Why:</b> {"; ".join(e(x) for x in r.get("reasons", []))}</div>' if r.get('reasons') else ''}
        <div style="margin-top:12px">
            <form method="post" action="/ui/override" style="margin:0">
                <input type="hidden" name="artist" value="{e(artist)}"><input type="hidden" name="title" value="{e(title)}"><input type="hidden" name="album" value="{e(album)}">
                <div class="form-inline">
                    <input name="artwork_url" placeholder="artwork URL" value="{e(art or '')}" style="flex:1">
                    <button type="submit" class="secondary">📌 Pin Override</button>
                </div>
            </form>
        </div>
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
                    f"<td class='{'ok' if has_art else 'no'}'><strong>{'✓ Resolved' if has_art else '✗ Unresolved'}</strong><br><span class='small'>{r['confidence']:.2f}</span></td>"
                    f"<td class='small'>{fmt_ms(r['resolve_ms'])}</td>"
                    f"</tr>")

    # Unresolved top tracks
    unresolved = _get_unresolved(20)
    unresolved_rows = ""
    for u in unresolved:
        unresolved_rows += (f"<tr>"
            f"<td><b>{e(u['artist'])}</b><br><span class='small'>{e(u['title'])}</span></td>"
            f"<td class='small'>{u['count']} times</td>"
            f"<td class='small'>{time.strftime('%H:%M:%S', time.localtime(u['last_seen']))}</td>"
            f"</tr>")

    # Overrides
    ov_rows = "".join(
        f"<tr><td><img src='{e(o['artwork_url'])}' width='40'></td>"
        f"<td><b>{e(o['artist'])}</b><br><span class='small'>{e(o['title'])}</span></td>"
        f"<td class='small'>{e(o['artwork_url'][:60])}...</td>"
        f"<td><form method='post' action='/ui/override/delete' style='margin:0'><input type='hidden' name='oid' value='{o['id']}'><button type='submit' class='secondary' style='padding:4px 8px;font-size:11px'>Remove</button></form></td></tr>"
        for o in db.list_overrides(limit=50))

    # Settings form
    settings_form = f"""
    <div class="section">
        <h2>⚙️ SecureNet Cirrus Configuration</h2>
        <div class="alert {'success' if (cirrus_ready and cirrus_in_outputs) else 'warning'}">
            <span class="status-indicator {'ok' if (cirrus_ready and cirrus_in_outputs) else 'warn'}"></span>
            <b>Cirrus Status:</b>
            {('✓ Configured and enabled' if (cirrus_ready and cirrus_in_outputs) else
              '⚠ Not configured' if not cirrus_ready else
              '⚠ Configured but not enabled in outputs')}
        </div>

        <div class="settings-grid">
            <div class="form-group">
                <label>Station Call Sign<span class="note">e.g., KKGO</span></label>
                <input type="text" id="cirrus_callsign" value="{e(settings.get('cirrus_callsign', ''))}" placeholder="Your station call sign">
            </div>
            <div class="form-group">
                <label>Cirrus Auth Token<span class="note">SecureNet credential · stored encrypted (Windows DPAPI)</span></label>
                <input type="password" id="cirrus_token" placeholder="{'Token saved - leave blank to keep it' if settings.get('cirrus_token') else 'Paste your Cirrus token (will be stored securely)'}">
            </div>
            <div class="form-group">
                <label>Enabled Outputs<span class="note">Comma-separated: log, securenet_cirrus, webhook</span></label>
                <input type="text" id="outputs" value="{e(settings.get('outputs', 'log'))}" placeholder="log,securenet_cirrus">
            </div>
            <div class="form-group">
                <label>TCP Port<span class="note">For PlayoutONE input</span></label>
                <input type="text" id="tcp_port" value="{e(settings.get('tcp_port', '8766'))}" placeholder="8766">
            </div>
            <div class="form-group">
                <label>Start with Windows<span class="note">Launch automatically when this PC restarts (like DCS Console / PlayoutONE)</span></label>
                <label style="text-transform:none;font-size:13px;color:var(--text);display:flex;align-items:center;gap:8px;cursor:pointer">
                    <input type="checkbox" id="autostart_on" {'checked' if settings.get('autostart', True) else ''} style="width:16px;height:16px;margin:0">
                    Start MetaBridge automatically at login
                    <span class="badge {'ok' if autostart.is_enabled() else 'warn'}">{'registered' if autostart.is_enabled() else 'not registered'}</span>
                </label>
            </div>
        </div>

        <button class="success" onclick="saveSettings()">💾 Save Settings</button>
        <span id="save-status" style="margin-left:12px;font-size:12px;color:var(--muted-2)"></span>

        <div style="margin-top:24px;padding:12px;background:var(--note);border-radius:4px;font-size:12px;color:var(--muted)">
            <b>ℹ️ Setup Instructions:</b><br>
            1. Enter your SecureNet Cirrus call sign and auth token above<br>
            2. Make sure "securenet_cirrus" is in the Outputs list<br>
            3. Click Save Settings<br>
            4. Test by entering a track in the Lookup tab — metadata should post to Cirrus<br>
            <br><b>Getting your token:</b> Log in to SecureNet > Cirrus > Settings > API Tokens. <b>Important:</b> Use a fresh token, never paste a token that was shared in chat.
            <br><br><b>Saved to:</b> <code>{e(str(SETTINGS_FILE))}</code> — kept across restarts and upgrades; new builds pick it up automatically.
        </div>
    </div>

    <script>
    async function saveSettings() {{
        const callsign = document.getElementById('cirrus_callsign').value;
        const token = document.getElementById('cirrus_token').value;
        const outputs = document.getElementById('outputs').value;
        const tcp_port = document.getElementById('tcp_port').value;
        const autostart_on = document.getElementById('autostart_on').checked ? '1' : '0';
        const status = document.getElementById('save-status');

        try {{
            const resp = await fetch('/api/settings', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
                body: `callsign=${{encodeURIComponent(callsign)}}&token=${{encodeURIComponent(token)}}&outputs=${{encodeURIComponent(outputs)}}&tcp_port=${{encodeURIComponent(tcp_port)}}&autostart_on=${{autostart_on}}`
            }});
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const data = await resp.json();
            status.textContent = '✓ Saved! Reloading...';
            status.style.color = '#2e9e5b';
            setTimeout(() => {{ window.location.href = '/?tab=settings'; }}, 600);
        }} catch (e) {{
            status.textContent = '✗ Error saving';
            status.style.color = '#dc3545';
        }}
    }}

    function toggleTheme() {{
        const cur = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
        const next = cur === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', next);
        try {{ localStorage.setItem('mb-theme', next); }} catch (e) {{}}
        paintThemeBtn();
    }}
    function paintThemeBtn() {{
        const b = document.getElementById('theme-btn'); if (!b) return;
        b.textContent = document.documentElement.getAttribute('data-theme') === 'dark' ? '☀️ Light' : '🌙 Dark';
    }}
    window.addEventListener('load', paintThemeBtn);

    function switchTab(name) {{
        document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
        document.querySelectorAll('.tab-nav a').forEach(el => el.classList.remove('active'));
        document.getElementById('tab-' + name).classList.add('active');
        document.querySelector('[data-tab="' + name + '"]').classList.add('active');
    }}
    window.addEventListener('load', () => switchTab('{tab}'));

    // Live refresh: while Monitor or Unresolved is showing and the user isn't
    // typing in a field, reload every 5 s so new songs appear on their own.
    setInterval(() => {{
        const active = document.querySelector('.tab-nav a.active');
        const name = active ? active.getAttribute('data-tab') : '';
        const typing = ['INPUT','TEXTAREA'].includes((document.activeElement||{{}}).tagName);
        if ((name === 'monitor' || name === 'unresolved') && !typing) {{
            window.location.href = '/?tab=' + name;
        }}
    }}, 5000);
    </script>
    """

    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AmpliPhy MetaBridge {VERSION}</title>
    <script>(function(){{try{{var t=localStorage.getItem('mb-theme');if(!t)t=(window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches)?'dark':'light';document.documentElement.setAttribute('data-theme',t);}}catch(e){{}}}})();</script>
    {STYLE}</head><body>
    <header>
        <div style="display:flex;align-items:center;gap:12px">
            <div class="brand">
                <video autoplay muted loop playsinline aria-label="AmpliPhy"><source src="/assets/logo.mp4" type="video/mp4"></video>
                <div class="brand-text"><span class="app">MetaBridge</span><span class="ver">{VERSION}</span></div>
            </div>
            <div style="flex:1"></div>
            <div class="header-right">
                <div><div>Real-Time Metadata Enrichment</div><div>SecureNet Cirrus Integration</div></div>
                <a href="/setup" class="theme-btn" style="text-decoration:none;display:inline-block">📖 Setup Guide</a>
                <button class="theme-btn" id="theme-btn" onclick="toggleTheme()" title="Switch light / dark">🌙 Dark</button>
            </div>
        </div>
    </header>
    <main>
        <div style="display:flex;gap:16px;margin-bottom:20px;flex-wrap:wrap">
            <div class="kpi"><b>{st['n'] or 0}</b><span>events</span></div>
            <div class="kpi"><b>{st['resolved_pct']}%</b><span>resolved</span></div>
            <div class="kpi"><b>{st['cache_hits'] or 0}</b><span>cache hits</span></div>
            <div class="kpi"><b>{fmt_ms(st['avg_resolve_ms'])}</b><span>avg resolve</span></div>
            <div class="kpi"><b>{fmt_ms(st['max_resolve_ms'])}</b><span>max resolve</span></div>
        </div>

        <div class="tab-nav">
            <a data-tab="monitor" onclick="switchTab('monitor')" class="active">📊 Monitor</a>
            <a data-tab="lookup" onclick="switchTab('lookup')">🔍 Try a Lookup</a>
            <a data-tab="unresolved" onclick="switchTab('unresolved')">⚠️ Unresolved</a>
            <a data-tab="overrides" onclick="switchTab('overrides')">📌 Overrides</a>
            <a data-tab="settings" onclick="switchTab('settings')">⚙️ Settings</a>
        </div>

        <div id="tab-monitor" class="tab-content active">
            <h2>Live Events (latest 50) <span class="small" style="font-weight:400">· auto-refreshes every 5 s</span></h2>
            <div class="section">
            <table>
                <tr><th>Time</th><th></th><th>Track</th><th>Status</th><th>Resolve Time</th></tr>
                {ev_rows if ev_rows else '<tr><td colspan="5" style="text-align:center;color:var(--muted-2);padding:32px">No events yet — waiting for input from PlayoutONE</td></tr>'}
            </table>
            </div>
        </div>

        <div id="tab-lookup" class="tab-content">
            <div class="section">
            <h2>🔍 Try a Metadata Lookup</h2>
            <form method="get" action="/">
                <div class="form-inline">
                    <input name="artist" placeholder="Artist" value="{e(artist)}" required>
                    <input name="title" placeholder="Title" value="{e(title)}" required>
                    <input name="album" placeholder="Album (optional)" value="{e(album)}">
                    <button type="submit">Resolve</button>
                </div>
            </form>
            {result_html}
            </div>
        </div>

        <div id="tab-unresolved" class="tab-content">
            <h2>⚠️ Unresolved Tracks (Top 20)</h2>
            <div class="section">
            {f'<table><tr><th>Track</th><th>Occurrences</th><th>Last Seen</th></tr>{unresolved_rows}</table>' if unresolved else '<p>No unresolved tracks!</p>'}
            </div>
        </div>

        <div id="tab-overrides" class="tab-content">
            <h2>📌 AmpliPhy Override Library ({len(db.list_overrides())})</h2>
            <div class="section">
            <form method="post" action="/ui/override" style="margin-bottom:16px">
                <div class="form-inline">
                    <input name="artist" placeholder="Artist" required style="min-width:150px">
                    <input name="title" placeholder="Title" required style="min-width:150px">
                    <input name="album" placeholder="Album (optional)" style="min-width:150px">
                    <input name="artwork_url" placeholder="Artwork URL" required style="flex:1;min-width:300px">
                    <button type="submit" class="success">➕ Add</button>
                </div>
            </form>
            <table>
                <tr><th></th><th>Artist</th><th>Title</th><th>Artwork URL</th><th></th></tr>
                {ov_rows if ov_rows else '<tr><td colspan="5" style="text-align:center;color:var(--muted-2);padding:20px">No overrides yet</td></tr>'}
            </table>
            </div>
        </div>

        <div id="tab-settings" class="tab-content">
            {settings_form}
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
