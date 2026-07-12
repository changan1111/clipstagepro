#!/bin/bash
# ClipStage — Mount NAS volumes on janam_edit_01
# Runs automatically at login via launchd
NAS="10.1.10.200"

mount_vol() {
    local name=$1
    local upper=$(echo "$name" | tr '[:lower:]' '[:upper:]')
    if [ ! -d "/Volumes/$upper" ]; then
        osascript -e "mount volume \"smb://$NAS/$name\"" 2>/dev/null || true
        sleep 1
    fi
}

mount_vol "edit"
mount_vol "edit2"
mount_vol "ingest"
mount_vol "playout"
mount_vol "digital"
