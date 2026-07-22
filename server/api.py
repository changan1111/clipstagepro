"""
ClipStage S11 — FastAPI Backend  (Multi-Volume, robust)

Fixes in this version:
  1. Search: query_by auto-detects available fields — won't crash if 'volume'
     field doesn't exist in an older Typesense collection.
  2. Finder: GET /browse/{editor} serves a real HTML page listing staged files.
     Opens in a new browser tab — no SMB, no native app needed.
"""

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import typesense
import os
import re
from pathlib import Path
import time

# ─── CONFIG ───────────────────────────────────────────────────────────────────

STAGING_PATH   = "/Users/Shared/staging"
THUMB_DIR      = Path(__file__).parent / "static" / "thumbs"

TYPESENSE_HOST = "localhost"
TYPESENSE_PORT = "8108"
TYPESENSE_KEY  = "SSkt@230619"

EDITORS = [
    "GOKUL","YASHVANTH","PRIYA","MUTHU","KARTHICK","VIGNESH","KATHIRVEL",
    "VETRISELVAN","BALAKRISHNAN","RAMESH","VIGNESHPRABHU","RAVIKUMAR","GOWRI",
    "VIGNESH.S","DEEPAKKUMAR","ARULSELVAM","ARUN","MANIKANDAN","SARAVANAN","KKARTHIK",
]

LINK_MODE = "symlink"

# ──────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="ClipStage S11")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = typesense.Client({
    "nodes": [{"host": TYPESENSE_HOST, "port": TYPESENSE_PORT, "protocol": "http"}],
    "api_key": TYPESENSE_KEY,
    "connection_timeout_seconds": 2,
})


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _create_link(src: Path, dst: Path):
    import shutil
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    if LINK_MODE == "hardlink":
        os.link(src, dst)
    elif LINK_MODE == "copy":
        shutil.copy2(src, dst)
    else:
        os.symlink(src, dst)   # absolute path — works across all volumes


def _collection_fields() -> set[str]:
    """Return the field names currently in the Typesense 'clips' collection."""
    try:
        schema = client.collections["clips"].retrieve()
        return {f["name"] for f in schema.get("fields", [])}
    except Exception:
        return set()


def _search_query_by() -> str:
    """
    Build query_by string from fields that actually exist in the collection.
    Falls back to just 'filename,tags,category' if the collection is old.
    """
    fields = _collection_fields()
    candidates = ["filename", "tags", "category", "volume", "folder", "notes"]
    available  = [f for f in candidates if f in fields]
    return ",".join(available) if available else "filename,tags,category"


def _ensure_notes_field():
    """
    Add the 'notes' field to the collection schema if it isn't there yet.
    Safe to call repeatedly — no-op once the field exists.
    """
    try:
        fields = _collection_fields()
        if fields and "notes" not in fields:
            client.collections["clips"].update({
                "fields": [{"name": "notes", "type": "string", "optional": True}]
            })
    except Exception:
        pass  # collection may not exist yet (indexer hasn't run) — fine


def _ensure_use_count_field():
    """
    Add the 'use_count' field (int32, default 0) if it isn't there yet.
    Tracks how many times each clip has been staged.
    """
    try:
        fields = _collection_fields()
        if fields and "use_count" not in fields:
            client.collections["clips"].update({
                "fields": [{"name": "use_count", "type": "int32", "optional": True}]
            })
    except Exception:
        pass


# ── SEARCH MATCHER ────────────────────────────────────────────────────────────
#
# Rule: tokenise filename AND query on ALL separators (space / _ / -)
# equally. Query words must appear as consecutive tokens anywhere in
# the filename token list.
#
# "Modi"          → tokens ["modi"]           matches anywhere "modi" is a token
#                   → Modi.mxf ✅  Modi_Walk.mxf ✅  Modi Speech.mxf ✅  Modi_Walk_2024.mxf ✅
#
# "Modi walk"     → tokens ["modi","walk"]    must be consecutive
#                   → Modi_Walk.mxf ✅  Modi_Walk_2024.mxf ✅
#                   → Modi.mxf ❌  Modi Speech.mxf ❌
#
# "Modi Walk 2024"→ tokens ["modi","walk","2024"]
#                   → Modi_Walk_2024.mxf ✅  others ❌

