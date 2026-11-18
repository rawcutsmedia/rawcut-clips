#!/usr/bin/env python3
"""
Runs on the user's Windows laptop every evening (Windows Task Scheduler, "Rawcut GTA clips"),
because Twitch is blocked from Claude's cloud workspace and from the Claude app's shell.

It fetches the current campaign list and the already-used clip ids from the public repo,
then downloads the most-viewed Twitch clips of the last day for every active GTA streamer
campaign into <this folder>/inbox (split into small parts), using twitch_top_clips.py, which it
also refreshes from the repo each run. The evening Claude job then picks the files up.

Writes <this folder>/last_run.log. Never deletes anything outside its own temp folder.
"""
import datetime
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

RAW = "https://raw.githubusercontent.com/rawcutsmedia/rawcut-clips/main/"
HERE = Path(__file__).resolve().parent
LOG = HERE / "last_run.log"


def log(msg):
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def fetch(path, dest=None):
    with urllib.request.urlopen(RAW + path, timeout=60) as r:
        data = r.read()
    if dest:
        Path(dest).write_bytes(data)
    return data


def main():
    LOG.write_text("", encoding="utf-8")
    try:
        camps = json.loads(fetch("campaigns.json"))["campaigns"]
        cfg = json.loads(fetch("config.json")).get("gta", {})
        fetch("streams/used.json", HERE / "used.json")
        fetch("tools/twitch_top_clips.py", HERE / "twitch_top_clips.py")
    except Exception as ex:
        log(f"ERROR fetching from GitHub: {ex}")
        return 1
    chans = sorted({c["twitch"].lower() for c in camps.values()
                    if c.get("kind") == "stream" and c.get("active") and c.get("twitch")})
    if not chans:
        log("no active GTA campaigns; nothing to download")
        return 0
    log(f"channels: {', '.join(chans)}")
    py = sys.executable.replace("pythonw.exe", "python.exe")
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW: no console popping up
    # keep the Twitch downloader current (Twitch changes often); quick when already up to date
    subprocess.run([py, "-m", "pip", "install", "-q", "-U", "--user", "yt-dlp", "imageio-ffmpeg"],
                   capture_output=True, creationflags=flags)
    cmd = [py, str(HERE / "twitch_top_clips.py"),
           "--channels", ",".join(chans), "--out", str(HERE / "inbox"),
           "--per-channel", str(cfg.get("clips_per_channel_per_streamer", 6)),
           "--min-views", str(cfg.get("min_source_views", 20)),
           "--skip", str(HERE / "used.json"), "--new-batch"]
    r = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags)
    for line in (r.stdout + r.stderr).splitlines()[-30:]:
        log(line)
    log(f"exit {r.returncode}")
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
