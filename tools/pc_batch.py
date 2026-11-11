#!/usr/bin/env python3
"""
PC top-up helper: turn footage downloaded on the user's PC into queued clips.

YouTube blocks downloads from the cloud, so the source videos (and their .en.vtt subtitles)
live on the user's PC in Videos/Boxabl. The PC cuts short lossless pieces, the pieces are
staged into the cloud session, and the cloud renders them with clipkit.py.
See "PC top-up job" in CLAUDE.md for the order of steps.

  python tools/pc_batch.py need                       how many clips the queue needs
  python tools/pc_batch.py sources                    usable PC sources, best first
  python tools/pc_batch.py transcript <sid> <vtt>     make projects/<sid>/transcript.txt
  python tools/pc_batch.py words <sid> <from> <to>    word timings (seconds) in a window
  python tools/pc_batch.py cutplan <sid> [<sid>...]   plan slots + print the PC command
  python tools/pc_batch.py render                     render every staged slot (slow)
  python tools/pc_batch.py review                     contact sheet + transcript ends
  python tools/pc_batch.py drop <slot> [...]          reject rendered clips
  python tools/pc_batch.py queue                      copy clips to queue/, record used_ranges
  python tools/pc_batch.py status <sid> <status> [note]
"""
import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("CLIPKIT_WORK", str(Path.home() / "rawcut-work"))
import clipkit as ck  # noqa: E402

WORK = Path(os.environ["CLIPKIT_WORK"])
PLAN = WORK / "pc_plan.json"
RENDER = WORK / "render"
UPLOADS = Path("/mnt/user-data/uploads")
SRC_FILE = ROOT / "sources.json"
MAX_SLOTS = 20


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def save_sources(d):
    SRC_FILE.write_text(json.dumps(d, indent=1) + "\n", encoding="utf-8")


def vid_of(src):
    return Path(src["pc_file"]).stem


def find_upload(name):
    hits = sorted(glob.glob(str(UPLOADS / "**" / name), recursive=True))
    return Path(hits[0]) if hits else None


def cmd_need(a):
    cfg = load(ROOT / "config.json")
    target = cfg["factory"]["min_queue_days"] * cfg["clips_per_day"]
    have = len(list((ROOT / "queue").glob("*.mp4")))
    need = max(0, min(target - have, cfg["factory"]["max_clips_per_run"]))
    print(json.dumps({"queue": have, "target": target, "need": need}))


def cmd_sources(a):
    camps = load(ROOT / "campaigns.json")["campaigns"]
    rows = []
    for s in load(SRC_FILE)["sources"]:
        c = camps.get(s.get("campaign"), {})
        if s.get("pc_file") and c.get("active") and s.get("status") in ("new", "in_progress"):
            rows.append(s)
    rows.sort(key=lambda s: (s.get("priority", 9), len(s.get("used_ranges", []))))
    for s in rows:
        used = sum(e - b for b, e in s.get("used_ranges", []))
        print(f"{s['id']}  [{s['status']}, priority {s.get('priority')}, {len(s.get('used_ranges', []))} clips used "
              f"({used:.0f}s)]  {s['label']}\n    PC subtitles: ~/mnt/Videos/Boxabl/{vid_of(s)}.en.vtt")
    if not rows:
        print("No usable PC sources left. Download more approved footage (see sources.json _pc_footage).")


def cmd_transcript(a):
    src = {s["id"]: s for s in load(SRC_FILE)["sources"]}[a[0]]
    vtt = Path(a[1])
    pdir = ck.PROJECTS / a[0]
    pdir.mkdir(parents=True, exist_ok=True)
    shutil.copy(vtt, pdir / "subs.en.vtt")
    n = ck.write_transcript(pdir, ck.parse_vtt(pdir / "subs.en.vtt"))
    print(f"{n} lines -> {pdir / 'transcript.txt'}")
    print(f"Already used (skip within 10 s of these): {src.get('used_ranges', [])}")
    print(f"Write picks to {pdir / 'clips.json'} as "
          '[{"start": <first word start>, "end": <last word end>, "title": "<8 words max>"}]')


def cmd_words(a):
    w = ck.parse_vtt_words(ck.PROJECTS / a[0] / "subs.en.vtt")
    lo, hi = float(a[1]), float(a[2])
    print(" ".join(f"{x['w']}@{x['s']:.2f}-{x['e']:.2f}" for x in w if lo <= x["s"] <= hi))