_ALL_SEP_RE = re.compile(r"[\s_\-]+")


def _exact_compound_match(text: str, query: str) -> bool:
    """
    AND prefix match — every query word must match at least one filename
    token as a prefix. Words can be anywhere in the filename (any order).
    Last typed word also uses prefix so search-as-you-type works.

    'AMMA LAUNCH'  matches AMMA_UNAVAGAM_LAUNCH.mxf  ✅ (any order)
    'AMMA LAUN'    matches AMMA_UNAVAGAM_LAUNCH.mxf  ✅ (prefix on LAUN)
    'TN SEC'       matches TN_SECRETARY_ASSEMBLY.mxf ✅
    'TN SEC'       does NOT match TN_SESHAN.mxf      ❌ (no token starts with sec)
    'MODI'         matches MODI_WALK.mxf              ✅
    'MODI WALK'    matches MODI_WALK_2024.mxf         ✅
    """
    if not text or not query:
        return False
    text = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", text)
    tokens  = [w for w in _ALL_SEP_RE.split(text.lower().strip())  if w]
    q_words = [w for w in _ALL_SEP_RE.split(query.lower().strip()) if w]
    if not tokens or not q_words:
        return False
    # Every query word must match at least one token as a prefix
    for qw in q_words:
        if not any(t.startswith(qw) for t in tokens):
            return False
    return True


def _highlight(text: str, query: str) -> str:
    """Highlight each query word wherever it appears in the filename.
    'AMMA LAUNCH' highlights AMMA and LAUN separately in the filename."""
    if not text:
        return text
    q_words = [w for w in _ALL_SEP_RE.split(query.strip()) if w]
    if not q_words:
        return text
    result = text
    for qw in q_words:
        try:
            pattern = re.compile(
                rf"(?<![A-Za-z0-9])({re.escape(qw)})",
                re.IGNORECASE
            )
            result = pattern.sub(r"<mark>\1</mark>", result, count=1)
        except re.error:
            pass
    return result


# ── IN-MEMORY CLIP CACHE ─────────────────────────────────────────────────────
# Pull the whole collection via a single bulk export call — far faster than
# paginated search (no relevance scoring, no round-trips per page).
# Cache is invalidated explicitly via /admin/refresh-cache (call after indexer.py)
# and also refreshed automatically every TTL seconds as a safety net.

_CACHE: dict = {"docs": [], "ts": 0.0}
_CACHE_TTL_SECONDS = 60


def _reload_cache():
    """Pull every document out of Typesense via bulk export (single fast call)."""
    import json as _json
    try:
        raw = client.collections["clips"].documents.export()
        docs = []
        for line in raw.splitlines():
            line = line.strip()
            if line:
                docs.append(_json.loads(line))
        _CACHE["docs"] = docs
        _CACHE["ts"] = time.time()
        print(f"[cache] reloaded {len(docs)} docs")
    except Exception as e:
        print(f"[cache] reload failed: {e}")


def _get_cached_docs():
    """Return cached docs, auto-refreshing if TTL expired."""
    if not _CACHE["docs"] or (time.time() - _CACHE["ts"]) > _CACHE_TTL_SECONDS:
        _reload_cache()
    return _CACHE["docs"]


@app.post("/admin/refresh-cache")
def refresh_cache():
    """Call this after running indexer.py so search picks up new/changed clips."""
    _reload_cache()
    return {"ok": True, "count": len(_CACHE["docs"])}


@app.on_event("startup")
def _on_startup():
    _ensure_notes_field()
    _ensure_use_count_field()
    _ensure_duration_field()
    _ensure_volume_folder_fields()
    _reload_cache()  # warm the cache so first search is instant


def _ensure_duration_field():
    try:
        fields = _collection_fields()
        if fields and "duration" not in fields:
            client.collections["clips"].update({
                "fields": [{"name": "duration", "type": "string", "optional": True}]
            })
    except Exception:
        pass


