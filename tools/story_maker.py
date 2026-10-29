#!/usr/bin/env python3
"""
Story video maker: turns a written scary story into a vertical narrated video.

  python tools/story_maker.py make <story.json> [--out queue] [--campaign scary-stories] [--seed N]

story.json: {"title": "...", "story": "paragraphs separated by blank lines"}

What it does (all offline, no API keys):
  1. Narrates the title, then the story, with the Piper "ryan-high" voice (downloaded once from
     GitHub), slowed and pitched down with a little reverb for a creepy tone.
  2. Times every word (sentence audio is exact; words inside a sentence are spread by phoneme count).
  3. Lays it over a random stretch of Minecraft parkour from assets/gameplay/, darkened with a
     vignette, plus a quiet generated drone underneath.
  4. Shows the title on a card while it is read, then big word-by-word captions (current word red).
  5. Writes <out>/<stamp>_<NN>_<slug>.mp4 and a matching .json sidecar for the morning job.
"""
import argparse
import datetime
import json
import os
import random
import re
import subprocess
import sys
import tarfile
import urllib.request
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
WORK = Path(os.environ.get("CLIPKIT_WORK") or Path.home() / "rawcut-work")
VOICE_DIR = WORK / "voices"
VOICE_URL = "https://github.com/rhasspy/piper/releases/download/v0.0.2/voice-en-us-ryan-high.tar.gz"
VOICE_FILE = VOICE_DIR / "en-us-ryan-high.onnx"
GAMEPLAY = Path(os.environ.get("STORY_GAMEPLAY") or ROOT / "assets" / "gameplay")
FONT = ROOT / "fonts" / "Anton-Regular.ttf"
W, H, FPS = 1080, 1920, 30      # design canvas (card, caption positions)
OUT_W, OUT_H = 720, 1280        # encoded size
PITCH = 0.93          # lower = deeper voice (tempo is restored, so timings don't move)
PACE = 1.08           # piper length_scale: >1 = slower, more deliberate
GAP, PARA_GAP, TITLE_GAP = 0.28, 0.6, 0.7


def ff():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"ffmpeg failed:\n{r.stderr[-1500:]}")
    return r


def duration(path):
    r = subprocess.run([ff(), "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0


# ----------------------------------------------------------------- voice

def load_voice():
    if not VOICE_FILE.exists():
        VOICE_DIR.mkdir(parents=True, exist_ok=True)
        tgz = VOICE_DIR / "voice.tgz"
        print("downloading narrator voice (~100 MB, once)...", flush=True)
        urllib.request.urlretrieve(VOICE_URL, tgz)
        with tarfile.open(tgz) as t:
            t.extractall(VOICE_DIR, filter="data")
        tgz.unlink()
    try:
        from piper import PiperVoice
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "piper-tts"], check=True)
        from piper import PiperVoice
    return PiperVoice.load(str(VOICE_FILE))


def split_sentences(text):
    out = []
    for para in re.split(r"\n\s*\n", text.strip()):
        para = re.sub(r"\s+", " ", para).strip()
        sents = [s.strip() for s in re.split(r'(?<=[.!?…])["”’]?\s+(?=[A-Z"“‘\'(])', para) if s.strip()]
        for i, s in enumerate(sents):
            out.append((s, i == len(sents) - 1))  # (sentence, ends a paragraph)
    return out


def speak(voice, sentence, rate):
    from piper import SynthesisConfig
    cfg = SynthesisConfig(length_scale=PACE)
    audio = np.concatenate([c.audio_float_array for c in voice.synthesize(sentence, syn_config=cfg)])
    loud = np.where(np.abs(audio) > 0.02 * (np.abs(audio).max() or 1))[0]
    s0, s1 = (loud[0], loud[-1]) if len(loud) else (0, len(audio) - 1)
    return audio, s0 / rate, s1 / rate


def word_weights(voice, words):
    ws = []
    for w in words:
        core = re.sub(r"[^\w'’-]", "", w) or w
        try:
            n = sum(len(p) for p in voice.phonemize(core))
        except Exception:
            n = 0
        ws.append(max(n, len(core) * 0.8, 1))
    return ws


def narrate(voice, title, story):
    """Returns (float audio @rate, rate, words[{w,s,e}], title_end_seconds)."""
    rate = voice.config.sample_rate
    pieces, words, t = [], [], 0.0
    parts = [(s, False, True) for s, _ in split_sentences(title)]
    parts += [(s, p, False) for s, p in split_sentences(story)]
    title_end = 0.0
    for i, (sent, para_end, is_title) in enumerate(parts):
        audio, a0, a1 = speak(voice, sent, rate)
        toks = sent.split()
        ws = word_weights(voice, toks)
        span, acc = max(a1 - a0, 0.2), 0.0
        for tok, wt in zip(toks, ws):
            s = t + a0 + span * acc / sum(ws)
            acc += wt
            words.append({"w": tok, "s": s, "e": t + a0 + span * acc / sum(ws), "title": is_title})
        pieces.append(audio)
        t += len(audio) / rate
        last_title = is_title and (i + 1 == len(parts) or not parts[i + 1][2])
        gap = TITLE_GAP if last_title else (PARA_GAP if para_end else GAP)
        if last_title:
            title_end = t + gap * 0.6
        pieces.append(np.zeros(int(gap * rate), dtype=np.float32))
        t += gap
    return np.concatenate(pieces), rate, words, title_end


