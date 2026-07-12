#!/bin/bash
# ClipStage — Middle Machine One-Time Setup
# Works from ANY directory — just run: bash INSTALL_ONCE.sh

# Find where THIS script lives (works even if you cd'd elsewhere)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CS_DIR="$HOME/Documents/ClipStage"
PLIST_DIR="$HOME/Library/LaunchAgents"

clear
echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║     ClipStage — Middle Machine Setup         ║"
echo "║     janam_edit_01  /  10.1.10.203            ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# ── Verify we have the plist files ──
for f in com.clipstage.typesense.plist com.clipstage.api.plist com.clipstage.mount.plist mount_volumes.sh; do
    if [ ! -f "$SCRIPT_DIR/$f" ]; then
        echo "❌ Missing file: $f"
        echo "   Make sure ALL files from MiddleMachine/ are in the same folder."
        exit 1
    fi
done

echo "→ Files found. Setting up..."
echo ""

mkdir -p "$PLIST_DIR"
mkdir -p "$CS_DIR"

# ── Copy files ──
cp "$SCRIPT_DIR/com.clipstage.typesense.plist" "$PLIST_DIR/"
cp "$SCRIPT_DIR/com.clipstage.api.plist"        "$PLIST_DIR/"
cp "$SCRIPT_DIR/com.clipstage.mount.plist"      "$PLIST_DIR/"
cp "$SCRIPT_DIR/mount_volumes.sh"               "$CS_DIR/"

chmod 644 "$PLIST_DIR"/com.clipstage.*.plist
chmod +x  "$CS_DIR/mount_volumes.sh"
echo "✅ Files copied"

# ── Fix python path — find where uvicorn actually is ──
PYTHON=$(which python3 2>/dev/null || echo "/usr/bin/python3")
UVICORN_PATH=$(python3 -m uvicorn --version 2>/dev/null && echo "ok" || echo "")

echo "   Python: $PYTHON"

# Update the API plist with the correct python path
sed -i '' "s|/usr/bin/python3|$PYTHON|g" "$PLIST_DIR/com.clipstage.api.plist"
echo "✅ Python path set to: $PYTHON"

# ── Unload any old versions first ──
echo ""
echo "→ Stopping any existing services..."
launchctl unload "$PLIST_DIR/com.clipstage.typesense.plist" 2>/dev/null && echo "  stopped: typesense" || true
launchctl unload "$PLIST_DIR/com.clipstage.api.plist"        2>/dev/null && echo "  stopped: api"       || true
launchctl unload "$PLIST_DIR/com.clipstage.mount.plist"      2>/dev/null && echo "  stopped: mount"     || true
sleep 1

# ── Also kill any manually started processes ──
pkill -f "typesense-server" 2>/dev/null || true
pkill -f "uvicorn api:app"  2>/dev/null || true
sleep 1

# ── Load services ──
echo ""
echo "→ Starting services..."

launchctl load "$PLIST_DIR/com.clipstage.typesense.plist"
echo "  ✅ Typesense service loaded"

launchctl load "$PLIST_DIR/com.clipstage.api.plist"
echo "  ✅ ClipStage API service loaded"

launchctl load "$PLIST_DIR/com.clipstage.mount.plist"
echo "  ✅ Volume mount service loaded"

# ── Wait for Typesense to be ready before API ──
echo ""
echo "→ Waiting for Typesense to start (up to 20 seconds)..."
for i in $(seq 1 20); do
    if curl -sf http://localhost:8108/health 2>/dev/null | grep -q "true"; then
        echo "  ✅ Typesense is up! (${i}s)"
        break
    fi
    printf "  ."
    sleep 1
done
echo ""

# ── Health checks ──
echo "→ Running health checks..."
sleep 3

if curl -sf http://localhost:8108/health 2>/dev/null | grep -q "true"; then
    echo "  ✅ Typesense  →  http://localhost:8108  OK"
else
    echo "  ⚠️  Typesense not responding — check: tail -f /tmp/typesense.log"
fi

if curl -sf http://localhost:8000/health 2>/dev/null | grep -q "ok"; then
    echo "  ✅ ClipStage   →  http://localhost:8000  OK"
else
    echo "  ⚠️  API not responding yet — check: tail -f /tmp/clipstage_api.log"
    echo "      (May need 10 more seconds on first boot)"
fi

# ── Mount volumes ──
echo ""
echo "→ Mounting NAS volumes..."
bash "$CS_DIR/mount_volumes.sh"
sleep 2

# Show what's mounted
echo "  Mounted volumes:"
ls /Volumes/ | sed 's/^/    /'

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║  ✅ Setup complete!                          ║"
echo "║                                              ║"
echo "║  Services now auto-start on every boot.     ║"
echo "║  Editors open: http://10.1.10.203:8000      ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
