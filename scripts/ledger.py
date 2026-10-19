#!/usr/bin/env python3
"""
Post ledger (log/posts.jsonl): one line per Buffer post.

  python scripts/ledger.py add --platform tiktok --post-id ID --due-at ISO --media media/... \
         --campaign KEY --title "..." [--status scheduled|draft|error] [--error "..."]
  python scripts/ledger.py pending        # posts whose time has passed but have no final status
  python scripts/ledger.py update --post-id ID --status sent [--link URL] [--error "..."]
  python scripts/ledger.py summary        # counts + queue size
"""
import argparse
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, load_config  # noqa: E402

LOG = ROOT / "log" / "posts.jsonl"


def read():
    if not LOG.exists():
        return []
    return [json.loads(l) for l in LOG.read_text(encoding="utf-8").splitlines() if l.strip()]


def write(rows):
    LOG.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add")
    for f in ("--platform", "--post-id", "--due-at", "--media", "--campaign", "--title"):
        p.add_argument(f, required=f in ("--platform", "--due-at", "--media"))
    p.add_argument("--status", default="scheduled")
    p.add_argument("--error")
    u = sub.add_parser("update")
    u.add_argument("--post-id", required=True)
    u.add_argument("--status")
    u.add_argument("--link")
    u.add_argument("--error")
    sub.add_parser("pending")
    sub.add_parser("summary")
    a = ap.parse_args()

    cfg = load_config()
    tz = ZoneInfo(cfg.get("timezone", "America/New_York"))
    now = datetime.now(tz)
    rows = read()

    if a.cmd == "add":
        rows.append({"platform": a.platform, "post_id": a.post_id, "due_at": a.due_at,
                     "media": a.media, "raw_url": cfg["raw_base_url"] + a.media,
                     "campaign": a.campaign, "title": a.title, "status": a.status,
                     "error": a.error, "link": None, "submitted": False,
                     "created_at": now.isoformat(timespec="seconds")})
        write(rows)
        print("added")
    elif a.cmd == "update":
        hit = False
        for r in rows:
            if r.get("post_id") == a.post_id:
                hit = True
                for k in ("status", "link", "error"):
                    v = getattr(a, k)
                    if v is not None:
                        r[k] = v
                r["checked_at"] = now.isoformat(timespec="seconds")
        write(rows)
        print("updated" if hit else "post id not found")
    elif a.cmd == "pending":
        due = [r for r in rows if r.get("status") in ("scheduled", "sending")
               and r.get("due_at") and datetime.fromisoformat(r["due_at"]) <= now]
        print(json.dumps(due, indent=2))
    elif a.cmd == "summary":
        queue = sorted(p.name for p in (ROOT / "queue").glob("*.mp4"))
        counts = {}
        for r in rows:
            counts[r.get("status")] = counts.get(r.get("status"), 0) + 1
        per_day = max(1, int(cfg.get("clips_per_day", 2)))
        print(json.dumps({"queue_clips": len(queue), "queue_days_left": round(len(queue) / per_day, 1),
                          "post_status_counts": counts}, indent=2))


if __name__ == "__main__":
    main()
