# bike-edit learnings

Newest first. One line per finding. These override the defaults in SKILL.md.

## User preferences
- 2026-11-02 **Shot length sweet spot ~1.5-2.5 s.** 0.64-1.28 s beat cuts (01c, 04, 04b) were 'way too fast, cut off'. 2.4-3.25 s slow-mo shots (01) were too slow. Let each shot show a COMPLETE action (approach -> pass -> exit) and cut only when the move has finished. A speed change inside one continuous clip is fine and does not count as a cut.
- 2026-11-02 **All on-screen text in English.**
- 2026-11-02 **Text only when it fits** - not on every edit by default.
- 2026-11-02 Planned: a race edit using ONLY clips from that one race, in the style of the Andorra reel.
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
- **Identifying the user:** always the same WHITE full-face helmet. Race kit = yellow/orange (Giant). Other sessions often black. Many riders in the footage wear white helmets - check kit too, and ask when unsure. Never build an edit around a rider you have not confirmed is the user.
- 3.9.2026 clips (IMG_0926a/b, 9537, 9548, 9560): rider in yellow/orange kit; IMG_9560 = podium, yellow rider on top step, banner "TORNI...". GPS = same park as the night session (60.366, 24.757), not Sappee (Sappee GPS ~61.39, 24.51 = IMG_7911/7916 on 29.8).
- 26-27.9.2026 = Sappee, Finnish DH Cup final (user won). User's chosen clips: IMG_6465 (user = distant yellow rider jumping between trees at 4.6-5.6 s - I first wrongly called it a marshal; zoom in before dismissing distant figures), IMG_6505 (user close pass by the tape 10.4-13 s), IMG_6539 (indoor prize ceremony, user in navy Giant jersey receives the red trophy ~9.4 s and holds it ~19-21 s), photos IMG_8356 (race action, plate 1), IMG_8354 (podium, user centre), IMG_8355 (panning photo, plate 1, landscape - crop centre). Close passes in 6427/6455/6472 are other riders.
- The user won the Finnish DH Cup final (they named it Sappee). Race edit postponed: **the user will upload exactly the clips they want for it** - use only those.
- iPhone, mostly 4K60 portrait (rotation metadata -90/90), some 1080p30. 60 fps clips slow-mo cleanly; 30 fps clips need speed >= 0.5.
- Night session clips at the floodlit wooden kicker are already very blue/cyan - use `grade: night`, not `aesthetic`.

## Technical
- Drive: copy -> share copy -> curl -> trash copy. Trash does NOT revoke the link; user must empty trash.
- Input-seek (`-ss` before `-i`) is essential on 4K sources; trim-from-start took ~2 min per segment.

## Feedback log
- 2026-11-02 04/04b Sappee: cuts far too short, shots end before the action does. Remade with 1.3-2.6 s shots.
- 2026-11-02 01b_night_session_fast: too fast, sped up - rejected. Do not use speed > 1.
- 2026-11-02 01_night_session: SFX "ihan hirveät", tempo too slow. Visuals not criticised.
