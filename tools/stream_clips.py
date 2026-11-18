#!/usr/bin/env python3
"""
Stream clips: turn a streamer's Twitch clip into a vertical, captioned clip ready for the queue.

The clips are downloaded on the user's computer by tools/twitch_top_clips.py (Twitch is blocked
from the cloud), staged into this session, then edited here. See "GTA clip job" in CLAUDE.md.

  python tools/stream_clips.py assemble <manifest.json>       rebuild staged parts, check md5
  python tools/stream_clips.py transcript <video>             what is said (cached), to write the hook
  python tools/stream_clips.py frame <video> [--at 0.4]       PNG to look at (find the facecam box)
  python tools/stream_clips.py make <video> --campaign KEY --hook "..."
        [--facecam X,Y,W,H | --layout blur] [--start S --end E]
        [--clip-id ID --clip-title "..." --clip-url URL --views N --streamer NAME] [--out queue]
  python tools/stream_clips.py used                           clip ids already made (skip them)

Layout: with a facecam box (campaign "facecam" or --facecam), the webcam goes on top and the
gameplay below, captions on the seam. Without one, the whole frame sits on a blurred copy.
Paid campaigns get an on-screen "#AD" badge for the first seconds (disclosure at the start).
Captions come from an offline speech model (NVIDIA Parakeet via sherpa-onnx, from GitHub).
"""
import argparse
import datetime
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("CLIPKIT_WORK", str(Path.home() / "rawcut-work"))
import clipkit as ck  # noqa: E402

WORK = Path(os.environ["CLIPKIT_WORK"])
SW = WORK / "stream"
USED = ROOT / "streams" / "used.json"
UPLOADS = Path("/mnt/user-data/uploads")
FONT_DIR = ROOT / "fonts"
ASR_NAME = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8"
ASR_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{ASR_NAME}.tar.bz2"
W, H = ck.OUT_W, ck.OUT_H  # 1080x1920


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def load_json(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def campaign(key):
    camps = load_json(ROOT / "campaigns.json", {}).get("campaigns", {})
    if key not in camps:
        die(f"campaign {key!r} is not in campaigns.json")
    return camps[key]


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ------------------------------------------------------------------ speech -> words

_REC = None


def recognizer():
    global _REC
    if _REC:
        return _REC
    try:
        import sherpa_onnx
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "sherpa-onnx", "soundfile"],
                       check=True)
        import sherpa_onnx
    mdir = WORK / "asr" / ASR_NAME
    if not (mdir / "tokens.txt").exists():
        (WORK / "asr").mkdir(parents=True, exist_ok=True)
        tb = WORK / "asr" / f"{ASR_NAME}.tar.bz2"
        print("downloading the speech model once (~480 MB)...", flush=True)
        urllib.request.urlretrieve(ASR_URL, tb)
        with tarfile.open(tb) as t:
            t.extractall(WORK / "asr")
        tb.unlink()
    _REC = sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=str(mdir / "encoder.int8.onnx"), decoder=str(mdir / "decoder.int8.onnx"),
        joiner=str(mdir / "joiner.int8.onnx"), tokens=str(mdir / "tokens.txt"),
        model_type="nemo_transducer", num_threads=max(1, os.cpu_count() or 1))
    return _REC


def words_of(video):
    """[{w, s, e}] for the whole clip, cached next to the work files."""
    SW.mkdir(parents=True, exist_ok=True)
    cache = SW / f"{md5(video)}.words.json"
    if cache.exists():
        return load_json(cache)
    import soundfile as sf
    wav = SW / "tmp.wav"
    subprocess.run([ck.ff(), "-y", "-v", "error", "-i", str(video), "-vn", "-ac", "1",
                    "-ar", "16000", str(wav)], check=True)
    audio, sr = sf.read(str(wav), dtype="float32")
    rec = recognizer()
    words, step = [], 75 * sr  # one pass for normal clips (<75 s) so no sentence is split
    for off in range(0, len(audio), step):
        st = rec.create_stream()
        st.accept_waveform(sr, audio[off:off + step])
        rec.decode_stream(st)
        r = st.result
        t0 = off / sr
        durs = list(getattr(r, "durations", []) or [0.08] * len(r.tokens))
        for tok, ts, d in zip(r.tokens, r.timestamps, durs):
            if tok.startswith(" ") or not words:
                if tok.strip():
                    words.append({"w": tok.strip(), "s": t0 + ts, "e": t0 + ts + max(d, 0.04)})
            else:
                words[-1]["w"] += tok
                words[-1]["e"] = t0 + ts + max(d, 0.04)
    wav.unlink(missing_ok=True)
    words = [w for w in words if re.search(r"\w", w["w"])]
    cache.write_text(json.dumps(words), encoding="utf-8")
    return words


# ------------------------------------------------------------------ captions

