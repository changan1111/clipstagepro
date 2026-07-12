import os
import subprocess
import hashlib

ARCHIVE = "/Volumes/EDIT/ARCHIVE"
THUMB_DIR = "/Users/janam_edit_01/typesense-data/thumbs"
os.makedirs(THUMB_DIR, exist_ok=True)

files = []
for root, dirs, filenames in os.walk(ARCHIVE):
    for f in filenames:
        if f.lower().endswith('.mxf'):
            files.append(os.path.join(root, f))

print(f"Found {len(files)} MXF files")
done = 0
skipped = 0

for i, path in enumerate(files):
    cache_key = hashlib.md5(path.encode()).hexdigest()
    thumb_path = f"{THUMB_DIR}/{cache_key}.jpg"

    if os.path.exists(thumb_path):
        done += 1
        continue

    try:
        subprocess.run([
            "ffmpeg", "-i", path,
            "-ss", "00:00:03",
            "-vframes", "1",
            "-vf", "scale=320:180",
            "-q:v", "5", "-y", thumb_path
        ], capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        skipped += 1
        open(thumb_path, 'w').close()

    if i % 100 == 0:
        print(f"Progress: {i}/{len(files)} | Done: {done} | Skipped: {skipped}")

print(f"Finished! Done: {done} Skipped: {skipped}")
