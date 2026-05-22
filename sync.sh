#!/bin/bash
# Sub-20 dashboard sync — runs at 4am via launchd
set -euo pipefail

REPO="$HOME/sub20-dashboard"
LOG="$REPO/sync.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

log "=== Sub-20 sync started ==="

cd "$REPO"

# Optional: pull any manual edits first
git pull --ff-only origin main 2>/dev/null || true

# Generate the dashboard (reads local Garmin DB, calls Strava + Notion APIs)
log "Generating dashboard…"
python3 generate_dashboard.py >> "$LOG" 2>&1

# Commit and push if anything changed
if git diff --quiet index.html; then
    log "index.html unchanged — nothing to push."
else
    log "Committing updated dashboard…"
    git add index.html
    git commit -m "Daily sync $(date '+%Y-%m-%d')"
    git push origin main
    log "Pushed to GitHub."
fi

log "=== Done ==="
