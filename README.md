# AmpliPhy MetaBridge

Real-time music metadata enrichment middleware for internet radio.

    Input Adapter -> TrackEvent -> Resolver Core -> EnrichedTrackEvent -> Output Adapter(s)

A station's automation says "the song changed, here is Artist/Title". MetaBridge
identifies the release, finds trusted cover art, and forwards the enriched
Now Playing metadata downstream — automatically, with no per-song manual entry.

The core knows nothing about PlayoutONE or SecureNet. They are Adapter #1
because that is AmpliPhy's production environment. Other automation systems
and streaming platforms are new adapters, not a rewrite.

## Status

| Piece | State |
|---|---|
| Resolver core (normalize → override → cache → Apple Music / Deezer / Spotify* / MusicBrainz → confidence scoring) | **done**, live-tested: 14/22 previously-failing tracks resolved, 7/8 controls preserved, 0 wrong matches |
| Input adapter: PlayoutONE Monitor (HTTP GET/POST, TCP line) | **done** |
| Output adapter: `log` (record only) | **done** — default |
| Output adapter: `webhook` (JSON POST to any URL — website/app feed, testing) | **done** |
| Output adapter: `securenet_cirrus` — Cirrus **Direct Metadata Posting** (`media_info_update_v2.cfm`) | **done**, tested against a mock Cirrus endpoint; first live post pending |
| Timing instrumentation (source → received → resolve → sent), events log, CSV, dashboard | **done** |
| Phase 2B prefetch of `%%NextArtist%%/%%NextTitle%%` | fields accepted and stored; not acted on (by design) |

\* Spotify only runs when `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` are set.

## Run it

```bash
pip3 install fastapi uvicorn
python3 -m uvicorn app:app --host 0.0.0.0 --port 8765
```

Dashboard: <http://127.0.0.1:8765/> — live events with per-hop timings, resolve rate, override library.

### Point PlayoutONE Monitor at it

Add an HTTP output in Monitor with a URL like (recommended — fields arrive separately):

    http://<metabridge-host>:8765/inbound/playoutone?artist=%%Artist%%&title=%%Title%%&album=%%Album%%&isrc=%%ISRC%%

or the same one-string format Monitor sends SecureNet today:

    http://<metabridge-host>:8765/inbound/playoutone?data=%%Artist%% - %%Title%%

