#!/bin/bash
# ClipStage — Editor One-Click
# For EDITOR Macs (not the middle machine).
# 1) Mounts the staging share so Finder/FCP can see staged clips
# 2) Opens the ClipStage web app in the default browser
#
# One-time setup on each editor Mac:
#   chmod +x /path/to/open_clipstage.command
# Then just double-click it any time.

STAGING_SHARE="smb://10.1.10.203/staging"
APP_URL="http://10.1.10.203:8000"

echo "→ Mounting staging share ($STAGING_SHARE)..."
if [ ! -d "/Volumes/staging" ]; then
    osascript -e "mount volume \"$STAGING_SHARE\"" 2>/dev/null || true
    sleep 2
fi

if [ -d "/Volumes/staging" ]; then
    echo "  ✅ staging mounted at /Volumes/staging"
else
    echo "  ⚠️  Could not mount automatically — if prompted, enter your NAS login."
fi

echo "→ Opening ClipStage ($APP_URL)..."
open "$APP_URL"

echo "Done. You can close this window."