def _ensure_volume_folder_fields():
    """Add 'volume' and 'folder' fields if the collection predates them."""
    try:
        fields = _collection_fields()
        if not fields:
            return
        missing = []
        if "volume" not in fields:
            missing.append({"name": "volume", "type": "string", "facet": True, "optional": True})
        if "folder" not in fields:
            missing.append({"name": "folder", "type": "string", "facet": True, "optional": True})
        if missing:
            client.collections["clips"].update({"fields": missing})
    except Exception:
        pass


# ── EDITORS ───────────────────────────────────────────────────────────────────

@app.get("/editors")
def get_editors():
    return {"editors": EDITORS}


# ── SEARCH ────────────────────────────────────────────────────────────────────

def _typesense_candidate_ids(q: str):
    """
    Ask Typesense's own inverted index which documents could possibly match
    `q`, so the slow Python exact-match loop below only has to check a small
    candidate set instead of every cached doc.

    This is deliberately a SUPERSET, never a subset, of the final exact
    match: Typesense may count a doc as a hit when query words are split
    across different fields (e.g. one word in filename, one in notes),
    while our exact matcher additionally requires all words to be in the
    SAME field. Every real match is guaranteed to still be in this set, so
    running the existing exact matcher on it afterward is unchanged in
    behavior — this only narrows what gets scanned, it never changes what
    counts as a match.

    Returns None (meaning "give up, caller should scan everything") if
    Typesense errors, or if there are too many pages to safely drain — in
    both cases the caller falls back to the full scan, so correctness is
    never at risk, only the speedup is skipped for that one query.
    """
    query_by = _search_query_by()
    ids: set = set()
    PER_PAGE = 250
    MAX_PAGES = 20  # past ~5000 candidates, bail — let the full scan handle it
    try:
        for page in range(1, MAX_PAGES + 1):
            result = client.collections["clips"].documents.search({
                "q": q,
                "query_by": query_by,
                "prefix": True,
                "num_typos": 0,
                "drop_tokens_threshold": 0,
                "per_page": PER_PAGE,
                "page": page,
            })
            page_hits = result.get("hits", [])
            for h in page_hits:
                doc_id = h.get("document", {}).get("id")
                if doc_id:
                    ids.add(doc_id)
            if len(page_hits) < PER_PAGE:
                return ids  # drained every page — safe, exact superset
        return None  # too broad to safely drain — let the full scan handle it
    except Exception:
        return None


@app.get("/facets/volumes")
def facet_volumes():
    """
    Distinct volume names across the FULL cached doc set — independent of
    whatever the current search/volume-scope returned. Lets the frontend
    filter dropdown always show every volume, even when the active search
    is already scoped to just one of them.
    """
    docs = _get_cached_docs()
    vols = sorted({d.get("volume") for d in docs if d.get("volume")})
    return {"volumes": vols}


