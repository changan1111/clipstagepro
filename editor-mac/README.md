# ClipStage — Editor Mac Setup

Editors don't run any servers. You only need to (1) open the app in a browser and (2) mount the staging share so you can drag clips into Final Cut Pro. `open_clipstage.command` does both in one double-click.

## One-time setup (each editor Mac)
```bash
chmod +x /path/to/open_clipstage.command
```
Put the file somewhere handy — Desktop or Dock.

## Daily use
1. Double-click **`open_clipstage.command`**. It mounts the staging share and opens http://10.1.10.203:8000.
2. Pick your name → search → select clips → **Stage**.
3. In FCP: **File → Import → Media**, go to `/Volumes/staging/YourName/`, select clips → **Leave Files in Place**. Clips link straight from the archive, nothing is copied.

If double-clicking opens a text editor instead of running: right-click → **Open With → Terminal** (macOS remembers it).

## What the one-click does
- Mounts `smb://10.1.10.203/staging` → appears at `/Volumes/staging`
- Opens the ClipStage web app in your default browser

Nothing is installed on the NAS or middle machine from here — this is pure convenience for the editor's own Mac.

## Troubleshooting
- **"Cannot reach server"** → check studio network; ask supervisor.
- **Staging won't mount / asks for login** → enter your NAS credentials once; macOS can remember them in Keychain.
- **Clips show ⚠️ in staging** → the archive volume may be offline on the middle machine; tell the admin.
- **Browser blank** → wait ~30s and refresh.

See `README_EDITORS.txt` for the printable version.
