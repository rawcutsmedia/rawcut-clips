"""Shared helpers for the Rawcut posting scripts."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_config():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def load_campaigns():
    return json.loads((ROOT / "campaigns.json").read_text(encoding="utf-8")).get("campaigns", {})


def ffmpeg_exe():
    """System ffmpeg if present, otherwise the static build from the imageio-ffmpeg wheel."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "imageio-ffmpeg"], check=True)
        import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def probe(path):
    """Duration, size and audio presence, parsed from `ffmpeg -i` (works without ffprobe)."""
    r = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    err = r.stderr
    info = {"duration": None, "width": None, "height": None, "has_audio": " Audio:" in err}
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if m:
        h, mi, s = m.groups()
        info["duration"] = int(h) * 3600 + int(mi) * 60 + float(s)
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", err)
    if m:
        info["width"], info["height"] = int(m.group(1)), int(m.group(2))
    return info


def slugify(text, maxlen=50):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return s[:maxlen].strip("-") or "clip"