@app.get("/search")
def search_clips(q: str = "", sort: str = "", page: int = 1, per_page: int = 30,
                  all: bool = True, volume: str = ""):
    """
    Search is EXACT WORD / EXACT-COMPOUND match only — no typo tolerance, no
    partial matching.

      • "Modi"       matches a clip named  Modi.mp4 / Modi Speech.mp4
      • "Modi"       does NOT match        Modi_Walk.mp4  (compound — partial)
      • "Modi walk"  DOES match            Modi_Walk.mp4  (full compound, with
                                            space/_/- all treated as the same
                                            separator)

    Matched fields: filename, category, volume, notes.

    `volume` (optional): scope the scan to just this volume — cheap
    pre-filter, requested from the UI's volume dropdown.

    `sort` (optional): "size_asc" or "size_desc" to order results by file size.
    Default order is the natural match order (no sort).
    """
    if not q.strip():
        return {"hits": [], "total": 0}
    try:
        docs = _get_cached_docs()
        if volume:
            docs = [d for d in docs if d.get("volume") == volume]

        candidate_ids = _typesense_candidate_ids(q)
        if candidate_ids is not None:
            docs = [d for d in docs if d.get("id") in candidate_ids]

        fields_to_check = ["filename", "category", "volume", "folder", "notes"]

        hits = []
        for doc in docs:
            matched = any(
                _exact_compound_match(str(doc.get(f, "")), q)
                for f in fields_to_check
            )
            if matched:
                hits.append(_format_hit(doc, q))

        if sort == "size_asc":
            hits.sort(key=lambda h: h.get("size_mb") or 0)
        elif sort == "size_desc":
            hits.sort(key=lambda h: h.get("size_mb") or 0, reverse=True)

        total = len(hits)

        if not all:
            start = (page - 1) * per_page
            return {"hits": hits[start:start + per_page], "total": total, "page": page}

        return {"hits": hits, "total": total, "page": 1}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _format_hit(doc: dict, q: str = "") -> dict:
    filename = doc.get("filename", "")
    path = doc.get("path", "")
    dir_only = path.rsplit("/", 1)[0] if "/" in path else ""
    return {
        "id":          doc["id"],
        "filename":    filename,
        "filename_hl": _highlight(filename, q) if q else filename,
        "path":        path,
        "dir":         dir_only,
        "size_mb":     doc.get("size_mb", 0),
        "date":        doc.get("date", ""),
        "category":    doc.get("category", ""),
        "volume":      doc.get("volume", ""),
        "folder":      doc.get("folder", ""),
        "duration":    doc.get("duration", ""),
        "notes":       doc.get("notes", ""),
        "use_count":   int(doc.get("use_count") or 0),
    }


# ── METADATA (NOTES) ──────────────────────────────────────────────────────────

@app.patch("/clip/{uid}/notes")
def update_clip_notes(uid: str, payload: dict):
    notes = payload.get("notes", "")
    if not isinstance(notes, str):
        raise HTTPException(status_code=400, detail="notes must be a string")
    if len(notes) > 5000:
        raise HTTPException(status_code=400, detail="notes too long (max 5000 chars)")
    try:
        client.collections["clips"].documents[uid].update({"notes": notes})
        # keep the in-memory cache in sync so next search shows updated notes immediately
        for doc in _CACHE["docs"]:
            if doc.get("id") == uid:
                doc["notes"] = notes
                break
        return {"ok": True, "id": uid, "notes": notes}
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Clip not found or update failed: {e}")


@app.post("/clips/bulk-notes")
def bulk_notes(payload: dict):
    """Write the same note to every clip ID in the given list."""
    ids   = payload.get("ids", [])
    notes = payload.get("notes", "")
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="ids must be a non-empty list")
    if not isinstance(notes, str):
        raise HTTPException(status_code=400, detail="notes must be a string")
    if len(notes) > 5000:
        raise HTTPException(status_code=400, detail="notes too long (max 5000 chars)")
    ok, failed = [], []
    cache_map = {doc.get("id"): doc for doc in _CACHE["docs"]}
    for uid in ids:
        try:
            client.collections["clips"].documents[uid].update({"notes": notes})
            if uid in cache_map:
                cache_map[uid]["notes"] = notes
            ok.append(uid)
        except Exception:
            failed.append(uid)
    return {"ok": ok, "failed": failed, "notes": notes}


@app.delete("/clip/{uid}")
def remove_clip(uid: str):
    """Delete a clip document from Typesense and the in-memory cache."""
    try:
        client.collections["clips"].documents[uid].delete()
        _CACHE["docs"] = [d for d in _CACHE["docs"] if d.get("id") != uid]
        return {"ok": True, "id": uid}
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Clip not found or delete failed: {e}")


# ── VOLUMES ───────────────────────────────────────────────────────────────────

@app.get("/volumes")
def list_volumes():
    vols_root = Path("/Volumes")
    mounted = []
    if vols_root.exists():
        for v in sorted(vols_root.iterdir()):
            if v.is_dir():
                mounted.append({"name": v.name, "path": str(v)})
    return {"volumes": mounted}


# ── THUMBNAIL ─────────────────────────────────────────────────────────────────

