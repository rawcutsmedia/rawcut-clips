#!/usr/bin/env python3
"""
Print the next free posting times for each connected channel.

  python scripts/next_slots.py --count 2

Uses the slot times in config.json (local timezone), skips times already used in
log/posts.jsonl, and only returns times at least 20 minutes from now.
Output: {"tiktok": ["2026-10-20T12:30:00-04:00", ...], "youtube": [...]}
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, load_config  # noqa: E402


def taken_times():
    taken = set()
    log = ROOT / "log" / "posts.jsonl"
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            if line.strip():
                e = json.loads(line)
                if e.get("status") not in ("error", "deleted"):
                    taken.add((e.get("platform"), e.get("due_at")))
    return taken


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=2)
    a = ap.parse_args()
    cfg = load_config()
    tz = ZoneInfo(cfg.get("timezone", "America/New_York"))
    now = datetime.now(tz) + timedelta(minutes=20)
    taken = taken_times()
    out = {}
    for platform, channel_id in cfg["buffer"]["channels"].items():
        if not channel_id:
            continue
        times = sorted(cfg["slots"].get(platform, []))
        picks, day = [], now.date()
        while len(picks) < a.count and day <= now.date() + timedelta(days=14):
            for hhmm in times:
                h, m = map(int, hhmm.split(":"))
                dt = datetime(day.year, day.month, day.day, h, m, tzinfo=tz)
                iso = dt.isoformat(timespec="seconds")
                if dt > now and (platform, iso) not in taken and len(picks) < a.count:
                    picks.append(iso)
            day += timedelta(days=1)
        out[platform] = picks
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
