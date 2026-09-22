# AmpliPhy Media Bridge - GitHub Automated Build Setup

## What This Does
Automatically builds `AmpliPhyBridge.exe` on GitHub whenever you push code changes. No manual PyInstaller commands needed.

## Setup Steps (5 minutes)

### 1. Create Repository on GitHub
- Go to https://github.com/new
- Repository name: `ampliphy-media-bridge`
- Description: `AmpliPhy Media Bridge - Metadata Resolver for Independent Artists`
- Choose "Public" or "Private"
- Click "Create repository"

### 2. Push Code to GitHub
Open Command Prompt and run:

```
cd C:\Users\[YourUsername]\Desktop\ampliphy-github-setup
git init
git add .
git commit -m "Initial commit: AmpliPhy Media Bridge source code"
git branch -M main
git remote add origin https://github.com/AmpliphyATL/ampliphy-media-bridge.git
git push -u origin main
```

*(Replace the URL with your actual repository URL from GitHub)*

### 3. Watch the Build
- Go to your GitHub repository
- Click "Actions" tab
- Watch the build run automatically
- Wait 2-3 minutes for completion

### 4. Download the .exe
- Build completes ✓
- Click the latest workflow run
- Scroll down to "Artifacts"
- Download `AmpliPhyBridge` (contains your .exe)
- Extract it
- Copy `AmpliPhyBridge.exe` to `C:\PlayoutONE\metabridge 5\`

## Done!
Your `.exe` is built automatically. Every time you update the code on GitHub, a new `.exe` is built automatically.

## Future Updates
To rebuild the `.exe`:
1. Make changes to your code locally
2. Run: `git add . && git commit -m "Your message" && git push`
3. GitHub builds it automatically
4. Download the new `.exe` from Actions

That's it.
