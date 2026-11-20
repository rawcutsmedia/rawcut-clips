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
- `scary-stories` (not paid; no #ad) is **paused** since Nov 17, 2026 (`paused: true`): the
  account is focusing on the Clix GTA VI campaign. `boxabl` is retired (`active: false`).
- GTA VI streamer campaigns (from contentrewards.com) are `gta6-<streamer>` entries with
  `kind: "stream"` and `paid: true`; `gta6-template` shows the fields. Active now: `gta6-clix`
  (Clix GTA VI marathon pool), posted on the main `@mediarawcut` TikTok by the **GTA clip job**
  (evening), which also submits each link on Content Rewards. The morning job never posts them.
- Background gameplay lives in `assets/gameplay/` with credits in `assets/gameplay/credits.json`.
  It's free-to-use footage that requires credit. Never delete it.

## Clip factory job (write and render story videos)
Goal: keep at least `factory.min_queue_days` × `clips_per_day` videos in `queue/` (3 × 5 = 15).
1. `git pull --rebase origin main`. If the `scary-stories` campaign has `paused: true` or
   `active: false`, stop right away and say only "Stories are paused." Otherwise run
   `python scripts/ledger.py summary`. need = target − `queue_clips`, at most
   `factory.max_clips_per_run`. If need is 0, stop and say "Queue is full".
2. Set up: `pip install -q kokoro-onnx==0.6.1 soundfile piper-tts imageio-ffmpeg numpy pillow` and
   `export CLIPKIT_WORK=$HOME/rawcut-work`. The narrator voice (Kokoro, set in `config.json` → `story`) downloads from GitHub on first use.
   Keep kokoro-onnx pinned to 0.6.1: story_maker uses its word timings.
3. Read `tools/STORY_PROMPT.txt` (the writing rules), `insights.md` (what gets views), and the last
   60 lines of `stories/log.jsonl` (premises already used).
4. Write `need` new stories as `stories/<YYYY-MM-DD>/<NN>.json`, following STORY_PROMPT exactly:
   - original fiction, 160–210 words, an 8–16 word hook title, and a final line of 2–8 words;
   - thrilling, not just creepy: a frightening first line, something worse every few sentences,
     every paragraph ending on a pull, and a twist built on a detail planted early;
   - every story a different theme and premise, none repeating the log.
   Then run STORY_PROMPT's self-check on each one and rewrite it until every answer is yes. Also
   rewrite it if it is under 160 words, gives the twist away in the title, or breaks a content rule.
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
3. List `queue/*.mp4`, oldest first (by filename). If there are none, stop and say so. If the
   `scary-stories` campaign has `paused: true` and there is nothing else to post, stop and say only
   "Stories are paused; nothing to post."
4. Work out which clips are allowed, up to `clips_per_day` (currently 5, so one clip per slot;
   every clip goes to every connected platform in its campaign's `platforms`). **Skip GTA stream
   clips** (sidecar `kind: "stream"`): the GTA clip job posts those in the evening.
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
- GTA stream clips: start the caption with `#ad` (the disclosure must come first), then the hook:
  the clip's `title` reworded, or the wildest or funniest moment in `transcript`, with the
  streamer's name in it (for Clix: the word "Clix" must be in the caption). Never spoil GTA VI
  story missions, never portray the streamer negatively, never promise something the clip doesn't
  show. Add every `required_text` item exactly as written (e.g. `#Clix` with its capital C, even
  though other hashtags are lowercase), every `required_mentions` item and every `caption_rules`
  item. English only. No credit line. `isAiGenerated` is always false for stream clips.

## GTA clip job (evening, uses the user's computer)
Downloads the streamers' most-viewed Twitch clips on the user's computer (Twitch is blocked in the
cloud), edits them into vertical clips, posts them on the main `@mediarawcut` TikTok and submits
each link on Content Rewards within 30 minutes. Runs unattended; the user does nothing.
1. `git pull --rebase origin main`. Active GTA campaigns are the `campaigns.json` entries with
   `kind: "stream"` and `active: true`; each names a `twitch` login. If there are none, stop and say
   "No active GTA campaigns yet." Read every `clip_rules` and `caption_rules` item first: one
   rule break (above all anything AI-generated) can ban the account from every Reach campaign.
2. **Post at least `gta.min_posts_per_day` (4) clips tonight; the user wants no fewer.** Use stream
   clips already in `queue/` (sidecar `kind: "stream"`, oldest first) that still meet the rules,
   then make new ones for the rest. Then, if more good clips are available, make up to
   `gta.buffer_clips` extra and leave them in `queue/` unposted as insurance for a night when the
   download fails. If there aren't 4 good new clips, use unused ones from the last 3 days, then
   relax the Twitch-views bar; only post fewer than 4 when there is truly nothing usable, and say so.
   **Learn first:** read the `## GTA clips` section of `insights.md` and follow its rules (which
   moments, hook styles, lengths and hashtags win). Make exactly one clip per night an
   **experiment**: change one thing the insights haven't tested enough (a different hook style,
   length bucket, moment type or hashtag), so the system keeps discovering what works. Mark it
   `"experiment": true` in its features (step 8).
3. Reach the computer with `get_device_info`. If it can't be reached, stop and say so in one plain
   line ("Your computer wasn't on with the Claude app open, so no GTA clips tonight").
   **Work fast:** the laptop is only promised until 10:30 PM. Start the speech-model setup
   (`pip install ...` and a first `stream_clips.py transcript` call, which downloads the model) in
   the background right away, while you stage and look at clips. Post each clip as soon as it
   passes the quality check instead of waiting for all of them, so the first one goes out around
   8:15 PM. After 10:00 PM, post only what's ready and finish.
4. The downloading already happened on Windows: the Windows scheduled task "Rawcut GTA clips"
   (daily 7:20 PM, Python 3.13) runs `pc_gta_daily.py` in `gta.pc_folder`
   (`C:\Users\Declan\Videos\RawcutGTA`; in `device_bash` it's `~/mnt/Videos/RawcutGTA`). Twitch is
   blocked in this cloud workspace and in `device_bash` too, so never try to download here or
   there. Read `last_run.log` and `inbox/manifest.json` with `device_bash` (`cat`), or by staging
   them if `device_bash` isn't ready yet.
   - Keep the laptop's script current: with `device_bash`, download
     `https://raw.githubusercontent.com/rawcutsmedia/rawcut-clips/main/tools/pc_gta_daily.py` over
     `<pc_folder>/pc_gta_daily.py` (overwriting is fine; it fixes tomorrow's run).
   - If the log is missing, older than today 7:00 PM, or shows an error (for example a timeout
     because the laptop had just woken up), say so in the final message (quote the error), then
     **don't give up**: use the clips in `inbox/` that aren't in `streams/used.json` and are from
     the last 3 days, as long as they still meet the campaign's rules.
   Never delete or move anything on the computer.
5. Stage `inbox/manifest.json` and every part it lists with `device_stage_files` (Windows paths
   under `C:\Users\Declan\Videos\RawcutGTA\inbox\`; ≤50 per call; if a call fails, stage fewer
   files at a time and retry once). Then set up here:
   `pip install -q sherpa-onnx soundfile imageio-ffmpeg numpy pillow`,
   `export CLIPKIT_WORK=$HOME/rawcut-work`, and run
   `python tools/stream_clips.py assemble /mnt/user-data/uploads/Videos/RawcutGTA/inbox/manifest.json`.
   Skip clips already in `streams/used.json`.
6. For each ready clip, most views first, until tonight's 4 plus the buffer are made:
   - `python tools/stream_clips.py frame <path>` and look at the PNG. Skip the clip unless it shows
     the campaign's `game_filter` in real gameplay from the campaign's period (not menus, loading
     screens, another game or another creator's content), the streamer is the main focus, and it
     breaks no `clip_rules`.
   - Facecam: check the campaign's `facecam` box against the PNG every time (sources are
     1280x720). If the webcam isn't there (new layout, IRL camera, no cam), read the new box (X,Y,W,H
     in source pixels, the camera image only, never sponsor overlays or logos) and save it as
     `"facecam": [X, Y, W, H]`, or `[]` for no webcam (blurred layout).
   - `python tools/stream_clips.py transcript <path>`. Skip clips with no real moment, clips
     where the streamer comes off badly, and clips built around slurs or sexual jokes. (On-screen
     captions mask strong swearing automatically; keep the hook and the post caption clean.)
   - Write the on-screen hook: 3–8 words that make someone stop scrolling (the wildest or funniest
     moment, a question, the streamer's reaction), with the streamer's name in it (e.g. "Clix
     can't stop crashing"). Positive or neutral about the streamer. No GTA VI story spoilers,
     nothing the clip doesn't show.
   - Cut for watch time: start on the peak, not the setup (the funniest or wildest moment, or the
     start of it, within the first 2 seconds), drop dead air and the slow ending. Aim for 10–30
     seconds; never longer than the campaign's `max_seconds`.
   - `python tools/stream_clips.py make <path> --campaign <key> --hook "<hook>" --clip-id <id>
     --clip-title "<twitch title>" --clip-url <url> --views <views> --streamer "<streamer>"`.
     Use `--start`/`--end` (seconds) to cut a slow start or a dead ending, staying within the
     campaign's `min_seconds`/`max_seconds`.
7. Quality check every new clip: frames at 1.5 s and the midpoint (`-ss <t> -i <video> -frames:v 1`).
   Hook readable and naming the streamer, webcam not cut off, captions readable, no added logo or
   badge (unless the campaign sets `disclosure_badge`). Delete a broken clip and its `.json`.
8. Stage and post 4 of them now (buffer clips stay in `queue/`), on the channel in
   `gta.post_channel` (`tiktok` = `@mediarawcut`):
   - `python scripts/prepare_clip.py queue/<file>.mp4` for each clip, then
     `git add -A && git commit -m "GTA clips <date>" && git push origin HEAD:main` (the raw URL must
     exist before Buffer fetches it). Check `curl -sI <raw_url>` returns 200 if the network allows.
   - Buffer `list_posts` (status `scheduled`) for that channel: stay within
     `max_scheduled_per_channel`, and keep every GTA post at least 10 minutes away from a scheduled
     story post.
   - `create_post` for each clip: `schedulingType: "automatic"`, `mode: "customScheduled"`, the
     first `dueAt` about 5 minutes from now, then `gta.spacing_minutes` apart; `assets` = the
     `raw_url` video; `text` = the caption (Caption rules → GTA stream clips);
     `metadata.tiktok.isAiGenerated: false`. Never `saveToDraft` unless `test_mode` is true (then
     stop after drafting; nothing to submit).
   - Record each post with what it tried, so the evening job can learn from it:
     `python scripts/ledger.py add --platform tiktok --post-id <id> --due-at <dueAt>
     --media <media_path> --campaign <key> --title "<hook>" --tags "<hashtags>" --status scheduled
     --features '{"hook": "<hook>", "hook_style": "<question|reaction|statement|quote|stakes>",
     "moment": "<funny|fail|glitch|chase|clutch|rage|wholesome|chat>", "seconds": <length>,
     "post_time": "<HH:MM>", "source_views": <twitch views>, "experiment": <true|false>}'`.
     Use only those labels so posts can be compared.
9. Submit each link on Content Rewards within `submit_within_minutes` (30) of its post going live:
   - Wait until its `dueAt` (use a background `sleep` or Monitor; never a foreground sleep), then
     call Buffer `get_post` every 2 minutes until it is sent and has the public TikTok link
     (`externalLink` / `serviceLink`). Give up on a post after 20 minutes and list it as a problem.
   - Open the campaign's `campaign_url` in the Claude desktop app's built-in browser on the user's
     computer (Claude_Browser tools; contentrewards.com is already allowed there and signed in as
     Declan Kennedy, campaign status "Joined"). Click the orange **Submit clip** button, paste the
     TikTok link, pick TikTok / @mediarawcut if asked, submit, and read back the confirmation. Then
     open `https://contentrewards.com/c/submissions` and check the link is listed. Don't touch
     anything else in the account (settings, payouts, other campaigns).
   - Then `python scripts/ledger.py update --post-id <id> --status sent --link <url> --submitted`.
   - While `/c/submissions` is open, also read the review status and views of every earlier GTA
     submission listed there and record them:
     `python scripts/ledger.py update --post-id <id> --cr-status <approved|rejected|pending>
     --cr-views <n> [--cr-note "<rejection reason>"]` (match rows by TikTok link).
   - Submit only clips made for that campaign, each link once. Never submit a story video.
   - If no browser is available or it isn't signed in, don't stop the job: put every unsubmitted
     link at the top of the final message as "Submit now: <link> → <campaign_url>" (the final
     message reaches the user's phone), and say which one-time step is missing.
10. `git add -A && git commit -m "GTA posts <date>" && git push origin HEAD:main`.
11. Final message (it is sent to the user's phone): first any links that still need submitting,
    then clips posted (hook, time, Twitch views, submitted yes/no), clips skipped and why.

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
   For each post that's in the ledger, store every metric Buffer returned:
   `python scripts/ledger.py update --post-id <id> --metrics '{"views": n, "likes": n, "comments": n,
   "shares": n, ...}'` (use the metric `type` names as keys; views fall back to plays/impressions).
   Skip posts with no metrics yet.
   If `execute_query` isn't available, use `list_posts` with `includeMetrics: true` instead.
   Then run `python scripts/ledger.py stats` and `python scripts/ledger.py summary`, and call
   `list_posts` with status `scheduled` for tomorrow.
5. Send **one** plain-text email with the Gmail connector to the recipient named in the run prompt:
   - Subject: `Rawcut daily: <n> live, <views> views this week` (add `[TEST MODE]` when test_mode is
     true)
   - **Submit these links**: only paid-campaign posts whose ledger row has `submitted: false`,
     grouped by campaign → platform, with link, title and posted time. Leave this section out when
     there are none. (The GTA clip job submits its own links; list how many it submitted.)
   - **Today's posts**: platform, title, link.
   - **Views (last 7 days)**: total views, and the top 3 posts with their views. For paid campaigns
     only, also give posts over the minimum payout and estimated earnings, labelled as an estimate.
     For stories, add progress toward TikTok Creator Rewards (10,000 followers and 100,000 views in
     30 days) if Buffer reports followers.
   - **Problems**: failed posts with Buffer's error; clips waiting for a campaign.
   - **Queue**: clips left and roughly how many days that covers. If fewer than 2 days are left,
     say so.
   - **Tomorrow**: scheduled posts.
6. **Learn (every day, GTA clips):** run `python scripts/ledger.py learn --kind stream`. When it says
   `"enough_data": true` (8+ GTA posts at least a day old), rewrite the `## GTA clips` section of
   `insights.md` (12 lines or fewer, leave other sections alone):
   - what wins and what loses by moment type, hook style, length, posting time and hashtag, with
     average views, interaction rate and Content Rewards approval, and the post counts behind
     each (ignore buckets with fewer than 3 posts);
   - any rejection reasons from Content Rewards, as a rule to avoid them;
   - then 3–5 concrete rules for tomorrow's GTA clip job, including what tonight's experiment
     should test next.
   Only state what the numbers show. Before there's enough data, leave the section as is.
   Add one line to the email: the single biggest lesson so far.
7. **GTA top-up (keeps the 4-a-day minimum):** count today's GTA posts in the ledger (campaign
   `gta6-*`, `due_at` today). If fewer than `gta.min_posts_per_day` went out (for example the laptop
   was off) and stream clips wait in `queue/`, post the missing number now on `@mediarawcut`:
   `prepare_clip.py`, commit and push, `create_post` (first `dueAt` 5 minutes from now, then 10
   minutes apart; caption per Caption rules → GTA stream clips; `isAiGenerated: false`), and
   `ledger.py add` with `--features` built from the sidecar. Wait until each is sent, get its
   TikTok link, and put the links at the very top of the email and of your final message (it
   reaches the user's phone) as "Submit now (within 30 min): <link> → <campaign_url>", because this
   job can't use the laptop's browser to submit them.
8. Cleanup: `python scripts/cleanup.py` deletes videos whose posts all finished more than
   `cleanup_after_days` ago (Buffer has already uploaded them to the platforms).
   **On Sundays only, and only while stories are not paused**, first update the `## Scary stories`
   section of `insights.md` from `python scripts/ledger.py stats --days 14` (leave the GTA section
   alone). Keep that section to 15 lines or fewer:
   - which story themes, title styles, posting times and hashtags (`hashtag_avg_views`) got the most
     views and which got the fewest;
   - then 3–5 concrete rules for the next week.
   Only state what the numbers show, and once there are at least 20 measured posts.
   **On the first Sunday of each month**, also refresh each active campaign's `hashtags` bank in
   `campaigns.json`. Search the web for this month's popular TikTok and YouTube Shorts hashtags in the
   campaign's niche. Add clearly relevant ones, drop tags `insights.md` shows doing badly, keep `always`
   and `avoid` as they are, and update `researched`.
   **Every Sunday, as the very last step**, run `bash scripts/squash_history.sh`. It commits
   and pushes today's changes, then replaces the git history with one commit, so deleted videos
   stop taking up space. Don't skip it: without it the repo grows by about 1 GB a week. If it fails,
   say so with the error in your final message (that summary is sent to the user as a notification).
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
- Never delete or move files on the user's computer.
- Never post a GTA clip for a streamer without an active `gta6-*` campaign for that streamer.
- Never submit a story video (AI-narrated) to a streamer campaign, and never add AI voice or AI
  visuals to a stream clip: campaigns like Reach ban accounts for AI content.
