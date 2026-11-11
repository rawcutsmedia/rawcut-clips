#!/usr/bin/env python3
"""
Delete video files whose posts are all finished, to keep the repo small.

  python scripts/cleanup.py [--dry-run]

A file in media/ is removed only when every ledger entry that uses it has a final
status (sent / error / deleted) and its posting time is more than `cleanup_after_days`
days ago. Drafts and scheduled posts are never touched. Files with no ledger entry
at all are kept.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, load_config  # noqa: E402
from ledger import read  # noqa: E402

FINAL = {"sent", "error", "deleted"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    cfg = load_config()
    tz = ZoneInfo(cfg.get("timezone", "America/New_York"))
    cutoff = datetime.now(tz) - timedelta(days=float(cfg.get("cleanup_after_days", 2)))

    by_media = {}
    for r in read():
        by_media.setdefault(r.get("media"), []).append(r)

    removed, freed = [], 0
    for media, rows in by_media.items():
        path = ROOT / media if media else None
        if not path or not path.exists():
            continue
        done = all(r.get("status") in FINAL for r in rows)
        old = all(r.get("due_at") and datetime.fromisoformat(r["due_at"]) < cutoff for r in rows)
        if done and old:
            freed += path.stat().st_size
            removed.append(media)
            if not a.dry_run:
                path.unlink()
                side = path.with_suffix(".json")
                if side.exists():
                    side.unlink()
    for d in sorted((ROOT / "media").glob("*/")):
        if d.is_dir() and not any(d.iterdir()) and not a.dry_run:
            d.rmdir()
    print(json.dumps({"removed": removed, "freed_mb": round(freed / 1e6, 1),
                      "dry_run": a.dry_run}, indent=2))


if __name__ == "__main__":
    main()
