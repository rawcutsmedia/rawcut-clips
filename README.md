# Rawcut Media: automated scary-story videos

Original scary stories, narrated by a deep AI voice over Minecraft parkour, with big word-by-word
captions. Everything below runs on its own every day:

| When (New York time) | Job | What it does |
|---|---|---|
| ~3:07 AM daily | Clip factory | Writes new original stories (rules in `tools/STORY_PROMPT.txt`) and renders them with `tools/story_maker.py` until 4 days of videos are queued |
| ~6:07 AM daily | Morning job | Takes the next **5** videos from `queue/`, writes a different caption per platform, and schedules them in Buffer |
| ~10:47 PM daily | Evening job | Checks what went live, emails you views and problems, and deletes posted videos from the repo |

**Posting times (New York):** TikTok 7:00 AM, 1:00 PM, 6:00 PM, 8:30 PM, 10:00 PM · YouTube 7:30 AM, 12:30 PM, 4:30 PM, 7:30 PM, 9:30 PM.

## Your part
Nothing day to day. Read the nightly email. Ask Claude if you want to change the style, the voice,
the number of posts, or the niche.

## How a video is made
1. A story JSON (`stories/<date>/<NN>.json`: title + story) is written by the factory.
2. `python tools/story_maker.py make <story.json>` narrates it offline (Piper "ryan-high" voice,
   pitched down with reverb), times every word, picks a random stretch of gameplay from
   `assets/gameplay/`, darkens it, adds a quiet drone, shows the title on a card, then captions.
3. The `.mp4` and its `.json` land in `queue/`; the morning job posts them.

Gameplay footage is free-to-use with credit (`assets/gameplay/credits.json`); every caption ends
with the credit.

## Settings
- `config.json`: `test_mode`, `clips_per_day`, posting `slots`, `factory.min_queue_days`.
- `campaigns.json` → `scary-stories`: caption rules and the hashtag bank (refreshed monthly).
- `insights.md`: what's getting views, updated every Sunday and used by every job.

## Housekeeping
- This repo is **public** so Buffer can download the videos. Don't put anything private in it.
- The evening job deletes videos 2 days after they post and compresses history every Sunday.