def write_wav(path, audio, rate):
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


# ----------------------------------------------------------------- visuals

def title_card(title, handle, out_png):
    from PIL import Image, ImageDraw, ImageFont
    card_w, pad = 940, 54
    f_title = ImageFont.truetype(str(FONT), 66)
    f_small = ImageFont.truetype(str(FONT), 34)
    words, lines, cur = title.split(), [], ""
    for wd in words:  # wrap to the card width
        test = (cur + " " + wd).strip()
        if f_title.getlength(test) > card_w - 2 * pad and cur:
            lines.append(cur)
            cur = wd
        else:
            cur = test
    lines.append(cur)
    line_h = 82
    card_h = pad + 60 + 26 + line_h * len(lines) + pad
    img = Image.new("RGBA", (W, card_h + 40), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x0 = (W - card_w) // 2
    d.rounded_rectangle([x0, 20, x0 + card_w, 20 + card_h], 34, fill=(14, 14, 16, 238),
                        outline=(120, 12, 12, 255), width=4)
    d.ellipse([x0 + pad, 20 + pad, x0 + pad + 56, 20 + pad + 56], fill=(150, 10, 10, 255))
    d.text((x0 + pad + 28, 20 + pad + 28), "R", font=f_small, fill=(255, 255, 255, 255), anchor="mm")
    d.text((x0 + pad + 76, 20 + pad + 28), handle, font=f_small, fill=(170, 170, 175, 255), anchor="lm")
    y = 20 + pad + 60 + 26
    for ln in lines:
        d.text((x0 + pad, y), ln, font=f_title, fill=(245, 245, 245, 255))
        y += line_h
    img.save(out_png)


def ass_time(t):
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def clean(w):
    w = re.sub(r"[^\w'’?!%$&/-]", "", w.upper())
    return w.replace("\\", "").replace("{", "").replace("}", "")


def captions_ass(words, start_after, total):
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Anton,124,&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,-1,0,0,0,100,100,1,0,1,8,4,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    body = [w for w in words if not w["title"] and w["s"] >= start_after - 0.05]
    chunks, cur = [], []
    for i, w in enumerate(body):
        cur.append(w)
        nxt = body[i + 1] if i + 1 < len(body) else None
        if (len(cur) >= 3 or sum(len(x["w"]) for x in cur) >= 14 or nxt is None
                or re.search(r"[.?!,;:…]$", w["w"]) or (nxt and nxt["s"] - w["e"] > 0.3)):
            chunks.append(cur)
            cur = []
    ev = []
    for ci, ch in enumerate(chunks):
        nxt_s = chunks[ci + 1][0]["s"] if ci + 1 < len(chunks) else min(total, ch[-1]["e"] + 0.6)
        end = nxt_s if nxt_s - ch[-1]["e"] < 0.6 else ch[-1]["e"] + 0.25
        toks = [clean(x["w"]) for x in ch]
        for j, w in enumerate(ch):
            s = ch[0]["s"] if j == 0 else w["s"]
            e = ch[j + 1]["s"] if j + 1 < len(ch) else end
            if e <= s:
                continue
            txt = " ".join(f"{{\\c&H2A2AE6&}}{t}{{\\c&HFFFFFF&}}" if k == j else t for k, t in enumerate(toks))
            pop = "\\fscx85\\fscy85\\t(0,80,\\fscx100\\fscy100)" if j == 0 else ""
            ev.append(f"Dialogue: 0,{ass_time(s)},{ass_time(e)},Cap,,0,0,0,,{{\\an5\\pos(540,1080){pop}}}{txt}")
    return head + "\n".join(ev) + "\n"


def pick_background(total, rng):
    clips = sorted(GAMEPLAY.glob("*.mp4"))
    if not clips:
        sys.exit(f"no gameplay footage in {GAMEPLAY}")
    credits = {}
    cf = GAMEPLAY / "credits.json"
    if cf.exists():
        credits = json.loads(cf.read_text(encoding="utf-8"))
    durs = {c: duration(c) for c in clips}
    long_enough = [c for c in clips if durs[c] >= total + 1] or clips
    c = rng.choice(long_enough)
    start = rng.uniform(0, max(0.0, durs[c] - total - 1))
    credit = next((v for k, v in credits.items() if c.name.startswith(k)), None)
    return c, start, durs[c] < total + 1, credit


# ----------------------------------------------------------------- make

def slugify(t, n=40):
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:n].strip("-") or "story"


