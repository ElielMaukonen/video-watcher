---
name: bike-edit
description: Make showy short-form MTB / cycling edits (5-20 s, 9:16) from the user's own raw clips in Google Drive - cuts, slow-mo, punch-ins, flashes, black & white moments, animated text and synthesised sound design. Use whenever the user says "tee editti", "editoi", "make an edit", "uudet videot Drivessa", points at new riding clips, or gives feedback on a previous edit. Also use to record what the user liked or disliked so the next edit is better.
---

# bike-edit

The user (Finnish, write to them in Finnish) rides MTB and wants Shorts/Reels/TikTok edits of
their clips. This skill is a living document: **after every round of feedback, update
`LEARNINGS.md` in this folder and commit it.** The skill is meant to get better with every video.

Read `LEARNINGS.md` first, every time. It overrides the defaults below.

## Pipeline

### 1. Get the clips from Drive
Drive MCP `download_file_content` returns base64 into context - useless for 5-130 MB videos.
What works:
1. `search_files` with `mimeType contains 'video/'` (+ `createdTime >` to get only new ones).
   The user's uploads have landed in My Drive root even when they said a folder name - search
   by type and date, not only by folder.
2. **Never share the originals.** `copy_file` each to `tmp-claude-<name>`, `share_file` the copy
   with `permissionId: anyoneWithLink, role: reader`.
3. Download in parallel:
   `curl -sSL -o NAME "https://drive.usercontent.google.com/download?id=ID&export=download&confirm=t"`
   and check every byte count against `fileSize`.
4. `trash_file` each copy. **Trashed files stay publicly downloadable** (verified). There is no
   permanent-delete tool, so tell the user plainly to empty the trash / delete the
   `tmp-claude-*` files permanently, then re-check with `search_files ... trashed = true` and a
   curl of one link. Do not say it is clean until both checks pass.
5. Ask for the one-time sharing consent once per session, not per file. Tell the user to use
   **Auto** mode (dropdown next to the prompt box) so it is not 80 approvals.

Raw clips go in the scratchpad (`raw/`), never in the repo.

### 2. Watch everything
`scripts/sheets.sh overview RAW OUT` → read every `G_*.jpg`. Then group clips by **scene + outfit**:
same jacket / helmet / bike / light = one edit. The user explicitly wants outfits to match within
an edit (mixing is allowed when it is on purpose). Several riders appear in the footage - group by
outfit, do not assume which one is the user unless told.

Then `scripts/sheets.sh dense RAW OUT CLIP...` for the clips you will use and pick exact times:
approach, takeoff, peak, landing, closest pass to camera, deepest berm lean.

### 3. Write a plan
`edit/plans/NN_name.json` - schema in the docstring of `edit/engine.py`. Proven structure:
- **Hook (0-1 s):** real-time approach with `zoom: push` + `riser`, straight into slow-mo of the
  best moment (`speed 0.4-0.5`, `drop`). Title text over the hook.
- **Rhythm:** hard cuts on a beat grid (e.g. 94 bpm: 0.64 s real-time hits, 1.28 s slow-mo). At most one deliberate speed-up per edit. No synthesised SFX. Alternate a short real-time hit (`punch`)
  with a slow-mo payoff (1.2-3.5 s). Beat drums drop out during slow-mo, come back on the cut.
- **B&W** on 1-2 slow-mo moments, not more.
- **Finale:** biggest move, slow, then real-time landing with `shake`.
- 12-16 s total. Title style "aesthetic": small white serif, lowercase-ish, italic subtitle in
  parentheses, upper fifth of frame (from the user's reference shorts).

### 4. Render
```bash
python3 edit/engine.py edit/plans/NN_name.json OUT.mp4 --src RAW_DIR
```
4 CPU cores: optical-flow slow-mo (`minterpolate mci`) is the slow part, ~1-2 min per slow
segment on 4K. Run in the background, one render at a time.

### 5. Check before sending
Pull 6-8 frames from the output and look at them: text legible and not covering the rider,
grade not crushed, B&W where intended, no black/frozen frames at segment joins. Check audio
peaks with `ffmpeg -af volumedetect`. Fix, then `SendUserFile` the MP4.

### 6. Learn
Ask what to change. Write every concrete preference into `LEARNINGS.md` (dated, one line each),
adjust defaults in `engine.py` if it is a general preference, commit and push to
`ElielMaukonen/video-watcher`.

## Known limits
- No music library. The engine synthesises a minimal beat + SFX. Suggest the user add a trending
  sound in the app; offer a version with `pad_gain: 0` and drums off if they will.
- YouTube downloads are blocked from this cloud host. For reference videos, use public
  thumbnails (`i.ytimg.com/vi/ID/{hq1,hq2,hq3,maxresdefault}.jpg`) or ask for screenshots.
