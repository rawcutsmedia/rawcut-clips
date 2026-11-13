#!/usr/bin/env python3
"""
Story video maker: turns a written scary story into a vertical narrated video.

  python tools/story_maker.py make <story.json> [--out queue] [--campaign scary-stories] [--seed N]

story.json: {"title": "...", "story": "paragraphs separated by blank lines"}

What it does (all offline, no API keys):
  1. Narrates the title, then each paragraph in one take, with a natural Kokoro voice
     (config.json -> "story"; model downloaded once from GitHub; Piper is the fallback), paced like
     a storyteller: real pauses at punctuation, a beat before the last line, which is read slower.
     A light "narrator mic" EQ and compression, no effects or noise.
  2. Times every word from the model's own phoneme timings (exact caption sync).
  3. Lays it over a random stretch of Minecraft parkour from assets/gameplay/, darkened with a
     vignette. No background music or noise.
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
# model-files-v1.1 is the same Kokoro v1.0 voice, exported so it reports when every sound is spoken
# (exact caption timing, and pauses placed exactly at punctuation).
KOKORO_DIR = WORK / "kokoro-v1.1"
KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/"
KOKORO_FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")
GAMEPLAY = Path(os.environ.get("STORY_GAMEPLAY") or ROOT / "assets" / "gameplay")
FONT = ROOT / "fonts" / "Anton-Regular.ttf"
W, H, FPS = 1080, 1920, 30      # design canvas (card, caption positions)
OUT_W, OUT_H = 720, 1280        # encoded size
PACE = 1.08           # piper fallback only: length_scale >1 = slower
GAP, PARA_GAP, TITLE_GAP = 0.28, 0.5, 0.7


def story_settings():
    """config.json -> "story": {"engine": "kokoro", "voice": "am_michael", "speed": 0.94,
    "sentence_pause": 0.42, "clause_pause": 0.16}. A voice can be a blend: "am_michael*0.7+am_fenrir*0.3"."""
    try:
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8")).get("story", {})
    except Exception:
        cfg = {}
    return {"engine": cfg.get("engine", "kokoro"),
            "voice": os.environ.get("STORY_VOICE") or cfg.get("voice", "am_michael"),
            "speed": float(os.environ.get("STORY_SPEED") or cfg.get("speed", 0.94)),
            "sentence_pause": float(cfg.get("sentence_pause", 0.42)),
            "clause_pause": float(cfg.get("clause_pause", 0.16))}


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

class KokoroNarrator:
    """Natural-sounding neural voice (Kokoro, Apache-2.0), model files from GitHub releases."""

    def __init__(self, voice, speed, sentence_pause=0.42, clause_pause=0.16):
        KOKORO_DIR.mkdir(parents=True, exist_ok=True)
        for f in KOKORO_FILES:
            if not (KOKORO_DIR / f).exists():
                print(f"downloading {f} (once)...", flush=True)
                urllib.request.urlretrieve(KOKORO_URL + f, KOKORO_DIR / f)
        try:
            from kokoro_onnx import Kokoro
        except ImportError:
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "kokoro-onnx==0.6.1"], check=True)
            from kokoro_onnx import Kokoro
        self.k = Kokoro(str(KOKORO_DIR / KOKORO_FILES[0]), str(KOKORO_DIR / KOKORO_FILES[1]))
        self.style = self._voice(voice)
        self.name, self.speed, self.rate = voice, speed, 24000
        self.sentence_pause, self.clause_pause = sentence_pause, clause_pause
        self.timed = bool(getattr(self.k, "has_timings", False)) and hasattr(self.k, "create_timed")

    def _voice(self, spec):
        """"am_michael" or a blend like "am_michael*0.7+am_fenrir*0.3"."""
        parts = []
        for p in spec.split("+"):
            name, _, w = p.strip().partition("*")
            if name not in self.k.get_voices():
                sys.exit(f"unknown Kokoro voice {name}; pick from {sorted(self.k.get_voices())}")
            parts.append((name, float(w or 1)))
        if len(parts) == 1:
            return parts[0][0]
        tot = sum(w for _, w in parts)
        return sum(self.k.get_voice_style(n) * (w / tot) for n, w in parts).astype(np.float32)

    def say(self, sentence, speed=None):
        audio, self.rate = self.k.create(sentence, voice=self.style, speed=speed or self.speed, lang="en-us")
        return np.asarray(audio, dtype=np.float32)

    def say_timed(self, text, speed=None):
        """One take of `text` -> (audio, [(start, end) per whitespace token]) from the model's own
        timings, with pauses lengthened at sentence and clause marks so it breathes like a reader."""
        audio, self.rate, spoken = self.k.create_timed(
            text, voice=self.style, speed=speed or self.speed, lang="en-us",
            sentence_pause=self.sentence_pause, clause_pause=self.clause_pause)
        audio = np.asarray(audio, dtype=np.float32)
        return audio, map_word_times(text.split(), spoken, self.phonemes, len(audio) / self.rate)

    def phonemes(self, word):
        try:
            return self.k.tokenizer.phonemize(word, "en-us")
        except Exception:
            return ""

    def weight(self, word):
        return len(re.sub(r"[ˈˌː\s.,!?]", "", self.phonemes(word)))


class PiperNarrator:
    """Fallback voice (Piper ryan-high)."""

    def __init__(self):
        self.v = load_voice()
        self.rate = self.v.config.sample_rate

    def say(self, sentence):
        from piper import SynthesisConfig
        cfg = SynthesisConfig(length_scale=PACE)
        return np.concatenate([c.audio_float_array for c in self.v.synthesize(sentence, syn_config=cfg)])

    def weight(self, word):
        try:
            return sum(len(p) for p in self.v.phonemize(word))
        except Exception:
            return 0


def get_narrator():
    st = story_settings()
    if st["engine"] == "kokoro":
        try:
            return KokoroNarrator(st["voice"], st["speed"], st["sentence_pause"], st["clause_pause"])
        except SystemExit:
            raise
        except Exception as e:  # keep the factory running if Kokoro can't load
            print(f"Kokoro unavailable ({e}); falling back to Piper", flush=True)
    return PiperNarrator()


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


# --- say numbers and times the way a person would ("3:02 AM" -> "three oh two in the morning")
ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
        "fifteen sixteen seventeen eighteen nineteen").split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([AaPp]\.?\s?[Mm]\.?)(?!\w)|\b(\d{1,2}):(\d{2})\b")
ROOM_RE = re.compile(r"\b([Rr]oom|[Aa]partment|[Uu]nit|[Ss]uite|[Ss]pace|[Cc]abin|[Ss]ite|[Ff]light|"
                     r"[Bb]us|[Rr]oute|[Ll]ot|[Gg]ate)\s+(\d{3,4})\b")


def num_words(n):
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ("" if n % 10 == 0 else "-" + ONES[n % 10])
    return str(n)


def pair_words(two):  # "14" -> "fourteen", "05" -> "oh five", "00" -> "hundred"
    if two == "00":
        return "hundred"
    return "oh " + ONES[int(two[1])] if two[0] == "0" else num_words(int(two))


def say_time(h, m, ampm):
    h, m = int(h), int(m or 0)
    pm = bool(ampm) and ampm.lower().startswith("p")
    if ampm and h == 12 and m == 0:
        return "noon" if pm else "midnight"
    words = num_words(h) + ("" if m == 0 else " " + pair_words(f"{m:02d}"))
    if not ampm:
        return words + (" o'clock" if m == 0 else "")
    if not pm:
        return words + " in the morning"
    return words + (" in the afternoon" if h == 12 or h < 6 else " in the evening" if h < 9 else " at night")


def speech_text(text):
    def time_sub(m):
        if m.group(3):
            rest = m.string[m.end():].lstrip()
            end = "." if m.group(3).endswith(".") and (not rest or rest[0].isupper()) else ""
            return say_time(m.group(1), m.group(2), re.sub(r"[.\s]", "", m.group(3))) + end
        return say_time(m.group(4), m.group(5), None)

    def room_sub(m):
        n = m.group(2)
        head = ONES[int(n[0])] if len(n) == 3 else num_words(int(n[:2]))
        return f"{m.group(1)} {head} {pair_words(n[-2:])}"

    return ROOM_RE.sub(room_sub, TIME_RE.sub(time_sub, text))


def paragraphs(text):
    return [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


def word_weights(voice, words):
    ws = []
    for w in words:
        core = re.sub(r"[^\w'’-]", "", w) or w
        ws.append(max(voice.weight(core), len(core) * 0.8, 1))
    return ws


def align_words(audio, rate, toks, weights):
    """Time each word in one paragraph spoken in a single take. The pauses the voice makes at
    punctuation anchor the timeline; words between two anchors are spread by phoneme count."""
    fr = max(1, int(rate * 0.02))
    n = len(audio) // fr
    if n < 2:
        return [(0.0, len(audio) / rate)] * len(toks)
    rms = np.sqrt(np.mean(audio[: n * fr].reshape(n, fr) ** 2, axis=1))
    voiced = rms > max(rms.max() * 0.06, 1e-4)
    idx = np.where(voiced)[0]
    first, last = (idx[0], idx[-1]) if len(idx) else (0, n - 1)
    gaps, i = [], first
    while i <= last:
        if voiced[i]:
            i += 1
            continue
        j = i
        while j <= last and not voiced[j]:
            j += 1
        if (j - i) * 0.02 >= 0.12:
            if gaps and i * 0.02 - gaps[-1][1] < 0.08:  # a tiny blip splits one pause: merge
                gaps[-1] = (gaps[-1][0], j * 0.02)
            else:
                gaps.append((i * 0.02, j * 0.02))
        i = j
    s0, s1 = first * 0.02, (last + 1) * 0.02
    cum = np.concatenate([[0.0], np.cumsum(weights)])
    total = cum[-1] or 1.0
    bounds = [k + 1 for k, t in enumerate(toks[:-1]) if re.search(r"[.,!?;:…—–\"”')]$", t)]
    speech = max((s1 - s0) - sum(b - a for a, b in gaps), 1e-3)
    anchors, said, prev = [], 0.0, s0
    for a, b in gaps:
        said += a - prev
        prev = b
        want = said / speech * total
        last_k = anchors[-1][0] if anchors else 0
        cands = [k for k in bounds if k > last_k]
        if cands:
            k = min(cands, key=lambda k: abs(cum[k] - want))
            off = abs(cum[k] - want)
            # accept only if close overall AND the words since the last anchor fit the time spent
            if off <= 0.18 * total and off <= 0.5 * (cum[k] - cum[last_k]):
                anchors.append((k, a, b))
    segs, k0, t0 = [], 0, s0
    for k, a, b in anchors:
        segs.append((k0, k, t0, a))
        k0, t0 = k, b
    segs.append((k0, len(toks), t0, s1))
    out = []
    for a_tok, b_tok, ta, tb in segs:
        sw = sum(weights[a_tok:b_tok]) or 1.0
        acc = 0.0
        for j in range(a_tok, b_tok):
            s = ta + (tb - ta) * acc / sw
            acc += weights[j]
            out.append((s, ta + (tb - ta) * acc / sw))
    return out


PUNCT = set(".,!?;:…—–-\"“”'‘’()")
STRESS = set("ˈˌː")


def _sounds(ph):
    """Space-separated phoneme words that contain at least one real sound."""
    return [g for g in ph.split() if any(c not in PUNCT and c not in STRESS for c in g)]


def map_word_times(toks, spoken, phonemize, length):
    """Turn the model's per-phoneme timings into (start, end) for each text token.
    Each word is phonemized on its own and its sounds are matched against the sounds the model
    actually spoke (espeak runs short words together, e.g. "on the" -> "ɔnðə", so word counts
    alone don't line up). Words with no matched sound are placed between their neighbours."""
    import difflib
    flat = [t for t in spoken if t.phoneme not in PUNCT and t.phoneme not in STRESS
            and not t.phoneme.isspace()]
    if not flat:
        return [(0.0, length)] * len(toks)
    exp, owner = [], []
    for i, w in enumerate(toks):
        for ch in "".join(_sounds(phonemize(re.sub(r"[^\w'’-]", "", w) or w))):
            if ch not in STRESS:
                exp.append(ch)
                owner.append(i)
    got = [t.phoneme for t in flat]
    hit = [[] for _ in toks]
    sm = difflib.SequenceMatcher(None, exp, got, autojunk=False)
    for a, b, n in sm.get_matching_blocks():
        for j in range(n):
            hit[owner[a + j]].append(b + j)
    out = [(flat[min(h)].start, flat[max(h)].end) if h else None for h in hit]
    # fill words that matched nothing: share the gap between the neighbours
    i = 0
    while i < len(out):
        if out[i] is not None:
            i += 1
            continue
        j = i
        while j < len(out) and out[j] is None:
            j += 1
        lo = out[i - 1][1] if i > 0 else flat[0].start
        hi = out[j][0] if j < len(out) else flat[-1].end
        step = max(hi - lo, 0.05 * (j - i)) / (j - i)
        for k in range(i, j):
            out[k] = (lo + (k - i) * step, lo + (k - i + 1) * step)
        i = j
    # keep it monotonic (a mismatch must never send a caption backwards)
    fixed, t_prev = [], 0.0
    for s, e in out:
        s = max(s, t_prev)
        e = max(e, s + 0.05)
        fixed.append((s, e))
        t_prev = s
    return fixed


def merge_punct_tokens(text):
    """Glue stand-alone punctuation ("word — word") onto the word before it, so every token is a word."""
    toks = []
    for t in text.split():
        if toks and not re.search(r"\w", t):
            toks[-1] += t
        else:
            toks.append(t)
    return " ".join(toks)


def delivery(blocks, base, rng):
    """How a storyteller would read each block: (speed, pause after). The last short line is read
    slower after a long beat; one-line paragraphs get a beat after them; questions and trailing
    lines hold longer; the middle drifts a little so it never sounds metronomic."""
    plan = []
    n = len(blocks)
    for i, (text, is_title) in enumerate(blocks):
        words = len(text.split())
        last = i == n - 1
        nxt_is_last = i == n - 2
        if is_title:
            speed, gap = base * 0.98, 0.85
        elif last and words <= 10:
            speed, gap = base * 0.86, 0.0
        elif i == 1:
            speed, gap = base * 0.97, 0.6
        else:
            speed, gap = base * rng.uniform(0.98, 1.03), rng.uniform(0.5, 0.65)
        if not is_title and words <= 6 and not last:
            speed, gap = min(speed, base * 0.93), max(gap, 0.75)
        if text.rstrip("\"”'’").endswith(("?", "…", "...")):
            gap = max(gap, 0.75)
        if nxt_is_last:
            gap = 1.05
        plan.append((max(0.5, min(2.0, speed)), gap))
    return plan


def narrate(voice, title, story, rng=None):
    """Speak the title, then each paragraph in one take (so intonation flows like a person
    reading), pacing each block like a storyteller (see delivery()).
    Returns (float audio @rate, rate, words[{w,s,e,title}], title_end_seconds)."""
    rng = rng or random.Random(0)
    voice.say("Hello.")  # sets voice.rate
    rate = voice.rate
    pieces, words, t, title_end = [], [], 0.0, 0.0
    blocks = [(merge_punct_tokens(speech_text(title)), True)] + \
             [(merge_punct_tokens(speech_text(p)), False) for p in paragraphs(story)]
    timed = getattr(voice, "timed", False)
    base = getattr(voice, "speed", 1.0)
    for (text, is_title), (speed, gap) in zip(blocks, delivery(blocks, base, rng)):
        toks = text.split()
        if timed:
            audio, times = voice.say_timed(text, speed)
        else:
            audio = voice.say(text, speed) if isinstance(voice, KokoroNarrator) else voice.say(text)
            times = align_words(audio, rate, toks, word_weights(voice, toks))
        for tok, (s, e) in zip(toks, times):
            words.append({"w": tok, "s": t + s, "e": t + e, "title": is_title})
        pieces.append(audio)
        t += len(audio) / rate
        if is_title:
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

    voice = get_narrator()
    audio, rate, words, title_end = narrate(voice, title, text, rng)
    total = len(audio) / rate + 1.2
    write_wav(tmp / "voice_raw.wav", audio, rate)

    card = tmp / "card.png"
    title_card(title, a.handle, card)
    (tmp / "caps.ass").write_text(captions_ass(words, title_end, total), encoding="utf-8")
    fonts = tmp / "fonts"
    fonts.mkdir(exist_ok=True)
    (fonts / FONT.name).write_bytes(FONT.read_bytes())

    bg, bg_start, loop, credit = pick_background(total, rng)
    # No pitch shifting, echo or background noise (they made it sound robotic). Instead a light
    # "narrator mic" chain: a little low-end warmth, softer sibilants and gentle compression, so it
    # sounds like a person close to a microphone rather than a synthesizer.
    voice_f = ("[1:a]aresample=44100,highpass=f=70,"
               "equalizer=f=150:t=q:w=0.9:g=2.5,equalizer=f=6500:t=q:w=1.5:g=-2,lowpass=f=12000,"
               "acompressor=threshold=-21dB:ratio=2.2:attack=10:release=150:makeup=1.5,"
               "loudnorm=I=-15:TP=-1.5:LRA=9,apad[a]")
    # Layout is designed at 1080x1920; the file is encoded at OUT_W x OUT_H (720p keeps files ~15 MB,
    # the gameplay is 720p anyway, and libass scales the captions from PlayRes automatically).
    k = OUT_W / W
    # Vary the background so videos cut from the same footage don't look alike: random mirror,
    # zoom (0-18%) and crop position.
    flip = "hflip," if rng.random() < 0.5 else ""
    z = rng.uniform(1.0, 1.18)
    zw, zh = int(OUT_W * z) // 2 * 2, int(OUT_H * z) // 2 * 2
    rx, ry = rng.random(), rng.random()
    vid_f = (f"[0:v]{flip}scale={zw}:{zh}:force_original_aspect_ratio=increase,"
             f"crop={OUT_W}:{OUT_H}:(in_w-{OUT_W})*{rx:.2f}:(in_h-{OUT_H})*{ry:.2f},fps={FPS},"
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
           "-filter_complex", f"{vid_f};{voice_f}",
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
