#!/usr/bin/env python3
"""
ClipKit - a free vertical clip maker (TikTok / Reels / Shorts).

Runs on your own computer with free tools: yt-dlp (download), faster-whisper
(speech-to-text) and ffmpeg (cutting, 9:16 reframing, burned-in captions).

Commands (run from inside the ClipKit folder):
  py clipkit.py fetch  <URL or video file> [--name NAME] [--from 00:30:00 --to 01:30:00]
  py clipkit.py transcript <project>       [--model base.en]
  py clipkit.py suggest    <project>       [--count 8 --length 35]
  py clipkit.py cut        <project>       [--layout crop|blur|split] [--facecam X,Y,W,H]
  py clipkit.py list

Typical flow:
  1. fetch       -> downloads the source into projects/<name>/
  2. transcript  -> writes projects/<name>/transcript.txt
  3. Paste PICK_MOMENTS_PROMPT.txt + transcript.txt into Claude, save the JSON
     it returns as projects/<name>/clips.json   (or run `suggest` for an
     automatic, no-AI first pass)
  4. cut         -> writes finished 1080x1920 clips to projects/<name>/clips/
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# CLIPKIT_WORK lets the cloud version keep big downloads outside the git repo
WORK = Path(os.environ.get("CLIPKIT_WORK") or HERE).resolve()
PROJECTS = WORK / "projects"
FONTS = WORK / "fonts"
OUT_W, OUT_H = 1080, 1920

# ----------------------------------------------------------------- helpers


def die(msg):
    print(f"\n[ClipKit] {msg}\n")
    sys.exit(1)


def need(tool, hint):
    if shutil.which(tool) is None:
        die(f"'{tool}' was not found. {hint}\nThen close and reopen this window.")


_FF = None


def ff():
    """ffmpeg executable: $FFMPEG, then PATH, then the imageio-ffmpeg wheel's static build."""
    global _FF
    if _FF:
        return _FF
    exe = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not exe:
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            die("ffmpeg was not found. Install it:  winget install Gyan.FFmpeg  "
                "(or: pip install imageio-ffmpeg). Then close and reopen this window.")
    _FF = exe
    return exe


def ytdlp_extra():
    """Extra yt-dlp flags so it works where ffmpeg/deno aren't on PATH (cloud)."""
    extra = []
    if not shutil.which("ffmpeg"):
        extra += ["--ffmpeg-location", ff()]
    if not shutil.which("deno") and shutil.which("node"):
        extra += ["--js-runtimes", "node"]
    return extra


def run(cmd, cwd=None, quiet=False):
    kw = {}
    if quiet:
        kw = {"stdout": subprocess.DEVNULL, "stderr": subprocess.PIPE}
    r = subprocess.run(cmd, cwd=cwd, **kw)
    if r.returncode != 0:
        err = (r.stderr or b"").decode("utf-8", "replace")[-1500:] if quiet else ""
        die(f"Command failed: {cmd[0]} ...\n{err}")
    return r


def slugify(text, maxlen=40):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (s[:maxlen].strip("-")) or "clip"


def parse_time(t):
    """'01:02:03.5', '02:03', '123.4' or a number -> seconds (float)."""
    if isinstance(t, (int, float)):
        return float(t)
    t = str(t).strip().replace(",", ".")
    parts = t.split(":")
    secs = 0.0
    for p in parts:
        secs = secs * 60 + float(p)
    return secs