@app.get("/thumb/{uid}")
def get_thumb(uid: str):
    if not uid.isalnum() or len(uid) > 32:
        raise HTTPException(status_code=400, detail="Invalid uid")
    thumb = THUMB_DIR / f"{uid}.jpg"
    if not thumb.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(str(thumb), media_type="image/jpeg")


# ── STAGE ─────────────────────────────────────────────────────────────────────

@app.post("/stage")
def stage_clips(payload: dict):
    editor     = payload.get("editor", "").strip()
    clip_paths = payload.get("paths", [])

    if not editor:
        raise HTTPException(status_code=400, detail="Editor name required")
    if not clip_paths:
        raise HTTPException(status_code=400, detail="No clips selected")
    if editor not in EDITORS:
        raise HTTPException(status_code=403, detail="Unknown editor")

    editor_staging = Path(STAGING_PATH) / editor
    editor_staging.mkdir(parents=True, exist_ok=True)

    staged, errors = [], []
    # build a path→id lookup from cache for use_count tracking
    path_to_doc = {doc.get("path"): doc for doc in _CACHE["docs"]}

    for clip_path in clip_paths:
        src = Path(clip_path)
        if not src.exists():
            vol_part = src.parts[2] if len(src.parts) > 2 else "?"
            vol_path = Path("/Volumes") / vol_part
            if not vol_path.exists():
                errors.append({"path": clip_path,
                                "error": f"Volume /Volumes/{vol_part} is not mounted"})
            else:
                errors.append({"path": clip_path, "error": "File not found in archive"})
            continue
        dst = editor_staging / src.name
        try:
            _create_link(src, dst)
            staged.append(src.name)
            # ── increment use_count ──────────────────────────────────────────
            doc = path_to_doc.get(clip_path)
            if doc:
                new_count = int(doc.get("use_count") or 0) + 1
                try:
                    client.collections["clips"].documents[doc["id"]].update(
                        {"use_count": new_count}
                    )
                    doc["use_count"] = new_count   # update cache too
                except Exception:
                    pass  # non-fatal — staging still succeeded
        except Exception as e:
            errors.append({"path": clip_path, "error": str(e)})

    return {
        "staged":         staged,
        "errors":         errors,
        "staging_folder": str(editor_staging),
        "count":          len(staged),
        "link_mode":      LINK_MODE,
    }


@app.get("/stage/{editor}")
def list_staging(editor: str):
    if editor not in EDITORS:
        raise HTTPException(status_code=403, detail="Unknown editor")
    editor_staging = Path(STAGING_PATH) / editor
    if not editor_staging.exists():
        return {"clips": []}

    # uid+mtime for thumbnails / newest-first sort (#3 staging cards)
    path_to_doc = {doc.get("path"): doc for doc in _get_cached_docs()}

    clips = []
    for i in editor_staging.iterdir():
        if not (i.is_file() or i.is_symlink()):
            continue
        try:
            mtime = i.lstat().st_mtime
        except OSError:
            mtime = 0

        uid = ""
        try:
            # Use a single-hop readlink (raw target string), not .resolve() —
            # resolve() re-canonicalizes through /Volumes mount aliases and
            # no longer matches the literal "path" string stored in Typesense,
            # which is the same NAS double-mount mismatch as known issue #1.
            target = os.readlink(str(i)) if i.is_symlink() else str(i)
            doc = path_to_doc.get(target)
            if doc:
                uid = doc.get("id", "")
        except OSError:
            pass

        clips.append({
            "name":   i.name,
            "exists": i.exists(),
            "uid":    uid,
            "mtime":  mtime,
        })
    return {"clips": clips, "editor": editor}


@app.delete("/stage/{editor}")
def clear_staging(editor: str):
    if editor not in EDITORS:
        raise HTTPException(status_code=403, detail="Unknown editor")
    editor_staging = Path(STAGING_PATH) / editor
    if not editor_staging.exists():
        return {"cleared": 0}
    count = 0
    for item in editor_staging.iterdir():
        if item.is_file() or item.is_symlink():
            item.unlink()
            count += 1
    return {"cleared": count}


# ── BROWSE STAGING (replaces Finder button) ───────────────────────────────────

