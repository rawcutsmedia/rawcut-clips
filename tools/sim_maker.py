#!/usr/bin/env python3
"""
Make an original physics-simulation short: 1080x1920, 60 fps, with its own synthesized sound.
Everything is drawn and voiced by this script, so there is no outside footage, music or AI voice.

  python tools/sim_maker.py auto [--count 3] [--out queue]      # pick fresh matchups and make them
  python tools/sim_maker.py make --matchup gta6-vs-gta5 [--format battle|escape] [--seed N]
                                  [--out queue] [--no-series]
  python tools/sim_maker.py list                                 # matchups, rounds played, last used

Formats
  battle  "Last ball standing": balls bounce in a round arena; a hit takes HP off the ball that
          was hit, more for a harder hit. After ~22 s hits get stronger and the arena shrinks.
  escape  "First to escape wins": balls fall through nested spinning rings with one gap each;
          the first ball out of the outer ring wins. After ~22 s the gaps slowly widen.

The winner is decided by the physics and a random seed. Nothing is chosen in advance, and seeds
are only re-rolled to keep the video length in range (the result is never looked at).

Matchups live in sims/matchups.json. Each render appends to sims/log.jsonl and updates the
running series score in sims/series.json ("Round 3, GTA 6 leads 2-1"). Output:
<out>/<YYYYMMDD-HHMMSS>_sim_<slug>.mp4 plus a .json sidecar (campaign "sim-battles", kind "sim").
Needs: pip install pygame-ce numpy imageio-ffmpeg
"""
import argparse
import json
import math
import os
import random
import subprocess
import sys
import tempfile
import wave
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from common import ffmpeg_exe, load_config, slugify  # noqa: E402

W, H, FPS, SUB = 1080, 1920, 60, 4
CX, CY = 540, 1010          # arena centre: clear of the title above and TikTok's caption below
SR = 44100
BG_TOP, BG_BOT = (20, 20, 38), (6, 6, 12)
FONT = str(ROOT / "fonts" / "Anton-Regular.ttf")
SIMS = ROOT / "sims"
WIN_HOLD = 2.8               # seconds of winner card at the end
LEN_RANGE = (14.0, 42.0)     # keep videos short: completion and rewatches drive reach


# ---------------------------------------------------------------- helpers
def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(c1, c2, a):
    """a=0 -> c1, a=1 -> c2"""
    return tuple(int(c1[i] + (c2[i] - c1[i]) * a) for i in range(3))


def lum(c):
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


class Ball:
    def __init__(s, i, name, color, x, y, vx, vy, r):
        s.i, s.name, s.color = i, name, hex2rgb(color)
        s.x, s.y, s.vx, s.vy, s.r = x, y, vx, vy, r
        s.hp, s.alive, s.level = 100.0, True, 0

    def speed(s):
        return math.hypot(s.vx, s.vy)

    def set_speed(s, v):
        sp = s.speed() or 1.0
        s.vx, s.vy = s.vx / sp * v, s.vy / sp * v

    def rotate(s, a):
        c, n = math.cos(a), math.sin(a)
        s.vx, s.vy = s.vx * c - s.vy * n, s.vx * n + s.vy * c


def collide_pairs(balls, f, cool, events, damage_mult=None):
    """Equal-mass elastic collisions. With damage_mult, each ball loses HP in proportion to how
    fast the other ball was moving into it (so the faster, more direct hitter deals more)."""
    alive = [b for b in balls if b.alive]
    for ai in range(len(alive)):
        for bi in range(ai + 1, len(alive)):
            a, b = alive[ai], alive[bi]
            dx, dy = b.x - a.x, b.y - a.y
            dist = math.hypot(dx, dy)
            if dist <= 0 or dist >= a.r + b.r:
                continue
            nx, ny = dx / dist, dy / dist
            ov = (a.r + b.r - dist) / 2
            a.x, a.y, b.x, b.y = a.x - nx * ov, a.y - ny * ov, b.x + nx * ov, b.y + ny * ov
            an, bn = a.vx * nx + a.vy * ny, b.vx * nx + b.vy * ny
            if an - bn <= 0:
                continue
            ua, ub = an, -bn           # each ball's speed towards the other
            a.vx, a.vy = a.vx + (bn - an) * nx, a.vy + (bn - an) * ny
            b.vx, b.vy = b.vx + (an - bn) * nx, b.vy + (an - bn) * ny
            key = (a.i, b.i)
            if cool.get(key, -99) > f - 6:
                continue
            cool[key] = f
            px, py = a.x + nx * a.r, a.y + ny * a.r
            if damage_mult is None:
                events.append((f, "tap", {"x": px, "y": py, "v": an - bn}))
                continue
            dmg_b = round(max(3.0, ua) * 1.3 * damage_mult)
            dmg_a = round(max(3.0, ub) * 1.3 * damage_mult)
            a.hp -= dmg_a
            b.hp -= dmg_b
            events.append((f, "hit", {"x": px, "y": py, "v": an - bn, "i": a.i, "j": b.i,
                                      "di": dmg_a, "dj": dmg_b}))


