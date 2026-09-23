# AmpliPhy MetaBridge — Station Setup Guide

MetaBridge sits between your playout/automation system and your streaming
provider. Every time a song starts, it receives the artist/title, finds the
correct cover art (Apple Music, Deezer, Spotify, MusicBrainz — in that order),
and posts the complete now-playing package to your stream so listeners see
artwork for **every** song, including independent artists your provider
cannot match on its own.

This guide is included with every build. It covers what works today; sections
for other playout systems and stream providers will be added as they are
tested.

---

## 1. Install (or upgrade)

1. Download the newest `AmpliPhyBridge-vNN.zip` and unzip it anywhere
   (Downloads is fine).
2. Double-click `AmpliPhyBridge.exe`.
   - **First install:** it copies itself to
     `C:\Users\<you>\AppData\Local\AmpliPhy MetaBridge\`, adds a desktop
     shortcut, registers itself to start with Windows, and opens.
   - **Upgrade:** it closes the version that is running, replaces it, and
     reopens. Your settings, token, history and cover-art cache are kept.
     Nothing to re-enter.
3. The window title shows the version, e.g. `AmpliPhy MetaBridge v.46`.

Everything MetaBridge writes lives in that one AppData folder:

| File | What it is |
|---|---|
| `AmpliPhyBridge.exe` | the installed program |
| `metabridge.env.json` | your settings (token encrypted with Windows DPAPI) |
| `resolver.sqlite` | history + cover-art cache (trimmed to 30 days automatically) |
| `AmpliPhyBridge.log` | today's log (rolls over nightly, 7 days kept) |

MetaBridge listens only on this PC (`127.0.0.1`). Nothing on your network or
the internet can reach its dashboard or its API.

---

## 2. Dashboard → Settings tab

Open MetaBridge and click **⚙️ Settings**.

| Field | Value |
|---|---|
| Station Call Sign | the call sign your stream provider knows you by (e.g. `WERUDB`) |
| Cirrus Auth Token | from SecureNet — see §4. Paste once; it is stored encrypted |
| Enabled Outputs | `log,securenet_cirrus` |
| TCP Port | `8766` (leave as is unless it clashes with something) |
| Start with Windows | ✔ checked |

Click **💾 Save Settings**. The status bar should read
**✓ Configured and enabled**.

---

## 3. Playout system → MetaBridge

### 3a. PlayoutONE (tested)

PlayoutONE's *Metadata Output* only speaks TCP, so MetaBridge listens on a
TCP port for it.

In PlayoutONE: **Settings → Metadata Output → Add** (or edit the existing one)

| Setting | Value |
|---|---|
| Type | `TCP` |
| IP Address | `127.0.0.1`  ← plain address, **no** `http://` |
| Port | `8766` |
| Return Value | `queued` |
| Format | `artist=%%Artist%%\|title=%%Title%%\|duration=%%DurationSE%%\|album=%%Album%%\|\|NewLine\|\|` |

Notes:

- `duration` is required — the stream provider rejects updates without it.
  `%%DurationSE%%` is the duration in seconds.
- `||NewLine||` must be at the end; MetaBridge reads one line per song.
- Items with an empty artist (ad breaks, sweepers, IDs) are skipped on
  purpose and never sent to the stream.
- To confirm it is flowing: play a song and watch the **📊 Monitor** tab; it
  refreshes every 5 seconds.

### 3b. Any system that can call a URL (HTTP)

If your automation can fire a web request when a song starts, point it at:

```
http://127.0.0.1:8765/inbound/playoutone?artist=<artist>&title=<title>&duration=<seconds>&album=<album>
```

GET or POST, form or JSON — all accepted. The reply is `queued`.

### 3c. Other playout systems — compatibility (researched, not yet tested)

