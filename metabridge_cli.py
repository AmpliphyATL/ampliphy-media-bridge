#!/usr/bin/env python3
"""AmpliPhy MetaBridge command line. Standard library only.

  python3 metabridge_cli.py resolve "Artist" "Title" ["Album"]     resolve one track (JSON out)
  python3 metabridge_cli.py test [--no-cache]                        run data/test_dataset.json through the resolver, write reports
  python3 metabridge_cli.py simulate [--gap SECONDS]                 fire the dataset through the FULL pipeline as if Monitor sent it
  python3 metabridge_cli.py events [N]                               show the last N timed events
  python3 metabridge_cli.py stats                                    resolve rate + latency summary
  python3 metabridge_cli.py cirrus-test "Artist" "Title" [DURATION_SECONDS] [Album]
                                                                    send ONE controlled track to Cirrus (first live milestone)
  python3 metabridge_cli.py override "Artist" "Title" URL [Album]    add / replace a manual override
  python3 metabridge_cli.py overrides                                list overrides
  python3 metabridge_cli.py cache-clear                              wipe the resolved-track cache

  Set METABRIDGE_REPLAY=1 to use the captured Apple/Deezer results instead of the live APIs.
"""
from __future__ import annotations

import html
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from metabridge import config, db  # noqa: E402

config.load()
from metabridge.core import resolve  # noqa: E402
from metabridge.scoring import ACCEPT_THRESHOLD  # noqa: E402


def cmd_resolve(args):
    if len(args) < 2:
        sys.exit("usage: resolve ARTIST TITLE [ALBUM]")
    print(json.dumps(resolve(args[0], args[1], args[2] if len(args) > 2 else "", debug=True), indent=2, ensure_ascii=False))


def cmd_override(args):
    if len(args) < 3:
        sys.exit("usage: override ARTIST TITLE ARTWORK_URL [ALBUM]")
    print(json.dumps(db.set_override(args[0], args[1], args[2], args[3] if len(args) > 3 else ""), indent=2, ensure_ascii=False))


def cmd_overrides(_):
    for o in db.list_overrides():
        print(f"[{o['id']}] {o['artist']} — {o['title']}  ->  {o['artwork_url']}")


def cmd_cache_clear(_):
    print(f"cleared {db.cache_clear()} cached tracks")


def cmd_simulate(args):
    """Pretend to be PlayoutONE Monitor: build TrackEvents and run the whole pipeline."""
    from metabridge.events import TrackEvent
    from metabridge.pipeline import handle
    gap = 0.0
    if "--gap" in args:
        gap = float(args[args.index("--gap") + 1])
    ds_path = os.path.join(HERE, "data", "test_dataset.json")
    with open(ds_path, encoding="utf-8") as f:
        ds = json.load(f)
    for group in ("failures", "controls"):
        for t in ds[group]:
            src_ts = time.time()
            time.sleep(0.02)  # simulated transport delay Monitor -> MetaBridge
            ev = TrackEvent(artist=t["artist"], title=t["title"], album=t.get("album", ""), source="simulator",
                            raw=f"{t['artist']} - {t['title']}", source_ts=src_ts)
            en = handle(ev)
            print(f"{'OK ' if en.artwork_url else '-- '}{t['artist'][:28]:28} — {t['title'][:26]:26} {str(en.source or 'UNRESOLVED'):17} "
                  f"{en.confidence:.2f} cache={'Y' if en.cache_hit else 'n'} intake={en.intake_ms}ms resolve={en.resolve_ms}ms total={en.total_ms}ms", flush=True)
            time.sleep(gap)
    cmd_stats([])