# ---------------------------------------------------------------- simulations
def sim_battle(sides, rng, max_s=LEN_RANGE[1] + WIN_HOLD):
    n = len(sides)
    r = {2: 96, 3: 82, 4: 72, 5: 64}.get(n, 56)
    R = 470.0
    a0 = rng.uniform(0, 2 * math.pi)
    balls = []
    for i, s in enumerate(sides):
        a = a0 + 2 * math.pi * i / n
        ang, sp = rng.uniform(0, 2 * math.pi), rng.uniform(9, 12)
        balls.append(Ball(i, s["name"], s["color"], CX + 230 * math.cos(a), CY + 230 * math.sin(a),
                          sp * math.cos(ang), sp * math.sin(ang), r))
    frames, events, cool = [], [], {}
    f, end_f, winner = 0, None, None
    while True:
        t = f / FPS
        mult = 1.0 if t < 22 else 1.6 if t < 32 else 2.6
        if t > 26:
            R = max(300.0, R - 0.3)
        for _ in range(SUB):
            for b in balls:
                if b.alive:
                    b.x += b.vx / SUB
                    b.y += b.vy / SUB
            for b in balls:
                if not b.alive:
                    continue
                dx, dy = b.x - CX, b.y - CY
                d = math.hypot(dx, dy) or 1e-6
                if d + b.r > R:
                    nx, ny = dx / d, dy / d
                    b.x, b.y = CX + nx * (R - b.r), CY + ny * (R - b.r)
                    vn = b.vx * nx + b.vy * ny
                    if vn > 0:
                        b.vx, b.vy = b.vx - 2 * vn * nx, b.vy - 2 * vn * ny
                        b.rotate(rng.uniform(-0.12, 0.12))
                        events.append((f, "wall", {"i": b.i, "x": CX + nx * R, "y": CY + ny * R, "v": vn}))
            collide_pairs(balls, f, cool, events, damage_mult=mult)
            alive = [b for b in balls if b.alive]
            dead = [b for b in alive if b.hp <= 0]
            if dead:
                if len(dead) == len(alive):       # everyone hit zero at once: most HP left survives
                    keep = max(dead, key=lambda b: b.hp)
                    keep.hp = 1
                    dead.remove(keep)
                for b in dead:
                    b.alive, b.hp = False, 0
                    events.append((f, "pop", {"i": b.i, "x": b.x, "y": b.y}))
        for b in balls:
            if b.alive:
                b.set_speed(min(15.0 + 2 * (mult - 1), max(8.5, b.speed())))
        frames.append({"balls": [(b.x, b.y, b.r, max(0, math.ceil(b.hp)), b.alive, 0) for b in balls],
                       "arena": R})
        f += 1
        alive = [b for b in balls if b.alive]
        if winner is None and len(alive) == 1:
            winner, end_f = alive[0], f
            events.append((f, "win", {"i": winner.i}))
        if winner is not None and f >= end_f + int(WIN_HOLD * FPS):
            break
        if f > max_s * FPS:
            return None
    return {"format": "battle", "frames": frames, "events": events, "winner": winner.i,
            "win_frame": end_f, "rings": None}


