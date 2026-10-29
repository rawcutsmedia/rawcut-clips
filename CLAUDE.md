# Rawcut Media: automated posting (instructions for Claude)

This repo runs the Rawcut Media accounts. Since Oct 29, 2026 the content is **original scary
stories**: an AI narrator over Minecraft parkour gameplay with big word-by-word captions, written
and rendered automatically. Scheduled Claude runs (the "clip factory job", the "morning job" and the
"evening job") follow this file exactly. They run unattended: don't ask questions, make the safe
choice, and report what you did in the final message and the email.

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
- Active campaign: `scary-stories` (not paid; no #ad). `boxabl` is retired (`active: false`).
- Background gameplay lives in `assets/gameplay/` with credits in `assets/gameplay/credits.json`.
  It's free-to-use footage that requires credit. Never delete it.

## Clip factory job (write and render story videos)
Goal: keep at least `factory.min_queue_days` × `clips_per_day` videos in `queue/` (3 × 5 = 15).
1. `git pull --rebase origin main`, then `python scripts/ledger.py summary`. need = target −
   `queue_clips`, at most `factory.max_clips_per_run`. If need is 0, stop and say "Queue is full".
2. Set up: `pip install -q piper-tts imageio-ffmpeg numpy pillow` and
   `export CLIPKIT_WORK=$HOME/rawcut-work`. The narrator voice downloads from GitHub on first use.
3. Read `tools/STORY_PROMPT.txt` (the writing rules), `insights.md` (what gets views), and the last
   60 lines of `stories/log.jsonl` (premises already used).
4. Write `need` new stories as `stories/<YYYY-MM-DD>/<NN>.json`, following STORY_PROMPT exactly:
   - original fiction, 170–230 words, a 10–22 word hook title, and a short final line;
   - every story a different theme and premise, none repeating the log.
   Then reread each one. Rewrite it if it is under 170 words, gives the twist away in the title, or
   breaks a content rule.
5. Render each story:
   `python tools/story_maker.py make stories/<date>/<NN>.json --out queue` (about 1.5 minutes each;
   run them in the background with a single command when there are several). It prints JSON with
   the duration. Append one line per story to `stories/log.jsonl`:
   `{"date": "...", "file": "...", "title": "...", "premise": "...", "theme": "...", "duration": n}`.
6. Quality check every new video: grab frames at 2 s and at the midpoint with the imageio ffmpeg
   (`-ss <t> -i <video> -frames:v 1 <png>`) and look at them. The title card must be readable at
   2 s, captions centered mid-video, gameplay visible. Delete a video and its `.json` if it's broken
   or under 61 s, and write a longer replacement.
7. `git add queue stories && git commit -m "Factory: <n> stories" && git push origin HEAD:main`.
8. Final message: stories added (title, length, theme) and the queue total with days covered.

## Morning job (schedule today's posts)
1. `git pull --rebase origin main`.
2. Read `config.json` and `campaigns.json`.
3. List `queue/*.mp4`, oldest first (by filename). If there are none, stop and say so.
4. Work out which clips are allowed, up to `clips_per_day` (currently 5, so one clip per slot;
   every clip goes to every connected platform):
   - The clip's sidecar `queue/<same name>.json` names a `campaign`. If the sidecar or the campaign
     is missing, or the campaign isn't `active`, **don't post it**. Leave it in the queue and list it
     as "needs a campaign" in your summary.
   - Respect the campaign's `platforms`, `min_seconds` and `max_seconds`.
5. Voiceover: only when `config.voiceover.enabled` is true **and** the campaign's `voiceover_allowed`
   is true. Write a spoken hook of 12 words or fewer that teases the clip without spoiling it.
   (Story videos already have narration; their campaign doesn't allow an extra voiceover.)
6. For each allowed clip run
   `python scripts/prepare_clip.py queue/<file>.mp4 [--voiceover-text "<hook>"]`.
   It moves the clip to `media/<date>/` and prints JSON with `raw_url`, `title`, `transcript`,
   `ai_generated`, `credit` and `warnings`. If it fails, leave the clip and report the error.
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
    - TikTok: `text` = TikTok caption; `metadata.tiktok.isAiGenerated` = the `ai_generated` value.
    - YouTube: `text` = description; `metadata.youtube` =
      `{"title": "<YouTube title>", "categoryId": "<config.youtube_category_id>", "privacy": "public",
      "madeForKids": false, "isAiGenerated": <ai_generated>}`.
    - Instagram (once connected): `metadata.instagram = {"type": "reel", "shouldShareToFeed": true,
      "isAiGenerated": <ai_generated>}`.
11. Record every post, successful or failed:
    `python scripts/ledger.py add --platform <p> --post-id <id> --due-at <dueAt> --media <media_path>
    --campaign <key> --title "<title>" --tags "<the hashtags used, space-separated>"
    --status <scheduled|draft|error> [--error "..."]`.
12. `git add -A && git commit -m "Log posts <date>" && git push origin HEAD:main`.
13. Final message: what was scheduled (platform, time, title), anything skipped and why, and how
    many clips are left in `queue/` (`python scripts/ledger.py summary`).

### Caption rules
- If `insights.md` exists, read it first and apply its lessons about hooks, titles and topics.
- Line 1 is the hook, and a bold hook beats a descriptive one. Use one of two forms:
  - the most unsettling concrete detail, stated flatly ("The footage shows an empty porch.");
  - a question that makes the viewer stay ("Would you have opened the door?").
  Never give away the ending. Never claim a story is true or that it came from Reddit or a real
  person. Never mock anyone, never use politics or rage bait about groups of people.
  Use different wording on each platform; never paste the same caption twice.
- Use the clip's `title` and `transcript` for context. If prepare_clip printed a `credit`, end the
  caption (TikTok) or description (YouTube) with it exactly as given. The gameplay license requires
  this.
- Paid campaigns (`paid: true`) must include **#ad** and every item in `required_text` and
  `required_mentions`, word for word. Unpaid campaigns never use #ad.
- Follow every item in the campaign's `caption_rules`.
- Hashtags: 4–5 per post, all lowercase, picked from the campaign's `hashtags` bank in
  `campaigns.json`:
  - all of `always`;
  - 1–2 from `core`, rotated so clips in a row don't repeat the same set;
  - 1 from `by_topic` that matches what the clip is about.
  - YouTube also gets everything in `youtube_only` (`#shorts`).
  - Never use anything in `avoid`.
  - If `insights.md` names hashtags that do better or worse, follow it.
  - Campaigns without a `hashtags` bank: 3–5 relevant niche hashtags. Never #fyp, #foryou or #viral.
- At most 2 emojis. No engagement bait ("comment YES", "like for part 2").
- TikTok caption: under 150 characters before the credit and hashtags. Hashtags go at the end.
- YouTube title: 90 characters max, with no hashtags in it. For stories, use a punchy version of the
  story title. YouTube description: 1–2 lines, then the credit, then the hashtags in this order:
  `#ad` first for paid campaigns only, then the campaign's `always` tags, a `core` tag, the rest,
  and `#shorts` last.

## Evening job (daily report)
1. `git pull --rebase origin main`.
2. `python scripts/ledger.py pending` lists posts whose time has passed.
3. For each one, call Buffer `get_post`. Update the ledger:
   `python scripts/ledger.py update --post-id <id> --status <sent|error> [--link <url>] [--error "..."]`.
   Use the post's public link if the response has one (fields such as `externalLink`/`serviceLink`).
   Otherwise use the channel's profile URL and say the exact link needs copying from the app.
4. Views: make one Buffer `execute_query` call for the posts sent in the last 7 days:
   `query { posts(first: 100, input: {organizationId: "6ad639a2de0f08ea4b18d4a9", filter: {status: [sent],
   dueAt: {start: "<7 days ago, ISO>", end: "<now, ISO>"}}}) { edges { node { id metrics { type value } } } } }`.
   For each post that's in the ledger, run `python scripts/ledger.py update --post-id <id> --views <n>`. Use
   the `views` metric, or `impressions` if there's no `views`. Skip posts with no metrics yet.
   If `execute_query` isn't available, use `list_posts` with `includeMetrics: true` instead.
   Then run `python scripts/ledger.py stats` and `python scripts/ledger.py summary`, and call
   `list_posts` with status `scheduled` for tomorrow.
5. Send **one** plain-text email with the Gmail connector to the recipient named in the run prompt:
   - Subject: `Rawcut daily: <n> live, <views> views this week` (add `[TEST MODE]` when test_mode is
     true)
   - **Submit these links**: only for campaigns with `paid: true`, grouped by campaign → platform,
     with link, title and posted time. Leave this section out when there are none (stories are
     unpaid, so there is nothing to submit).
   - **Today's posts**: platform, title, link.
   - **Views (last 7 days)**: total views, and the top 3 posts with their views. For paid campaigns
     only, also give posts over the minimum payout and estimated earnings, labelled as an estimate.
     For stories, add progress toward TikTok Creator Rewards (10,000 followers and 100,000 views in
     30 days) if Buffer reports followers.
   - **Problems**: failed posts with Buffer's error; clips waiting for a campaign.
   - **Queue**: clips left and roughly how many days that covers. If fewer than 2 days are left,
     say so.
   - **Tomorrow**: scheduled posts.
6. Cleanup: `python scripts/cleanup.py` deletes videos whose posts all finished more than
   `cleanup_after_days` ago (Buffer has already uploaded them to the platforms).
   **On Sundays only**, first update `insights.md` from `python scripts/ledger.py stats --days 14`. Keep it
   to 15 lines or fewer:
   - which story themes, title styles, posting times and hashtags (`hashtag_avg_views`) got the most
     views and which got the fewest;
   - then 3–5 concrete rules for the next week.
   Only state what the numbers show, and once there are at least 20 measured posts.
   **On the first Sunday of each month**, also refresh each active campaign's `hashtags` bank in
   `campaigns.json`. Search the web for this month's popular TikTok and YouTube Shorts hashtags in the
   campaign's niche. Add clearly relevant ones, drop tags `insights.md` shows doing badly, keep `always`
   and `avoid` as they are, and update `researched`. Then squash git history so deleted videos stop
   taking up space:
   `git add -A && git commit -m "Evening <date>" ; git checkout --orphan squash && git add -A &&
   git commit -m "Squash history <date>" && git push --force origin squash:main`.
   On other days just commit and push to `main`.

## Never
- Never post a clip that isn't in `queue/` or has no active campaign.
- Never delete or move anything in `media/` except through `scripts/cleanup.py` (scheduled posts
  depend on those links), and never delete `assets/`.
- Never commit anything from `$CLIPKIT_WORK` (finished videos written to `queue/` are the one
  exception).
- Never put API keys, emails or tokens in this repo. It's public.
- Never retry a failed Buffer call more than once. Log it and move on.
- Never present a story as true, as a real Reddit post, or as about a real person.