def cmd_make(a):
    story = json.loads(Path(a.story).read_text(encoding="utf-8"))
    title, text = story["title"].strip(), story["story"].strip()
    rng = random.Random(a.seed if a.seed is not None else hash(title))
    tmp = WORK / "story_tmp" / slugify(title)
    tmp.mkdir(parents=True, exist_ok=True)

    voice = load_voice()
    audio, rate, words, title_end = narrate(voice, title, text)
    total = len(audio) / rate + 1.2
    write_wav(tmp / "voice_raw.wav", audio, rate)

    card = tmp / "card.png"
    title_card(title, a.handle, card)
    (tmp / "caps.ass").write_text(captions_ass(words, title_end, total), encoding="utf-8")
    fonts = tmp / "fonts"
    fonts.mkdir(exist_ok=True)
    (fonts / FONT.name).write_bytes(FONT.read_bytes())

    bg, bg_start, loop, credit = pick_background(total, rng)
    voice_f = (f"[1:a]asetrate={rate}*{PITCH},aresample=44100,atempo={1 / PITCH:.4f},"
               f"highpass=f=70,lowpass=f=7200,aecho=0.8:0.55:55|110:0.16|0.09,volume=1.7,apad[vo]")
    amb_f = (f"anoisesrc=color=brown:amplitude=0.06:sample_rate=44100:d={total:.2f},lowpass=f=320[n];"
             f"sine=f=55:sample_rate=44100:d={total:.2f},volume=0.05[s1];"
             f"sine=f=58.2:sample_rate=44100:d={total:.2f},volume=0.04[s2];"
             f"[n][s1][s2]amix=inputs=3:normalize=0,afade=t=in:d=1.5,"
             f"afade=t=out:st={max(0, total - 2):.2f}:d=2,volume=9.0[amb]")
    # Layout is designed at 1080x1920; the file is encoded at OUT_W x OUT_H (720p keeps files ~15 MB,
    # the gameplay is 720p anyway, and libass scales the captions from PlayRes automatically).
    k = OUT_W / W
    vid_f = (f"[0:v]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H},fps={FPS},"
             f"eq=brightness=-0.07:saturation=0.75:contrast=1.05,vignette=PI/3.2,setsar=1[bg];"
             f"[2:v]format=rgba,scale=iw*{k:.4f}:-1,"
             f"fade=t=out:st={max(0.1, title_end - 0.35):.2f}:d=0.35:alpha=1[card];"
             f"[bg][card]overlay=(W-w)/2:(H-h)/2-{140 * k:.0f}:enable='lte(t,{title_end:.2f})'[v1];"
             f"[v1]subtitles=caps.ass:fontsdir=fonts[v]")
    out_dir = Path(a.out) if Path(a.out).is_absolute() else ROOT / a.out
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    n = len(list(out_dir.glob(f"{stamp}_*.mp4"))) + 1
    out = out_dir / f"{stamp}_{n:02d}_{slugify(title)}.mp4"
    bg_in = (["-stream_loop", "-1"] if loop else []) + ["-ss", f"{bg_start:.2f}", "-i", str(bg)]
    cmd = [ff(), "-y", "-v", "error", *bg_in, "-i", "voice_raw.wav", "-loop", "1", "-i", "card.png",
           "-filter_complex", f"{vid_f};{voice_f};{amb_f};[vo][amb]amix=inputs=2:normalize=0:duration=first[a]",
           "-map", "[v]", "-map", "[a]", "-t", f"{total:.2f}",
           "-c:v", "libx264", "-preset", a.preset, "-crf", "23", "-maxrate", "1800k", "-bufsize", "3600k",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
           "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, cwd=tmp, check=False, capture_output=True, text=True)
    if not out.exists() or out.stat().st_size < 100_000:
        r = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True)
        sys.exit(f"render failed:\n{r.stderr[-1500:]}")
    side = {"title": title, "campaign": a.campaign, "kind": "story",
            "source_title": "Original story, AI-narrated", "duration": round(total, 2),
            "transcript": f"{title} {text}", "ai_generated": True,
            "credit": f"Gameplay: {credit}" if credit else None,
            "background": f"{bg.name}@{bg_start:.0f}s"}
    out.with_suffix(".json").write_text(json.dumps(side, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "video": str(out.relative_to(ROOT) if out.is_relative_to(ROOT) else out),
                      "duration": round(total, 1), "size_mb": round(out.stat().st_size / 1e6, 1),
                      "words": len(text.split()), "background": side["background"]}))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("story")
    m.add_argument("--out", default="queue")
    m.add_argument("--campaign", default="scary-stories")
    m.add_argument("--handle", default="@mediarawcut")
    m.add_argument("--seed", type=int)
    m.add_argument("--preset", default="veryfast")
    m.set_defaults(fn=cmd_make)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
