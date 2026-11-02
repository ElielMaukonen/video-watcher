---
name: tiktok-post
description: Publish videos to the user's own TikTok account through TikTok's official Content Posting API, free, using the user's own TikTok developer app — one video or a whole batch, direct post or as a draft to the TikTok app inbox. Trigger on any request to post, publish, upload or schedule a video to TikTok ("julkaise tiktokiin", "postaa tämä tiktokkiin", "lataa nämä videot tiktokiin", "post this to TikTok", "upload these to TikTok"), on setting up or logging in to the TikTok connection, or on checking a TikTok post's status. Use it instead of Metricool when the user wants to avoid Metricool's post limit.
---

# tiktok-post

Posts to TikTok with the official Content Posting API. No scheduler and no subscription: the
user's own developer app talks to TikTok directly. Python stdlib only; ffprobe, when present,
checks each video's length against the account's limit before the upload starts.

Run from the repo root (`py` on Windows, `python3` on macOS and Linux):

```
python3 .claude/skills/tiktok-post/scripts/tiktok_post.py --check
```

## Ground rules

- **Posting is public and outward-facing. Confirm before every real post**: file, caption,
  privacy level, and the AI and branded-content labels. A batch the user approved as a whole
  counts as one confirmation. `--dry-run` validates without posting; use it first.
- **Never pick a privacy level for the user.** TikTok's rules forbid a default. Run `whoami`, show
  the `privacy_level_options` it returns, and ask.
- **Never ask for the client secret in chat, and never print it.** The user stores it with
  `setup` in their own terminal, or as an environment secret (`TIKTOK_CLIENT_KEY`,
  `TIKTOK_CLIENT_SECRET`) in the cloud environment settings. If they paste it anyway, use it once
  with `setup` and tell them to rotate it in the developer portal.
- `.tiktok/` holds the secret, the tokens and the post log. It is gitignored and must stay that
  way. Never `git add` it.

## One-time setup (the user does this; walk them through it)

1. Create a free account at https://developers.tiktok.com, then **Manage apps → Connect an app**.
2. Add the **Login Kit** and **Content Posting API** products. In Content Posting API, turn on
   **Direct Post**. Scopes: `user.info.basic`, `video.publish`, `video.upload`.
3. Platform: **Desktop**. Redirect URI: `http://localhost:3455/callback/`, exactly. A different
   one must also be set in `TIKTOK_REDIRECT_URI`.
4. In **Sandbox**, add their own TikTok account as a target user so they can test before the
   app is reviewed. Then submit the app for review.
5. Store the client key and secret: `setup` (prompts, with the secret hidden), or environment
   secrets.

### Unaudited apps are private-only

Until TikTok approves the app, **every post is private (`SELF_ONLY`)**, and the TikTok account
itself must be set to private while posting. Otherwise init fails with
`unaudited_client_can_only_post_to_private_accounts`. Tell the user this up front. It is
TikTok's rule, not this tool's. Once the app passes review, public posting works. Videos
already posted privately can be switched to public in the TikTok app.

## Log in

Interactive, on the user's own machine: `auth` prints a URL, listens on localhost:3455 and
catches the redirect.

Two-step, for a cloud session or any time Claude is driving:

```
python3 .claude/skills/tiktok-post/scripts/tiktok_post.py auth-url
```

Give the user the printed URL. After they approve, their browser lands on
`http://localhost:3455/callback/?code=...` and will probably show "can't connect". **That is
expected.** Ask them to paste the whole address from the address bar, then:

```
python3 .claude/skills/tiktok-post/scripts/tiktok_post.py auth-finish "<pasted address>"
```

The code in that address expires within minutes and works only once, so finish promptly.
Access tokens last about 2 hours and refresh automatically. The login lasts 180 days.
**Refresh tokens rotate**: every refresh replaces the stored one, so a copied token goes stale.
That is why there is no `TIKTOK_REFRESH_TOKEN` variable. In a cloud session `.tiktok/` disappears
with the container, so expect to log in again in each new session. On the user's own computer
the login persists.

