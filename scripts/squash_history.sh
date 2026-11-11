#!/usr/bin/env bash
# Replace the repo's history with a single commit of the current files, so videos that
# cleanup.py already deleted stop taking up space on GitHub. Current files (queue/, media/,
# assets/) are untouched, so every link Buffer uses keeps working.
#   bash scripts/squash_history.sh
set -euo pipefail
cd "$(dirname "$0")/.."
git fetch -q origin main
git pull -q --rebase origin main
git add -A
git commit -q -m "Evening $(date +%F)" || true
git push -q origin HEAD:main
before=$(git rev-list --count HEAD)
remote=$(git rev-parse origin/main)
git checkout -q --orphan squash-tmp
git add -A
git commit -q -m "Squash history $(date +%F)

Co-Authored-By: Claude <noreply@anthropic.com>"
# --force-with-lease: refuse if someone pushed since we fetched (never overwrite new work)
git push -q --force-with-lease=main:"$remote" origin squash-tmp:main
git checkout -q -B main squash-tmp
git branch -q -D squash-tmp
git branch -q -u origin/main main 2>/dev/null || true
echo "history squashed: $before commits -> 1"
