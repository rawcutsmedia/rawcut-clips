#!/usr/bin/env python3
"""
Turn Google Drive *folder* sources into one source per video file.

  python scripts/expand_drive.py

For every entry in sources.json with "type": "drive_folder" and status "new", lists the
folder (and subfolders) with gdown, adds each video file as its own source
(https://drive.google.com/file/d/<id>/view), and marks the folder "expanded".
Re-run with --refresh to look for new files in already-expanded folders.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "sources.json"
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi")


def slug(text, n=40):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:n].strip("-") or "file"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="also re-list expanded folders")
    a = ap.parse_args()
    try:
        import gdown
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "gdown"], check=True)
        import gdown

    data = json.loads(SRC.read_text(encoding="utf-8"))
    sources = data["sources"]
    known = {s.get("drive_id") for s in sources if s.get("drive_id")}
    report = []
    for s in list(sources):
        if s.get("type") != "drive_folder":
            continue
        if s.get("status") != "new" and not (a.refresh and s.get("status") == "expanded"):
            continue
        try:
            files = gdown.download_folder(url=s["url"], skip_download=True, quiet=True,
                                          output=tempfile.mkdtemp(prefix="drive_"))
        except Exception as e:  # listing failed: record it, keep going
            s["status"] = "blocked"
            s["error"] = f"folder listing failed: {e}"[:300]
            report.append(f"{s['id']}: listing failed ({e})")
            continue
        added = 0
        for f in files or []:
            fid = getattr(f, "id", None)
            path = getattr(f, "path", "") or ""
            name = os.path.basename(path)
            if not fid or fid in known or not name.lower().endswith(VIDEO_EXT):
                continue
            sid = f"{s['campaign']}-drive-{slug(os.path.splitext(path)[0])}"
            base, n = sid, 2
            while any(x["id"] == sid for x in sources):
                sid, n = f"{base}-{n}", n + 1
            sources.append({"id": sid, "campaign": s["campaign"], "label": f"{s['label']}: {path}",
                            "url": f"https://drive.google.com/file/d/{fid}/view", "drive_id": fid,
                            "priority": s.get("priority", 0), "status": "new", "used_ranges": []})
            known.add(fid)
            added += 1
        s["status"] = "expanded"
        s["files_found"] = s.get("files_found", 0) + added
        report.append(f"{s['id']}: {added} new video file(s)")
    SRC.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    print("\n".join(report) or "no drive folders to expand")


if __name__ == "__main__":
    main()