def cmd_cirrus_test(args):
    """First live milestone: one controlled track through resolve -> Cirrus Direct Metadata Posting."""
    if len(args) < 2:
        sys.exit("usage: cirrus-test ARTIST TITLE [DURATION_SECONDS] [ALBUM]")
    from metabridge.events import TrackEvent
    from metabridge.pipeline import handle
    from metabridge.outputs import securenet_cirrus as cz
    dur = int(float(args[2])) if len(args) > 2 and args[2] else 0
    ev = TrackEvent(artist=args[0], title=args[1], album=args[3] if len(args) > 3 else "", duration_ms=dur * 1000,
                    source="cirrus_test", raw="cirrus-test", source_ts=time.time())
    os.environ["METABRIDGE_OUTPUTS"] = "securenet_cirrus"
    cfg = cz._cfg()
    print(f"Cirrus endpoint : {cfg['url']}  ({cfg['method']}, responseType={cfg['response']}{', DRY RUN' if cfg['dry_run'] else ''})")
    print(f"callsign        : {cfg['callsign'] or '(not set)'}    token: {'set' if cfg['token'] else '(NOT SET)'}")
    en = handle(ev)
    print(f"resolved        : {en.source or 'UNRESOLVED'} ({en.confidence:.2f}) {'cache' if en.cache_hit else ''} in {en.resolve_ms} ms")
    print(f"artwork         : {en.artwork_url or '(none — cover omitted, Cirrus will match on its own)'}")
    for o in en.outputs:
        p = o.payload or {}
        print(f"sent params     : " + ", ".join(f"{k}={v}" for k, v in p.items() if not k.startswith('_')))
        print(f"duration source : {p.get('_duration_source')}    cover sent: {p.get('_cover_sent')}")
        print(f"Cirrus result   : {'OK' if o.ok else 'FAILED'} — {o.status}   (roundtrip {o.roundtrip_ms} ms)")
    print(f"timing          : intake {en.intake_ms} ms · resolve {en.resolve_ms} ms · received→sent {en.total_ms} ms")


def cmd_events(args):
    n = int(args[0]) if args else 20
    for r in reversed(db.list_events(n)):
        print(f"{time.strftime('%H:%M:%S', time.localtime(r['received_ts']))} {r['input_source']:18} {r['artist'][:24]:24} — {r['title'][:24]:24} "
              f"{str(r['artwork_source'] or 'UNRESOLVED'):17} {r['confidence']:.2f} {'cache' if r['cache_hit'] else '     '} "
              f"intake={r['intake_ms']} resolve={r['resolve_ms']}ms total={r['total_ms']}ms")


def cmd_stats(_):
    s = db.event_stats()
    print(f"\nevents={s['n'] or 0} resolved={s['resolved_pct']}% cache_hits={s['cache_hits'] or 0} "
          f"avg_resolve={int(s['avg_resolve_ms'] or 0)}ms max_resolve={int(s['max_resolve_ms'] or 0)}ms "
          f"avg_total={int(s['avg_total_ms'] or 0)}ms avg_intake={int(s['avg_intake_ms'] or 0) if s['avg_intake_ms'] else '—'}ms")


def cmd_test(args):
    use_cache = "--no-cache" not in args
    ds_path = os.path.join(HERE, "data", "test_dataset.json")
    with open(ds_path, encoding="utf-8") as f:
        ds = json.load(f)

    rows = []
    for group in ("failures", "controls"):
        for t in ds[group]:
            t0 = time.time()
            r = resolve(t["artist"], t["title"], t.get("album", ""), use_cache=use_cache, debug=True)
            rows.append({"group": group, **t, "result": r, "secs": round(time.time() - t0, 1)})
            status = "OK " if r.get("artwork_url") else "-- "
            print(f"{status} [{group[:4]} #{t['n']:>2}] {t['artist']} — {t['title']}  =>  "
                  f"{r.get('source') or 'UNRESOLVED'} ({r.get('confidence', 0):.2f})", flush=True)

    out_json = os.path.join(HERE, "data", "test_results.json")
    out_html = os.path.join(HERE, "data", "test_results.html")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(render_html(rows))

    f_rows = [r for r in rows if r["group"] == "failures"]
    c_rows = [r for r in rows if r["group"] == "controls"]
    f_ok = sum(1 for r in f_rows if r["result"].get("artwork_url"))
    c_ok = sum(1 for r in c_rows if r["result"].get("artwork_url"))
    print(f"\nFailure set resolved: {f_ok}/{len(f_rows)}    Control set resolved: {c_ok}/{len(c_rows)}")
    print(f"Reports: {out_html}\n         {out_json}")


