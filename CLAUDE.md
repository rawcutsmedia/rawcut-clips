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
4. Work out which clips are allowed, up to `clips_per_day` (currently 5, so one clip per slot;
   every clip goes to every connected platform):
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
    --campaign <key> --title "<hook title>" --tags "<the hashtags used, space-separated>"
    --status <scheduled|draft|error> [--error "..."]`.
12. `git add -A && git commit -m "Log posts <date>" && git push origin HEAD:main`.
13. Final message: what was scheduled (platform, time, title), anything skipped and why, and how
    many clips are left in `queue/` (`python scripts/ledger.py summary`).

### Caption rules
- If `insights.md` exists, read it first and apply its lessons about hooks, titles and topics.
- Line 1 is the hook, and a bold hook beats a descriptive one. In the first week, bold claims and Elon
  hooks got 100–300 views while plain tour descriptions got 0–10. Use one of two forms:
  - the clip's most surprising or debatable claim, stated flatly ("A whole house every 15 minutes.");
  - a pick-a-side question for the viewer ("Would you live in 400 sq ft for $50K?", "Is this the
    future of housing?").
  Debate is good; negativity isn't. Never call the brand fake, a scam or a gimmick, never mock anyone,
  never claim anything the clip doesn't support, and never use politics or rage bait about groups of
  people (campaigns reject these, and they get posts taken down). Never give away the punchline.
  Use different wording on each platform; never paste the same caption twice.
- Use the clip's `title`, `transcript` and `source_title` for context. Credit the source when known
  (for example "🎙️ via <show or creator>").
- Paid campaigns (`paid: true`) must include **#ad** and every item in `required_text` and
  `required_mentions`, word for word.
- Follow every item in the campaign's `caption_rules` (for example Boxabl: tag @boxabl, spell
  BOXABL right, never mention stock or a ticker).
- Hashtags: 4–5 per post, all lowercase, picked from the campaign's `hashtags` bank in
  `campaigns.json`:
  - all of `always`;
  - 1–2 from `core`, rotated so clips in a row don't repeat the same set;
  - 1 from `by_topic` that matches what the clip is about.
  - YouTube also gets everything in `youtube_only` (`#shorts`).
  - Never use anything in `avoid`.
  - If `insights.md` names hashtags that do better or worse, follow it.
  - Campaigns without a `hashtags` bank: 3–5 relevant niche hashtags. Never #fyp, #foryou or #viral.
