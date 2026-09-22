# MetaBridge Console — Setup & Deployment

## For macOS Users (Your Setup)

### Quick Start
1. Copy the entire `metabridge 5` folder to a permanent location (e.g., `/Applications/MetaBridge/`)
2. **Double-click `MetaBridge.command`** to launch
3. The console window opens, Flask server starts automatically
4. Configure your PlayoutONE connection in the **Settings** tab
5. Monitor live events in the **Dashboard** tab

### What Happens When You Double-Click
- ✓ Flask server starts in background (no terminal visible)
- ✓ Console window opens with dark professional interface
- ✓ Automatically connects to Flask server at `localhost:8765`
- ✓ Live stats refresh every 2 seconds
- ✓ All logs appear in the **Logs** tab

### Configuration (First Time)
1. Open **Settings** tab
2. Enter:
   - PlayoutONE IP: your monitor IP (default: `127.0.0.1`)
   - PlayoutONE Port: monitor port (default: `2766`)
   - Cirrus URL: SecureNet endpoint
   - Cirrus Username: your SecureNet username
3. Click **Save Settings**
4. Restart the app for changes to take effect

### Dashboard Tabs

**Dashboard**
- Live now-playing track
- Key metrics: events, resolution %, cache hits, response time
- Expandable "Resolved Tracks" section (shows all resolved songs)
- Expandable "Unresolved Tracks" section (shows problem tracks)

**Settings**
- Configure PlayoutONE connection
- Set Cirrus credentials
- Enable/disable auto-start on launch

**Logs**
- Real-time activity log
- Error messages and status updates

## For Deployment to Other AmpliPHy Users

### Package Structure
```
MetaBridge/
├── MetaBridge.command          # Mac launcher (double-click to run)
├── console.py                  # Desktop app
├── app.py                       # Flask server
├── metabridge/
│   ├── __init__.py
│   ├── core.py                # Resolution logic
│   ├── db.py                  # SQLite storage
│   ├── normalize.py           # Metadata parsing
│   ├── providers.py           # Data sources
│   ├── config.py
│   ├── inputs/
│   ├── outputs/
│   └── ...
├── data/
│   └── resolver.sqlite        # Cache database
├── README.md
└── CONSOLE_SETUP.md
```

### Requirements
- Python 3.8+
- Flask / FastAPI / Uvicorn
- tkinter (built-in to Python on macOS)
- Requests library

### Installation for Other Users

**Option A: Standalone App (Recommended)**
```bash
# Users just need to:
# 1. Download the folder
# 2. Double-click MetaBridge.command
# Done!
```

**Option B: Docker Container**
```bash
docker build -t metabridge .
docker run -p 8765:8765 metabridge
```

**Option C: Python Package**
```bash
pip install metabridge
metabridge-console
```

## Troubleshooting

**"Python 3 is required"**
→ Install Python 3 from python.org or via Homebrew

**Console won't start**
→ Check the Logs tab for error messages
→ Verify port 8765 is not in use: `lsof -i :8765`

**No stats showing**
→ Check Settings tab configuration
→ Verify PlayoutONE is sending metadata

**Resolved tracks not showing**
→ Monitor has not received any tracks yet
→ Wait for a song to play on the stream

## Next Steps

1. **Test locally** — Double-click and verify it works
2. **Configure settings** — Set up PlayoutONE and Cirrus info
3. **Monitor a full show** — Run for a few hours and check stats
4. **Package for distribution** — Create installer for other stations

## Notes

- The console app is lightweight (~5MB)
- Starts Flask server automatically (no terminal)
- SQLite database stores everything locally
- No external cloud dependencies
- Configuration saved to `~/.metabridge_config.json`
