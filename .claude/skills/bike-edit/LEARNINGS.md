# bike-edit learnings

Newest first. One line per finding. These override the defaults in SKILL.md.

## User preferences
- 2026-11-02 **Speed-ups (speed > 1) only rarely: at most ONE clear, deliberate moment per edit, and only when it suits the video.** 01b (2-2.8x ramps on every clip) was 'way too fast'. "Tempo" means faster CUTTING: shorter shots, hard cuts on the beat; default is real-time + slow-mo.
- 2026-11-02 Loves the big red squashed-serif titles in the Andorra reel (style `red_title`).
- 2026-11-02 **Synthesised SFX (whoosh/impact/riser/drop, numpy) sound terrible - do not use them.**
- 2026-11-02 **Tempo too slow** in edit 1 (15 s, 8 segments, slow-mo 2.4-3.25 s each). Cut much faster.
- 2026-11-02 References the user likes: Brandon Semenuk and Jackson Goldstone fan edits (YouTube shorts fQGryqEwego, mnWoyZdRgYI, UQXxn8GSNuI, CVsxdTYdv34, dto3v8FvTB0, lSTp64wObfk).
- 2026-11-02 Style: "aesthetic" (warm faded grade, small serif titles), the first of the two reference shorts. Second reference (gritty square sepia frame) not chosen.
- 2026-11-02 Wants: animations, sound effects, on-screen text, cuts, slow-mo, black & white moments.
- 2026-11-02 Outfits should match within one edit; mixing allowed when deliberate.
- 2026-11-02 Clips are 5-20 s; edits short-form vertical.

## Reference analysis
- 2026-11-02 Screen recordings uploaded to Drive are the way to see reference videos (YouTube is blocked here; thumbnails give only 3-4 frames and no audio). Ask for screen recordings.
- 2026-11-02 Ref "ScreenRecording 20-23-00" (Instagram reel, race edit, Andorra Round 6, ~30 s): music ~94 bpm with dense hi-hat onsets every ~0.25 s; hard cuts (no flashes), shots 0.5-2.5 s; huge full-width RED condensed serif titles ("ANDORRA", "ROUND6") as their own beats; B&W close-ups of rider (helmet, portrait) intercut with colour riding; story arc: intro -> start gate -> riding -> celebration -> podium champagne -> title card.
- 2026-11-02 Thumbnails of the 6 YouTube refs (Semenuk / Goldstone fan edits): velocity speed ramps, tight crops, small caps trick labels at bottom (MANUAL, TAIL WHIP, SLOWMO), letterbox, B&W/colour alternation.

## Footage notes
- iPhone, mostly 4K60 portrait (rotation metadata -90/90), some 1080p30. 60 fps clips slow-mo cleanly; 30 fps clips need speed >= 0.5.
- Night session clips at the floodlit wooden kicker are already very blue/cyan - use `grade: night`, not `aesthetic`.

## Technical
- Drive: copy -> share copy -> curl -> trash copy. Trash does NOT revoke the link; user must empty trash.
- Input-seek (`-ss` before `-i`) is essential on 4K sources; trim-from-start took ~2 min per segment.

## Feedback log
- 2026-11-02 01b_night_session_fast: too fast, sped up - rejected. Do not use speed > 1.
- 2026-11-02 01_night_session: SFX "ihan hirveät", tempo too slow. Visuals not criticised.
