# AmpliPhy MetaBridge — Setup Guide

This guide walks you through setting up MetaBridge with the new user-friendly Settings interface.

## What's New

MetaBridge now includes a **Settings page in the dashboard** where you can enter your SecureNet Cirrus credentials directly. No more manual `.env` file editing needed!

## Project Structure

```
metabridge_new/
├── app_enhanced.py              # Main FastAPI app with Settings UI
├── metabridge_launcher.py       # Desktop launcher (PyWebView)
├── metabridge/                  # Core library
│   ├── config.py               # Config loader
│   ├── core.py                 # Resolution engine
│   ├── db.py                   # Database & caching
│   ├── events.py               # Event models
│   ├── normalize.py            # Track normalization
│   ├── pipeline.py             # Processing pipeline
│   ├── providers.py            # Metadata providers
│   ├── scoring.py              # Confidence scoring
│   ├── outputs/                # Output adapters
│   │   ├── log.py
│   │   ├── securenet_cirrus.py # Cirrus posting
│   │   └── webhook.py
│   └── inputs/                 # Input adapters
│       ├── playoutone_monitor.py
│       ├── playoutone_http_poll.py
│       └── tcp_listener.py
├── data/                        # Database files (auto-created)
├── metabridge.env.json         # Settings file (auto-created)
└── SETUP_GUIDE.md              # This file
```

## Quick Start

### 1. Install Dependencies

```bash
pip install fastapi uvicorn requests webview requests
```

### 2. Run the Dashboard

**Option A: As a Desktop Application** (Recommended)
```bash
python metabridge_launcher.py
```

This will:
- Start the FastAPI server in the background
- Open the dashboard in a native desktop window
- No terminal window visible

**Option B: Direct Server**
```bash
python -m uvicorn app_enhanced:app --host 0.0.0.0 --port 8765
```

Then open http://localhost:8765 in your browser.

### 3. Configure Cirrus Credentials

1. Open the dashboard
2. Click the **⚙️ Settings** tab
3. Fill in:
   - **Station Call Sign**: Your call sign (e.g., KKGO)
   - **Cirrus Auth Token**: Your SecureNet token
   - **Enabled Outputs**: Include `securenet_cirrus` in the list
4. Click **💾 Save Settings**

Settings are automatically saved to `metabridge.env.json` (in your project folder).

## How to Get Your Cirrus Token

1. Log in to **SecureNet Systems** → **Cirrus**
2. Go to **Settings** → **API Tokens**
3. Click **Generate New Token** (or use an existing one)
4. Copy the token
5. **Important**: Never use a token that was pasted into chat. Generate a fresh one.

## Verifying It Works

1. In the dashboard, go to the **🔍 Try a Lookup** tab
2. Enter an artist, title, and album
3. Click **Resolve**
4. You should see the resolved metadata and artwork

If the lookup doesn't resolve, check:
- ✓ Settings tab shows "✓ Configured and enabled"
- ✓ Your Cirrus token is valid (not expired or revoked)
- ✓ Your internet connection is working

## Integration with PlayoutONE

MetaBridge can receive live track information from PlayoutONE Monitor:

1. In PlayoutONE, go to **Monitor** → **Web Server**
2. Set the URL to:
   ```
   http://YOUR_MACHINE_IP:8765/inbound/playoutone?artist=%%Artist%%&title=%%Title%%&album=%%Album%%
   ```
3. Click **Test** to verify connectivity
4. When a track plays, MetaBridge will:
   - Resolve the metadata
   - Post it to Cirrus (if configured)
   - Log it in the **📊 Monitor** tab

## Dashboard Tabs

- **📊 Monitor**: Live track events from PlayoutONE
- **🔍 Try a Lookup**: Test metadata resolution manually
- **⚠️ Unresolved**: Tracks that couldn't be resolved (most frequent)
- **📌 Overrides**: Your custom artwork mappings
- **⚙️ Settings**: Cirrus credentials and configuration

## Advanced Configuration

You can also set environment variables directly (they take precedence over the settings file):

```bash
export METABRIDGE_CIRRUS_CALLSIGN="KKGO"
export METABRIDGE_CIRRUS_TOKEN="your-token-here"
export METABRIDGE_OUTPUTS="log,securenet_cirrus"
export METABRIDGE_TCP_PORT="8766"

python -m uvicorn app_enhanced:app --host 0.0.0.0 --port 8765
```

## Troubleshooting

### Dashboard shows "Not configured" in Settings
- You haven't filled in the Cirrus credentials yet. See step 3 above.

### "Port 8765 already in use"
- Another instance of MetaBridge is running. Close it or use a different port:
  ```bash
  python -m uvicorn app_enhanced:app --port 8766
  ```

### Metadata not resolving
- Check Settings → Cirrus Status should show "✓ Configured and enabled"
- Try a test lookup in the **🔍 Try a Lookup** tab
- If it fails, your Cirrus token may be invalid

### PyWebView not opening
- Make sure you've installed it: `pip install pywebview`
- The app will fall back to your default browser if PyWebView isn't available

## Security Notes

- Your Cirrus token is stored in `metabridge.env.json` on your machine only
- The file is **not** backed up to GitHub (add to .gitignore)
- Never commit this file to version control
- If you suspect your token is compromised, rotate it immediately in SecureNet

## Files Created Automatically

- `metabridge.env.json`: Your saved settings
- `data/metabridge.db`: Event log and cache
- `data/overrides.db`: Your manual artwork overrides

## Next Steps

1. ✓ Install dependencies
2. ✓ Run the launcher
3. ✓ Enter Cirrus credentials in Settings
4. ✓ Test with a manual lookup
5. ✓ Connect PlayoutONE Monitor (optional)
