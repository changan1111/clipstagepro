"""
ClipStage S11 — Multi-Volume Archive Indexer

Scans ALL configured NAS volumes (not just EDIT/ARCHIVE) and records:
  volume  → the NAS share name          e.g.  EDIT2
  folder  → top-level folder in volume  e.g.  KARTHIK_DND
so the browser UI can filter by volume + folder.

Fill in before running:
  SCAN_VOLUMES    → which /Volumes/* shares to index
  EXCLUDE_FOLDERS → folder names skipped at ANY depth
  TYPESENSE_KEY   → same as api.py and 1_start_typesense.bat

Usage:
  python indexer.py --dry      count files only, nothing written
  python indexer.py            full index (first time or delta update)
  python indexer.py --force    wipe index and reindex from scratch
  python indexer.py --prune    also delete index entries whose file is gone
                               (only checks volumes that are mounted)

Delta runs are fast: files whose size+date are unchanged reuse the
duration already stored in Typesense — ffprobe is skipped for them.
"""

import os, sys, hashlib, time, requests, subprocess, json
from pathlib import Path
import typesense

# ─── CONFIG — REPLACE THESE ───────────────────────────────────────────────────

# Volumes to scan (names under /Volumes). Whole volume is walked recursively.
SCAN_VOLUMES = [
    "EDIT",
    "EDIT2",
    "INGEST",
    "PLAYOUT",
    "DIGITAL",
    "SHARE FOLDER",
]

# Folder names skipped at ANY depth (case-insensitive exact name match).
EXCLUDE_FOLDERS = {
    "@recycle",
    "#recycle",
    "@recently-snapshot",
    ".trashes",
    ".temporaryitems",
    "lost+found",
    "$recycle.bin",
}

TYPESENSE_KEY  = os.environ.get("TYPESENSE_KEY", "SSkt@230619")
TYPESENSE_HOST = "localhost"
TYPESENSE_PORT = "8108"
API_PORT       = "8000"   # ClipStage FastAPI port — used for cache refresh

# ──────────────────────────────────────────────────────────────────────────────

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mxf", ".avi", ".mkv", ".m4v", ".mpg", ".mpeg", ".r3d", ".braw"}
BATCH_SIZE = 500

client = typesense.Client({
    "nodes": [{"host": TYPESENSE_HOST, "port": TYPESENSE_PORT, "protocol": "http"}],
    "api_key": TYPESENSE_KEY,
    "connection_timeout_seconds": 5
})

SCHEMA = {
    "name": "clips",
    "fields": [
        {"name": "id",        "type": "string"},
        {"name": "filename",  "type": "string"},
        {"name": "path",      "type": "string", "index": False},
        {"name": "size_mb",   "type": "float"},
        {"name": "date",      "type": "string"},
        {"name": "category",  "type": "string", "facet": True},
        {"name": "volume",    "type": "string", "facet": True, "optional": True},
        {"name": "folder",    "type": "string", "facet": True, "optional": True},
        {"name": "ext",       "type": "string", "facet": True},
        {"name": "tags",      "type": "string"},
        {"name": "use_count", "type": "int32",  "optional": True},
        {"name": "duration",  "type": "string", "optional": True},
    ],
    "default_sorting_field": "size_mb"
}


def setup_collection(force=False):
    try:
        client.collections["clips"].retrieve()
        if force:
            client.collections["clips"].delete()
            client.collections.create(SCHEMA)
            print("Collection wiped and recreated.")
        else:
            print("Collection exists — emplacing new/changed files (notes + use_count preserved).")
            _ensure_new_fields()
    except Exception:
        client.collections.create(SCHEMA)
        print("Collection created.")


def _ensure_new_fields():
    """Add volume/folder fields to an existing (older) collection. No-op once present."""
    try:
        schema = client.collections["clips"].retrieve()
        existing = {f["name"] for f in schema.get("fields", [])}
        missing = []
        if "volume" not in existing:
            missing.append({"name": "volume", "type": "string", "facet": True, "optional": True})
        if "folder" not in existing:
            missing.append({"name": "folder", "type": "string", "facet": True, "optional": True})
        if missing:
            client.collections["clips"].update({"fields": missing})
            print(f"Schema updated — added: {', '.join(f['name'] for f in missing)}")
    except Exception as e:
        print(f"Schema check skipped: {e}")


def _load_existing_docs() -> dict:
    """
    Export existing docs → {id: doc}. Used to skip ffprobe on unchanged
    files (big speedup on delta runs) and for --prune.
    """
    try:
        raw = client.collections["clips"].documents.export()
        out = {}
        for line in raw.splitlines():
            line = line.strip()
            if line:
                d = json.loads(line)
                out[d["id"]] = d
        return out
    except Exception:
        return {}