def sim_escape(sides, rng, max_s=LEN_RANGE[1] + WIN_HOLD):
    n_rings = rng.choice([3, 4, 4])
    R_in, R_out = 170.0, 470.0
    radii = [R_in + (R_out - R_in) * k / (n_rings - 1) for k in range(n_rings)]
    spacing = (R_out - R_in) / (n_rings - 1)
    r = min(46.0, spacing / 2 - 9)
    th = 8.0
    rings = []
    for k, R in enumerate(radii):
        sign = 1 if k % 2 == 0 else -1
        rings.append({"R": R, "theta": rng.uniform(0, 2 * math.pi),
                      "omega": sign * rng.uniform(0.8, 1.7) / FPS,
                      "half": max(rng.uniform(0.30, 0.37), math.asin(min(1, (r + 12) / R)) + 0.08)})
    balls = []
    for i, s in enumerate(sides):
        a = rng.uniform(0, 2 * math.pi)
        d = rng.uniform(0, R_in - r - th - 12)
        ang, sp = rng.uniform(0, 2 * math.pi), rng.uniform(6, 9)
        balls.append(Ball(i, s["name"], s["color"], CX + d * math.cos(a), CY + d * math.sin(a),
                          sp * math.cos(ang), sp * math.sin(ang), r))
    g = 0.30
    frames, events, cool = [], [], {}
    f, end_f, winner = 0, None, None
    while True:
        t = f / FPS
        for ring in rings:
            ring["theta"] += ring["omega"]
            if t > 22:
                ring["half"] = min(1.15, ring["half"] + 0.0035)
        for _ in range(SUB):
            for b in balls:
                if not b.alive:
                    continue
                b.vy += g / SUB
                b.x += b.vx / SUB
                b.y += b.vy / SUB
                dx, dy = b.x - CX, b.y - CY
                d = math.hypot(dx, dy) or 1e-6
                nx, ny = dx / d, dy / d
                phi = math.atan2(dy, dx)
                for k, ring in enumerate(rings):
                    R = ring["R"]
                    if abs(d - R) >= r + th / 2:
                        continue
                    delta = abs(wrap(phi - ring["theta"]))
                    if delta > ring["half"]:           # over the solid part of the ring
                        vn = b.vx * nx + b.vy * ny
                        if d < R:
                            b.x, b.y = CX + nx * (R - th / 2 - r), CY + ny * (R - th / 2 - r)
                            hit = vn > 0
                        else:
                            b.x, b.y = CX + nx * (R + th / 2 + r), CY + ny * (R + th / 2 + r)
                            hit = vn < 0
                        if hit:
                            b.vx, b.vy = b.vx - 2 * vn * nx, b.vy - 2 * vn * ny
                            b.rotate(rng.uniform(-0.05, 0.05))
                            events.append((f, "wall", {"i": b.i, "x": CX + nx * R, "y": CY + ny * R,
                                                       "v": abs(vn), "ring": k}))
                    else:                               # inside the gap: only its two end caps can hit
                        for side in (-1, 1):
                            ea = ring["theta"] + side * ring["half"]
                            ex, ey = CX + R * math.cos(ea), CY + R * math.sin(ea)
                            ddx, ddy = b.x - ex, b.y - ey
                            dd = math.hypot(ddx, ddy)
                            if 0 < dd < r + th / 2:
                                mx, my = ddx / dd, ddy / dd
                                b.x, b.y = ex + mx * (r + th / 2), ey + my * (r + th / 2)
                                vn = b.vx * mx + b.vy * my
                                if vn < 0:
                                    b.vx, b.vy = b.vx - 2 * vn * mx, b.vy - 2 * vn * my
                                    events.append((f, "wall", {"i": b.i, "x": ex, "y": ey,
                                                               "v": abs(vn), "ring": k}))
                    dx, dy = b.x - CX, b.y - CY
                    d = math.hypot(dx, dy) or 1e-6
                    nx, ny = dx / d, dy / d
                    phi = math.atan2(dy, dx)
            collide_pairs(balls, f, cool, events)
        for b in balls:
            if not b.alive:
                continue
            if winner is None:
                b.set_speed(min(16.0, max(5.5, b.speed())))
            d = math.hypot(b.x - CX, b.y - CY)
            lvl = sum(1 for R in radii if d - b.r > R + th / 2)
            if lvl > b.level and winner is None:
                b.level = lvl
                events.append((f, "ring", {"i": b.i, "level": lvl, "x": b.x, "y": b.y,
                                           "ring": lvl - 1}))
            if winner is None and b.level == n_rings and d > R_out + b.r + 14:
                winner, end_f = b, f
                events.append((f, "win", {"i": b.i, "x": b.x, "y": b.y}))
        frames.append({"balls": [(b.x, b.y, b.r, 0, b.alive, b.level) for b in balls],
                       "rings": [(rg["R"], rg["theta"], rg["half"]) for rg in rings]})
        f += 1
        if winner is not None and f >= end_f + int(WIN_HOLD * FPS):
            break
        if f > max_s * FPS:
            return None
    return {"format": "escape", "frames": frames, "events": events, "winner": winner.i,
            "win_frame": end_f, "n_rings": n_rings, "th": th}


def simulate(fmt, sides, seed):
    """Re-roll the seed (up to 40 times) until the video length is in range. Only the length
    is checked, never who won, so every side keeps a fair chance."""
    for k in range(40):
        rng = random.Random(seed + k * 7919)
        out = (sim_battle if fmt == "battle" else sim_escape)(sides, rng)
        if out is None:
            continue
        secs = out["win_frame"] / FPS
        if LEN_RANGE[0] <= secs <= LEN_RANGE[1]:
            out["seed"] = seed + k * 7919
            return out
    raise RuntimeError("no seed gave a video length in range")


# ---------------------------------------------------------------- sound
def _t(sec):
    return np.arange(int(sec * SR)) / SR


def tone_pluck(freq, vol):
    t = _t(0.5)
    env = np.exp(-t * 7.5) * np.minimum(1, t / 0.003)
    s = (np.sin(2 * np.pi * freq * t) + 0.35 * np.sin(4 * np.pi * freq * t) * np.exp(-t * 6)
         + 0.12 * np.sin(6 * np.pi * freq * t) * np.exp(-t * 10))
    return (vol * env * s).astype(np.float32)


