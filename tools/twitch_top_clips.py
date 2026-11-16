#!/usr/bin/env python3
"""
Runs on the USER'S COMPUTER (Twitch is blocked from the cloud). Downloads the most-viewed
Twitch clips of the last day for the given streamers, so viewers have already picked the best
moments, and splits each file into small parts that the Claude bridge can carry.

  python twitch_top_clips.py --channels xqc,ishowspeed --out ~/Videos/GTA/inbox
        [--per-channel 3] [--hours 24] [--min-views 50] [--skip used.json] [--max-new 1]

  --max-new 1  downloads at most one new clip per run, so each call stays short (the bridge
               gives each command about 45 seconds). Run it again until it prints "done": true.
  --new-batch  start a new day's batch (forget yesterday's manifest).

Nothing in --out is ever deleted (the bridge can't delete in your folders): parts are named
clip01.part000, clip02.part000 ... and simply overwritten by the next batch, so the folder stays
the same size. Downloads and re-encodes happen in the system temp folder.

Writes <out>/manifest.json: {"clips": [{"id", "channel", "title", "views", "url", "duration",
"created", "parts": [...], "md5"}], "candidates": [...]}. Needs Python 3.9+; installs yt-dlp
and imageio-ffmpeg with pip on first use.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PART = 4_000_000
RANGES = {24: "24hr", 168: "7d", 720: "30d"}


def ensure(mod, pkg):
    try:
        return __import__(mod)
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--user", pkg], check=False)
        try:
            return __import__(mod)
        except ImportError:
            subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                            "--break-system-packages", pkg], check=True)
            return __import__(mod)


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def list_clips(yt_dlp, channel, hours):
    rng = RANGES.get(hours, "24hr")
    url = f"https://www.twitch.tv/{channel}/clips?filter=clips&range={rng}"
    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "playlistend": 40}
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(url, download=False) or {}
    out = []
    for e in info.get("entries") or []:
        if not e or not e.get("id"):
            continue
        out.append({"id": str(e["id"]), "channel": channel, "title": e.get("title") or "",
                    "views": int(e.get("view_count") or 0), "url": e.get("url") or e.get("webpage_url"),
                    "duration": e.get("duration"), "created": e.get("timestamp")})
    return out


def shrink(src, dst):
    """720p, at most 60 fps, modest bitrate: small enough to stage, sharp enough to crop."""
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    r = subprocess.run([ff, "-y", "-v", "error", "-i", str(src), "-vf", "scale=-2:'min(720,ih)'",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-maxrate", "3000k",
                        "-bufsize", "6000k", "-r", "60", "-c:a", "aac", "-b:a", "128k",
                        "-movflags", "+faststart", str(dst)], capture_output=True, text=True)
    return r.returncode == 0 and Path(dst).exists()


def split(path, out_dir, slot):
    parts, i = [], 0
    with open(path, "rb") as f:
        while True:
            b = f.read(PART)
            if not b:
                break
            name = f"clip{slot:02d}.part{i:03d}"
            (out_dir / name).write_bytes(b)
            parts.append(name)
            i += 1
    return parts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channels", required=True, help="comma-separated Twitch logins")
    ap.add_argument("--out", required=True)
    ap.add_argument("--per-channel", type=int, default=3)
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--min-views", type=int, default=50)
    ap.add_argument("--max-seconds", type=float, default=62)
    ap.add_argument("--skip", help="JSON list/dict of clip ids already used")
    ap.add_argument("--max-new", type=int, default=99)
    ap.add_argument("--new-batch", action="store_true")
    a = ap.parse_args()

    yt_dlp = ensure("yt_dlp", "yt-dlp")
    ensure("imageio_ffmpeg", "imageio-ffmpeg")
    out = Path(os.path.expanduser(a.out))
    out.mkdir(parents=True, exist_ok=True)
    man_p = out / "manifest.json"
    man = json.loads(man_p.read_text()) if man_p.exists() and not a.new_batch else None
    if not man:
        man = {"clips": [], "candidates": [], "started": int(time.time())}
    skip = set()
    if a.skip and Path(os.path.expanduser(a.skip)).exists():
        s = json.loads(Path(os.path.expanduser(a.skip)).read_text())
        skip = set(s if isinstance(s, list) else s.keys())
    have = {c["id"] for c in man["clips"]}

    if not man.get("candidates"):  # list once per batch, then download one by one
        cands = []
        for ch in [c.strip().lower() for c in a.channels.split(",") if c.strip()]:
            try:
                clips = list_clips(yt_dlp, ch, a.hours)
            except Exception as ex:
                print(json.dumps({"channel": ch, "error": str(ex)[:300]}))
                continue
            clips = [c for c in clips if c["id"] not in skip and c["views"] >= a.min_views
                     and (c["duration"] or 0) <= a.max_seconds]
            clips.sort(key=lambda c: -c["views"])
            cands += clips[:a.per_channel]
        man["candidates"] = cands
        man_p.write_text(json.dumps(man, indent=1))

    new = 0
    for c in man["candidates"]:
        if c["id"] in have or new >= a.max_new:
            continue
        tmp = Path(tempfile.gettempdir()) / "rawcut-twitch"
        tmp.mkdir(exist_ok=True)
        raw = tmp / f"{c['id']}.raw.mp4"
        fin = tmp / f"{c['id']}.mp4"
        t0 = time.time()
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "outtmpl": str(raw),
                                   "format": "best[height<=1080]/best", "overwrites": True}) as y:
                y.download([c["url"]])
        except Exception as ex:
            c["error"] = str(ex)[:300]
            man_p.write_text(json.dumps(man, indent=1))
            continue
        if not shrink(raw, fin):
            fin = raw
        c["parts"] = split(fin, out, len(man["clips"]) + 1)
        c["md5"] = md5(fin)
        c["seconds_taken"] = round(time.time() - t0, 1)
        for p in (raw, tmp / f"{c['id']}.mp4"):
            try:
                p.unlink()
            except OSError:
                pass
        man["clips"].append(c)
        have.add(c["id"])
        new += 1
        man_p.write_text(json.dumps(man, indent=1))
    left = [c["id"] for c in man["candidates"] if c["id"] not in have and not c.get("error")]
    print(json.dumps({"done": not left, "downloaded": len(man["clips"]), "left": len(left),
                      "manifest": str(man_p)}))


if __name__ == "__main__":
    main()
