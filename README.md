# Rawcut Media: clip posting system

Clips you drop in `queue/` get posted to TikTok and YouTube Shorts through Buffer, plus Instagram
once it's connected. Two scheduled Claude runs do the work:

| When (New York time) | Job | What it does |
|---|---|---|
| ~8:47 AM daily | Morning job | Takes the next clips from `queue/`, writes a different caption per platform (with #ad for paid campaigns), optionally adds an AI voiceover hook, and schedules the posts in Buffer for 12:30–1 PM and 7:30–8 PM |
| ~9:37 PM daily | Evening job | Checks which posts went live and emails you the links to submit to Whop/Vyro, plus any problems and how many clips are left |

The full rules Claude follows are in [`CLAUDE.md`](CLAUDE.md).

## Your daily part (~10 minutes)
1. **Make clips** with ClipKit on your PC: `py clipkit.py cut NAME --campaign <campaign-key>`.
   Each clip comes out as an `.mp4` plus a matching `.json` (title, transcript, campaign).
2. **Upload both files to `queue/`**: open this repo on github.com → `queue` folder →
   **Add file → Upload files** → drag in the `.mp4` and `.json` pairs → **Commit changes**.
   (The browser limit is 25 MB per file. ClipKit keeps clips under that.)
3. **At night, paste the links** from the evening email into your campaigns on Whop/Vyro.

## Campaigns
Every clip must say which campaign it belongs to. Clips without an active campaign are never
posted. Add each campaign you join to [`campaigns.json`](campaigns.json): name, `paid`,
required hashtags/mentions, allowed platforms, and whether AI voiceovers are allowed. Or just paste
the campaign's rules to Claude and ask it to add the entry.

## Settings ([`config.json`](config.json))
- `test_mode: true` → posts are saved as **drafts** in Buffer so you can check them first. Set to
  `false` to go live.
- `clips_per_day`, posting `slots`, `youtube_category_id`.
- `voiceover.enabled` → AI hook voiceover with Gemini (`gemini-3.8-flash-tts`). Off by default.
  It's only ever used for campaigns with `voiceover_allowed: true`. The API key lives in the
  Claude cloud environment as a network secret, never in this repo.

## Housekeeping
- This repo is **public** so Buffer can download the videos. Don't put anything private in it.
- GitHub likes repos under ~1 GB. About once a month, start a fresh repo or ask Claude to clean up
  `media/` folders whose posts have all gone out.
