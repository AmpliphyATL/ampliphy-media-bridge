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
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
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
* { box-sizing: border-box; }
body { font-family: -apple-system, Helvetica, Arial, sans-serif; margin: 0; padding: 0; background: #fafafa; color: #222; }
header { background: linear-gradient(135deg, #fff 0%, #f9f9f9 100%); padding: 20px 24px; border-bottom: 2px solid #e0e0e0; box-shadow: 0 2px 4px rgba(0,0,0,0.08); }
main { margin: 24px auto; max-width: 1200px; }
h1 { font-size: 24px; margin: 0; }
h1 small { font-size: 14px; font-weight: 400; color: #777; }
h2 { font-size: 16px; margin: 24px 0 16px; padding-bottom: 8px; border-bottom: 1px solid #e0e0e0; }
input, textarea, select { padding: 8px; margin: 4px 0; border: 1px solid #ddd; border-radius: 4px; font-size: 13px; font-family: inherit; }
input:focus, textarea:focus, select:focus { outline: 0; border-color: #007aff; box-shadow: 0 0 0 2px rgba(0,122,255,0.1); }
button { padding: 8px 14px; margin: 4px 4px 4px 0; background: #007aff; color: #fff; border: 0; border-radius: 4px; font-size: 13px; cursor: pointer; transition: background 0.2s; }
button:hover { background: #0051d5; }
button.secondary { background: #e8e8e8; color: #222; }
button.secondary:hover { background: #d0d0d0; }
button.success { background: #2e9e5b; }
button.success:hover { background: #237d48; }
button.danger { background: #dc3545; }
button.danger:hover { background: #bb2d3b; }
table { border-collapse: collapse; width: 100%; background: #fff; border: 1px solid #e0e0e0; border-radius: 4px; overflow: hidden; }
td, th { border-bottom: 1px solid #e0e0e0; padding: 10px; font-size: 13px; vertical-align: top; text-align: left; }
th { background: #f5f5f5; font-weight: 500; }
tr:last-child td { border-bottom: 0; }
img { border: 1px solid #ddd; border-radius: 4px; background: #fff; }
.alert { padding: 12px; border-radius: 4px; margin-bottom: 16px; }
.alert.warning { background: #fff8e6; border-left: 4px solid #ff9800; color: #e65100; }
.alert.success { background: #e8f5e9; border-left: 4px solid #4caf50; color: #2e7d32; }
.alert.error { background: #ffebee; border-left: 4px solid #dc3545; color: #c62828; }
.kpi { display: inline-block; background: #fff; border: 1px solid #e0e0e0; border-radius: 6px; padding: 14px 16px; margin: 0 8px 12px 0; font-size: 13px; min-width: 100px; }
.kpi b { font-size: 18px; display: block; color: #007aff; }
.kpi span { color: #777; font-size: 12px; }
.ok { color: #2e9e5b; font-weight: 500; }
.no { color: #dc3545; font-weight: 500; }
.small { color: #777; font-size: 12px; }
.form-group { margin-bottom: 16px; padding: 12px; background: #f9f9f9; border-radius: 4px; border-left: 3px solid #ddd; }
.form-group label { display: block; font-size: 12px; font-weight: 500; margin-bottom: 4px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; }
.form-group label .note { font-weight: 400; color: #999; font-size: 11px; display: block; margin-top: 2px; }
.form-inline { display: flex; gap: 8px; align-items: flex-end; flex-wrap: wrap; }
.form-inline input { margin: 0; flex: 1; min-width: 200px; }
.form-inline button { margin: 0; white-space: nowrap; }
.section { background: #fff; padding: 16px; border-radius: 6px; margin-bottom: 16px; border: 1px solid #e0e0e0; }
.tab-nav { display: flex; gap: 8px; margin-bottom: 16px; border-bottom: 2px solid #e0e0e0; flex-wrap: wrap; }
.tab-nav a { padding: 10px 16px; text-decoration: none; color: #666; border-bottom: 3px solid transparent; cursor: pointer; transition: all 0.2s; }
.tab-nav a:hover { color: #007aff; }
.tab-nav a.active { color: #007aff; border-bottom-color: #007aff; }
.tab-content { display: none; }
.tab-content.active { display: block; }
.badge { display: inline-block; padding: 2px 8px; background: #e8e8e8; border-radius: 3px; font-size: 11px; margin: 0 2px; }
.badge.ok { background: #e8f5e9; color: #2e7d32; }
.badge.warn { background: #fff8e6; color: #e65100; }
.unresolved-row { background: #fffbfb; }
.settings-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 768px) { .settings-grid { grid-template-columns: 1fr; } }
.status-indicator { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }
.status-indicator.ok { background: #4caf50; }
.status-indicator.warn { background: #ff9800; }
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
                <label style="text-transform:none;font-size:13px;color:#222;display:flex;align-items:center;gap:8px;cursor:pointer">
                    <input type="checkbox" id="autostart_on" {'checked' if settings.get('autostart', True) else ''} style="width:16px;height:16px;margin:0">
                    Start MetaBridge automatically at login
                    <span class="badge {'ok' if autostart.is_enabled() else 'warn'}">{'registered' if autostart.is_enabled() else 'not registered'}</span>
                </label>
            </div>
        </div>

        <button class="success" onclick="saveSettings()">💾 Save Settings</button>
        <span id="save-status" style="margin-left:12px;font-size:12px;color:#999"></span>

        <div style="margin-top:24px;padding:12px;background:#f0f0f0;border-radius:4px;font-size:12px;color:#555">
            <b>ℹ️ Setup Instructions:</b><br>
            1. Enter your SecureNet Cirrus call sign and auth token above<br>
            2. Make sure "securenet_cirrus" is in the Outputs list<br>
            3. Click Save Settings<br>
            4. Test by entering a track in the Lookup tab — metadata should post to Cirrus<br>
            <br><b>Getting your token:</b> Log in to SecureNet > Cirrus > Settings > API Tokens. <b>Important:</b> Use a fresh token, never paste a token that was shared in chat.
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

    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AmpliPhy MetaBridge {VERSION}</title>{STYLE}</head><body>
    <header>
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px">
            <div style="font-size:28px;font-weight:bold;color:#007aff;font-family:Georgia,serif">🎵</div>
            <div>
                <div style="font-size:12px;color:#999;text-transform:uppercase;letter-spacing:1px;font-weight:500">AmpliPhy</div>
                <div style="font-size:20px;font-weight:600;color:#222">MetaBridge <span style="font-size:12px;font-weight:500;color:#999;margin-left:6px">{VERSION}</span></div>
            </div>
            <div style="flex:1"></div>
            <div style="text-align:right;font-size:11px;color:#999">
                <div>Real-Time Metadata Enrichment</div>
                <div>SecureNet Cirrus Integration</div>
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
                {ev_rows if ev_rows else '<tr><td colspan="5" style="text-align:center;color:#999;padding:32px">No events yet — waiting for input from PlayoutONE</td></tr>'}
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
                {ov_rows if ov_rows else '<tr><td colspan="5" style="text-align:center;color:#999;padding:20px">No overrides yet</td></tr>'}
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