def tone_bell(freq, vol, dur=1.6):
    t = _t(dur)
    s = np.zeros_like(t)
    for p, a, dcy in ((1, 1, 2.6), (2.76, 0.45, 4.5), (5.4, 0.22, 7), (8.93, 0.1, 11)):
        s += a * np.sin(2 * np.pi * freq * p * t) * np.exp(-t * dcy)
    return (vol * s * np.minimum(1, t / 0.002)).astype(np.float32)


def tone_thump(vol, rng):
    t = _t(0.22)
    f = 150 * np.exp(-t * 9) + 55
    ph = 2 * np.pi * np.cumsum(f) / SR
    s = np.sin(ph) * np.exp(-t * 18) + 0.35 * rng.standard_normal(len(t)) * np.exp(-t * 90)
    return (vol * s).astype(np.float32)


def tone_pop(vol, rng):
    t = _t(0.45)
    f = 700 * np.exp(-t * 6) + 90
    ph = 2 * np.pi * np.cumsum(f) / SR
    s = 0.7 * np.sin(ph) * np.exp(-t * 7) + 0.6 * rng.standard_normal(len(t)) * np.exp(-t * 14)
    return (vol * s).astype(np.float32)


def add(buf, at, snd):
    at = max(0, at)
    end = min(len(buf), at + len(snd))
    if end > at:
        buf[at:end] += snd[:end - at]


PENTA = [0, 2, 4, 7, 9]
PHRASES = [[0, 2, 4, 7, 4, 2, 5, 9, 7, 4, 2, 4, 0, 2, 4, 9],
           [4, 7, 9, 7, 4, 2, 0, 2, 4, 5, 7, 9, 10, 9, 7, 4],
           [0, 4, 7, 9, 7, 4, 2, 4, 5, 4, 2, 0, 2, 4, 7, 4],
           [7, 5, 4, 2, 4, 7, 9, 7, 10, 9, 7, 4, 5, 4, 2, 0]]


def scale_freq(root, step):
    octave, idx = divmod(step, 5)
    return root * 2 ** (octave + PENTA[idx] / 12)


def make_audio(sim, wav_path, seed):
    rng = np.random.default_rng(seed)
    n_frames = len(sim["frames"])
    buf = np.zeros(int(n_frames / FPS * SR) + SR, np.float32)
    root = 261.63 * 2 ** (random.Random(seed).choice([-3, -2, 0, 2, 3, 5]) / 12)
    phrase = random.Random(seed + 1).choice(PHRASES)
    note, last_pluck = 0, -10
    for f, typ, d in sim["events"]:
        at = int(f / FPS * SR)
        if typ == "wall":
            if f - last_pluck < 4:
                continue
            last_pluck = f
            vol = min(0.42, 0.16 + abs(d.get("v", 8)) / 45)
            add(buf, at, tone_pluck(scale_freq(root, phrase[note % len(phrase)]), vol))
            note += 1
        elif typ == "tap":
            add(buf, at, tone_pluck(scale_freq(root, 10), 0.12))
        elif typ == "hit":
            add(buf, at, tone_thump(min(0.9, 0.35 + d["v"] / 30), rng))
        elif typ == "pop":
            add(buf, at, tone_pop(0.8, rng))
        elif typ == "ring":
            add(buf, at, tone_bell(scale_freq(root, 5 + 2 * d["level"]), 0.5))
        elif typ == "win":
            for k, step in enumerate((5, 7, 9, 10, 12)):
                add(buf, at + int(k * 0.11 * SR), tone_bell(scale_freq(root, step), 0.42, 2.2))
    buf = np.tanh(buf * 1.1) * 0.9
    pcm = (buf * 32767).astype(np.int16)
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