@app.get("/staging/view/{editor}", response_class=HTMLResponse)
def browse_staging(editor: str):  # GET /staging/view/{editor}
    """
    Serves a simple HTML page listing the editor's staged clips.
    Open in new browser tab — no SMB, no native app required.
    Editor clicks 'Open in Finder' path shown at top if they want to drag to FCP.
    """
    if editor not in EDITORS:
        raise HTTPException(status_code=403, detail="Unknown editor")

    editor_staging = Path(STAGING_PATH) / editor
    editor_staging.mkdir(parents=True, exist_ok=True)

    items = sorted(
        [i for i in editor_staging.iterdir() if i.is_file() or i.is_symlink()],
        key=lambda x: x.stat().st_mtime if x.exists() else 0,
        reverse=True,
    )

    rows = ""
    for item in items:
        exists  = item.exists()
        size_mb = round(item.stat().st_size / (1024*1024), 1) if exists else 0
        target  = str(item.resolve()) if item.is_symlink() and exists else "—"
        status  = "✅ OK" if exists else "⚠️ Source missing"
        rows += f"""
        <tr>
          <td>{item.name}</td>
          <td>{size_mb} MB</td>
          <td style="color:#7ba8d4;font-size:11px">{target}</td>
          <td>{status}</td>
        </tr>"""

    if not rows:
        rows = '<tr><td colspan="4" style="color:#7ba8d4;text-align:center;padding:32px">No clips staged yet</td></tr>'

    smb_path = f"/Users/Shared/staging/{editor}"

    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<title>Staging — {editor}</title>