def render_html(rows) -> str:
    def esc(x):
        return html.escape(str(x if x is not None else ""))

    def row_html(r):
        res = r["result"]
        m = res.get("match") or {}
        art = res.get("artwork_url")
        conf = res.get("confidence", 0) or 0
        if art:
            cls, label = "ok", "RESOLVED"
        elif res.get("rejected_best"):
            cls, label = "low", "REJECTED (low confidence)"
        else:
            cls, label = "none", "NO CANDIDATES"
        rb = res.get("rejected_best") or {}
        alt = ""
        if not art and rb:
            alt = (f"<div class='alt'>Best rejected: {esc(rb.get('artist'))} — {esc(rb.get('title'))} "
                   f"({esc(rb.get('provider'))}, {rb.get('confidence', 0):.2f})<br>"
                   f"<img src='{esc(rb.get('artwork_url'))}' width='60'></div>")
        provs = res.get("providers") or {}
        prov_html = "<br>".join(
            f"{esc(p)}: " + (f"{v['confidence']:.2f}" if v else "—") for p, v in provs.items()
        )
        return (f"<tr class='{cls}'><td>{r['group'][:4]} #{r['n']}</td>"
                f"<td><b>{esc(r['artist'])}</b><br>{esc(r['title'])}<br><i>{esc(r.get('album'))}</i></td>"
                f"<td>{'<img src=' + chr(34) + esc(art) + chr(34) + ' width=90>' if art else ''}</td>"
                f"<td><span class='tag'>{label}</span><br>{esc(res.get('source') or '')} · {conf:.2f}</td>"
                f"<td>{esc(m.get('artist'))}<br>{esc(m.get('title'))}<br><i>{esc(m.get('album'))}</i>"
                f"{'<br><a href=' + chr(34) + esc(m.get('provider_url')) + chr(34) + ' target=_blank>open</a>' if m.get('provider_url') else ''}{alt}</td>"
                f"<td class='small'>{prov_html}</td>"
                f"<td class='small'>{'<br>'.join(esc(x) for x in res.get('reasons', []))}</td></tr>")

    body = "".join(row_html(r) for r in rows)
    f_rows = [r for r in rows if r["group"] == "failures"]
    c_rows = [r for r in rows if r["group"] == "controls"]
    f_ok = sum(1 for r in f_rows if r["result"].get("artwork_url"))
    c_ok = sum(1 for r in c_rows if r["result"].get("artwork_url"))
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>AmpliPhy MetaBridge — resolver test</title>
<style>
body{{font-family:-apple-system,Helvetica,Arial,sans-serif;margin:24px;color:#222}}
table{{border-collapse:collapse;width:100%}} td,th{{border:1px solid #ddd;padding:8px;vertical-align:top;font-size:13px}}
th{{background:#f4f4f4;text-align:left}} tr.ok td:first-child{{border-left:6px solid #2e9e5b}}
tr.low td:first-child{{border-left:6px solid #e0a000}} tr.none td:first-child{{border-left:6px solid #c33}}
.tag{{font-weight:700}} .small{{font-size:11px;color:#555}} .alt{{margin-top:6px;padding:6px;background:#fff6e0;font-size:11px}}
img{{border:1px solid #ccc;border-radius:4px}}
</style></head><body>
<h1>AmpliPhy MetaBridge — resolver test run</h1>
<p>Failure set resolved: <b>{f_ok}/{len(f_rows)}</b> &nbsp;·&nbsp; Control set resolved: <b>{c_ok}/{len(c_rows)}</b>
&nbsp;·&nbsp; accept threshold {ACCEPT_THRESHOLD:.2f} &nbsp;·&nbsp; {time.strftime('%Y-%m-%d %H:%M')}</p>
<table><tr><th>#</th><th>Station metadata</th><th>Art</th><th>Outcome</th><th>Matched record</th><th>Per provider</th><th>Why</th></tr>
{body}</table></body></html>"""


if __name__ == "__main__":
    cmds = {"resolve": cmd_resolve, "test": cmd_test, "simulate": cmd_simulate, "cirrus-test": cmd_cirrus_test, "events": cmd_events, "stats": cmd_stats, "override": cmd_override, "overrides": cmd_overrides, "cache-clear": cmd_cache_clear}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__)
        sys.exit(1)
    cmds[sys.argv[1]](sys.argv[2:])