def get_duration(fpath: Path) -> str:
    """
    Extract clip duration using ffprobe. Returns formatted string like '00:02:34'
    or '' if ffprobe is not available or fails.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                str(fpath)
            ],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return ""
        data = json.loads(result.stdout)
        for stream in data.get("streams", []):
            raw = stream.get("duration", "")
            if raw:
                secs = float(raw)
                h = int(secs // 3600)
                m = int((secs % 3600) // 60)
                s = int(secs % 60)
                return f"{h:02d}:{m:02d}:{s:02d}"
    except Exception:
        pass
    return ""


def _volume_and_folder(fpath: Path, vol_root: Path) -> tuple[str, str]:
    """
    /Volumes/EDIT2/KARTHIK_DND/x.mxf → ("EDIT2", "KARTHIK_DND")
    /Volumes/EDIT2/x.mxf             → ("EDIT2", "(root)")
    """
    volume = vol_root.name
    try:
        rel = fpath.relative_to(vol_root)
        folder = rel.parts[0] if len(rel.parts) > 1 else "(root)"
    except ValueError:
        folder = "(root)"
    return volume, folder


def index_volume(vol_root: Path, existing: dict, dry: bool, counters: dict, batch: list):
    """Walk one volume, appending docs to the shared batch."""
    print(f"\nScanning: {vol_root}")
    vol_start, vol_total = time.time(), 0

    for root, dirs, files in os.walk(vol_root):
        # prune hidden + excluded folders at any depth
        dirs[:] = [
            d for d in dirs
            if not d.startswith('.') and d.lower() not in EXCLUDE_FOLDERS
        ]
        for fname in files:
            if fname.startswith("._"):        # macOS AppleDouble junk on SMB
                continue
            ext = Path(fname).suffix.lower()
            if ext not in VIDEO_EXTENSIONS:
                continue
            fpath = Path(root) / fname
            try:
                stat     = fpath.stat()
                size_mb  = round(stat.st_size / (1024 * 1024), 2)
                mod_date = time.strftime("%Y-%m-%d", time.localtime(stat.st_mtime))
                uid      = hashlib.md5(str(fpath).encode()).hexdigest()[:16]
                volume, folder = _volume_and_folder(fpath, vol_root)

                # ── ffprobe skip: unchanged file → reuse stored duration ──
                prev = existing.get(uid)
                if (prev and prev.get("duration")
                        and prev.get("size_mb") == size_mb
                        and prev.get("date") == mod_date):
                    duration = prev["duration"]
                    counters["reused"] += 1
                else:
                    duration = "" if dry else get_duration(fpath)

                doc = {
                    "id":       uid,
                    "filename": fname,
                    "path":     str(fpath),
                    "size_mb":  size_mb,
                    "date":     mod_date,
                    "category": folder,          # kept for backward compat
                    "volume":   volume,
                    "folder":   folder,
                    "ext":      ext.lstrip('.').upper(),
                    "tags":     f"{fname} {volume} {folder} {ext}",
                    "duration": duration,
                }
                batch.append(doc)
                counters["total"] += 1
                vol_total += 1
                if not dry and len(batch) >= BATCH_SIZE:
                    client.collections["clips"].documents.import_(batch, {"action": "emplace"})
                    batch.clear()
                if counters["total"] % 1000 == 0:
                    elapsed = time.time() - counters["start"]
                    print(f"  {counters['total']:,} files indexed... ({elapsed:.0f}s)")
            except Exception as e:
                counters["errors"] += 1
                print(f"  SKIP {fname}: {e}")

    print(f"  {vol_root.name}: {vol_total:,} clips ({time.time()-vol_start:.0f}s)")


def prune_missing(existing: dict, scanned_volumes: set, dry: bool):
    """Delete index entries whose file no longer exists — only for volumes
    that were actually scanned this run (never touches unmounted volumes)."""
    removed = 0
    for uid, doc in existing.items():
        vol = doc.get("volume") or ""
        if vol not in scanned_volumes:
            # legacy docs without a volume field: derive from path
            p = doc.get("path", "")
            parts = Path(p).parts
            vol = parts[2] if len(parts) > 2 else ""
            if vol not in scanned_volumes:
                continue
        path = doc.get("path", "")
        if path and not Path(path).exists():
            removed += 1
            if not dry:
                try:
                    client.collections["clips"].documents[uid].delete()
                except Exception:
                    pass
    print(f"Pruned {removed:,} stale entr{'y' if removed==1 else 'ies'}"
          + (" (dry run — nothing deleted)" if dry else ""))


def index_archive(dry=False, prune=False):
    roots, skipped = [], []
    for name in SCAN_VOLUMES:
        p = Path("/Volumes") / name
        (roots if p.exists() else skipped).append(p)
    if skipped:
        for p in skipped:
            print(f"WARNING: volume not mounted, skipping: {p}")
    if not roots:
        print("\nERROR: none of the SCAN_VOLUMES are mounted. Check /Volumes.")
        sys.exit(1)

    existing = {} if dry else _load_existing_docs()
    if existing:
        print(f"Loaded {len(existing):,} existing docs (durations will be reused for unchanged files).")

    counters = {"total": 0, "errors": 0, "reused": 0, "start": time.time()}
    batch: list = []

    for vol_root in roots:
        index_volume(vol_root, existing, dry, counters, batch)

    if not dry and batch:
        client.collections["clips"].documents.import_(batch, {"action": "emplace"})

    elapsed = time.time() - counters["start"]
    label = "DRY RUN — " if dry else ""
    print(f"\n{label}Done: {counters['total']:,} clips in {elapsed:.1f}s  "
          f"({counters['errors']} errors, {counters['reused']:,} durations reused)")

    if prune:
        prune_missing(existing, {r.name for r in roots}, dry)

    if dry:
        print("Nothing was written. Run without --dry to index.")
        return

    # ── Auto cache refresh ──────────────────────────────────────────────────
    # Tell the running ClipStage API to reload its in-memory cache so new/
    # changed clips are immediately searchable without restarting uvicorn.
    try:
        r = requests.post(
            f"http://localhost:{API_PORT}/admin/refresh-cache",
            timeout=5
        )
        if r.ok:
            data = r.json()
            print(f"Cache refreshed — {data.get('count', '?')} docs now in memory.")
        else:
            print(f"Cache refresh returned HTTP {r.status_code} — restart API if needed.")
    except requests.exceptions.ConnectionError:
        print("API not running — cache will refresh on next search (TTL auto-reload).")
    except Exception as e:
        print(f"Cache refresh skipped: {e}")


if __name__ == "__main__":
    dry   = "--dry"   in sys.argv
    force = "--force" in sys.argv
    prune = "--prune" in sys.argv
    if not dry:
        setup_collection(force=force)
    index_archive(dry, prune)