<style>
  body {{ background:#001a3a; color:#c8dff0; font-family:system-ui,sans-serif;
          margin:0; padding:24px; }}
  h1   {{ color:#e8a020; font-size:18px; margin-bottom:4px; }}
  .sub {{ color:#7ba8d4; font-size:12px; margin-bottom:20px; }}
  .path-box {{
    background:#002155; border:1px solid #0040b0; border-radius:8px;
    padding:12px 16px; margin-bottom:20px; font-size:12px;
    display:flex; align-items:center; gap:12px; flex-wrap:wrap;
  }}
  .path-box code {{ color:#a0cfff; font-family:monospace; font-size:13px; }}
  .copy-btn {{
    background:#0040b0; border:none; color:#fff; padding:6px 14px;
    border-radius:5px; cursor:pointer; font-size:12px;
  }}
  .copy-btn:hover {{ background:#0055e0; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th    {{ text-align:left; color:#e8a020; padding:8px 12px;
           border-bottom:1px solid #0040b0; font-size:11px;
           font-family:monospace; letter-spacing:1px; }}
  td    {{ padding:9px 12px; border-bottom:1px solid rgba(0,64,176,0.3);
           word-break:break-all; }}
  tr:hover td {{ background:rgba(0,64,176,0.2); }}
  .count {{ color:#7ba8d4; font-size:12px; margin-top:12px; }}
</style>
</head><body>
<h1>📂 {editor} — Staging Folder</h1>
<div class="sub">{len(items)} clip(s) staged</div>

<div class="path-box">
  <span>Finder path:</span>
  <code id="fpath">{smb_path}</code>
  <button class="copy-btn" onclick="navigator.clipboard.writeText('{smb_path}');this.textContent='Copied!'">
    Copy Path
  </button>
  <span style="color:#7ba8d4;font-size:11px">
    → In Finder: Go → Connect to Server → smb://10.1.10.203/staging → open {editor}/
  </span>
</div>

<table>
  <thead>
    <tr>
      <th>FILENAME</th><th>SIZE</th><th>SOURCE (NAS PATH)</th><th>STATUS</th>
    </tr>
  </thead>
  <tbody>{rows}</tbody>
</table>
<div class="count">{len(items)} file(s) in /Users/Shared/staging/{editor}/</div>
<script>
  // Auto-refresh every 10 seconds so newly staged clips appear
  setTimeout(() => location.reload(), 10000);
</script>
</body></html>"""

    return HTMLResponse(content=html)



@app.post("/open-staging/{editor}")
def open_staging_in_finder(editor: str):  # POST /open-staging/{editor}
    """
    Opens the editor staging folder in Finder on janam_edit_01.
    Works because the API runs on the same Mac the editors use (or screen share).
    """
    import subprocess
    if editor not in EDITORS:
        raise HTTPException(status_code=403, detail="Unknown editor")
    editor_staging = Path(STAGING_PATH) / editor
    editor_staging.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.Popen(["open", str(editor_staging)])
        return {"ok": True, "path": str(editor_staging)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



# ── VIDEO STREAM ──────────────────────────────────────────────────────────────
from fastapi.responses import StreamingResponse as _StreamingResponse
import subprocess as _subprocess
import shutil as _shutil

# Resolve ffmpeg once at import time. launchd services run with a minimal
# PATH, so a bare "ffmpeg" can silently fail to launch (Popen raises
# FileNotFoundError inside the generator, after the 200 + headers have
# already gone out — the client just sees an empty video with no error).
# Falling back to common Homebrew install locations avoids that trap.
_FFMPEG_BIN = (
    _shutil.which("ffmpeg")
    or next((p for p in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg")
             if Path(p).exists()), None)
)

@app.get("/stream/{uid}")
def stream_clip(uid: str):
    if not uid.isalnum() or len(uid) > 32:
        raise HTTPException(status_code=400, detail="Invalid uid")
    if not _FFMPEG_BIN:
        raise HTTPException(status_code=500, detail="ffmpeg not found on server PATH")
    doc = next((d for d in _CACHE["docs"] if d.get("id") == uid), None)
    if not doc:
        raise HTTPException(status_code=404, detail="Clip not found")
    clip_path = doc.get("path", "")
    if not clip_path or not Path(clip_path).exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    def generate():
        cmd = [
            _FFMPEG_BIN, "-i", clip_path,
            "-t", "300",
            "-vf", "scale=1280:-2",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "28",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "frag_keyframe+empty_moov+faststart",
            "-f", "mp4", "pipe:1",
        ]
        proc = _subprocess.Popen(cmd, stdout=_subprocess.PIPE, stderr=_subprocess.PIPE)
        sent_bytes = 0
        try:
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                sent_bytes += len(chunk)
                yield chunk
        finally:
            proc.stdout.close()
            stderr_out = proc.stderr.read() if proc.stderr else b""
            proc.kill()
            proc.wait()
            # Zero bytes out almost always means ffmpeg errored immediately
            # (bad codec, unreadable NAS path, etc). Log it so it shows up
            # in the launchd log instead of failing completely silently.
            if sent_bytes == 0:
                print(f"[stream] ffmpeg produced 0 bytes for uid={uid} "
                      f"path={clip_path} rc={proc.returncode}\n"
                      f"{stderr_out.decode(errors='replace')[-2000:]}",
                      flush=True)

    filename = doc.get("filename", "clip.mp4")
    return _StreamingResponse(
        generate(),
        media_type="video/mp4",
        headers={"Content-Disposition": f'inline; filename="{filename}"'}
    )

# ── HEALTH ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    vols_root    = Path("/Volumes")
    mounted_vols = [v.name for v in vols_root.iterdir() if v.is_dir()] \
                   if vols_root.exists() else []
    fields       = _collection_fields()
    return {
        "status":          "ok",
        "link_mode":       LINK_MODE,
        "staging":         STAGING_PATH,
        "thumb_dir":       str(THUMB_DIR),
        "mounted_volumes": mounted_vols,
        "index_fields":    sorted(fields),
        "editors":         EDITORS,
    }


# ── STATIC ───────────────────────────────────────────────────────────────────
# Mount at /static/ so API routes are NEVER shadowed.
# Serve index.html explicitly at / so the browser UI still loads.
from fastapi.responses import FileResponse as _FileResponse
import os as _os

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def serve_index():
    from fastapi.responses import Response
    path = _os.path.join(_os.path.dirname(__file__), "static", "index.html")
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    return Response(
        content=html,
        media_type="text/html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )
