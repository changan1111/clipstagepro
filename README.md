# ClipStage

Newsroom NAS archive **search & staging** tool for TamilJanam. Editors search the entire media archive from a browser, stage the clips they want, and drag them straight into Final Cut Pro — no manual folder digging, no copying gigabytes around.

- **Backend:** FastAPI (`uvicorn`) + [Typesense](https://typesense.org) search engine
- **Indexer:** Python script that walks the NAS volumes and feeds Typesense
- **Frontend:** single-page browser UI served by the API
- **Staging:** symlinks clips into a per-editor folder under `/Users/Shared/staging/` — no file copying

Editors reach the app at **http://10.1.10.XXX:8000**.

---

## How it works (architecture)

```
   ┌─────────────┐         ┌──────────────────────────── MIDDLE MACHINE ─────────────────────────────┐
   │   NAS        │  SMB    │  janam_edit_01 / 10.1.10.XXX                                             │
   │ 10.1.10.XXX  │◄───────►│                                                                          │
   │ EDIT, EDIT2, │ mounts  │   mount_volumes.sh ──► /Volumes/EDIT, EDIT2, INGEST, PLAYOUT, DIGITAL    │
   │ INGEST, ...  │         │                                                                          │
   └─────────────┘         │   indexer.py ──scans──► Typesense (:8108) ──serves──► api.py (:8000)     │
                           │                                                          │               │
                           │   stage ──► symlinks into /Users/Shared/staging/<editor>/               │
                           └──────────────────────────────────┬───────────────────────────────────────┘
                                                              │ HTTP :8000  +  SMB staging share
                                    ┌─────────────────────────┴───────────────────────────┐
                                    │  EDITOR MACS                                          │
                                    │  browser → http://10.1.10.XXX:8000  (search & stage)  │
                                    │  Finder  → smb://10.1.10.XXX/staging  (drag to FCP)   │
                                    └───────────────────────────────────────────────────────┘
```

Only the **middle machine** mounts the NAS. Editors just open a URL to search/stage, and mount the `staging` share to drag results into FCP.

### The four moving pieces (middle machine)

| # | Piece | Role | Runs how | Port |
|---|---|---|---|---|
| 1 | NAS mount | See the media | launchd, at login + every 5 min | — |
| 2 | Typesense | Search index (data on disk, survives reboot) | launchd service (auto-restart) | 8108 |
| 3 | Indexer | Fills Typesense with new/changed clips | **cron, nightly** (not a service) | — |
| 4 | API (uvicorn) | Web app | launchd service (auto-restart) | 8000 |

**Indexer is incremental** — each run only touches new/changed files (unchanged clips reuse stored data and skip `ffprobe`). It auto-refreshes the API cache when done, so new clips are searchable immediately without restarting anything. A machine reboot needs **no** re-index; Typesense keeps its data on disk.

---

## Repository layout

```
ClipStage/
├── README.md                     ← you are here
├── .gitignore
├── server/                       ← the application
│   ├── api.py                    FastAPI backend (search, stage, browse)
│   ├── indexer.py                NAS → Typesense indexer (incremental)
│   ├── generate_thumbs.py        optional thumbnail generator
│   ├── requirements.txt          Python deps
│   ├── config.example.env        copy to .env, set TYPESENSE_KEY
│   └── static/index.html         single-page browser UI
├── middle-machine/               ← run ClipStage on the middle Mac
│   ├── INSTALL_ONCE.sh           one-time: installs the 3 auto-start services
│   ├── com.clipstage.typesense.plist
│   ├── com.clipstage.api.plist
│   ├── com.clipstage.mount.plist
│   ├── mount_volumes.sh          mounts the NAS shares
│   ├── start_clipstage.command   double-click manual launcher (all 4 pieces)
│   └── README.md                 middle-machine setup guide
└── editor-mac/                   ← per-editor convenience
    ├── open_clipstage.command    double-click: mount staging + open app
    ├── README_EDITORS.txt        editor daily-use guide
    └── README.md                 editor setup guide
```

---

## Quick start (middle machine)

Full detail in [`middle-machine/README.md`](middle-machine/README.md).

**Prerequisites:** Typesense server installed (`brew install typesense/tap/typesense-server@27.1`), Python 3 with deps:
```bash
cd server && pip3 install -r requirements.txt
```

**Recommended — auto-start services (survives reboots & crashes):**
```bash
# Put server/ files in ~/Documents/ClipStage/ and middle-machine/ files alongside, then:
cd ~/Documents/ClipStage && bash INSTALL_ONCE.sh
```
This installs Typesense + API + mount as launchd services that start on every boot and restart on crash. Then add the nightly indexer:
```bash
crontab -e
# add:
0 2 * * * cd ~/Documents/ClipStage && python3 indexer.py --prune >> /tmp/clipstage_index.log 2>&1
```
Run the first index by hand so search has data immediately:
```bash
python3 indexer.py
```

**Alternative — manual one-click:** double-click `start_clipstage.command` (mounts → Typesense → index → API in one window). Good for troubleshooting; does **not** survive reboot. Don't run this at the same time as the services — they collide on ports 8108/8000.

---

## Editor setup

Editors need nothing installed to search — just the URL. To drag staged clips into FCP they mount the staging share. `editor-mac/open_clipstage.command` does both in one double-click. See [`editor-mac/README.md`](editor-mac/README.md).

---

## Configuration notes

- **Typesense API key** is read from the `TYPESENSE_KEY` env var (falls back to the shipped default). Set it in a local `.env` (copy `server/config.example.env`) and keep `.env` out of git.
- **Editors list** is hardcoded in `server/api.py` (`EDITORS = [...]`). Add/remove names there and restart the API.
- **Volumes to index** are in `server/indexer.py` (`SCAN_VOLUMES`) and mounted by `middle-machine/mount_volumes.sh`. Keep the two lists in sync.
- **Staging mode** is `LINK_MODE` in `api.py` — `symlink` (default, no copy), `hardlink`, or `copy`.
- **NAS IP** `10.1.10.XXX`, **middle machine** `10.1.10.XX` — change in `mount_volumes.sh`, the plists, and `open_clipstage.command` if your network differs.

---

## Health checks

```bash
ls /Volumes/                        # EDIT, EDIT2, INGEST, PLAYOUT, DIGITAL
curl http://localhost:8108/health   # Typesense → {"ok":true}
curl http://localhost:8000/health   # API → ok
```

## Logs

| What | Where |
|---|---|
| API | `/tmp/clipstage_api.log` (and `clipstage_error.log`) |
| Typesense | `/tmp/typesense.log` |
| Mounts | `/tmp/clipstage_mount.log` |
| Nightly index | `/tmp/clipstage_index.log` |
