# Rawcut Media: clip posting system

Clips you drop in `queue/` get posted to TikTok and YouTube Shorts through Buffer, plus Instagram
once it's connected. Two scheduled Claude runs do the work:

| When (New York time) | Job | What it does |
|---|---|---|
| ~3:07 AM daily | Clip factory | Keeps 3 days of clips in `queue/`. It downloads approved campaign footage from [`sources.json`](sources.json), picks the best moments, and cuts captioned 9:16 clips that follow each campaign's rules |
| ~6:07 AM daily | Morning job | Takes the next **5** clips from `queue/`, writes a different caption per platform (with #ad for paid campaigns), optionally adds an AI voiceover hook, and schedules them in Buffer |
| ~10:47 PM daily | Evening job | Checks which posts went live, emails you the links to submit to Whop/Vyro (plus problems and clips left), and deletes videos that have finished posting |

**Posting times (New York):** TikTok 7:00 AM, 1:00 PM, 6:00 PM, 8:30 PM, 10:00 PM · YouTube 7:30 AM, 12:30 PM, 4:30 PM, 7:30 PM, 9:30 PM.
Chosen from Buffer's 2026 data: evenings (6–11 PM) perform best on both apps, plus the morning scroll, lunch, and YouTube Shorts' strong 4 PM slot.
Each clip goes to both accounts, so you need **5 clips a day (35 a week)** in `queue/`.

The full rules Claude follows are in [`CLAUDE.md`](CLAUDE.md).

## Your daily part (~5 minutes)
The clip factory makes the clips. Your only daily job is step 3 below (pasting links). Steps 1–2
are for when you want to add your own clips by hand.

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
- GitHub likes repos under ~1 GB. The evening job deletes videos 2 days after they've posted and
  compresses the repo's history every Sunday, so it stays small on its own.