PC_CMD = r'''B=$(ls -d "$HOME"/mnt/*/Boxabl 2>/dev/null | head -1); cd "$B" && mkdir -p rawcut-pieces && cat > rawcut-pieces/plan.json <<'EOF'
__PLAN__
EOF
python3 - <<'PY'
import json, subprocess
plan = json.load(open("rawcut-pieces/plan.json")); off = {}
for slot, (vid, s, e) in plan.items():
    src = vid + ".mp4"
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "packet=pts_time,flags", "-of", "csv=p=0", "-read_intervals",
                        f"{max(0, s - 25)}%{s}", src], capture_output=True, text=True)
    ks = [float(l.split(",")[0]) for l in r.stdout.splitlines()
          if "K" in l and l.split(",")[0] not in ("", "N/A")]
    ks = [k for k in ks if k <= max(0.0, s - 2.5)]
    k = ks[-1] if ks else 0.0
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{k + 0.01:.3f}", "-i", src, "-t",
                    f"{e + 3.5 - k:.3f}", "-map", "0:v:0", "-map", "0:a:0", "-c", "copy",
                    "-avoid_negative_ts", "make_zero", f"rawcut-pieces/{slot}.mp4"], check=True)
    off[slot] = k
json.dump(off, open("rawcut-pieces/offsets.json", "w"))
print("cut", len(off), "pieces in", __import__("os").getcwd() + "/rawcut-pieces")
PY'''


def cmd_cutplan(a):
    srcs = {s["id"]: s for s in load(SRC_FILE)["sources"]}
    plan, n = {}, 0
    for sid in a:
        clips = json.loads((ck.PROJECTS / sid / "clips.json").read_text(encoding="utf-8"))
        for c in clips:
            n += 1
            if n > MAX_SLOTS:
                break
            title = c["title"].strip()
            camp = srcs[sid]["campaign"]
            if camp == "boxabl" and "BOXABL" not in title.upper():
                sys.exit(f"title must contain BOXABL: {title}")
            if len(title.split()) > 8 or not 12 <= float(c["end"]) - float(c["start"]) <= 58:
                sys.exit(f"bad clip (title over 8 words or length outside 12-58 s): {c}")
            plan[f"slot{n:02d}"] = {"sid": sid, "vid": vid_of(srcs[sid]), "start": float(c["start"]),
                                   "end": float(c["end"]), "title": title,
                                   "layout": c.get("layout", "blur"), "campaign": camp}
    PLAN.write_text(json.dumps(plan, indent=1), encoding="utf-8")
    pc = {k: [v["vid"], v["start"], v["end"]] for k, v in plan.items()}
    print(f"{len(plan)} clips planned. Run this with device_bash (one call):\n")
    print(PC_CMD.replace("__PLAN__", json.dumps(pc)))
    names = ", ".join(f"rawcut-pieces/{k}.mp4" for k in plan)
    print(f"\nThen stage ~/mnt/Videos/Boxabl/rawcut-pieces/offsets.json and the pieces "
          f"(3 per call): {names}")


def cmd_render(a):
    plan = load(PLAN)
    offs_p = find_upload("offsets.json")
    if not offs_p:
        sys.exit("offsets.json not staged yet")
    offs = load(offs_p)
    srcs = {s["id"]: s for s in load(SRC_FILE)["sources"]}
    camps = load(ROOT / "campaigns.json")["campaigns"]
    RENDER.mkdir(parents=True, exist_ok=True)
    for slot, p in plan.items():
        if (RENDER / f"{slot}.done").exists() or (RENDER / f"{slot}.dropped").exists():
            continue
        piece = find_upload(f"{slot}-b.mp4") or find_upload(f"{slot}.mp4")
        if not piece:
            print(f"{slot}: not staged yet")
            continue
        pdir = ck.PROJECTS / slot
        pdir.mkdir(parents=True, exist_ok=True)
        shutil.copy(ck.PROJECTS / p["sid"] / "subs.en.vtt", pdir / "subs.en.vtt")
        label = re.sub(r"\s*\([^)]*views\)", "", srcs[p["sid"]]["label"])
        (pdir / "project.json").write_text(json.dumps({
            "source": str(piece), "offset": offs[slot], "title": label,
            "url": srcs[p["sid"]]["url"]}), encoding="utf-8")
        (pdir / "clips.json").write_text(json.dumps([{
            "start": p["start"], "end": p["end"], "title": p["title"]}]), encoding="utf-8")
        out = RENDER / slot
        shutil.rmtree(out, ignore_errors=True)
        cmd = [sys.executable, str(ROOT / "tools" / "clipkit.py"), "cut", slot,
               "--campaign", p["campaign"], "--outdir", str(out),
               "--font", str(ROOT / "fonts" / "Anton-Regular.ttf"), "--font-name", "Anton",
               "--layout", p["layout"], "--words", "vtt"]
        fixes = camps.get(p["campaign"], {}).get("caption_fixes")
        if fixes:
            cmd += ["--fix", fixes]
        print(f"{slot}: rendering {p['title']}", flush=True)
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0 and list(out.glob("*.mp4")):
            (RENDER / f"{slot}.done").touch()
        else:
            print(f"{slot}: FAILED\n{(r.stdout + r.stderr)[-800:]}")
    print("render finished")


