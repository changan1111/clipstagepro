"""
ClipStage S11 — Thumbnail Generator (fixed v2)
"""

import os
import subprocess
import hashlib
import time

SCAN_VOLUMES = [
    "EDIT",
    "EDIT2",
    "INGEST",
    "PLAYOUT",
    "DIGITAL",
    "SHARE FOLDER",
]

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mxf", ".avi", ".mkv", ".m4v", ".mpg", ".mpeg", ".r3d", ".braw"}

THUMB_DIR = "/Users/janam_edit_01/Documents/ClipStage/static/thumbs"

EXCLUDE_FOLDERS = {"@recycle","#recycle",".trashes",".temporaryitems","lost+found","$recycle.bin"}

os.makedirs(THUMB_DIR, exist_ok=True)

# Collect all files
files = []
for vol_name in SCAN_VOLUMES:
    vol_path = os.path.join("/Volumes", vol_name)
    if not os.path.exists(vol_path):
        print(f"WARNING: {vol_path} not mounted — skipping")
        continue
    print(f"Scanning {vol_path} ...")
    for root, dirs, filenames in os.walk(vol_path):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d.lower() not in EXCLUDE_FOLDERS]
        for f in filenames:
            if f.startswith("._"):
                continue
            ext = os.path.splitext(f)[1].lower()
            if ext in VIDEO_EXTENSIONS:
                files.append(os.path.join(root, f))

total = len(files)
print(f"\nFound {total:,} video files")
print(f"Thumbnails → {THUMB_DIR}\n")

done = 0
skipped = 0
generated = 0
errors = 0
start_time = time.time()

for i, path in enumerate(files):
    uid = hashlib.md5(path.encode()).hexdigest()[:16]
    thumb_path = os.path.join(THUMB_DIR, uid + ".jpg")

    if os.path.exists(thumb_path):
        done += 1
    else:
        try:
            subprocess.run([
                "ffmpeg", "-i", path,
                "-ss", "00:00:03",
                "-vframes", "1",
                "-vf", "scale=320:180",
                "-q:v", "5", "-y", thumb_path
            ], capture_output=True, timeout=30)
            generated += 1
        except subprocess.TimeoutExpired:
            skipped += 1
            open(thumb_path, 'w').close()
        except Exception as e:
            errors += 1

    if (i + 1) % 100 == 0 or (i + 1) == total:
        elapsed = time.time() - start_time
        pct = (i + 1) / total * 100
        rate = (i + 1) / elapsed if elapsed > 0 else 1
        eta = int((total - i - 1) / rate)
        print(f"[{pct:5.1f}%] {i+1:,}/{total:,} | new:{generated} cached:{done} skip:{skipped} ETA:{eta//60}m{eta%60}s")

elapsed = time.time() - start_time
print(f"\nDone in {int(elapsed//60)}m {int(elapsed%60)}s")
print(f"  Generated:     {generated:,}")
print(f"  Already had:   {done:,}")
print(f"  Timed out:     {skipped:,}")
print(f"  Errors:        {errors:,}")