- At most 2 emojis. No engagement bait ("comment YES").
- TikTok caption: under 150 characters before the hashtags. Hashtags go at the end.
- YouTube title: 90 characters max, with no hashtags in it (they waste title space). Description: 1–2
  lines, then the hashtags in this order: `#ad` first, then `#boxabl` (the campaign's brand tag), then
  a `core` tag, then the rest, with `#shorts` last. The first three show above the title, which keeps
  the #ad disclosure visible.

## Clip factory job (keep the queue stocked)
Goal: keep at least `factory.min_queue_days` × `clips_per_day` clips in `queue/` (4 × 5 = 20),
cut from the approved footage in `sources.json`.
1. `git pull --rebase origin main`, then `python scripts/ledger.py summary`. If `queue_clips` is
   already at the target, stop and say "Queue is full". If no source with an active campaign and
   status `new`/`in_progress` can be downloaded from the cloud (every one is a YouTube URL while
   `try_youtube` is false), stop right away and say "No cloud footage; the PC top-up job keeps
   the queue stocked".
2. Set up (every run):
   `pip install -q "yt-dlp[default,deno]" faster-whisper imageio-ffmpeg numpy gdown` and
   `export CLIPKIT_WORK=$HOME/rawcut-work`. Downloads and work files live there, **outside the
   repo**. Never commit anything from it.
3. Run `python scripts/expand_drive.py`. It turns Google Drive folder sources into one source per
   video file. Then pick a source from `sources.json`: its campaign must be `active` and its
   `status` must be `new` or `in_progress` (skip `done`, `blocked`, `expanded`). **Skip YouTube
   URLs while `config.factory.try_youtube` is false**, because YouTube blocks cloud downloads with a
   bot check. Take the lowest `priority` number first, then the one with the fewest `used_ranges`.
   Set its `status` to `in_progress` while it still has unused moments.
4. Download: `python tools/clipkit.py fetch "<url>" --name <source id>`. For a Google Drive file,
   if that fails or gives a tiny or HTML file, use gdown:
   `python -m gdown --fuzzy "<url>" -O $CLIPKIT_WORK/<source id>.mp4`, then
   `python tools/clipkit.py fetch $CLIPKIT_WORK/<source id>.mp4 --name <source id>`.
   If it still fails after one retry, set `status` to `"blocked"` with an `"error"` note and move
   on to the next source. If a YouTube download hits the bot check, stop trying YouTube for this
   run.
5. Transcript: `python tools/clipkit.py transcript <source id> --model base.en`, then read
   `$CLIPKIT_WORK/projects/<source id>/transcript.txt`.
6. Pick moments (needed = target − queue, at most `max_clips_per_run` per run) using the rules in
   `tools/PICK_MOMENTS_PROMPT.txt` **and** the campaign's `clip_rules`. Skip anything within 10
   seconds of a range in the source's `used_ranges`. Each clip should run 20–55 seconds. The hook
   title is 8 words or fewer, and for Boxabl it **must contain the word BOXABL**. Write the JSON
   array to `$CLIPKIT_WORK/projects/<source id>/clips.json`.
7. Render straight into the queue:
   `python tools/clipkit.py cut <source id> --campaign <campaign key> --outdir queue
   --font fonts/Anton-Regular.ttf --font-name Anton --layout <layout>`.
   Use `--layout blur` for tours and wide shots, where the whole home or product has to stay
   visible. Use `--layout crop` for one person talking to the camera. If a source mixes both, render
   in two passes with `--only`.
8. Quality check: for each new clip, grab a frame
   (`python -c "import imageio_ffmpeg,subprocess,sys;subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-ss','2','-i',sys.argv[1],'-frames:v','1','-y',sys.argv[2]])" <clip> /tmp/check.png`)
   and look at it. Delete the clip and its `.json` if it's black, cuts off the main subject, has
   unreadable text, or breaks a campaign rule.
9. Add each kept clip's `[start, end]` (seconds) to the source's `used_ranges` in `sources.json`.
   If fewer than 3 good unused moments remain in that source, set its `status` to `"done"`.
10. `git add queue sources.json && git commit -m "Factory: <n> clips from <source id>" &&
    git push origin HEAD:main`.
11. If the queue is still below target, repeat from step 3 with another source, up to
    `max_sources_per_run` sources.
12. Final message: clips added (title, length, source), sources marked done or blocked, and the
    queue total.

## PC top-up job (keep the queue stocked from footage on the user's PC)
A scheduled task that runs on the user's computer (Tue, Thu and Sat at 7:57 PM New York time).
YouTube blocks cloud downloads, so approved footage was downloaded once onto the PC: each
`sources.json` entry with `pc_file` sits in the PC's `Videos/Boxabl` folder, next to its
`<id>.en.vtt` subtitles. The PC cuts short lossless pieces, and this session renders them with
`tools/pc_batch.py`. Run unattended: don't ask questions.
1. If the repo isn't already here, call `add_repo` (owner `rawcutsmedia`, repo `rawcut-clips`,
   access `push`), clone it and cd into it. Then `git pull --rebase origin main`,
   `pip install -q imageio-ffmpeg numpy` and `export CLIPKIT_WORK=$HOME/rawcut-work`.
2. `python tools/pc_batch.py need`. If `need` is 0, stop and say "Queue is full".
3. Check the PC with device_bash: `ls "$HOME"/mnt/*/Boxabl | head`. If the device tools are
   missing or the computer can't be reached, stop and say: "Your computer was off or the Claude
   app was closed, so no clips were made. Queue: <n> clips (<days> days)."
