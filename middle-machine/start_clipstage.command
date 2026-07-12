#!/bin/bash
# ClipStage — One-Click Launcher
# Starts all four pieces in order: mount → Typesense → indexer → API (uvicorn)
cd /Users/janam_edit_01/Documents/ClipStage

echo "→ Mounting NAS volumes..."
bash mount_volumes.sh
sleep 2

echo "→ Starting Typesense (if not already running)..."
if ! curl -sf http://localhost:8108/health | grep -q true; then
    /opt/homebrew/Cellar/typesense-server@27.1/27.1/bin/typesense-server \
        --data-dir=/Users/janam_edit_01/typesense-data \
        --api-key=SSkt@230619 --listen-port=8108 \
        > /tmp/typesense.log 2>&1 &
    for i in $(seq 1 20); do
        curl -sf http://localhost:8108/health | grep -q true && break
        sleep 1
    done
fi

echo "→ Running indexer (delta — new/changed clips only)..."
python3 indexer.py --prune

echo "→ Starting ClipStage API on port 8000..."
pkill -f "uvicorn api:app" 2>/dev/null
python3 -m uvicorn api:app --host 0.0.0.0 --port 8000