def fmt_hms(sec):
    sec = max(0, int(round(sec)))
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def ass_time(sec):
    sec = max(0.0, sec)
    cs = int(round(sec * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def probe(path):
    if not shutil.which("ffprobe"):  # parse `ffmpeg -i` instead (cloud / imageio-ffmpeg)
        r = subprocess.run([ff(), "-hide_banner", "-i", str(path)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        err = r.stderr
        m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", err)
        if not m:
            die(f"Could not read video: {path}\n{err[-500:]}")
        d = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", err)
        fps = re.search(r"(\d+(?:\.\d+)?) fps", err)
        dur = (int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3))) if d else 0.0
        return {"w": int(m.group(1)), "h": int(m.group(2)),
                "fps": float(fps.group(1)) if fps else 30.0, "duration": dur}
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        die(f"Could not read video: {path}\n{out.stderr[-500:]}")
    j = json.loads(out.stdout)
    st = j["streams"][0]
    num, den = (st.get("r_frame_rate") or "30/1").split("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    return {"w": int(st["width"]), "h": int(st["height"]), "fps": fps,
            "duration": float(j["format"].get("duration", 0) or 0)}


def project_dir(name):
    p = Path(name)
    if p.is_dir() and (p / "project.json").exists():
        return p.resolve()
    p = PROJECTS / name
    if not (p / "project.json").exists():
        die(f"No project called '{name}'. Run `py clipkit.py list` to see projects.")
    return p


def load_project(pdir):
    meta = json.loads((pdir / "project.json").read_text(encoding="utf-8"))
    src = Path(meta["source"])
    if not src.is_absolute():
        src = pdir / src
    if not src.exists():
        die(f"Source video is missing: {src}")
    meta["source_path"] = src
    return meta


# ----------------------------------------------------------------- whisper

_MODEL_CACHE = {}


def get_model(name, gpu=False):
    key = (name, gpu)
    if key not in _MODEL_CACHE:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            die("faster-whisper is not installed. Double-click SETUP.bat first.")
        print(f"  loading speech model '{name}' (first time downloads it, ~1 min)...")
        _MODEL_CACHE[key] = WhisperModel(
            name, device="cuda" if gpu else "cpu",
            compute_type="float16" if gpu else "int8")
    return _MODEL_CACHE[key]


def extract_wav(src, start, dur, dst):
    cmd = [ff(), "-y", "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(src)]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", str(dst)]
    run(cmd, quiet=True)


def transcribe_words(wav, model_name, gpu=False):
    """Return a list of {'w': text, 's': start, 'e': end} for each spoken word."""
    model = get_model(model_name, gpu)
    segments, _ = model.transcribe(str(wav), word_timestamps=True, vad_filter=True,
                                   language="en" if model_name.endswith(".en") else None)
    words = []
    for seg in segments:
        for w in (seg.words or []):
            text = w.word.strip()
            if text:
                words.append({"w": text, "s": float(w.start), "e": float(w.end)})
    return words


def transcribe_segments(wav, model_name, gpu=False, total=None):
    model = get_model(model_name, gpu)
    segments, _ = model.transcribe(str(wav), vad_filter=True,
                                   language="en" if model_name.endswith(".en") else None)
    out = []
    for seg in segments:
        out.append({"s": float(seg.start), "e": float(seg.end), "text": seg.text.strip()})
        if total:
            pct = min(100, int(seg.end / total * 100))
            print(f"\r  transcribing... {pct}%", end="", flush=True)
    print()
    return out


# ----------------------------------------------------------------- fetch


def cmd_fetch(a):
    src = a.source
    PROJECTS.mkdir(parents=True, exist_ok=True)
    local = Path(src)
    if local.exists():
        name = a.name or slugify(local.stem)
        pdir = PROJECTS / name
        pdir.mkdir(parents=True, exist_ok=True)
        meta = {"source": str(local.resolve()), "title": local.stem, "url": None}
        (pdir / "project.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(f"\nProject '{name}' created from local file.\nNext: py clipkit.py transcript {name}")
        return

    need("yt-dlp", "Install it:  winget install yt-dlp.yt-dlp")
    ff()
    info = subprocess.run(["yt-dlp", *ytdlp_extra(), "--encoding", "utf-8", "--no-playlist", "--skip-download",
                           "--print", "%(id)s|||%(title)s", src],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if info.returncode != 0 or "|||" not in info.stdout:
        die("yt-dlp could not read that link. Try `yt-dlp -U` (or "
            "`winget upgrade yt-dlp.yt-dlp`) to update it. For YouTube, yt-dlp also "
            "needs Deno:  winget install DenoLand.Deno\n" + info.stderr[-800:])
    vid, title = info.stdout.strip().splitlines()[-1].split("|||", 1)
    name = a.name or slugify(title) or slugify(vid)
    pdir = PROJECTS / name
    pdir.mkdir(parents=True, exist_ok=True)

    base = ["yt-dlp", *ytdlp_extra(), "--no-playlist", "-P", str(pdir)]
    section = []
    if a.start or a.end:
        s = a.start or "0"
        e = a.end or "inf"
        section = ["--download-sections", f"*{s}-{e}", "--force-keyframes-at-cuts"]
    print(f"\nDownloading: {title}")
    run(base + section + [
        "-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
        "--merge-output-format", "mp4", "-o", "source.%(ext)s", src])
    # English subtitles (YouTube auto-captions) make the transcript step instant.
    if not section:
        subprocess.run(base + ["--skip-download", "--write-subs", "--write-auto-subs",
                               "--sub-langs", "en.*,en", "--sub-format", "vtt",
                               "-o", "subs.%(ext)s", src],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    srcs = sorted(pdir.glob("source.*"))
    srcs = [p for p in srcs if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")]
    if not srcs:
        die("Download finished but no video file was found.")
    meta = {"source": srcs[0].name, "title": title, "url": src}
    (pdir / "project.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"\nProject '{name}' ready.\nNext: py clipkit.py transcript {name}")


# ----------------------------------------------------------------- transcript

TAG_RE = re.compile(r"<[^>]+>")


def parse_vtt(path):
    """YouTube auto-captions -> [(start_seconds, line)], with rolling duplicates removed."""
    lines_out = []
    last = None
    cue_start = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "-->" in raw:
            cue_start = parse_time(raw.split("-->")[0].strip())
            continue
        if cue_start is None:
            continue
        txt = TAG_RE.sub("", raw).strip()
        txt = re.sub(r"\s+", " ", txt)
        if not txt or txt == last:
            continue
        if last and txt.startswith(last):  # rolling caption growth
            txt = txt[len(last):].strip()
            if not txt:
                continue
        lines_out.append((cue_start, txt))
        last = txt
    return lines_out


TS_RE = re.compile(r"<(\d+:\d+:\d+\.\d+|\d+:\d+\.\d+)>")


def parse_vtt_words(path):
    """Word timings from a .vtt. YouTube auto-captions carry a <time> tag per word;
    plain subtitles get their words spread evenly over the cue."""
    words, recent = [], []
    cue_s = cue_e = None

    def emit(s, text):
        for t in text.split():
            if not re.fullmatch(r"\[.*\]", t):  # [Music], [Applause]
                words.append({"w": t, "s": s, "e": None})

    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "-->" in raw:
            a_, b_ = raw.split("-->")
            cue_s, cue_e = parse_time(a_.strip()), parse_time(b_.strip().split()[0])
            continue
        if cue_s is None:
            continue
        plain = re.sub(r"\s+", " ", TAG_RE.sub("", raw)).strip()
        if not plain:
            continue
        if TS_RE.search(raw):
            parts = TS_RE.split(raw)  # [text0, t1, text1, t2, text2, ...]
            emit(cue_s, TAG_RE.sub("", parts[0]))
            for k in range(1, len(parts), 2):
                emit(parse_time(parts[k]), TAG_RE.sub("", parts[k + 1]))
        elif plain not in recent:
            toks = plain.split()
            step = max(0.05, (cue_e - cue_s) / max(1, len(toks)))
            for j, t in enumerate(toks):
                emit(cue_s + j * step, t)
        else:
            continue
        recent = (recent + [plain])[-3:]
    words.sort(key=lambda w: w["s"])
    for i, w in enumerate(words):
        nxt = words[i + 1]["s"] if i + 1 < len(words) else w["s"] + 0.6
        w["e"] = max(w["s"] + 0.05, min(nxt, w["s"] + 1.2))
    return words


def fix_words(words, pairs):
    """Apply 'from=to' fixes (case-insensitive, may span two words) e.g. 'box able=BOXABL'."""
    if not pairs:
        return words
    fixes = {}
    for p in pairs.split(","):
        if "=" in p:
            k, v = p.split("=", 1)
            fixes[k.strip().lower()] = v.strip()
    out, i = [], 0
    while i < len(words):
        w = words[i]
        core = re.sub(r"[^\w' ]", "", w["w"]).lower()
        if i + 1 < len(words):
            two = core + " " + re.sub(r"[^\w' ]", "", words[i + 1]["w"]).lower()
            if two in fixes:
                out.append({"w": fixes[two], "s": w["s"], "e": words[i + 1]["e"]})
                i += 2
                continue
        tail = re.sub(r"^[\w']+", "", w["w"])
        for k, v in fixes.items():
            if " " not in k and re.fullmatch(rf"{re.escape(k)}(s|'s)?", core):
                w = {**w, "w": v + core[len(k):] + tail}
                break
        out.append(w)
        i += 1
    return out


def write_transcript(pdir, items):
    """items: [(start_seconds, text)] -> ~12 second paragraphs with timestamps."""
    paras, cur, cur_start = [], [], None
    for s, t in items:
        if cur_start is None:
            cur_start = s
        cur.append(t)
        if s - cur_start >= 12:
            paras.append((cur_start, " ".join(cur)))
            cur, cur_start = [], None
    if cur:
        paras.append((cur_start, " ".join(cur)))
    txt = "\n".join(f"[{fmt_hms(s)}] {t}" for s, t in paras)
    (pdir / "transcript.txt").write_text(txt, encoding="utf-8")
    return len(paras)


def cmd_transcript(a):
    pdir = project_dir(a.project)
    meta = load_project(pdir)
    vtts = sorted(pdir.glob("subs*.vtt"))
    if vtts and not a.whisper:
        # prefer manual English subs, then auto-captions
        vtts.sort(key=lambda p: ("orig" in p.name, len(p.name)))
        items = parse_vtt(vtts[0])
        if items:
            n = write_transcript(pdir, items)
            print(f"Transcript written from YouTube captions ({n} lines): {pdir / 'transcript.txt'}")
            print(f"Next: paste PICK_MOMENTS_PROMPT.txt + that transcript into Claude, "
                  f"save the JSON as {pdir / 'clips.json'}")
            return
    ff()
    info = probe(meta["source_path"])
    wav = pdir / "audio_16k.wav"
    print("Extracting audio...")
    extract_wav(meta["source_path"], None, None, wav)
    segs = transcribe_segments(wav, a.model, a.gpu, total=info["duration"])
    (pdir / "segments.json").write_text(json.dumps(segs), encoding="utf-8")
    n = write_transcript(pdir, [(s["s"], s["text"]) for s in segs])
    wav.unlink(missing_ok=True)
    print(f"Transcript written ({n} lines): {pdir / 'transcript.txt'}")
    print(f"Next: paste PICK_MOMENTS_PROMPT.txt + that transcript into Claude, "
          f"save the JSON as {pdir / 'clips.json'}")


# ----------------------------------------------------------------- suggest


def loudness_curve(src, step=0.5):
    """dB loudness every `step` seconds, streamed so long VODs stay light on memory."""
    import numpy as np
    sr = 8000
    n = int(sr * step)
    p = subprocess.Popen([ff(), "-v", "error", "-i", str(src), "-vn", "-ac", "1",
                          "-ar", str(sr), "-f", "s16le", "-"], stdout=subprocess.PIPE)
    vals = []
    while True:
        buf = p.stdout.read(n * 2)
        if not buf:
            break
        x = np.frombuffer(buf, dtype=np.int16).astype(np.float32)
        rms = float(np.sqrt(np.mean(x * x)) + 1e-6)
        vals.append(20 * np.log10(rms / 32768.0))
    p.wait()
    return np.array(vals), step


def cmd_suggest(a):
    import numpy as np
    pdir = project_dir(a.project)
    meta = load_project(pdir)
    print("Scanning audio for the loudest, most energetic moments...")
    db, step = loudness_curve(meta["source_path"])
    if len(db) < 10:
        die("Video is too short to scan.")
    L = a.length
    win = max(1, int(6 / step))  # 6s smoothing for 'sustained hype'
    smooth = np.convolve(db, np.ones(win) / win, mode="same")
    base = np.median(smooth)
    spread = np.std(smooth) + 1e-6
    score = (smooth - base) / spread
    taken = np.zeros(len(db), dtype=bool)
    picks = []
    order = np.argsort(-score)
    n = int(L / step)  # clip length in steps; peak sits ~2/3 of the way in
    for i in order:
        if len(picks) >= a.count:
            break
        lo, hi = i - int(0.65 * n), i + int(0.35 * n)
        if lo < 0:
            lo, hi = 0, min(len(db), n)
        if hi > len(db):
            lo, hi = max(0, len(db) - n), len(db)
        if taken[max(0, lo - 4):hi + 4].any():
            continue
        taken[lo:hi] = True
        picks.append((lo * step, hi * step, float(score[i])))
    # snap to transcript segment boundaries when we have them
    segs = []
    if (pdir / "segments.json").exists():
        segs = json.loads((pdir / "segments.json").read_text(encoding="utf-8"))
    clips = []
    for s, e, sc in sorted(picks):
        if segs:
            starts = [g["s"] for g in segs if abs(g["s"] - s) <= 5]
            ends = [g["e"] for g in segs if abs(g["e"] - e) <= 5]
            s = min(starts, key=lambda v: abs(v - s)) if starts else s
            e = min(ends, key=lambda v: abs(v - e)) if ends else e
        clips.append({"title": "", "start": fmt_hms(s), "end": fmt_hms(e),
                      "note": f"energy score {sc:.1f} - watch it, then write a hook title"})
    out = pdir / "clips.json"
    if out.exists() and not a.force:
        out = pdir / "clips_suggested.json"
    out.write_text(json.dumps(clips, indent=2), encoding="utf-8")
    print(f"Wrote {len(clips)} suggestions to {out}")
    print("Check each one, add a short 'title' hook, then run: "
          f"py clipkit.py cut {pdir.name}" + ("" if out.name == "clips.json"
                                              else f" --clips {out.name}"))


# ----------------------------------------------------------------- captions

FONT_CANDIDATES = [
    # (file path, family name as libass sees it)
    (r"C:\Windows\Fonts\ariblk.ttf", "Arial Black"),
    (r"C:\Windows\Fonts\impact.ttf", "Impact"),
    (r"C:\Windows\Fonts\arialbd.ttf", "Arial"),
    ("/System/Library/Fonts/Supplemental/Arial Black.ttf", "Arial Black"),
    ("/System/Library/Fonts/Supplemental/Impact.ttf", "Impact"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVu Sans"),
]


def setup_font(user_font=None):
    FONTS.mkdir(parents=True, exist_ok=True)
    if user_font:
        fp = Path(user_font)
        if not fp.exists():
            die(f"Font file not found: {fp}")
        shutil.copy(fp, FONTS / fp.name)
        return fp.stem  # best guess at family; pass --font-name if it differs
    for path, family in FONT_CANDIDATES:
        if Path(path).exists():
            dst = FONTS / Path(path).name
            if not dst.exists():
                shutil.copy(path, dst)
            return family
    return "Arial"


def ass_escape(t):
    return t.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def chunk_words(words, max_words=3, max_chars=15):
    chunks, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        text_len = sum(len(x["w"]) + 1 for x in cur)
        brk = (len(cur) >= max_words or text_len >= max_chars or nxt is None
               or re.search(r"[.?!,;:]$", w["w"]) or (nxt and nxt["s"] - w["e"] > 0.35))
        if brk:
            chunks.append(cur)
            cur = []
    return chunks


def clean_word(w):
    w = re.sub(r"[^\w'?!%$&/-]", "", w["w"].upper())
    return ass_escape(w)


def build_ass(words, title, font, cap_y, title_y, title_full, dur, highlight="&H00FFFF&"):
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {OUT_W}
PlayResY: {OUT_H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},80,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,1,0,1,7,3,5,60,60,0,1
Style: Title,{font},64,&H00111111,&H00111111,&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,3,18,0,5,90,90,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    if title:
        t_end = dur if title_full else min(dur, 3.5)
        ev.append(f"Dialogue: 1,{ass_time(0)},{ass_time(t_end)},Title,,0,0,0,,"
                  f"{{\\an5\\pos(540,{title_y})}}{ass_escape(title)}")
    chunks = chunk_words(words)
    for ci, ch in enumerate(chunks):
        nxt_start = chunks[ci + 1][0]["s"] if ci + 1 < len(chunks) else dur
        ch_end = min(max(ch[-1]["e"] + 0.25, ch[-1]["e"]), nxt_start)
        if nxt_start - ch[-1]["e"] < 0.6:
            ch_end = nxt_start  # no flicker between close chunks
        toks = [clean_word(w) for w in ch]
        for j, w in enumerate(ch):
            s = ch[0]["s"] if j == 0 else w["s"]
            e = ch[j + 1]["s"] if j + 1 < len(ch) else ch_end
            if e <= s:
                continue
            parts = []
            for k, tok in enumerate(toks):
                parts.append(f"{{\\c{highlight}}}{tok}{{\\c&HFFFFFF&}}" if k == j else tok)
            pop = "\\fscx80\\fscy80\\t(0,90,\\fscx100\\fscy100)" if j == 0 else ""
            ev.append(f"Dialogue: 0,{ass_time(s)},{ass_time(e)},Cap,,0,0,0,,"
                      f"{{\\an5\\pos(540,{cap_y}){pop}}}" + " ".join(parts))
    return head + "\n".join(ev) + "\n"


# ----------------------------------------------------------------- layouts


def even(x):
    x = int(round(x))
    return x - (x % 2)


def layout_filter(layout, info, facecam=None, center=0.5):
    """Returns (filtergraph producing [base], caption_y, title_y, title_full)."""
    iw, ih = info["w"], info["h"]
    if layout == "crop":
        cw = min(iw, even(ih * 9 / 16))
        cx = even(max(0, min(iw - cw, (iw - cw) * center)))
        f = f"[0:v]crop={cw}:{ih}:{cx}:0,scale={OUT_W}:{OUT_H},setsar=1[base]"
        return f, 1330, 330, False
    if layout == "split":
        if not facecam:
            die("--layout split needs --facecam X,Y,W,H (the webcam box in the source video, "
                "in pixels). Tip: pause the video, take a screenshot, and measure it in Paint.")
        fx, fy, fw, fh = facecam
        top = even(min(OUT_H * 0.45, OUT_W * fh / fw))
        bot = OUT_H - top
        cw = min(iw, even(ih * OUT_W / bot))
        cx = even((iw - cw) * center)
        f = (f"[0:v]split[a][b];[a]crop={fw}:{fh}:{fx}:{fy},scale={OUT_W}:{top},setsar=1[t];"
             f"[b]crop={cw}:{ih}:{cx}:0,scale={OUT_W}:{bot},setsar=1[g];[t][g]vstack[base]")
        return f, top, 200, False
    # blur (default): whole frame in the middle, blurred copy behind it
    fg_h = even(min(OUT_H, OUT_W * ih / iw))
    f = (f"[0:v]split[a][b];"
         f"[a]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H},"
         f"boxblur=luma_radius=40:luma_power=2,eq=brightness=-0.12[bg];"
         f"[b]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease[fg];"
         f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[base]")
    vid_top = (OUT_H - fg_h) // 2
    vid_bot = vid_top + fg_h
    cap_y = min(OUT_H - 330, vid_bot + 190) if fg_h < OUT_H * 0.8 else int(OUT_H * 0.7)
    title_y = max(220, vid_top - 170) if fg_h < OUT_H * 0.8 else 300
    return f, cap_y, title_y, fg_h < OUT_H * 0.8


# ----------------------------------------------------------------- cut


def snap_to_words(words_abs, s, e, back=0.6, fwd=1.5):
    """Move start/end so the clip begins and ends on whole words."""
    if not words_abs:
        return s, e
    starts = [w for w in words_abs if w["s"] >= s - back]
    if starts:
        s = max(0.0, starts[0]["s"] - 0.15)
    inside = [w for w in words_abs if w["e"] <= e + fwd and w["s"] >= s]
    if inside:
        # prefer ending on a sentence end within the last ~3 seconds
        enders = [w for w in inside if re.search(r"[.?!]$", w["w"]) and w["e"] >= e - 3]
        last = enders[-1] if enders else inside[-1]
        nxt = [w for w in words_abs if w["s"] > last["s"] + 0.01]
        gap = (nxt[0]["s"] - last["e"]) if nxt else 1.0
        e = last["e"] + max(0.08, min(0.3, gap - 0.02))  # don't run into the next word
    return s, e


def cmd_cut(a):
    ff()
    pdir = project_dir(a.project)
    meta = load_project(pdir)
    src = meta["source_path"]
    cf = pdir / a.clips
    if not cf.exists():
        die(f"{cf} not found. Make it with Claude (see PICK_MOMENTS_PROMPT.txt) "
            f"or run: py clipkit.py suggest {pdir.name}")
    try:
        txt = cf.read_text(encoding="utf-8")
        m = re.search(r"\[.*\]", txt, re.S)  # tolerate extra text around pasted JSON
        clips = json.loads(m.group(0) if m else txt)
    except Exception as ex:
        die(f"clips.json is not valid JSON: {ex}")
    if a.only:
        keep = {int(x) for x in a.only.split(",")}
        clips = [c for i, c in enumerate(clips, 1) if i in keep]
    info = probe(src)
    font = setup_font(a.font)
    if a.font_name:
        font = a.font_name
    facecam = [int(v) for v in a.facecam.split(",")] if a.facecam else None
    fgraph, cap_y, title_y, title_full = layout_filter(a.layout, info, facecam, a.center)
    outdir = Path(a.outdir).resolve() if a.outdir else pdir / "clips"
    tmp = pdir / "tmp"
    outdir.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(exist_ok=True)
    fps_args = ["-r", "60"] if info["fps"] > 61 else []
    stamp = __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M")
    made = []
    # Caption timings: the video's own subtitles (instant, no speech model) or Whisper.
    vtt_words = None
    vtts = sorted(pdir.glob("subs*.vtt"), key=lambda p: ("orig" in p.name, len(p.name)))
    if vtts and a.words != "whisper" and not a.no_captions:
        vtt_words = fix_words(parse_vtt_words(vtts[0]), a.fix) or None
        if vtt_words:
            print(f"Captions from subtitles: {vtts[0].name}")
    if a.words == "vtt" and not vtt_words and not a.no_captions:
        die("--words vtt: no subs*.vtt with words in this project")
    offset = float(meta.get("offset", 0) or 0)  # source is a piece starting at this time
    for i, c in enumerate(clips, 1):
        s, e = parse_time(c["start"]), parse_time(c["end"])
        if e <= s:
            print(f"  skipping clip {i}: end is before start")
            continue
        hook_title = (c.get("title") or "").strip()
        title = hook_title if not a.no_title else ""
        label = slugify(hook_title or c.get("hook", "") or f"clip-{i}", 30)
        print(f"\n[{i}/{len(clips)}] {fmt_hms(s)}-{fmt_hms(e)}  {title}")
        words = []
        if not a.no_captions:
            if vtt_words:
                wabs = [w for w in vtt_words if w["s"] >= s - 2.0 and w["e"] <= e + 2.0]
            else:
                pad0 = max(0.0, s - 2.0)
                wav = tmp / f"c{i:02d}.wav"
                extract_wav(src, pad0 - offset, (e + 2.0) - pad0, wav)
                print("  transcribing for captions...")
                wabs = fix_words([{"w": w["w"], "s": w["s"] + pad0, "e": w["e"] + pad0}
                                  for w in transcribe_words(wav, a.model, a.gpu)], a.fix)
                wav.unlink(missing_ok=True)
            if not a.no_snap:  # subtitle timings are exact: clips.json gives word start/end
                s, e = snap_to_words(wabs, s, e, *((0.25, 0.05) if vtt_words else ()))
            if e - s > a.max_len:  # YouTube blocks claimed Shorts over 60s
                fits = [w for w in wabs if w["s"] >= s and w["e"] <= s + a.max_len - 0.3]
                e = (fits[-1]["e"] + 0.3) if fits else s + a.max_len
                print(f"  trimmed to {a.max_len:.0f}s (YouTube Shorts limit)")
            words = [{"w": w["w"], "s": w["s"] - s, "e": w["e"] - s}
                     for w in wabs if w["s"] >= s - 0.05 and w["e"] <= e + 0.05
                     and w["s"] < e - 0.12]
        if a.no_captions and e - s > a.max_len:
            e = s + a.max_len
            print(f"  trimmed to {a.max_len:.0f}s (YouTube Shorts limit)")
        dur = e - s
        ass = tmp / f"c{i:02d}.ass"
        ass.write_text(build_ass(words, title, font, cap_y, title_y, title_full, dur),
                       encoding="utf-8")
        rel_ass = os.path.relpath(ass, WORK).replace("\\", "/")
        rel_fonts = os.path.relpath(FONTS, WORK).replace("\\", "/")
        fg = fgraph + f";[base]subtitles={rel_ass}:fontsdir={rel_fonts}[v]"
        out = outdir / f"{stamp}_{i:02d}_{label}.mp4"
        print("  rendering 1080x1920...")
        run([ff(), "-y", "-v", "error", "-stats", "-ss", f"{s - offset:.3f}", "-i", str(src),
             "-t", f"{dur:.3f}", "-filter_complex", fg, "-map", "[v]", "-map", "0:a:0?",
             *fps_args, "-c:v", "libx264", "-preset", a.preset, "-crf", "21",
             "-maxrate", "3000k", "-bufsize", "6000k",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
             "-movflags", "+faststart", str(out)], cwd=WORK)
        # info file the auto-poster reads (upload it to queue/ together with the .mp4)
        side = {"title": hook_title, "campaign": a.campaign or c.get("campaign"),
                "source_title": meta.get("title"), "source_url": meta.get("url"),
                "start": fmt_hms(s), "end": fmt_hms(e), "duration": round(dur, 2),
                "transcript": " ".join(w["w"] for w in words)}
        out.with_suffix(".json").write_text(json.dumps(side, indent=2), encoding="utf-8")
        mb = out.stat().st_size / 1e6
        if mb > 25:
            print(f"  note: {mb:.0f} MB is over GitHub's 25 MB browser-upload limit")
        made.append(out)
    print(f"\nDone - {len(made)} clip(s) in {outdir}")
    if made and not a.campaign:
        print("Tip: add --campaign <key from campaigns.json> so the auto-poster knows "
              "which campaign these clips belong to (or edit the .json files).")


def cmd_list(a):
    if not PROJECTS.exists():
        print("No projects yet. Start with: py clipkit.py fetch <URL or file>")
        return
    for p in sorted(PROJECTS.iterdir()):
        if (p / "project.json").exists():
            n = len(list((p / "clips").glob("*.mp4"))) if (p / "clips").exists() else 0
            flags = ["transcript" if (p / "transcript.txt").exists() else "",
                     "clips.json" if (p / "clips.json").exists() else "",
                     f"{n} clips" if n else ""]
            print(f"  {p.name:42s} {' | '.join(f for f in flags if f)}")


# ----------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser(description="ClipKit - free vertical clip maker")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="download a video (or register a local file)")
    f.add_argument("source")
    f.add_argument("--name")
    f.add_argument("--from", dest="start", help="only download from this time, e.g. 01:20:00")
    f.add_argument("--to", dest="end", help="only download up to this time, e.g. 02:00:00")
    f.set_defaults(fn=cmd_fetch)

    t = sub.add_parser("transcript", help="make a timestamped transcript to pick moments from")
    t.add_argument("project")
    t.add_argument("--model", default="base.en", help="tiny.en (fastest), base.en, small.en")
    t.add_argument("--whisper", action="store_true", help="ignore YouTube captions")
    t.add_argument("--gpu", action="store_true")
    t.set_defaults(fn=cmd_transcript)

    s = sub.add_parser("suggest", help="auto-pick loud/hype moments (no AI needed)")
    s.add_argument("project")
    s.add_argument("--count", type=int, default=8)
    s.add_argument("--length", type=float, default=35, help="target clip length in seconds")
    s.add_argument("--force", action="store_true", help="overwrite an existing clips.json")
    s.set_defaults(fn=cmd_suggest)

    c = sub.add_parser("cut", help="render the clips listed in clips.json")
    c.add_argument("project")
    c.add_argument("--clips", default="clips.json")
    c.add_argument("--layout", choices=["blur", "crop", "split"], default="crop")
    c.add_argument("--facecam", help="X,Y,W,H of the webcam in the source (split layout)")
    c.add_argument("--center", type=float, default=0.5,
                   help="crop position left(0) to right(1) for crop/split")
    c.add_argument("--model", default="small.en", help="caption accuracy: base.en / small.en")
    c.add_argument("--only", help="render only these clip numbers, e.g. 1,3,4")
    c.add_argument("--no-captions", action="store_true")
    c.add_argument("--no-title", action="store_true")
    c.add_argument("--no-snap", action="store_true", help="don't adjust cuts to word edges")
    c.add_argument("--font", help="path to a .ttf to use for captions")
    c.add_argument("--font-name", help="font family name if using --font")
    c.add_argument("--max-len", type=float, default=59,
                   help="longest allowed clip in seconds (default 59 for YouTube Shorts)")
    c.add_argument("--outdir", help="write finished clips here instead of the project's clips/ folder")
    c.add_argument("--campaign", help="campaign key from campaigns.json (written to each clip's .json)")
    c.add_argument("--words", choices=["auto", "vtt", "whisper"], default="auto",
                   help="caption timings: the video's subtitles (auto when present) or Whisper")
    c.add_argument("--fix", help="caption word fixes, e.g. 'boxable=BOXABL,box able=BOXABL'")
    c.add_argument("--preset", default="veryfast")
    c.add_argument("--gpu", action="store_true")
    c.set_defaults(fn=cmd_cut)

    l = sub.add_parser("list", help="show projects")
    l.set_defaults(fn=cmd_list)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