# ---------------------------------------------------------------- drawing
class Text:
    def __init__(s):
        s.fonts, s.cache = {}, {}

    def font(s, size):
        if size not in s.fonts:
            s.fonts[size] = pygame.font.Font(FONT, size)
        return s.fonts[size]

    def render(s, text, size, color=(255, 255, 255), outline=4, ocolor=(0, 0, 0)):
        key = (text, size, color, outline, ocolor)
        if key in s.cache:
            return s.cache[key]
        f = s.font(size)
        base = f.render(text, True, color)
        if outline:
            o = f.render(text, True, ocolor)
            surf = pygame.Surface((base.get_width() + 2 * outline, base.get_height() + 2 * outline),
                                  pygame.SRCALPHA)
            for dx in range(-outline, outline + 1, max(1, outline // 2)):
                for dy in range(-outline, outline + 1, max(1, outline // 2)):
                    if dx * dx + dy * dy <= outline * outline + 1:
                        surf.blit(o, (dx + outline, dy + outline))
            surf.blit(base, (outline, outline))
        else:
            surf = base
        if len(s.cache) > 600:
            s.cache.clear()
        s.cache[key] = surf
        return surf

    def fit(s, text, max_w, size, min_size=18, **kw):
        while size > min_size and s.font(size).size(text)[0] + 2 * kw.get("outline", 4) > max_w:
            size -= 2
        return s.render(text, size, **kw)


def blit_center(surf, img, x, y):
    surf.blit(img, (int(x - img.get_width() / 2), int(y - img.get_height() / 2)))


def background():
    g = np.linspace(0, 1, H)[None, :, None]
    top, bot = np.array(BG_TOP)[None, None, :], np.array(BG_BOT)[None, None, :]
    arr = (top + (bot - top) * g) * np.ones((W, 1, 1))
    yy, xx = np.mgrid[0:W, 0:H]
    glow = np.exp(-(((yy - CX) / 520.0) ** 2 + ((xx - CY) / 620.0) ** 2))[:, :, None]
    arr = np.clip(arr + glow * 14, 0, 255).astype(np.uint8)
    return pygame.surfarray.make_surface(arr)


def thick_arc(surf, color, R, th, a0, a1):
    """Filled, antialiased ring segment from angle a0 to a1 (radians, screen coordinates)."""
    if a1 <= a0:
        return
    steps = max(6, int((a1 - a0) * R / 9))
    outer = [(CX + (R + th / 2) * math.cos(a0 + (a1 - a0) * k / steps),
              CY + (R + th / 2) * math.sin(a0 + (a1 - a0) * k / steps)) for k in range(steps + 1)]
    inner = [(CX + (R - th / 2) * math.cos(a1 - (a1 - a0) * k / steps),
              CY + (R - th / 2) * math.sin(a1 - (a1 - a0) * k / steps)) for k in range(steps + 1)]
    pts = outer + inner
    pygame.draw.polygon(surf, color, pts)
    pygame.draw.aalines(surf, color, True, pts)
    for a in (a0, a1):                                     # rounded end caps
        pygame.draw.aacircle(surf, color, (CX + R * math.cos(a), CY + R * math.sin(a)), th / 2)


def draw_ball(surf, txt, x, y, r, color, label, sub=None, scale=1.0, flash=0.0):
    r = r * scale
    if flash > 0:
        color = mix(color, (255, 255, 255), flash)
    dark = mix(color, (0, 0, 0), 0.45)
    pygame.draw.aacircle(surf, dark, (x, y), r)
    pygame.draw.aacircle(surf, color, (x, y), r - max(3, r * 0.08))
    pygame.draw.aacircle(surf, mix(color, (255, 255, 255), 0.45),
                         (x - r * 0.38, y - r * 0.40), max(3, r * 0.2))
    tc = (255, 255, 255) if lum(color) < 170 else (20, 20, 20)
    oc = (0, 0, 0) if tc[0] > 128 else (255, 255, 255)
    if sub is None:
        img = txt.fit(label, r * 1.75, int(r * 0.62), color=tc, outline=2, ocolor=oc)
        blit_center(surf, img, x, y)
    else:
        img = txt.fit(label, r * 1.7, int(r * 0.46), color=tc, outline=2, ocolor=oc)
        blit_center(surf, img, x, y - r * 0.26)
        img2 = txt.render(str(sub), int(r * 0.52), color=tc, outline=2, ocolor=oc)
        blit_center(surf, img2, x, y + r * 0.32)


def render(sim, meta, out_mp4, wav):
    pygame.init()
    txt = Text()
    surf = pygame.Surface((W, H))
    bg = background()
    sides = meta["sides"]
    colors = [hex2rgb(s["color"]) for s in sides]
    frames, fmt = sim["frames"], sim["format"]
    win_f, winner = sim["win_frame"], sim["winner"]
    ev_by_f = {}
    for f, typ, d in sim["events"]:
        ev_by_f.setdefault(f, []).append((typ, d))
    rng = random.Random(sim["seed"])
    parts, floats, flashes, ripples = [], [], {}, []
    hit_until, shake = {}, 0.0
    scene_copy = pygame.Surface((W, H))
    trails = [[] for _ in sides]
    shade = pygame.Surface((W, H), pygame.SRCALPHA)
    shade.fill((0, 0, 0, 205))

    title = txt.fit(meta["title"].upper(), 980, 104, outline=6)
    question = txt.render(meta["question"].upper(), 62, color=(255, 214, 64), outline=5)
    mode = "LAST BALL STANDING" if fmt == "battle" else "FIRST TO ESCAPE WINS"
    sub = f"{mode}  ·  ROUND {meta['round']}"
    subline = txt.render(sub, 40, color=(200, 200, 215), outline=3)

    ff = ffmpeg_exe()
    tmp_v = Path(out_mp4).with_suffix(".video.mp4")
    proc = subprocess.Popen([ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo",
                             "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                             "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(tmp_v)],
                            stdin=subprocess.PIPE)
    win_img = None
    for f, st in enumerate(frames):
        surf.blit(bg, (0, 0))
        # --- events -> effects
        for typ, d in ev_by_f.get(f, []):
            if typ == "wall" and len(ripples) < 40:
                ripples.append([d["x"], d["y"], colors[d["i"]], 0])
            if typ == "hit":
                hit_until[d["i"]] = hit_until[d["j"]] = f + 7
                shake = max(shake, min(14.0, 3 + d["v"] * 0.6))
                for _ in range(12):
                    a, v = rng.uniform(0, 2 * math.pi), rng.uniform(3, 9)
                    parts.append([d["x"], d["y"], v * math.cos(a), v * math.sin(a), 18, 18,
                                  (255, 240, 200), 4])
                for k, dmg in ((d["i"], d["di"]), (d["j"], d["dj"])):
                    bx, by, br = st["balls"][k][0], st["balls"][k][1], st["balls"][k][2]
                    floats.append([bx + rng.uniform(-20, 20), by - br - 10, f"-{dmg}", 40, 40])
            elif typ == "pop":
                for _ in range(46):
                    a, v = rng.uniform(0, 2 * math.pi), rng.uniform(4, 16)
                    parts.append([d["x"], d["y"], v * math.cos(a), v * math.sin(a), 46, 46,
                                  colors[d["i"]], rng.uniform(5, 11)])
            elif typ == "ring":
                flashes[d["ring"]] = (f, d["i"])
                floats.append([d["x"], d["y"] - 40, f"{d['level']}/{sim['n_rings']}", 45, 45])
            elif typ == "wall" and fmt == "escape":
                flashes.setdefault(("w", d["ring"]), None)
                flashes[("w", d["ring"])] = f
            elif typ == "win":
                for _ in range(90):
                    a, v = rng.uniform(-math.pi, 0), rng.uniform(8, 22)
                    parts.append([W / 2 + rng.uniform(-300, 300), H * 0.55, v * math.cos(a),
                                  v * math.sin(a), 90, 90,
                                  rng.choice([colors[winner], (255, 214, 64), (255, 255, 255)]),
                                  rng.uniform(5, 10)])
        # --- arena
        if fmt == "battle":
            R = st["arena"]
            danger = f / FPS > 22
            acc = (255, 80, 90) if danger else (150, 160, 255)
            if danger:
                acc = mix(acc, (255, 220, 220), 0.35 * (0.5 + 0.5 * math.sin(f / 5)))
            pygame.draw.aacircle(surf, mix(BG_BOT, acc, 0.12), (CX, CY), R + 26, 22)
            pygame.draw.aacircle(surf, mix(BG_BOT, acc, 0.30), (CX, CY), R + 12, 10)
            pygame.draw.aacircle(surf, acc, (CX, CY), R + 4, 5)
            if danger and f < win_f:
                blit_center(surf, txt.render("SUDDEN DEATH", 44, color=(255, 90, 100), outline=3),
                            W / 2, CY - R - 62)
        else:
            nr = len(st["rings"])
            for k, (R, theta, half) in enumerate(st["rings"]):
                base = pygame.Color(0)
                base.hsva = ((200 + 160 * k / max(1, nr - 1)) % 360, 70, 95, 100)
                col = (base.r, base.g, base.b)
                wf = flashes.get(("w", k))
                if wf is not None and f - wf < 6:
                    col = mix(col, (255, 255, 255), 0.55 * (1 - (f - wf) / 6))
                fl = flashes.get(k)
                if fl and f - fl[0] < 18:
                    col = mix(col, colors[fl[1]], 0.8 * (1 - (f - fl[0]) / 18))
                thick_arc(surf, col, R, sim["th"], theta + half, theta + 2 * math.pi - half)
        # --- wall ripples
        for rp in ripples:
            rp[3] += 1
            a = 1 - rp[3] / 16
            if a > 0:
                pygame.draw.aacircle(surf, mix(BG_BOT, rp[2], a), (rp[0], rp[1]), 8 + rp[3] * 4.5,
                                     max(2, int(6 * a)))
        ripples[:] = [rp for rp in ripples if rp[3] < 16]
        # --- trails
        for k, (x, y, r, hp, alive, lvl) in enumerate(st["balls"]):
            tr = trails[k]
            if alive:
                tr.append((x, y, r))
            del tr[:-14]
            if not alive:
                tr.clear()
            for m, (tx, ty, trr) in enumerate(tr[:-1]):
                a = (m + 1) / len(tr)
                pygame.draw.aacircle(surf, mix(BG_BOT, colors[k], 0.35 * a), (tx, ty), trr * (0.35 + 0.5 * a))
        # --- balls
        for k, (x, y, r, hp, alive, lvl) in enumerate(st["balls"]):
            if alive and -100 < x < W + 100 and -100 < y < H + 100:
                fl_amt = max(0, hit_until.get(k, -1) - f) / 7 * 0.75
                draw_ball(surf, txt, x, y, r, colors[k], sides[k]["name"],
                          sub=hp if fmt == "battle" else None, flash=fl_amt)
        # --- particles and floating numbers
        for p in parts:
            p[0] += p[2]
            p[1] += p[3]
            p[3] += 0.35
            p[4] -= 1
            a = max(0.0, p[4] / p[5])
            pygame.draw.aacircle(surf, mix(BG_BOT, p[6], a), (p[0], p[1]), max(1, p[7] * (0.5 + a / 2)))
        parts[:] = [p for p in parts if p[4] > 0]
        for fl in floats:
            fl[1] -= 1.6
            fl[3] -= 1
            a = max(0.0, fl[3] / fl[4])
            img = txt.render(fl[2], 44, color=mix((40, 40, 40), (255, 255, 255), a), outline=3)
            blit_center(surf, img, fl[0], fl[1])
        floats[:] = [fl for fl in floats if fl[3] > 0]
        # --- header: title, question, mode, live legend
        blit_center(surf, title, W / 2, 175)
        blit_center(surf, question, W / 2, 285)
        blit_center(surf, subline, W / 2, 352)
        n = len(sides)
        per_row = n if n <= 3 else math.ceil(n / 2)
        for k in range(n):
            row, colm = divmod(k, per_row)
            cnt = min(per_row, n - row * per_row)
            cw = 960 / cnt
            x0 = 60 + cw * colm + cw / 2
            y0 = 430 + row * 62
            x, y, r, hp, alive, lvl = st["balls"][k]
            val = (str(hp) if fmt == "battle" else f"{lvl}/{sim['n_rings']}")
            c = colors[k] if alive or fmt == "escape" else (80, 80, 90)
            label = txt.fit(f"{sides[k]['name']}  {val}", cw - 60, 40,
                            color=(255, 255, 255) if alive or fmt == "escape" else (110, 110, 120),
                            outline=3)
            pygame.draw.aacircle(surf, c, (x0 - label.get_width() / 2 - 8, y0), 15)
            blit_center(surf, label, x0 + 14, y0)
        # --- winner card
        if f >= win_f + int(0.45 * FPS):
            k = f - (win_f + int(0.45 * FPS))
            surf.blit(shade, (0, 0))
            if win_img is None:
                name = txt.fit(sides[winner]["name"].upper(), 960, 170, color=colors[winner]
                               if lum(colors[winner]) > 60 else (255, 255, 255), outline=8)
                wins = txt.render("WINS!", 140, outline=8)
                win_img = (name, wins)
            sc = min(1.0, 0.55 + k / 14 * 0.45)
            for img, yy in ((win_img[0], 880), (win_img[1], 1060)):
                im = pygame.transform.smoothscale(img, (int(img.get_width() * sc), int(img.get_height() * sc)))
                blit_center(surf, im, W / 2, yy)
            draw_ball(surf, txt, W / 2, 1290 + 12 * math.sin(k / 7), 95, colors[winner],
                      sides[winner]["name"])
            if meta.get("series_line"):
                blit_center(surf, txt.render(meta["series_line"], 46, color=(255, 214, 64), outline=4),
                            W / 2, 1450)
        if shake > 0.6 and f < win_f:
            scene_copy.blit(surf, (0, 0))
            surf.blit(bg, (0, 0))
            surf.blit(scene_copy, (rng.uniform(-shake, shake), rng.uniform(-shake, shake)))
        shake *= 0.78
        proc.stdin.write(pygame.image.tobytes(surf, "RGB"))
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg (video) failed")
    r = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-i", str(tmp_v), "-i", str(wav),
                        "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-ar", "44100", "-c:a", "aac",
                        "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(out_mp4)],
                       capture_output=True, text=True)
    tmp_v.unlink(missing_ok=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg (mux) failed: " + r.stderr[-400:])


# ---------------------------------------------------------------- matchups, series, log
def load_json(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def matchups():
    return {m["id"]: m for m in load_json(SIMS / "matchups.json", {"matchups": []})["matchups"]
            if m.get("active", True)}


def log_rows():
    p = SIMS / "log.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def pick_next(rows, count, rng):
    """Least recently used matchups first, no category twice in a row, formats alternating."""
    ms = list(matchups().values())
    last_used = {}
    for i, r in enumerate(rows):
        last_used[r["matchup"]] = i
    rng.shuffle(ms)
    ms.sort(key=lambda m: (last_used.get(m["id"], -1 - m.get("priority", 0)),))
    picks, last_cat = [], rows[-1]["category"] if rows else None
    last_fmt = rows[-1]["format"] if rows else rng.choice(["battle", "escape"])
    pool = ms[:]
    while len(picks) < count and pool:
        m = next((m for m in pool if m.get("category") != last_cat), pool[0])
        pool.remove(m)
        fmts = m.get("formats") or ["battle", "escape"]
        fmt = next((f for f in fmts if f != last_fmt), fmts[0])
        picks.append((m, fmt))
        last_cat, last_fmt = m.get("category"), fmt
    return picks


def make(m, fmt, out_dir, seed=None, use_series=True):
    sides = m["sides"]
    seed = seed if seed is not None else random.SystemRandom().randrange(1, 10 ** 9)
    series = load_json(SIMS / "series.json", {})
    s = series.get(m["id"], {"rounds": 0, "wins": {}})
    rnd = s["rounds"] + 1
    sim = simulate(fmt, sides, seed)
    win_name = sides[sim["winner"]]["name"]
    if use_series:
        s["rounds"] = rnd
        s["wins"][win_name] = s["wins"].get(win_name, 0) + 1
    wins = s["wins"] if use_series else {win_name: 1}
    if len(sides) == 2:
        a, b = sides[0]["name"], sides[1]["name"]
        series_line = f"SERIES: {a} {wins.get(a, 0)} - {wins.get(b, 0)} {b}".upper()
    else:
        lead = max(wins.items(), key=lambda kv: kv[1])
        series_line = f"MOST WINS: {lead[0]} ({lead[1]})".upper() if rnd > 1 else ""
    meta = {"title": m["title"], "question": m.get("question", "Who wins?"), "sides": sides,
            "round": rnd, "series_line": series_line if rnd > 1 or len(sides) == 2 else ""}
    cfg = load_config()
    now = datetime.now(ZoneInfo(cfg.get("timezone", "America/New_York")))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{now:%Y%m%d-%H%M%S}_sim_{slugify(m['title'] + ' ' + fmt, 40)}"
    mp4 = out_dir / f"{stem}.mp4"
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "a.wav"
        make_audio(sim, wav, sim["seed"])
        render(sim, meta, mp4, wav)
    dur = round(len(sim["frames"]) / FPS, 2)
    hook = f"{m['title']}: {m.get('question', 'Who wins?')}"
    side = {"title": hook, "campaign": "sim-battles", "kind": "sim", "format": fmt,
            "matchup": m["id"], "category": m.get("category"), "sides": [x["name"] for x in sides],
            "winner": win_name, "round": rnd, "series": wins, "seed": sim["seed"],
            "duration": dur, "winner_at": round(sim["win_frame"] / FPS, 2),
            "topic_tag": m.get("tag"), "ai_generated": False, "credit": None, "transcript": ""}
    mp4.with_suffix(".json").write_text(json.dumps(side, indent=1), encoding="utf-8")
    if use_series:   # --no-series is a test render: no series score, no log entry
        series[m["id"]] = s
        (SIMS / "series.json").write_text(json.dumps(series, indent=1), encoding="utf-8")
        with open(SIMS / "log.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"date": now.isoformat(timespec="seconds"), "file": mp4.name,
                                 "matchup": m["id"], "category": m.get("category"), "format": fmt,
                                 "winner": win_name, "round": rnd, "duration": dur}) + "\n")
    return {"file": str(mp4.relative_to(ROOT)) if mp4.is_relative_to(ROOT) else str(mp4),
            "title": hook, "format": fmt, "winner": win_name, "round": rnd, "duration": dur,
            "winner_at": side["winner_at"]}


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    a1 = sp.add_parser("make")
    a1.add_argument("--matchup", required=True)
    a1.add_argument("--format", choices=["battle", "escape"])
    a1.add_argument("--seed", type=int)
    a1.add_argument("--out", default=str(ROOT / "queue"))
    a1.add_argument("--no-series", action="store_true")
    a2 = sp.add_parser("auto")
    a2.add_argument("--count", type=int, default=1)
    a2.add_argument("--out", default=str(ROOT / "queue"))
    sp.add_parser("list")
    a = ap.parse_args()
    if a.cmd == "list":
        series = load_json(SIMS / "series.json", {})
        rows = log_rows()
        last = {r["matchup"]: r["date"][:10] for r in rows}
        for mid, m in matchups().items():
            print(f"{mid:28} {m.get('category', ''):10} rounds={series.get(mid, {}).get('rounds', 0):<3} "
                  f"last={last.get(mid, '-')}")
        return
    if a.cmd == "make":
        ms = matchups()
        if a.matchup not in ms:
            sys.exit(f"unknown matchup {a.matchup}; see: python tools/sim_maker.py list")
        m = ms[a.matchup]
        fmt = a.format or (m.get("formats") or ["battle"])[0]
        print(json.dumps(make(m, fmt, a.out, a.seed, not a.no_series)))
        return
    picks = pick_next(log_rows(), a.count, random.Random())
    for m, fmt in picks:
        print(json.dumps(make(m, fmt, a.out)), flush=True)


if __name__ == "__main__":
    main()