# Strong swearing and slurs are masked in on-screen captions (TikTok limits reach on them and
# campaigns reject hateful text). The audio is untouched.
_MASK = re.compile(
    r"(?i)^(\W*)(mother)?("
    r"f+u+c+k+(?:s|ed|er|ers|ing|in|in'|ery)?|f+k+|sh+i+t+(?:s|ty|ting|head|heads)?|"
    r"b+i+t+c+h+(?:es|y|ing)?|d+i+c+k+(?:s|head|heads)?|c+u+m+(?:s|ming|rag)?|c+u+n+t+s?|"
    r"p+u+s+s+y+|a+s+s+h+o+l+e+s?|n+i+g+g+(?:a|as|er|ers|uh)?|f+a+g+(?:s|got|gots)?|"
    r"r+e+t+a+r+d+(?:s|ed)?|w+h+o+r+e+s?|s+l+u+t+s?"
    r")(\W*)$")


def mask_word(w):
    m = _MASK.match(w)
    if not m:
        return w
    core = (m.group(2) or "") + m.group(3)
    return m.group(1) + core[0] + "*" * (len(core) - 1) + m.group(4)


def build_ass(words, hook, cap_y, title_y, dur, badge):
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Anton,104,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,1,0,1,8,3,5,60,60,0,1
Style: Title,Anton,74,&H00111111,&H00111111,&H00FFFFFF,&H00FFFFFF,0,0,0,0,100,100,0,0,3,20,0,5,80,80,0,1
Style: Badge,Anton,46,&H00FFFFFF,&H00FFFFFF,&H001A1AE0,&H001A1AE0,0,0,0,0,100,100,1,0,3,12,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    if badge:
        ev.append(f"Dialogue: 2,{ck.ass_time(0)},{ck.ass_time(min(dur, 4.0))},Badge,,0,0,0,,"
                  f"{{\\an7\\pos(56,64)}}{ck.ass_escape(badge)}")
    if hook:
        ev.append(f"Dialogue: 1,{ck.ass_time(0)},{ck.ass_time(min(dur, 3.8))},Title,,0,0,0,,"
                  f"{{\\an5\\pos(540,{title_y})\\fad(0,200)}}{ck.ass_escape(hook.upper())}")
    hi = "&H00F0FF&"  # yellow current word
    chunks = ck.chunk_words(words, max_words=3, max_chars=16)
    for ci, ch in enumerate(chunks):
        nxt = chunks[ci + 1][0]["s"] if ci + 1 < len(chunks) else dur
        ch_end = nxt if nxt - ch[-1]["e"] < 0.6 else min(ch[-1]["e"] + 0.3, nxt)
        toks = [ck.ass_escape(re.sub(r"[^\w'?!%$&/*-]", "", mask_word(w["w"]).upper())) for w in ch]
        for j, w in enumerate(ch):
            s = ch[0]["s"] if j == 0 else w["s"]
            e = ch[j + 1]["s"] if j + 1 < len(ch) else ch_end
            if e <= s:
                continue
            parts = [f"{{\\c{hi}}}{t}{{\\c&HFFFFFF&}}" if k == j else t for k, t in enumerate(toks)]
            pop = "\\fscx85\\fscy85\\t(0,80,\\fscx100\\fscy100)" if j == 0 else ""
            ev.append(f"Dialogue: 0,{ck.ass_time(s)},{ck.ass_time(e)},Cap,,0,0,0,,"
                      f"{{\\an5\\pos(540,{cap_y}){pop}}}" + " ".join(parts))
    return head + "\n".join(ev) + "\n"


# ------------------------------------------------------------------ commands

def cmd_assemble(a):
    man = load_json(a.manifest) or die(f"cannot read {a.manifest}")
    base = Path(a.manifest).parent
    out_dir = SW / "in"
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = []
    for c in man.get("clips", []):
        dst = out_dir / f"{c['id']}.mp4"
        if dst.exists() and md5(dst) == c["md5"]:
            ok.append(c)
            continue
        paths = []
        for p in c["parts"]:
            hit = base / p if (base / p).exists() else None
            if not hit:
                hits = sorted(glob.glob(str(UPLOADS / "**" / p), recursive=True))
                hit = Path(hits[0]) if hits else None
            paths.append(hit)
        if not all(paths):
            print(f"  {c['id']}: missing parts {[p for p, q in zip(c['parts'], paths) if not q]}")
            continue
        with open(dst, "wb") as f:
            for p in paths:
                f.write(p.read_bytes())
        if md5(dst) != c["md5"]:
            print(f"  {c['id']}: md5 mismatch, re-stage it")
            dst.unlink()
            continue
        ok.append(c)
    for c in ok:
        c["path"] = str(out_dir / f"{c['id']}.mp4")
    print(json.dumps({"ready": ok, "count": len(ok)}, indent=1))


def cmd_transcript(a):
    words = words_of(a.video)
    info = ck.probe(a.video)
    print(json.dumps({"duration": round(info["duration"], 2),
                      "text": " ".join(w["w"] for w in words),
                      "timed": [f"{w['s']:.1f} {w['w']}" for w in words][:400]}, indent=1))