4. `python tools/pc_batch.py sources` lists usable sources, best first. Work down the list until you
   have `need` good moments (at most 3 sources). For each source, stage its subtitles
   (`device_stage_files` on `~/mnt/Videos/Boxabl/<pc_file stem>.en.vtt`), run
   `python tools/pc_batch.py transcript <source id> <staged path>` and read the transcript. If it
   reads like a machine translation (the speaker isn't speaking English), run
   `python tools/pc_batch.py status <id> done "not English"` and move on.
5. Pick moments using `tools/PICK_MOMENTS_PROMPT.txt`, the campaign's `clip_rules`, and `insights.md`
   if it exists. `insights.md` is what has actually been getting views, so lean into it:
   - Each clip is 20–55 s, starts on the hook, and ends on a payoff.
   - Prefer moments built around a bold, surprising or debatable claim: speed ("a house every 15
     minutes"), price, size, durability tests, Elon Musk, or "fix the housing crisis". Plain feature
     walk-throughs (shower, ceilings) do worst; take them only when nothing better is left.
   - The hook title states the boldest *true* claim in the clip, or asks the viewer to pick a side
     ("Would you live in this $50K BOXABL?"). Never use a vague description.
   - Content is positive. Leave out anything within 10 s of the source's `used_ranges`, stock or
     investing talk, sponsor reads, and "like and subscribe" bait.
   - Hook title is 8 words or fewer and contains BOXABL.
   - Get exact times with `python tools/pc_batch.py words <id> <from> <to>`: `start` is the first
     word's start and `end` is the last word's end. Never end on "and", "so", or mid-sentence.
   - Write `$CLIPKIT_WORK/projects/<id>/clips.json`.
   - If a source has fewer than 3 good unused moments left, mark it done afterwards with
     `python tools/pc_batch.py status <id> done`.
6. `python tools/pc_batch.py cutplan <id> [<id> ...]` prints one device_bash command. Run it
   exactly as printed, in a single call. It writes `Videos/Boxabl/rawcut-pieces/slotNN.mp4` on the
   PC, overwriting the last batch.
7. Stage `~/mnt/Videos/Boxabl/rawcut-pieces/offsets.json` and the slots, 3 files per
   `device_stage_files` call. Uploads are rate-limited: if a file fails, start rendering what you
   have and retry it later, up to 5 tries. If one file keeps failing, copy it on the PC
   (`cp slotNN.mp4 slotNN-b.mp4`) and stage the copy.
8. `python tools/pc_batch.py render`, in the background (about 1.5 min per clip). Run it again after
   staging more slots; it skips finished ones.
9. Run `python tools/pc_batch.py review` and Read every contact sheet it makes.
   - `drop <slot>` any clip that is black, cuts off the main subject, has broken captions, starts
     or ends mid-word, or breaks a campaign rule.
   - If a caption misspells BOXABL or Casita, add the fix to the campaign's `caption_fixes`, delete
     `$CLIPKIT_WORK/render/<slot>.done`, and render again.
10. `python tools/pc_batch.py queue` copies the clips into `queue/` and records `used_ranges`. Then
    `git add queue sources.json campaigns.json && git commit -m "PC top-up: <n> clips" &&
    git push origin HEAD:main`.
11. Final message: clips added (title, length, source), sources marked done, and the queue total
    with days covered. If `sources` shows fewer than 3 usable sources, add: "PC footage is running
    low. Ask Claude to download more approved Boxabl videos."
On the PC, only write inside `Videos/Boxabl/rawcut-pieces`, and never delete anything.

## Evening job (report links to submit)
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
   Then run `python scripts/ledger.py stats` and `python scripts/ledger.py summary`, and call
   `list_posts` with status `scheduled` for tomorrow.
5. Send **one** plain-text email with the Gmail connector to the recipient named in the run prompt:
   - Subject: `Rawcut daily: <n> live, <n> to submit` (add `[TEST MODE]` when test_mode is true)
   - **PC reminder (Monday, Wednesday and Friday emails only)**: append ` · PC on tomorrow 7:57 PM`
     to the subject, and make the email's first line: "⏰ Reminder: tomorrow (<Tuesday / Thursday /
     Saturday>) at 7:57 PM New York time, the clip top-up runs on your computer. Have it on with
     the Claude desktop app open for about 30 minutes."
   - **Submit these links**: grouped by campaign → platform, link, title, posted time
     (skip `organic`; nothing to submit).
   - **Views (last 7 days)**: total views, posts over the minimum payout, estimated earnings (say it's
     an estimate; the campaign decides the final pay), and the top 3 posts with their views.
   - **Problems**: failed posts with Buffer's error; clips waiting for a campaign.
   - **Queue**: clips left and roughly how many days that covers. If fewer than 2 days are left,
     say so, and mention any `sources.json` entries marked `blocked` (the clip factory couldn't
     download them).
   - **Tomorrow**: scheduled posts.
6. Cleanup: `python scripts/cleanup.py` deletes videos whose posts all finished more than
   `cleanup_after_days` ago (Buffer has already uploaded them to the platforms).
   **On Sundays only**, first update `insights.md` from `python scripts/ledger.py stats --days 14`. Keep it
   to 15 lines or fewer:
   - which kinds of moments, hook titles, sources, posting times and hashtags (`hashtag_avg_views`) got
     the most views and which got the fewest;
   - then 3–5 concrete rules for the next week, e.g. "more price/durability moments", "titles that ask
     a question beat statements", "TikTok 6 PM beats 1 PM".
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
  depend on those links).
- Never commit downloaded source videos or anything from `$CLIPKIT_WORK` (finished clips copied
  into `queue/` are the one exception).
- Never put API keys, emails or tokens in this repo. It's public.
- Never retry a failed Buffer call more than once. Log it and move on.