## Post

```
python3 .claude/skills/tiktok-post/scripts/tiktok_post.py whoami
python3 .claude/skills/tiktok-post/scripts/tiktok_post.py post video.mp4 --title "Caption #fyp" --privacy SELF_ONLY --dry-run
python3 .claude/skills/tiktok-post/scripts/tiktok_post.py post video.mp4 --title "Caption #fyp" --privacy SELF_ONLY
```

| Flag | Meaning |
|---|---|
| `--privacy` | `PUBLIC_TO_EVERYONE`, `MUTUAL_FOLLOW_FRIENDS`, `FOLLOWER_OF_CREATOR`, `SELF_ONLY`. Required for a direct post |
| `--title` / `--title-file` | Caption, up to 2200 characters (UTF-16 units); #hashtags and @mentions work |
| `--draft` | Upload to the TikTok app inbox instead; the user finishes in the app. Max 5 pending per 24h |
| `--ai` | AI-generated content label. **Set it whenever the video was made or substantially edited with AI** |
| `--own-brand` / `--paid-partnership` | Commercial content disclosure. A paid partnership cannot be `SELF_ONLY` |
| `--no-comments` `--no-duet` `--no-stitch` | Interaction settings. The account's own "no one" settings always win |
| `--cover-ms N` | Cover frame, N ms into the video |
| `--no-wait` | Return after upload without polling status |

Video: MP4 (H.264 recommended), MOV or WebM, 23–60 fps, 360–4096 px per side, up to 4 GB. Length
is limited by the account; `whoami` shows `max_video_post_duration_sec`. A URL instead of a file
uses `PULL_FROM_URL`, which only works for a domain verified in the developer portal, so prefer
local files.

**Getting the file into a cloud session**: download it with curl from a link, fetch it from
Google Drive with the Drive connector, or produce it in-session (Remotion, ffmpeg). Then post the
local path.

## Batch: as many as TikTok allows

`batch posts.json` (or `.csv`) posts every row in order. It paces init calls to TikTok's 6 per
minute, logs each post to `.tiktok/posts.jsonl`, and **skips files already posted** (matched by
content hash). So when TikTok's daily cap stops it (exit code 3, `spam_risk_too_many_posts`),
re-running the same file the next day picks up where it stopped.

```json
[
  {"video": "videos/01.mp4", "title": "Part 1 #fyp", "privacy": "SELF_ONLY", "ai": true},
  {"video": "videos/02.mp4", "title": "Part 2", "privacy": "SELF_ONLY", "disable_duet": true}
]
```

CSV columns use the same names: `video,title,privacy,draft,ai,brand_organic,brand_content,disable_comment,disable_duet,disable_stitch,cover_ms`.
Relative paths resolve against the batch file's folder. `--privacy`, `--ai` and `--draft` on the
command line set defaults for rows that leave them blank. Always `--dry-run` a batch first.

TikTok caps posts per creator per day (around 15 through the API, shared with every other app
the user posts from), so "as many as possible" means about that many per day. The tool never
tries to get around it.

## Errors worth recognising

| Code | Meaning / fix |
|---|---|
| `unaudited_client_can_only_post_to_private_accounts` | Set the account private and use `SELF_ONLY`, or get the app reviewed |
| `spam_risk_too_many_posts` / `reached_active_user_cap` | Daily limit. Exit 3. Try again tomorrow |
| `spam_risk_too_many_pending_share` | 5 drafts are already waiting in the inbox |
| `access_token_invalid` / `invalid_grant` | Log in again |
| `scope_not_authorized` | The app lacks Content Posting API scopes; add them, then log in again |
| status `FAILED` + `duration_check_failed`, `frame_rate_check_failed`, `picture_size_check_failed`, `file_format_check_failed` | Re-encode with ffmpeg to H.264 MP4, 30 fps, 1080x1920, within the length limit |

Tests: `python3 -m unittest discover -s tests` (`tests/test_tiktok_post.py`, no network).