MetaBridge answers `200` with the bare word `queued` immediately (put `queued` in Monitor's *Return Value* box)
and does the lookup + forwarding in the background, so Monitor never waits on us. Tokens go in Monitor's *Parameters* box, one per line;
the URL box is the plain address. A repeat of the same track within 20 s is ignored.

TCP instead of HTTP: set `METABRIDGE_TCP_PORT=8766` and have Monitor send `%%Artist%% - %%Title%%` lines there.

### Cirrus (SecureNet) — Direct Metadata Posting

Copy `metabridge.env.example` to `metabridge.env` and fill in `METABRIDGE_CIRRUS_CALLSIGN` and a
**fresh** `METABRIDGE_CIRRUS_TOKEN` (the one that was pasted into a chat must be rotated first).
`metabridge.env` is git-ignored; the token is never written to logs, the event log, CSV, or the dashboard.

MetaBridge POSTs to `https://streamdb9.securenetsystems.net/dx/media_info_update_v2.cfm` with
`stationCallSign`, `authToken`, `title`, `artist`, `duration` (seconds), and when known `album`,
`isrc`, `category`. `cover` is sent **only** when MetaBridge has a trusted artwork match; otherwise it is
omitted so Cirrus does its own matching. `responseType=xml` is used so Cirrus's success/error comes back
and is recorded with the request/response round-trip time on every event.

**Duration is required by Cirrus and is never invented.** Order of truth:
1. the automation event — add `&duration=%%Duration%%` to the Monitor URL (seconds, ms, or `m:ss` all parse)
2. the matched provider record (Apple Music / Deezer / Spotify / MusicBrainz all report length)
3. neither → the Cirrus post is **skipped** and logged as an error; nothing is sent

#### First live milestone — one controlled track, station untouched

Do **not** repoint Monitor yet. With credentials in `metabridge.env`:

```bash
python3 metabridge_cli.py cirrus-test "Syd" "Anytime feat. James Fauntleroy" 269
```

That resolves the track, POSTs once to Cirrus, prints exactly what was sent (token masked), the Cirrus
response, and the timings. Then check the Cirrus console / AmpliPhy app for the artwork.
Add `METABRIDGE_CIRRUS_DRY_RUN=1` to rehearse without sending.
Note from Cirrus docs: direct posting does not update *in-stream* metadata by default; SecureNet support enables that separately.

### Choose outputs

```bash
METABRIDGE_OUTPUTS=log                      # default: record only
METABRIDGE_OUTPUTS=log,webhook              # + POST JSON to METABRIDGE_WEBHOOK_URL
METABRIDGE_OUTPUTS=log,securenet_cirrus     # + Cirrus Direct Metadata Posting
```

## Test without the station (or without network)

```bash
METABRIDGE_REPLAY=1 python3 metabridge_cli.py simulate     # fires the 30-track dataset through the full pipeline
python3 metabridge_cli.py stats                             # resolve rate + latencies
python3 metabridge_cli.py events 30                         # last 30 timed events
python3 metabridge_cli.py test                              # resolver-only test with live APIs, writes data/test_results.html
```

`METABRIDGE_REPLAY=1` swaps the live providers for the Apple/Deezer results captured on 2026-09-11,
so the pipeline can be exercised anywhere. Run `simulate` twice to see the cache path (≈4 ms vs ≈100 ms).

## Timing instrumentation

Every event records:

| field | meaning |
|---|---|
| `source_ts` | when automation says the track changed (only if Monitor passes `ts=` / airtime) |
| `received_ts` | when MetaBridge got it |
| `resolve_start` / `resolve_end` | the core's work (`resolve_ms`) |
| `sent_ts` per output | when each downstream payload went out |
| `intake_ms` | source → received |
| `total_ms` | received → last output sent |

`GET /events` (JSON), `GET /events.csv`, `GET /stats`, or the dashboard.
The remaining unmeasured hop — downstream → artwork visible in the AmpliPhy app — is measured
by eye against `sent_ts` until Cirrus exposes something we can read.

## Override library (no per-song typing required)

Pins artwork for releases that exist on no service. Add via dashboard, API, CLI, or bulk:

```bash
python3 metabridge_cli.py override "Jaydon" "The Way You Move" "https://ampliphy.net/art/jaydon.jpg"
```

(Bulk import from a spreadsheet is the next addition to this piece.)

## Layout

```
metabridge/normalize.py           metadata cleanup (feat./remix/accents/brackets)
metabridge/providers.py           Apple Music, Deezer, Spotify, MusicBrainz lookups (+ offline replay)
metabridge/scoring.py             confidence scoring + thresholds
metabridge/core.py                resolve(): override -> cache -> providers   (source/destination-neutral)
metabridge/events.py              TrackEvent / EnrichedTrackEvent + timing
metabridge/pipeline.py            handle(): resolve -> outputs -> event log
metabridge/inputs/playoutone_monitor.py   parses Monitor's HTTP / text formats
metabridge/inputs/tcp_listener.py         optional TCP line transport
metabridge/outputs/log.py                 record only
metabridge/outputs/webhook.py             JSON POST anywhere
metabridge/outputs/securenet_cirrus.py    Cirrus Direct Metadata Posting adapter
metabridge/config.py                      loads metabridge.env (secrets) into the environment
metabridge/db.py                  SQLite: cache, overrides, lookups, timed events
app.py                            FastAPI service + dashboard
metabridge_cli.py                 CLI: resolve / test / simulate / cirrus-test / events / stats / override
data/test_dataset.json            30-track Phase 1 dataset (+ captured provider results)
```

## Configuration

| variable | purpose |
|---|---|
| `METABRIDGE_OUTPUTS` | comma list of output adapters (default `log`) |
| `METABRIDGE_WEBHOOK_URL`, `METABRIDGE_WEBHOOK_TOKEN` | webhook adapter |
| `METABRIDGE_CIRRUS_CALLSIGN`, `METABRIDGE_CIRRUS_TOKEN` | Cirrus credentials (put them in `metabridge.env`) |
| `METABRIDGE_CIRRUS_METHOD`, `_RESPONSE`, `_STATIONFLAG`, `_DRY_RUN`, `_URL` | Cirrus adapter options (defaults: POST, xml, 0, off, documented endpoint) |
| `METABRIDGE_TCP_PORT` | enable TCP intake |
| `METABRIDGE_DEDUPE_SECONDS` | duplicate-notification window (20) |
| `METABRIDGE_REPLAY` | offline provider replay for testing |
| `RESOLVER_ACCEPT_THRESHOLD` | minimum confidence to use artwork (0.80) |
| `RESOLVER_DB` | SQLite path |
| `SPOTIFY_CLIENT_ID/SECRET` | enable Spotify provider |