def rendered():
    plan = load(PLAN)
    for slot, p in plan.items():
        if (RENDER / f"{slot}.done").exists() and not (RENDER / f"{slot}.dropped").exists():
            mp4 = sorted((RENDER / slot).glob("*.mp4"))[0]
            yield slot, p, mp4, load(mp4.with_suffix(".json"))


def cmd_review(a):
    ff = ck.ff()
    shots = []
    for slot, p, mp4, side in rendered():
        words = side["transcript"].split()
        print(f"{slot} {side['duration']:.1f}s {mp4.stat().st_size / 1e6:.1f}MB {side['title']}\n"
              f"   starts: {' '.join(words[:8])} ...\n   ends:   ... {' '.join(words[-8:])}")
        for t in (2, max(3, side["duration"] / 2)):
            png = RENDER / f"{slot}_{int(t)}.png"
            subprocess.run([ff, "-v", "error", "-ss", f"{t:.1f}", "-i", str(mp4), "-frames:v", "1",
                            "-vf", "scale=216:384", "-y", str(png)], check=False)
            if png.exists():
                shots.append(png)
    for i in range(0, len(shots), 10):
        part = shots[i:i + 10]
        print("  (left to right, 2 frames per clip): " + ", ".join(p.stem for p in part))
        sheet = RENDER / f"review_{i // 10 + 1}.png"
        args = [ff, "-v", "error"]
        for s in part:
            args += ["-i", str(s)]
        args += ["-filter_complex", f"hstack=inputs={len(part)}" if len(part) > 1 else "null",
                 "-y", str(sheet)]
        subprocess.run(args, check=False)
        print(f"contact sheet: {sheet}")


def cmd_drop(a):
    for slot in a:
        (RENDER / f"{slot}.dropped").touch()
        print(f"{slot} dropped")


def cmd_queue(a):
    items = list(rendered())
    by_src = {}
    for it in items:
        by_src.setdefault(it[1]["sid"], []).append(it)
    order = []
    while any(by_src.values()):  # mix sources so each day has variety
        for sid in list(by_src):
            if by_src[sid]:
                order.append(by_src[sid].pop(0))
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    q = ROOT / "queue"
    q.mkdir(exist_ok=True)
    data = load(SRC_FILE)
    srcs = {s["id"]: s for s in data["sources"]}
    for n, (slot, p, mp4, side) in enumerate(order, 1):
        label = mp4.stem.split("_", 2)[-1]
        dst = q / f"{stamp}_{n:02d}_{label}.mp4"
        shutil.copy(mp4, dst)
        dst.with_suffix(".json").write_text(json.dumps(side, indent=2), encoding="utf-8")
        s = srcs[p["sid"]]
        s.setdefault("used_ranges", []).append([round(p["start"] - 1, 1), round(p["end"] + 1, 1)])
        if s.get("status") == "new":
            s["status"] = "in_progress"
        print(f"queued {dst.name} ({side['duration']:.0f}s)")
        (RENDER / f"{slot}.dropped").touch()  # don't queue twice
    save_sources(data)
    print(f"{len(order)} clips queued; queue now {len(list(q.glob('*.mp4')))}")


def cmd_status(a):
    data = load(SRC_FILE)
    for s in data["sources"]:
        if s["id"] == a[0]:
            s["status"] = a[1]
            if len(a) > 2:
                s["error"] = " ".join(a[2:])
    save_sources(data)
    print(f"{a[0]} -> {a[1]}")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in [n[4:] for n in dir() if n.startswith("cmd_")]:
        sys.exit(__doc__)
    globals()["cmd_" + sys.argv[1].replace("-", "_")](sys.argv[2:])
