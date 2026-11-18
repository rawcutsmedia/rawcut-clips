#!/usr/bin/env python3
"""
Post ledger (log/posts.jsonl): one line per Buffer post.

  python scripts/ledger.py add --platform tiktok --post-id ID --due-at ISO --media media/... \
         --campaign KEY --title "..." [--status scheduled|draft|error] [--error "..."]
  python scripts/ledger.py pending        # posts whose time has passed but have no final status
  python scripts/ledger.py update --post-id ID --status sent [--link URL] [--error "..."]
         [--submitted]   # the link was submitted on Content Rewards
  python scripts/ledger.py summary        # counts + queue size
  python scripts/ledger.py learn --kind stream   # what wins / loses, by feature (self-improvement)

  add ... --features '{"hook_style": "question", "moment": "funny", "seconds": 18, ...}'
  update --post-id ID --metrics '{"views": 1200, "likes": 80, "comments": 6, "shares": 3}'
  update --post-id ID --cr-status approved|rejected|pending [--cr-views N] [--cr-note "why"]
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
    p.add_argument("--features", help="JSON of what this post tried (hook_style, moment, seconds...)")
    u = sub.add_parser("update")
    u.add_argument("--post-id", required=True)
    u.add_argument("--status")
    u.add_argument("--link")
    u.add_argument("--error")
    u.add_argument("--views", type=int)
    u.add_argument("--submitted", action="store_true", help="link submitted on Content Rewards")
    u.add_argument("--metrics", help="JSON of every metric Buffer returned (views, likes, ...)")
    u.add_argument("--cr-status", help="Content Rewards review status: approved/rejected/pending")
    u.add_argument("--cr-views", type=int, help="approved views Content Rewards shows")
    u.add_argument("--cr-note", help="rejection reason or note from Content Rewards")
    lp = sub.add_parser("learn", help="compare posts by feature to find what gets views")
    lp.add_argument("--kind", default="stream", help="stream (GTA clips) or story")
    lp.add_argument("--days", type=int, default=14)
    lp.add_argument("--min-age-hours", type=int, default=24, help="let views settle first")
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
                     "features": json.loads(a.features) if a.features else {},
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
                if a.metrics:
                    m = {k.lower(): v for k, v in json.loads(a.metrics).items()}
                    r["metrics"] = m
                    r["metrics_at"] = now.isoformat(timespec="seconds")
                    v = m.get("views", m.get("plays", m.get("impressions")))
                    if v is not None:
                        r["views"] = int(v)
                        r["views_at"] = now.isoformat(timespec="seconds")
                for k in ("cr_status", "cr_views", "cr_note"):
                    if getattr(a, k) is not None:
                        r[k] = getattr(a, k)
                if a.submitted:
                    r["submitted"] = True
                    r["submitted_at"] = now.isoformat(timespec="seconds")
                r["checked_at"] = now.isoformat(timespec="seconds")
        write(rows)
        print("updated" if hit else "post id not found")
    elif a.cmd == "pending":
        due = [r for r in rows if r.get("status") in ("scheduled", "sending")
               and r.get("due_at") and datetime.fromisoformat(r["due_at"]) <= now]
        print(json.dumps(due, indent=2))
    elif a.cmd == "summary":
        queue, gta = [], []
        for p in sorted((ROOT / "queue").glob("*.mp4")):
            try:
                kind = json.loads(p.with_suffix(".json").read_text(encoding="utf-8")).get("kind")
            except Exception:
                kind = None
            (gta if kind == "stream" else queue).append(p.name)
        counts = {}
        for r in rows:
            counts[r.get("status")] = counts.get(r.get("status"), 0) + 1
        per_day = max(1, int(cfg.get("clips_per_day", 2)))
        gta_day = max(1, int(cfg.get("gta", {}).get("clips_per_night", 4)))
        # queue_clips = stories (what the story factory tops up); GTA stream clips are counted apart
        print(json.dumps({"queue_clips": len(queue), "queue_days_left": round(len(queue) / per_day, 1),
                          "gta_queue_clips": len(gta), "gta_days_left": round(len(gta) / gta_day, 1),
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

    elif a.cmd == "learn":
        camps = json.loads((ROOT / "campaigns.json").read_text(encoding="utf-8"))["campaigns"]

        def kind(r):
            return camps.get(r.get("campaign"), {}).get("kind") or (
                "story" if r.get("campaign") == "scary-stories" else "other")

        rows2 = [r for r in rows if kind(r) == a.kind and r.get("status") == "sent"
                 and r.get("due_at") and r.get("views") is not None
                 and (now - datetime.fromisoformat(r["due_at"])).days < a.days
                 and (now - datetime.fromisoformat(r["due_at"])).total_seconds() >= a.min_age_hours * 3600]

        def eng(r):
            m = r.get("metrics") or {}
            inter = sum(int(m.get(k, 0) or 0) for k in ("likes", "comments", "shares", "saves",
                                                        "reactions", "favorites"))
            return inter / r["views"] if r["views"] else 0.0

        def bucket(name, v):
            if v is None:
                return None
            if name == "seconds":
                v = float(v)
                return "<=15s" if v <= 15 else "16-25s" if v <= 25 else "26-35s" if v <= 35 else "36s+"
            if name == "source_views":
                v = int(v)
                return "<1k" if v < 1000 else "1k-5k" if v < 5000 else "5k+"
            if name == "post_time":
                return str(v)[:2] + ":00"
            return str(v)

        groups = {}
        for r in rows2:
            feats = dict(r.get("features") or {})
            feats.pop("hook", None)
            for k, v in feats.items():
                b = bucket(k, v)
                if b is not None:
                    groups.setdefault(k, {}).setdefault(b, []).append(r)
            for t in r.get("tags") or []:
                groups.setdefault("hashtag", {}).setdefault(t, []).append(r)

        def summ(rs):
            vs = sorted(x["views"] for x in rs)
            appr = [x for x in rs if x.get("cr_status") in ("approved", "rejected")]
            return {"posts": len(rs), "avg_views": round(sum(vs) / len(vs)),
                    "median_views": vs[len(vs) // 2],
                    "engagement_rate_pct": round(100 * sum(eng(x) for x in rs) / len(rs), 2),
                    "approved_pct": round(100 * sum(x["cr_status"] == "approved" for x in appr) / len(appr))
                    if appr else None}

        out = {}
        for k, bs in groups.items():
            table = sorted(((b, summ(rs)) for b, rs in bs.items()
                            if len(rs) < len(rows2) or k != "hashtag"),
                           key=lambda x: x[1]["avg_views"], reverse=True)
            out[k] = [{"value": b, **st} for b, st in table]
        ranked = sorted(rows2, key=lambda r: r["views"], reverse=True)
        brief = lambda r: {"title": r.get("title"), "views": r["views"],
                           "engagement_pct": round(100 * eng(r), 2), "cr_status": r.get("cr_status"),
                           "features": r.get("features"), "link": r.get("link")}
        rejected = [{"title": r.get("title"), "note": r.get("cr_note")} for r in rows2
                    if r.get("cr_status") == "rejected"]
        print(json.dumps({
            "kind": a.kind, "measured_posts": len(rows2),
            "enough_data": len(rows2) >= 8,
            "total_views": sum(r["views"] for r in rows2),
            "avg_views": round(sum(r["views"] for r in rows2) / len(rows2)) if rows2 else 0,
            "by_feature": out,
            "top": [brief(r) for r in ranked[:3]],
            "bottom": [brief(r) for r in ranked[-3:]] if len(ranked) > 3 else [],
            "rejected_by_campaign": rejected,
            "note": "Compare buckets with 3+ posts only; fewer is noise. Views settle after ~24-48h.",
        }, indent=2))


if __name__ == "__main__":
    main()
