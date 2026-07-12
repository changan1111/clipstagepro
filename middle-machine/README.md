# ClipStage — Middle Machine Setup

The middle machine (`janam_edit_01` / `10.1.10.XXX`) runs everything. There are two ways to run it.

## The four pieces

1. **NAS mount** — `/Volumes/EDIT, EDIT2, INGEST, PLAYOUT, DIGITAL`
2. **Typesense** — search engine, port `8108` (data persists on disk)
3. **Indexer** — fills Typesense; **incremental**, runs on a nightly schedule
4. **API (uvicorn)** — the web app, port `8000`

Typesense + API + mount are always-on background services. The indexer is a scheduled job, not a service.

---

## Option A — Auto-start services (recommended)

Starts on every boot, restarts on crash, survives reboots. Set once, forget.

### One-time install
1. Put all `server/` files in `/Users/janam_edit_01/Documents/ClipStage/`, and all `middle-machine/` files alongside them.
2. Install Python deps:
   ```bash
   pip3 install -r requirements.txt
   ```
3. Run the installer (registers the 3 services with launchd):
   ```bash
   cd ~/Documents/ClipStage && bash INSTALL_ONCE.sh
   ```
   It copies the plists, fixes the Python path, loads Typesense + API + mount, and runs health checks. **Close the Terminal afterward — the services keep running** (launchd owns them now).
4. Add the nightly indexer (installer does NOT do this):
   ```bash
   crontab -e
   # add:
   0 2 * * * cd ~/Documents/ClipStage && python3 indexer.py --prune >> /tmp/clipstage_index.log 2>&1
   ```
5. First-time index so search has data immediately:
   ```bash
   python3 indexer.py
   ```

That's it. Every reboot brings Typesense, API, and mounts back automatically; the indexer runs itself nightly.

### Manage the services
```bash
# stop all
launchctl unload ~/Library/LaunchAgents/com.clipstage.mount.plist
launchctl unload ~/Library/LaunchAgents/com.clipstage.api.plist
launchctl unload ~/Library/LaunchAgents/com.clipstage.typesense.plist

# restart API after a code change
launchctl unload ~/Library/LaunchAgents/com.clipstage.api.plist
launchctl load   ~/Library/LaunchAgents/com.clipstage.api.plist
```

If your Python isn't at `/usr/bin/python3`, the installer auto-detects and patches the API plist. To change it manually, edit the `<string>...python3</string>` line in `com.clipstage.api.plist` and reload.

---

## Option B — Double-click launcher (manual)

`start_clipstage.command` brings up all four pieces in one window: mount → Typesense → index → API.

One-time:
```bash
chmod +x /Users/janam_edit_01/Documents/ClipStage/start_clipstage.command
```
Then double-click it in Finder any time. The API runs while that Terminal window is open; closing it stops the API. Typesense stays up (started detached).

Use this for occasional/troubleshooting starts only. **Do not run it while the Option A services are loaded** — two Typesense/API processes would collide on ports 8108/8000. It also does not survive reboot.

---

## Verify

```bash
ls /Volumes/                        # EDIT, EDIT2, INGEST, PLAYOUT, DIGITAL
curl http://localhost:8108/health   # Typesense → {"ok":true}
curl http://localhost:8000/health   # API → ok
```
Or open http://localhost:8000 and search.

## Indexer cheat-sheet

| Command | What |
|---|---|
| `python3 indexer.py` | Delta run — new/changed clips only (schedule this) |
| `python3 indexer.py --prune` | Delta + remove entries for deleted files |
| `python3 indexer.py --force` | Wipe index, rebuild from scratch |
| `python3 indexer.py --dry` | Count only, writes nothing |

New clips appear in search right after indexing — the indexer pings the API's cache refresh automatically. No API restart needed. No re-index needed after a reboot.

## Logs

| What | Where |
|---|---|
| API | `/tmp/clipstage_api.log`, `/tmp/clipstage_error.log` |
| Typesense | `/tmp/typesense.log` |
| Mounts | `/tmp/clipstage_mount.log` |
| Nightly index | `/tmp/clipstage_index.log` |

## Troubleshooting

- **Search empty** → Typesense down or nothing indexed. Check `/tmp/typesense.log`, run `python3 indexer.py`.
- **New clips missing** → run the indexer; confirm cron with `crontab -l`.
- **"Volume not mounted"** → `bash mount_volumes.sh`; check `ls /Volumes/` and Privacy & Security → Network Volumes on new Macs.
- **API won't start** → wrong Python path in the plist; run `which python3`, match it, reload.
- **Everything gone after reboot** → services weren't installed; re-run `bash INSTALL_ONCE.sh`.
