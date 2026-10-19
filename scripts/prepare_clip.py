#!/usr/bin/env python3
"""
Get one queued clip ready to post.

  python scripts/prepare_clip.py queue/<clip>.mp4 [--voiceover-text "Hook to speak"] [--dry-run]

- checks length (trims to max_clip_seconds) and file size (re-encodes if too big)
- optionally adds a Gemini voiceover hook over the first seconds (original audio ducked)
- moves the clip (and its .json sidecar) to media/YYYY-MM-DD/<slug>.mp4, where it stays
  forever so its public URL keeps working
- prints one JSON object with the public raw URL and clip details
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, ffmpeg_exe, load_config, probe, slugify  # noqa: E402


def run_ff(args):
    r = subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + r.stderr[-600:])


def add_voiceover(src, vo_wav, dst, duck):
    vo = probe(vo_wav)
    vo_len = (vo["duration"] or 3.0) + 0.25
    info = probe(src)
    if info["has_audio"]:
        fc = (f"[0:a]volume='if(lt(t,{vo_len:.2f}),{duck},1)':eval=frame[a0];"
              f"[1:a]adelay=150|150,volume=1.5[a1];"
              f"[a0][a1]amix=inputs=2:duration=first:normalize=0[a]")
    else:
        fc = "[1:a]adelay=150|150,apad[a]"
    run_ff(["-i", str(src), "-i", str(vo_wav), "-filter_complex", fc,
            "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
            "-shortest", "-movflags", "+faststart", str(dst)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("--date", help="folder date YYYY-MM-DD (default: today, local time)")
    ap.add_argument("--voiceover-text", help="spoken hook to generate with Gemini TTS")
    ap.add_argument("--vo-wav", help="use an existing .wav as the voiceover instead of Gemini")
    ap.add_argument("--dry-run", action="store_true", help="check only, don't move anything")
    a = ap.parse_args()

    cfg = load_config()
    src = Path(a.clip).resolve()
    if not src.exists():
        sys.exit(json.dumps({"ok": False, "error": f"not found: {a.clip}"}))
    side_path = src.with_suffix(".json")
    sidecar = json.loads(side_path.read_text(encoding="utf-8")) if side_path.exists() else {}

    warnings = []
    info = probe(src)
    if info["width"] and info["height"] and info["height"] <= info["width"]:
        warnings.append(f"not vertical ({info['width']}x{info['height']})")

    work = Path(tempfile.mkdtemp(prefix="clip_"))
    cur = src
    max_s = float(cfg.get("max_clip_seconds", 59))
    if info["duration"] and info["duration"] > max_s:
        out = work / "trim.mp4"
        run_ff(["-i", str(cur), "-t", f"{max_s}", "-c", "copy", "-movflags", "+faststart", str(out)])
        cur = out
        warnings.append(f"trimmed from {info['duration']:.1f}s to {max_s:.0f}s")

    voiced = False
    if a.voiceover_text or a.vo_wav:
        try:
            wav = a.vo_wav
            if not wav:
                from voiceover import synthesize
                wav = str(work / "vo.wav")
                synthesize(a.voiceover_text, wav)
            out = work / "voiced.mp4"
            add_voiceover(cur, wav, out, cfg.get("voiceover", {}).get("duck_volume", 0.22))
            cur, voiced = out, True
        except Exception as e:  # never block posting because of the voiceover
            warnings.append(f"voiceover skipped: {e}")

    max_mb = float(cfg.get("max_file_mb", 95))
    size_mb = cur.stat().st_size / 1e6
    if size_mb > max_mb:
        dur = probe(cur)["duration"] or max_s
        kbps = int(max_mb * 8000 / dur * 0.92) - 160
        out = work / "small.mp4"
        run_ff(["-i", str(cur), "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{kbps}k",
                "-maxrate", f"{kbps}k", "-bufsize", f"{2 * kbps}k", "-c:a", "aac", "-b:a", "160k",
                "-movflags", "+faststart", str(out)])
        cur = out
        warnings.append(f"re-encoded from {size_mb:.0f} MB")

    final = probe(cur)
    tz = ZoneInfo(cfg.get("timezone", "America/New_York"))
    day = a.date or datetime.now(tz).strftime("%Y-%m-%d")
    slug = slugify(sidecar.get("title") or src.stem)
    dest_dir = ROOT / "media" / day
    dest = dest_dir / f"{slug}.mp4"
    n = 2
    while dest.exists():
        dest = dest_dir / f"{slug}-{n}.mp4"
        n += 1
    rel = dest.relative_to(ROOT).as_posix()

    result = {
        "ok": True,
        "source": os.path.relpath(src, ROOT),
        "media_path": rel,
        "raw_url": cfg["raw_base_url"] + rel,
        "duration": round(final["duration"] or 0, 2),
        "size_mb": round(cur.stat().st_size / 1e6, 1),
        "width": final["width"], "height": final["height"],
        "voiceover": voiced,
        "campaign": sidecar.get("campaign"),
        "title": sidecar.get("title"),
        "source_title": sidecar.get("source_title"),
        "transcript": (sidecar.get("transcript") or "")[:1500],
        "warnings": warnings,
        "dry_run": a.dry_run,
    }
    if not a.dry_run:
        dest_dir.mkdir(parents=True, exist_ok=True)
        if cur == src:
            os.replace(src, dest)
        else:
            os.replace(cur, dest)
            src.unlink()
        sidecar.update({"media_path": rel, "raw_url": result["raw_url"], "voiceover": voiced,
                        "prepared_at": datetime.now(tz).isoformat(timespec="seconds")})
        dest.with_suffix(".json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
        if side_path.exists():
            side_path.unlink()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
