# ✨ MetaBridge with User-Friendly Settings UI

## What Changed

You now have a **completely rebuilt MetaBridge setup** with a user-friendly dashboard where you can enter your Cirrus credentials directly—no manual `.env` file editing needed!

## Key Improvements

### 1. **Settings Interface in Dashboard** ⚙️
- Enter your Cirrus callsign and token directly in the dashboard
- No need to manually create or edit files
- Settings are saved automatically
- Visual status indicator shows if Cirrus is configured and enabled

### 2. **Enhanced Dashboard**
- **Monitor Tab**: See all live events from PlayoutONE
- **Try a Lookup Tab**: Test metadata resolution manually
- **Unresolved Tab**: See tracks that need manual intervention
- **Overrides Tab**: Manage your custom artwork mappings
- **Settings Tab**: Configure Cirrus credentials right here

### 3. **Proper Desktop Application**
- Native PyWebView window (proper title bar, minimize/maximize buttons)
- No terminal window visible
- Runs as a standalone application
- `metabridge_launcher.py` makes it easy to start

## File Structure

```
metabridge_new/
├── app_enhanced.py              ← NEW: Main app with Settings UI
├── metabridge_launcher.py       ← NEW: Desktop launcher
├── requirements.txt             ← NEW: Dependencies
├── SETUP_GUIDE.md              ← NEW: Detailed setup instructions
├── README_NEW_SETUP.md         ← This file
├── metabridge/                 ← Core library (unchanged)
│   ├── config.py
│   ├── core.py
│   ├── db.py
│   ├── events.py
│   ├── normalize.py
│   ├── pipeline.py
│   ├── providers.py
│   ├── scoring.py
│   ├── inputs/
│   ├── outputs/
│   └── data/                   ← Databases created here
└── .gitignore                  ← NEW: Ignore sensitive files
```

## Quick Start (3 Steps)

### Step 1: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 2: Run the Application
```bash
python metabridge_launcher.py
```

This opens the dashboard in a native window at http://localhost:8765

### Step 3: Enter Your Cirrus Credentials
1. Click the **⚙️ Settings** tab
2. Enter:
   - **Station Call Sign**: e.g., KKGO
   - **Cirrus Auth Token**: Your SecureNet token
   - **Enabled Outputs**: `log,securenet_cirrus`
3. Click **💾 Save Settings**

That's it! Your metadata will now post to Cirrus.

## How to Get Your Cirrus Token

1. Log into **SecureNet Systems** → **Cirrus**
2. Go to **Settings** → **API Tokens**
3. Click **Generate New Token**
4. Copy and paste it into the Settings tab

**Important**: Use a fresh token, never one that was pasted into a chat.

## Testing It Works

1. In the dashboard, click **🔍 Try a Lookup**
2. Enter: Artist, Title, Album (optional)
3. Click **Resolve**
4. You should see resolved metadata with artwork

If it doesn't work, check the Settings tab—it will tell you if Cirrus is properly configured.

## Integration with PlayoutONE

When you're ready to send live track data from PlayoutONE:

1. In PlayoutONE Monitor, set the webhook URL to:
   ```
   http://YOUR_MACHINE_IP:8765/inbound/playoutone?artist=%%Artist%%&title=%%Title%%&album=%%Album%%
   ```

2. All tracks will appear in the **📊 Monitor** tab
3. Metadata will automatically post to Cirrus

See `SETUP_GUIDE.md` for detailed PlayoutONE configuration.

## Settings Are Saved

Your credentials are saved in `metabridge.env.json` on your local machine:
- This file is **not** committed to GitHub (.gitignore prevents it)
- Your token stays secure locally only
- You can move the file with your project

## For Developers / Advanced Users

**Use the original `app.py`** if you prefer the classic interface without Settings UI, or **`app_enhanced.py`** for the new version.

Both apps load the same core library, so everything is compatible.

## What if Something Goes Wrong?

### Dashboard doesn't load
- Make sure port 8765 isn't in use: `lsof -i :8765`
- Try a different port: `python -m uvicorn app_enhanced:app --port 8766`

### Settings not saving
- Check file permissions on the metabridge_new folder
- Make sure `metabridge.env.json` can be written

### Metadata not resolving
- Go to Settings tab — does it show "✓ Configured and enabled"?
- Try a test lookup in the Lookup tab
- Check your Cirrus token is valid (not expired)

### PyWebView window won't open
- The app falls back to your default browser
- Or install PyWebView: `pip install pywebview`

## Next: Push to GitHub

Once everything is tested locally, you can push this to your GitHub repository:

```bash
git add -A
git commit -m "Rebuilt MetaBridge with user-friendly Settings UI"
git push origin main
```

This is ready to be built as a Windows .exe using PyInstaller with the same workflow as before!

## Questions?

Refer to:
- `SETUP_GUIDE.md` — Detailed setup and configuration
- `DASHBOARD.md` — Dashboard feature documentation
- `app_enhanced.py` — Source code (well-commented)
