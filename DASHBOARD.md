# MetaBridge Dashboard v0.5.0

A complete user-facing monitoring and management interface for the AmpliPhy cover art resolver.

## Key Features

### 1. Real-Time Monitoring (`Monitor` tab)
- **Live Events Table**: Shows the latest 50 resolved/unresolved tracks in real time
- **Status Indicators**: ✓ Resolved vs ✗ Unresolved with confidence scores
- **Quick Details**: Each row links to a Deep Dive analysis for that track
- **Performance Metrics**: Resolution time tracking for each lookup

### 2. Unresolved Reports (`Unresolved` tab)
- **Top Unresolved Tracks**: Shows the most frequently unresolved songs (top 20)
- **Frequency Breakdown**: Displays how many times each track failed to resolve
- **Last Seen Timestamps**: When that track was last played
- **Retry Button**: Force re-resolution for any unresolved track with one click

### 3. Manual Lookup & Debugging (`Lookup` tab)
- **Search Form**: Query any artist/title manually to see the resolution result
- **Instant Feedback**: Shows whether the track resolved or not
- **Force Resolve**: Button to retry resolution even if cached
- **Deep Dive Link**: Jump to detailed analysis for any track
- **Pin Override**: Manually store artwork URLs for problematic tracks

### 4. Deep Dive Analysis Page (`/deep-dive`)
- **What Was Searched**: Shows the exact artist/title query
- **What Was Matched**: Displays the final selected track metadata
- **Candidate Evaluation**: Table of all candidate matches considered
  - Match quality (0-100%)
  - Selection status (used vs not used)
  - Reason for rejection in user-friendly terms
- **Why This Result**: Bullet points explaining the selection/rejection logic
- **No Provider Sources Revealed**: Hidden per critical policy—shows only match quality and metadata

### 5. Override Library (`Overrides` tab)
- **Manual Mapping Storage**: Store artist/title → artwork URL mappings
- **Quick Add Form**: Add new overrides on the fly
- **Active Override List**: Shows all currently pinned artwork mappings
- **Remove Button**: Delete overrides you no longer need

### 6. Data Export (`Export` tab)
- **CSV Download**: Download full resolution event log with timing data
- **JSON API**: Access raw event data programmatically at `/events`

### 7. System Health Dashboard
- **Key Metrics** at the top:
  - Total events processed
  - Resolution success rate (%)
  - Cache hit count
  - Average/max resolution time
- **Status Indicators**: Real-time health of the system

## New API Endpoints

```
GET  /unresolved                    List top unresolved tracks (limit=100)
POST /force-resolve                 Force re-resolve a track (bypasses cache)
GET  /deep-dive?artist=X&title=Y   Interactive debug view for any track
```

## Tab-Based Navigation

The dashboard uses tabbed interface for better organization:
- 📊 **Monitor** - Live events and status
- ⚠️ **Unresolved** - Problem tracks needing attention
- 🔍 **Lookup** - Manual resolution testing
- 📌 **Overrides** - Manual artwork mappings
- 📥 **Export** - Data download

## UI/UX Improvements

- **Modern Styling**: Clean, card-based layout with proper spacing
- **Color Coding**: Green (✓ resolved), Red (✗ unresolved), Blue (actions)
- **Responsive Design**: Works on desktop and tablet
- **Accessibility**: Semantic HTML, clear contrast, readable fonts
- **Performance**: Lightweight, no heavy dependencies

## Critical Design Constraint

**Provider sources are NEVER revealed to end users.** The dashboard only shows:
- ✓ Track is resolved / ✗ Track is unresolved
- Match quality (confidence %)
- Metadata (artist, title, album)
- General explanation of why matching succeeded/failed

What is NOT shown:
- Which provider returned the artwork (Apple, Deezer, Spotify, MusicBrainz)
- Confidence scores or technical metrics
- Cache vs fresh lookup details
- Scoring internals or provider errors

## Usage Examples

### Checking why a track didn't resolve
1. Go to **Unresolved** tab
2. Find the track in the "Top 20 Unresolved" list
3. Click **Retry** to force re-resolution
4. Or click track name to jump to Deep Dive for analysis
5. Check if metadata spelling is correct in your source system

### Pinning artwork for a problem track
1. Go to **Lookup** tab
2. Search for the artist/title
3. If resolved, the artwork URL appears in the result
4. Click **Pin Override** to manually store it
5. Future plays of that track will always use this artwork

### Debugging resolution failures
1. Search the track in **Lookup** tab
2. If unresolved, click **Deep Dive**
3. See all candidate matches that were considered
4. Check why the best match wasn't good enough
5. Verify metadata in your source system (PlayoutONE)

## Deployment

The dashboard runs as part of the main Flask app:

```bash
python3 -m uvicorn app:app --host 0.0.0.0 --port 8765
```

Then open: `http://localhost:8765`

On a remote computer, use the IP address: `http://192.168.1.100:8765`

## No Additional Dependencies

The dashboard uses only:
- Standard library (HTML, CSS, JavaScript)
- Existing MetaBridge API endpoints
- No external UI frameworks or CDN dependencies

All styling and interactivity is self-contained in the HTML.