def cmd_frame(a):
    info = ck.probe(a.video)
    t = info["duration"] * a.at
    out = SW / f"{Path(a.video).stem}_{int(t)}s.png"
    SW.mkdir(parents=True, exist_ok=True)
    subprocess.run([ck.ff(), "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", a.video, "-frames:v", "1",
                    str(out)], check=True)
    print(json.dumps({"png": str(out), "width": info["w"], "height": info["h"], "at": round(t, 1)}))


def cmd_used(a):
    print(json.dumps(sorted((load_json(USED, {}) or {}).keys())))


def cmd_make(a):
    camp = campaign(a.campaign)
    info = ck.probe(a.video)
    words = words_of(a.video)
    s = float(a.start or 0)
    e = float(a.end or info["duration"])
    max_s = float(camp.get("max_seconds", 60))
    if e - s > max_s:  # end on a whole word before the limit
        fits = [w for w in words if w["s"] >= s and w["e"] <= s + max_s - 0.3]
        e = fits[-1]["e"] + 0.3 if fits else s + max_s
    if e - s < float(camp.get("min_seconds", 5)):
        die(f"clip is only {e - s:.1f}s; campaign minimum is {camp.get('min_seconds')}s")
    dur = e - s
    fc = a.facecam or camp.get("facecam")
    if isinstance(fc, str):
        fc = [int(v) for v in fc.split(",")]
    layout = "split" if fc and a.layout != "blur" else "blur"
    fgraph, cap_y, title_y, _ = ck.layout_filter(layout, info, fc, a.center)
    if layout == "split":
        title_y = 170                         # hook at the very top, captions on the seam
    rel = [{"w": w["w"], "s": w["s"] - s, "e": w["e"] - s} for w in words
           if w["s"] >= s - 0.05 and w["s"] < e - 0.12]
    badge = camp.get("disclosure_badge", "#AD") if camp.get("paid") else ""
    SW.mkdir(parents=True, exist_ok=True)
    ass = SW / "clip.ass"
    ass.write_text(build_ass(rel, a.hook, cap_y, title_y, dur, badge), encoding="utf-8")
    out_dir = (ROOT / a.out) if not os.path.isabs(a.out) else Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = out_dir / f"{stamp}_s_{ck.slugify(a.hook or a.clip_title or 'clip', 36)}.mp4"
    fps = ["-r", "60"] if info["fps"] > 61 else []
    fg = (fgraph + f";[base]subtitles={ass.name}:fontsdir={FONT_DIR}[v]")
    subprocess.run([ck.ff(), "-y", "-v", "error", "-ss", f"{s:.3f}", "-i", str(Path(a.video).resolve()),
                    "-t", f"{dur:.3f}", "-filter_complex", fg, "-map", "[v]", "-map", "0:a:0?", *fps,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-maxrate", "4000k",
                    "-bufsize", "8000k", "-pix_fmt", "yuv420p",
                    "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "160k",
                    "-ar", "48000", "-movflags", "+faststart", str(out)], check=True, cwd=SW)
    side = {"title": a.hook or a.clip_title, "campaign": a.campaign, "kind": "stream",
            "streamer": a.streamer or camp.get("streamer"), "source_title": a.clip_title,
            "source_url": a.clip_url, "clip_id": a.clip_id, "source_views": a.views,
            "duration": round(dur, 2), "layout": layout,
            "transcript": " ".join(w["w"] for w in rel), "ai_generated": False, "credit": None}
    out.with_suffix(".json").write_text(json.dumps(side, indent=2), encoding="utf-8")
    if a.clip_id:
        used = load_json(USED, {}) or {}
        used[a.clip_id] = {"date": datetime.date.today().isoformat(), "file": out.name,
                           "campaign": a.campaign}
        USED.parent.mkdir(parents=True, exist_ok=True)
        USED.write_text(json.dumps(used, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"file": str(out), "duration": round(dur, 1), "layout": layout,
                      "mb": round(out.stat().st_size / 1e6, 1)}))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("assemble"); p.add_argument("manifest"); p.set_defaults(fn=cmd_assemble)
    p = sub.add_parser("transcript"); p.add_argument("video"); p.set_defaults(fn=cmd_transcript)
    p = sub.add_parser("frame"); p.add_argument("video"); p.add_argument("--at", type=float, default=0.4)
    p.set_defaults(fn=cmd_frame)
    p = sub.add_parser("used"); p.set_defaults(fn=cmd_used)
    p = sub.add_parser("make")
    p.add_argument("video")
    p.add_argument("--campaign", required=True)
    p.add_argument("--hook", default="", help="on-screen hook, 3-8 words")
    p.add_argument("--facecam", help="X,Y,W,H of the webcam in the source (overrides campaign)")
    p.add_argument("--layout", choices=["split", "blur"], default=None)
    p.add_argument("--center", type=float, default=0.5, help="gameplay crop centre, 0-1")
    p.add_argument("--start"), p.add_argument("--end")
    p.add_argument("--clip-id"), p.add_argument("--clip-title"), p.add_argument("--clip-url")
    p.add_argument("--views", type=int), p.add_argument("--streamer")
    p.add_argument("--out", default="queue")
    p.set_defaults(fn=cmd_make)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
