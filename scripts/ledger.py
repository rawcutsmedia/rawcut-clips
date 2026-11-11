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
    p.add_argument("--tags", help='hashtags used, e.g. "#ad #boxabl #tinyhouse"')
    u = sub.add_parser("update")
    u.add_argument("--post-id", required=True)
    u.add_argument("--status")
    u.add_argument("--link")
    u.add_argument("--error")
    u.add_argument("--views", type=int)
    sub.add_parser("pending")
    sub.add_parser("summary")
    s = sub.add_parser("stats", help="views + estimated earnings for recent sent posts")
    s.add_argument("--days", type=int, default=7)
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
                     "tags": (a.tags or "").lower().split(),
                     "created_at": now.isoformat(timespec="seconds")})
        write(rows)
        print("added")
    elif a.cmd == "update":
        hit = False
        for r in rows:
            if r.get("post_id") == a.post_id:
                hit = True
                for k in ("status", "link", "error", "views"):
                    v = getattr(a, k)
                    if v is not None:
                        r[k] = v
                if a.views is not None:
                    r["views_at"] = now.isoformat(timespec="seconds")
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
    elif a.cmd == "stats":
        camps = json.loads((ROOT / "campaigns.json").read_text(encoding="utf-8"))["campaigns"]
        recent = [r for r in rows if r.get("status") == "sent" and r.get("due_at")
                  and (now - datetime.fromisoformat(r["due_at"])).days < a.days]

        def earn(r):  # campaign pay rule: rate per 1k views, per-post minimum and cap
            c = camps.get(r.get("campaign"), {})
            if not c.get("paid") or not r.get("views"):
                return 0.0
            usd = r["views"] / 1000 * c.get("rate_per_1k", 0)
            if usd < c.get("min_payout_per_post", 0):
                return 0.0
            return min(usd, c.get("max_payout_per_post", usd))

        for r in recent:
            r["est_usd"] = round(earn(r), 2)
            r["when"] = datetime.fromisoformat(r["due_at"]).strftime("%a %I:%M %p")
        measured = [r for r in recent if r.get("views") is not None]
        ranked = sorted(measured, key=lambda r: r["views"], reverse=True)
        keep = ("platform", "title", "views", "est_usd", "when", "link")
        by_tag = {}
        for r in measured:
            for t in r.get("tags") or []:
                by_tag.setdefault(t, []).append(r["views"])
        tag_avg = sorted(((t, round(sum(v) / len(v)), len(v)) for t, v in by_tag.items()
                          if 3 <= len(v) < len(measured)),  # skip tags used on every post (#ad)
                         key=lambda x: x[1], reverse=True)
        print(json.dumps({
            "days": a.days, "posts_sent": len(recent), "posts_measured": len(measured),
            "total_views": sum(r["views"] for r in measured),
            "est_earnings_usd": round(sum(r["est_usd"] for r in recent), 2),
            "posts_over_min_payout": sum(1 for r in recent if r["est_usd"] > 0),
            "top": [{k: r.get(k) for k in keep} for r in ranked[:5]],
            "bottom": [{k: r.get(k) for k in keep} for r in ranked[-5:]] if len(ranked) > 5 else [],
            "hashtag_avg_views": [{"tag": t, "avg_views": v, "posts": n} for t, v, n in tag_avg],
        }, indent=2))


if __name__ == "__main__":
    main()
