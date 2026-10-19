# Rawcut Media: automated posting (instructions for Claude)

This repo runs the Rawcut Media clip accounts. Scheduled Claude runs (the "morning job" and the
"evening job") follow this file exactly. They run unattended: don't ask questions, make the
safe choice, and report what you did in the final message and the email.

## Fixed facts
- Buffer organization: "My organization", id `6ad639a2de0f08ea4b18d4a9` (Free plan).
- Channel IDs are in `config.json` → `buffer.channels`. Platforms with `null` are not connected; skip them.
  - TikTok `@mediarawcut`, YouTube "Raw Cut Media". Instagram not connected yet.
- Timezone: `America/New_York`. Every `dueAt` must carry the correct offset (EDT -04:00 / EST -05:00);
  `scripts/next_slots.py` already does this.
- Buffer Free plan: max 10 scheduled posts per channel. Never go over `max_scheduled_per_channel`.
- Media must be a public direct link. Files under `media/` are served at
  `https://raw.githubusercontent.com/rawcutsmedia/rawcut-clips/main/<path>`.
  **Always commit and push to `main`** (`git push origin HEAD:main`), never a `claude/` branch,
  or the links won't exist.
- `config.json` → `test_mode: true` means: create Buffer posts as **drafts** (`saveToDraft: true`)
  instead of scheduling them. Never change `test_mode` yourself.

## Morning job (schedule today's posts)
1. `git pull --rebase origin main`.
2. Read `config.json` and `campaigns.json`.
3. List `queue/*.mp4`, oldest first (by filename). If there are none, stop and say so.
4. Work out which clips are allowed, up to `clips_per_day`:
   - The clip's sidecar `queue/<same name>.json` names a `campaign`. If the sidecar or the campaign
     is missing, or the campaign isn't `active`, **don't post it**. Leave it in the queue and list it
     as "needs a campaign" in your summary.
   - Respect the campaign's `platforms`, `min_seconds` and `max_seconds`.
5. Voiceover: only when `config.voiceover.enabled` is true **and** the campaign's `voiceover_allowed`
   is true. Write a spoken hook of 12 words or fewer that teases the clip without spoiling it.
6. For each allowed clip run
   `python scripts/prepare_clip.py queue/<file>.mp4 [--voiceover-text "<hook>"]`.
   It moves the clip to `media/<date>/` and prints JSON with `raw_url`, `title`, `transcript`,
   `voiceover` and `warnings`. If it fails, leave the clip and report the error.
7. `git add -A && git commit -m "Stage clips <date>" && git push origin HEAD:main`.
   Optionally check `curl -sI <raw_url>` for HTTP 200. If the network blocks it, carry on.
8. `python scripts/next_slots.py --count <number of clips>` gives the next free times per platform.
9. Before creating posts, call Buffer `list_posts` (organizationId above, status `scheduled`,
   per channel). Skip a platform if it would exceed `max_scheduled_per_channel`.
10. For each clip and each connected platform in the campaign's `platforms`, create one Buffer post
    with `create_post`:
    - `channelId` from config, `schedulingType: "automatic"`, `mode: "customScheduled"`,
      `dueAt` from step 8, `assets: [{"video": {"url": "<raw_url>"}}]`,
      plus `saveToDraft: true` when `test_mode` is true.
    - TikTok: `text` = TikTok caption; `metadata.tiktok.isAiGenerated` = the `voiceover` value.
    - YouTube: `text` = description; `metadata.youtube` =
      `{"title": "<title>", "categoryId": "<config.youtube_category_id>", "privacy": "public",
      "madeForKids": false, "isAiGenerated": <voiceover>}`.
    - Instagram (once connected): `metadata.instagram = {"type": "reel", "shouldShareToFeed": true,
      "isAiGenerated": <voiceover>}`.
11. Record every post, successful or failed:
    `python scripts/ledger.py add --platform <p> --post-id <id> --due-at <dueAt> --media <media_path>
    --campaign <key> --title "<hook title>" --status <scheduled|draft|error> [--error "..."]`.
12. `git add -A && git commit -m "Log posts <date>" && git push origin HEAD:main`.
13. Final message: what was scheduled (platform, time, title), anything skipped and why, and how
    many clips are left in `queue/` (`python scripts/ledger.py summary`).

### Caption rules
- Line 1 is the hook: curiosity or stakes, never a lie, never the punchline. Different wording on
  each platform; never paste the same caption twice.
- Use the clip's `title`, `transcript` and `source_title` for context. Credit the source when known
  (for example "🎙️ via <show or creator>").
- Paid campaigns (`paid: true`) must include **#ad** and every item in `required_text` and
  `required_mentions`, word for word.
- 3–5 relevant lowercase hashtags. At most 2 emojis. No engagement bait ("comment YES").
- TikTok caption: under 150 characters before the hashtags.
- YouTube title: 90 characters max, ending in ` #Shorts`. Description: 1–2 lines, then hashtags,
  plus `#ad` if paid.

## Evening job (report links to submit)
1. `git pull --rebase origin main`.
2. `python scripts/ledger.py pending` lists posts whose time has passed.
3. For each one, call Buffer `get_post`. Update the ledger:
   `python scripts/ledger.py update --post-id <id> --status <sent|error> [--link <url>] [--error "..."]`.
   Use the post's public link if the response has one (fields such as `externalLink`/`serviceLink`).
   Otherwise use the channel's profile URL and say the exact link needs copying from the app.
4. `python scripts/ledger.py summary`, and `list_posts` with status `scheduled` for tomorrow.
5. Send **one** plain-text email with the Gmail connector to the recipient named in the run prompt:
   - Subject: `Rawcut daily: <n> live, <n> to submit` (add `[TEST MODE]` when test_mode is true)
   - **Submit these links**: grouped by campaign → platform, link, title, posted time
     (skip `organic`; nothing to submit).
   - **Problems**: failed posts with Buffer's error; clips waiting for a campaign.
   - **Queue**: clips left and roughly how many days that covers. If fewer than 2 days are left,
     say "Add more clips to queue/".
   - **Tomorrow**: scheduled posts.
6. Commit and push the ledger to `main`.

## Never
- Never post a clip that isn't in `queue/` or has no active campaign.
- Never delete or move anything in `media/` (scheduled posts depend on those links).
- Never put API keys, emails or tokens in this repo. It's public.
- Never retry a failed Buffer call more than once. Log it and move on.