| Playout system | How to connect | Status |
|---|---|---|
| RadioDJ | "Now Playing Info" plugin → TCP/IP or Web export (HTTP) | should work today |
| StationPlaylist Studio | Options → Now Playing → Output via TCP/UDP (`%a`, `%t`, `%S` = seconds) | should work today (TCP) |
| mAirList | Logging → HTTP GET/POST (all editions); TCP/UDP on Professional | should work today (HTTP) |
| RadioBOSS | Settings → Reports → Now-playing notifications → HTTP URL | should work today (HTTP) |
| PlayIt Live 2.16+ | Tools → Now Playing → TCP/IP or HTTP Web Request (`{{duration}}`) | should work today |
| BSI Simian Pro | Program Options → Metadata → TCP with template | should work today (TCP) |
| RCS NexGen | Format Config → Export → TCP (line-based, length in seconds) | should work today (TCP) |
| ProppFrexx OnAir | OnTrackPlay macro → `EXEC_SEND_TCP` | should work today (TCP) |
| MegaSeg (Mac) | Logging → Telnet `tcp://<pc-ip>:8766` | should work (LAN) |
| Radiologik DJ (Mac) | CustomPublishURL (HTTP POST) | should work (LAN) |
| Myriad Playout | needs the OCP add-on → TCP template | should work with OCP |
| Rivendell (Linux) | `pypad_urlwrite` (HTTP) / `pypad_udp` | should work (LAN) |
| SAM Broadcaster Pro | writes an XML/text file (`$song.SS$`) | next: file-watcher input |
| ZaraStudio / ZaraRadio | writes `CurrentSong.txt` (no duration) | next: file-watcher input |
| Jazler RadioStar 2 | writes `Exports\NowOnAir.xml` | next: file-watcher input |
| ENCO DAD | writes `C:\DAD\DAD.XML` (LENGTH) | next: file-watcher input |
| Dalet | `onair.xml` via Dalet.ini | next: file-watcher input |
| Aeron Studio | Export HTML/XML/Text | next: file-watcher input |
| AzuraCast / LibreTime | local now-playing JSON API | next: poll input |
| RCS Zetta | Now Playing Export → HTTP POST / TCP, Zetta-Lite XML | next: XML adapter |
| WideOrbit | TCP client sending XML (duration in ms) | next: XML adapter |
| BSI OpX / NextKast | TCP, fixed XML | next: XML adapter |
| AudioVAULT (BE) | AVAir is a TCP *server*; MetaBridge would have to connect to it | later: custom adapter |
| SAM Broadcaster Cloud, Backbone Radio | hosted — no local metadata output | not supported |

"Should work today" means the vendor documents a TCP-line or HTTP-call
output with a user-defined format and MetaBridge already accepts that — the
same setup as PlayoutONE with that system's own placeholders. If you run one
of these and get it working, send the exact settings and they will be added
above as tested.

---

## 4. MetaBridge → stream provider

### 4a. SecureNet Systems / Cirrus (tested)

1. Log in to SecureNet → **Cirrus → Settings → API Tokens**.
2. Create a **new** token for MetaBridge. Never reuse a token that has been
   pasted into an email or chat.
3. Enter the call sign and token in the Settings tab (§2).

MetaBridge posts to `media_info_update_v2` with artist, title, album,
duration, and — only when it has a confident match — a `cover` URL.
When there is no confident match it omits `cover` so Cirrus does its own
matching; your listeners always get the correct artist/title regardless.

### 4b. Other stream providers

Not yet documented: Simplecast, Live365, Radio.co, StreamGuys, Shoutcast /
Icecast metadata, Triton. MetaBridge also has a generic **webhook** output
(`Enabled Outputs: log,webhook` + `METABRIDGE_WEBHOOK_URL`) that posts the
full enriched record as JSON, which is the starting point for any of these.

---

## 5. Everyday use

- **📊 Monitor** — live feed of every song, with the art it found and how
  long the lookup took.
- **🔍 Try a Lookup** — type an artist/title to see what MetaBridge would
  find, and why.
- **⚠️ Unresolved** — songs no service had art for. Almost always
  independent artists. Use **📌 Overrides** to pin your own artwork URL for
  them; it is used from then on.
- **🌙 / ☀️** — dark or light theme.

---

## 6. If something is wrong

| Symptom | Check |
|---|---|
| Nothing appears in Monitor when songs play | PlayoutONE Metadata Output is TCP, IP `127.0.0.1` (no `http://`), port `8766`, Return Value `queued` |
| Settings says "Not configured" | Call sign and token entered and saved; outputs contains `securenet_cirrus` |
| Songs show but no art on the stream | Look at the Cirrus line in the log — `HTTP 200 · cirrus reported success` means the post worked |
| "Port 8765 is already in use" | An older MetaBridge is still running — close it from Task Manager and start again |

The log is at `C:\Users\<you>\AppData\Local\AmpliPhy MetaBridge\AmpliPhyBridge.log`.
Sending the last screenful of it is the fastest way to get help.

---

*AmpliPhy Media LLC · ampliphy.net*
