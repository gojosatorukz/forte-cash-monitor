# 08 — Synthetic test-clip prompts (text-to-video)

> **Robustness testing only.** These clips are AI-generated and are never used for training, threshold tuning or any metric reported to the jury. No reference images: never feed our dataset frames (X-*.mp4) or team footage into the video tool. Save every clip to `robust/synthetic/` as `<scene>_<A|B>_<viol|clean>_<tool>.mp4` together with a sidecar `<same name>.txt` containing the **exact prompt text**, the tool/model name and version, the generation date, and the expected label (type + second, or `clean`). Record the label from what the clip actually shows, not from the prompt — generators drift.

Context: `docs/07_procedures_coverage.md` (d) — these target the unseen/weakly-evidenced types where `38f9946` has a rule but little real recall evidence: T13 phone, T14 camera covered, T10 pocketing, T6 second person, T1 cash left, T3 safe open / key in lock.

## How to use

- Two camera angles per violation: **A** = high corner CCTV, **B** = ceiling top-down. One **clean** counterpart per scene (angle A; swap in the angle-B sentence to get a B clean twin if the tool budget allows).
- Keep each scene's violation and clean prompts identical except for the action block, so the pair differs in one step (the organizers' matched-pair style).
- Expected label second = the moment the wrong state becomes final (column *label s*).
- If the tool caps length, generate 10 s and compress the timeline proportionally; note it in the sidecar.
- Reject a clip if: the camera moves, the action is not visible, banknotes show real-currency designs or text, or a face looks like a real person.

| # | Scene | Target type | Expected label s (viol.) |
|---|---|---|---|
| 1 | `phone` — Phone use at the cash desk | T13 | 6 |
| 2 | `camcover` — Camera covered | T14 | 7 |
| 3 | `pocket` — Cashier pockets cash | T10 | 9 |
| 4 | `handbag` — Cash into personal handbag | T10/T11 | 7 |
| 5 | `intruder` — Second person enters the cash room | T6/T99 | 8 |
| 6 | `cashleft` — Cash left on desk when cashier leaves | T1 | 11 |
| 7 | `safeopen` — Safe left open | T3 | 7 |
| 8 | `safekey` — Safe key left in the lock, cashier leaves | T3 | 8 |

## 1. Phone use at the cash desk (`phone`, T13)

**phone_A_viol** (label ≈ 6 s)

```text
Security-camera footage look: fixed camera, absolutely no camera motion, no zoom, no cuts, slight wide-angle distortion, mild compression noise, muted colours, 720p, timestamp-free. Duration about 15 seconds, real-time speed. No logos, no text, no brand names, no recognisable real people. Camera mounted high in a ceiling corner, looking diagonally down at the desk, the safe and the glass door, all in frame. A small windowless bank-office cash room, about 3 by 4 metres, beige walls, grey vinyl floor. One plain office desk with: a grey electric banknote counting machine, an open silver laptop, a few rubber-banded stacks of generic unbranded paper banknotes, a pen and a paper form. Next to the desk stands a waist-high grey steel safe with a key lock and a single door. On the far wall is a glass office door with a metal frame leading to a corridor. Realistic cool-white fluorescent office lighting, slightly flat. A female cashier in her thirties, dark office blouse, hair tied back, sits at the desk. Action timeline: 0-4 s: she counts a stack of banknotes through the counting machine. 4-6 s: she takes a smartphone from her blouse pocket. 6-14 s: she holds the smartphone in her right hand in front of her face, scrolls and types with her thumb, banknotes lying on the desk next to her. 14-15 s: she still holds the phone.
```

**phone_B_viol** (label ≈ 6 s)

```text
Security-camera footage look: fixed camera, absolutely no camera motion, no zoom, no cuts, slight wide-angle distortion, mild compression noise, muted colours, 720p, timestamp-free. Duration about 15 seconds, real-time speed. No logos, no text, no brand names, no recognisable real people. Camera mounted on the ceiling directly above the desk, looking straight down (top-down view); the desk surface, the cashier's chair, the top of the safe with its door, and the door threshold are visible. A small windowless bank-office cash room, about 3 by 4 metres, beige walls, grey vinyl floor. One plain office desk with: a grey electric banknote counting machine, an open silver laptop, a few rubber-banded stacks of generic unbranded paper banknotes, a pen and a paper form. Next to the desk stands a waist-high grey steel safe with a key lock and a single door. On the far wall is a glass office door with a metal frame leading to a corridor. Realistic cool-white fluorescent office lighting, slightly flat. A female cashier in her thirties, dark office blouse, hair tied back, sits at the desk. Action timeline: 0-4 s: she counts a stack of banknotes through the counting machine. 4-6 s: she takes a smartphone from her blouse pocket. 6-14 s: she holds the smartphone in her right hand in front of her face, scrolls and types with her thumb, banknotes lying on the desk next to her. 14-15 s: she still holds the phone.
```

**phone_A_clean** (label: clean)

```text
Security-camera footage look: fixed camera, absolutely no camera motion, no zoom, no cuts, slight wide-angle distortion, mild compression noise, muted colours, 720p, timestamp-free. Duration about 15 seconds, real-time speed. No logos, no text, no brand names, no recognisable real people. Camera mounted high in a ceiling corner, looking diagonally down at the desk, the safe and the glass door, all in frame. A small windowless bank-office cash room, about 3 by 4 metres, beige walls, grey vinyl floor. One plain office desk with: a grey electric banknote counting machine, an open silver laptop, a few rubber-banded stacks of generic unbranded paper banknotes, a pen and a paper form. Next to the desk stands a waist-high grey steel safe with a key lock and a single door. On the far wall is a glass office door with a metal frame leading to a corridor. Realistic cool-white fluorescent office lighting, slightly flat. A female cashier in her thirties, dark office blouse, hair tied back, sits at the desk. Action timeline: 0-4 s: she counts a stack of banknotes through the counting machine. 4-12 s: she squares the stack, writes on the paper form, and types briefly on the laptop. 12-15 s: she places the stack in the safe. Nothing else in her hands; no phone anywhere in the room.
```

## 2. Camera covered (`camcover`, T14)

**camcover_A_viol** (label ≈ 7 s)

```text
Security-camera footage look: fixed camera, absolutely no camera motion, no zoom, no cuts, slight wide-angle distortion, mild compression noise, muted colours, 720p, timestamp-free. Duration about 15 seconds, real-time speed. No logos, no text, no brand names, no recognisable real people. Camera mounted high in a ceiling corner, looking diagonally down at the desk, the safe and the glass door, all in frame. A small windowless bank-office cash room, about 3 by 4 metres, beige walls, grey vinyl floor. One plain office desk with: a grey electric banknote counting machine, an open silver laptop, a few rubber-banded stacks of generic unbranded paper banknotes, a pen and a paper form. Next to the desk stands a waist-high grey steel safe with a key lock and a single door. On the far wall is a glass office door with a metal frame leading to a corridor. Realistic cool-white fluorescent office lighting, slightly flat. A female cashier in her thirties, dark office blouse, hair tied back, sits at the desk. Action timeline: 0-5 s: she counts banknotes at the desk. 5-7 s: she stands up, walks toward the camera and reaches up. 7-15 s: her palm and then a sheet of paper fully cover the lens; the image becomes a dark, blurry, almost uniform frame for the rest of the clip.
```

**camcover_B_viol** (label ≈ 7 s)

```text
Security-camera footage look: fixed camera, absolutely no camera motion, no zoom, no cuts, slight wide-angle distortion, mild compression noise, muted colours, 720p, timestamp-free. Duration about 15 seconds, real-time speed. No logos, no text, no brand names, no recognisable real people. Camera mounted on the ceiling directly above the desk, looking straight down (top-down view); the desk surface, the cashier's chair, the top of the safe with its door, and the door threshold are visible. A small windowless bank-office cash room, about 3 by 4 metres, beige walls, grey vinyl floor. One plain office desk with: a grey electric banknote counting machine, an open silver laptop, a few rubber-banded stacks of generic unbranded paper banknotes, a pen and a paper form. Next to the desk stands a waist-high grey steel safe with a key lock and a single door. On the far wall is a glass office door with a metal frame leading to a corridor. Realistic cool-white fluorescent office lighting, slightly flat. A female cashier in her thirties, dark office blouse, hair tied back, sits at the desk. Action timeline: 0-5 s: she counts banknotes at the desk. 5-7 s: she stands up, walks toward the camera and reaches up. 7-15 s: her palm and then a sheet of paper fully cover the lens; the image becomes a dark, blurry, almost uniform frame for the rest of the clip.
```

**camcover_A_clean** (label: clean)

```text
Security-camera footage look: fixed camera, absolutely no camera motion, no zoom, no cuts, slight wide-angle distortion, mild compression noise, muted colours, 720p, timestamp-free. Duration about 15 seconds, real-time speed. No logos, no text, no brand names, no recognisable real people. Camera mounted high in a ceiling corner, looking diagonally down at the desk, the safe and the glass door, all in frame. A small windowless bank-office cash room, about 3 by 4 metres, beige walls, grey vinyl floor. One plain office desk with: a grey electric banknote counting machine, an open silver laptop, a few rubber-banded stacks of generic unbranded paper banknotes, a pen and a paper form. Next to the desk stands a waist-high grey steel safe with a key lock and a single door. On the far wall is a glass office door with a metal frame leading to a corridor. Realistic cool-white fluorescent office lighting, slightly flat. A female cashier in her thirties, dark office blouse, hair tied back, sits at the desk. Action timeline: 0-5 s: she counts banknotes at the desk. 5-7 s: she stands up and walks past under the camera to a shelf and back, never touching the camera. 7-15 s: she sits and continues counting; the room stays fully visible the whole time. (Optional hard negative: the room lights dim slightly at 10 s.)
```

## 3. Cashier pockets cash (`pocket`, T10)

**pocket_A_viol** (label ≈ 9 s)

```text
Security-camera footage look: fixed camera, absolutely no camera motion, no zoom, no cuts, slight wide-angle distortion, mild compression noise, muted colours, 720p, timestamp-free. Duration about 15 seconds, real-time speed. No logos, no text, no brand names, no recognisable real people. Camera mounted high in a ceiling corner, looking diagonally down at the desk, the safe and the glass door, all in frame. A small windowless bank-office cash room, about 3 by 4 metres, beige walls, grey vinyl floor. One plain office desk with: a grey electric banknote counting machine, an open silver laptop, a few rubber-banded stacks of generic unbranded paper banknotes, a pen and a paper form. Next to the desk stands a waist-high grey steel safe with a key lock and a single door. On the far wall is a glass office door with a metal frame leading to a corridor. Realistic cool-white fluorescent office lighting, slightly flat. A female cashier in her thirties, dark office blouse, hair tied back, sits at the desk. Action timeline: 0-5 s: she runs a stack of banknotes through the counting machine. 5-8 s: she splits the stack in two with both hands. 8-10 s: she slides the smaller part off the desk edge into the side pocket of her trousers, glancing at the door. 10-15 s: she puts the remaining larger part into the safe and closes it.
```

**pocket_B_viol** (label ≈ 9 s)

```text
Security-camera footage look: fixed camera, absolutely no camera motion, no zoom, no cuts, slight wide-angle distortion, mild compression noise, muted colours, 720p, timestamp-free. Duration about 15 seconds, real-time speed. No logos, no text, no brand names, no recognisable real people. Camera mounted on the ceiling directly above the desk, looking straight down (top-down view); the desk surface, the cashier's chair, the top of the safe with its door, and the door threshold are visible. A small windowless bank-office cash room, about 3 by 4 metres, beige walls, grey vinyl floor. One plain office desk with: a grey electric banknote counting machine, an open silver laptop, a few rubber-banded stacks of generic unbranded paper banknotes, a pen and a paper form. Next to the desk stands a waist-high grey steel safe with a key lock and a single door. On the far wall is a glass office door with a metal frame leading to a corridor. Realistic cool-white fluorescent office lighting, slightly flat. A female cashier in her thirties, dark office blouse, hair tied back, sits at the desk. Action timeline: 0-5 s: she runs a stack of banknotes through the counting machine. 5-8 s: she splits the stack in two with both hands. 8-10 s: she slides the smaller part off the desk edge into the side pocket of her trousers, glancing at the door. 10-15 s: she puts the remaining larger part into the safe and closes it.
```

**pocket_A_clean** (label: clean)

```text
Security-camera footage look: fixed camera, absolutely no camera motion, no zoom, no cuts, slight wide-angle distortion, mild compression noise, muted colours, 720p, timestamp-free. Duration about 15 seconds, real-time speed. No logos, no text, no brand names, no recognisable real people. Camera mounted high in a ceiling corner, looking diagonally down at the desk, the safe and the glass door, all in frame. A small windowless bank-office cash room, about 3 by 4 metres, beige walls, grey vinyl floor. One plain office desk with: a grey electric banknote counting machine, an open silver laptop, a few rubber-banded stacks of generic unbranded paper banknotes, a pen and a paper form. Next to the desk stands a waist-high grey steel safe with a key lock and a single door. On the far wall is a glass office door with a metal frame leading to a corridor. Realistic cool-white fluorescent office lighting, slightly flat. A female cashier in her thirties, dark office blouse, hair tied back, sits at the desk. Action timeline: 0-5 s: she runs a stack of banknotes through the counting machine. 5-8 s: she squares the whole stack and wraps a band around it. 8-15 s: she puts the whole stack into the safe and closes it; her hands never go to her pockets or body.
```

## 4. Cash into personal handbag (`handbag`, T10/T11)

**handbag_A_viol** (label ≈ 7 s)

```text
Security-camera footage look: fixed camera, absolutely no camera motion, no zoom, no cuts, slight wide-angle distortion, mild compression noise, muted colours, 720p, timestamp-free. Duration about 15 seconds, real-time speed. No logos, no text, no brand names, no recognisable real people. Camera mounted high in a ceiling corner, looking diagonally down at the desk, the safe and the glass door, all in frame. A small windowless bank-office cash room, about 3 by 4 metres, beige walls, grey vinyl floor. One plain office desk with: a grey electric banknote counting machine, an open silver laptop, a few rubber-banded stacks of generic unbranded paper banknotes, a pen and a paper form. Next to the desk stands a waist-high grey steel safe with a key lock and a single door. On the far wall is a glass office door with a metal frame leading to a corridor. Realistic cool-white fluorescent office lighting, slightly flat. A female cashier in her thirties, dark office blouse, hair tied back, sits at the desk. Action timeline: A brown leather personal handbag sits on the floor next to her chair. 0-5 s: she counts banknotes on the desk. 5-9 s: she takes several banknotes from the top of the stack, leans down and drops them into the open handbag. 9-15 s: she zips the handbag and continues working.
```

**handbag_B_viol** (label ≈ 7 s)

```text
Security-camera footage look: fixed camera, absolutely no camera motion, no zoom, no cuts, slight wide-angle distortion, mild compression noise, muted colours, 720p, timestamp-free. Duration about 15 seconds, real-time speed. No logos, no text, no brand names, no recognisable real people. Camera mounted on the ceiling directly above the desk, looking straight down (top-down view); the desk surface, the cashier's chair, the top of the safe with its door, and the door threshold are visible. A small windowless bank-office cash room, about 3 by 4 metres, beige walls, grey vinyl floor. One plain office desk with: a grey electric banknote counting machine, an open silver laptop, a few rubber-banded stacks of generic unbranded paper banknotes, a pen and a paper form. Next to the desk stands a waist-high grey steel safe with a key lock and a single door. On the far wall is a glass office door with a metal frame leading to a corridor. Realistic cool-white fluorescent office lighting, slightly flat. A female cashier in her thirties, dark office blouse, hair tied back, sits at the desk. Action timeline: A brown leather personal handbag sits on the floor next to her chair. 0-5 s: she counts banknotes on the desk. 5-9 s: she takes several banknotes from the top of the stack, leans down and drops them into the open handbag. 9-15 s: she zips the handbag and continues working.
```

**handbag_A_clean** (label: clean)

```text
Security-camera footage look: fixed camera, absolutely no camera motion, no zoom, no cuts, slight wide-angle distortion, mild compression noise, muted colours, 720p, timestamp-free. Duration about 15 seconds, real-time speed. No logos, no text, no brand names, no recognisable real people. Camera mounted high in a ceiling corner, looking diagonally down at the desk, the safe and the glass door, all in frame. A small windowless bank-office cash room, about 3 by 4 metres, beige walls, grey vinyl floor. One plain office desk with: a grey electric banknote counting machine, an open silver laptop, a few rubber-banded stacks of generic unbranded paper banknotes, a pen and a paper form. Next to the desk stands a waist-high grey steel safe with a key lock and a single door. On the far wall is a glass office door with a metal frame leading to a corridor. Realistic cool-white fluorescent office lighting, slightly flat. A female cashier in her thirties, dark office blouse, hair tied back, sits at the desk. Action timeline: No handbag in the room. 0-5 s: she counts banknotes on the desk. 5-15 s: she bands the stack and puts it into the safe, then closes it.
```

## 5. Second person enters the cash room (`intruder`, T6/T99)

**intruder_A_viol** (label ≈ 8 s)

```text
Security-camera footage look: fixed camera, absolutely no camera motion, no zoom, no cuts, slight wide-angle distortion, mild compression noise, muted colours, 720p, timestamp-free. Duration about 18 seconds, real-time speed. No logos, no text, no brand names, no recognisable real people. Camera mounted high in a ceiling corner, looking diagonally down at the desk, the safe and the glass door, all in frame. A small windowless bank-office cash room, about 3 by 4 metres, beige walls, grey vinyl floor. One plain office desk with: a grey electric banknote counting machine, an open silver laptop, a few rubber-banded stacks of generic unbranded paper banknotes, a pen and a paper form. Next to the desk stands a waist-high grey steel safe with a key lock and a single door. On the far wall is a glass office door with a metal frame leading to a corridor. Realistic cool-white fluorescent office lighting, slightly flat. A female cashier in her thirties, dark office blouse, hair tied back, sits at the desk. Action timeline: 0-5 s: she counts banknotes at the desk, the glass door is closed. 5-8 s: a man in a dark hoodie, not in office clothes, opens the glass door and walks in. 8-14 s: he walks behind the desk to the cashier's side, stands beside her and leans over the cash; she keeps working. 14-18 s: he picks up a stack of banknotes and looks at it.
```

**intruder_B_viol** (label ≈ 8 s)

```text
Security-camera footage look: fixed camera, absolutely no camera motion, no zoom, no cuts, slight wide-angle distortion, mild compression noise, muted colours, 720p, timestamp-free. Duration about 18 seconds, real-time speed. No logos, no text, no brand names, no recognisable real people. Camera mounted on the ceiling directly above the desk, looking straight down (top-down view); the desk surface, the cashier's chair, the top of the safe with its door, and the door threshold are visible. A small windowless bank-office cash room, about 3 by 4 metres, beige walls, grey vinyl floor. One plain office desk with: a grey electric banknote counting machine, an open silver laptop, a few rubber-banded stacks of generic unbranded paper banknotes, a pen and a paper form. Next to the desk stands a waist-high grey steel safe with a key lock and a single door. On the far wall is a glass office door with a metal frame leading to a corridor. Realistic cool-white fluorescent office lighting, slightly flat. A female cashier in her thirties, dark office blouse, hair tied back, sits at the desk. Action timeline: 0-5 s: she counts banknotes at the desk, the glass door is closed. 5-8 s: a man in a dark hoodie, not in office clothes, opens the glass door and walks in. 8-14 s: he walks behind the desk to the cashier's side, stands beside her and leans over the cash; she keeps working. 14-18 s: he picks up a stack of banknotes and looks at it.
```

**intruder_A_clean** (label: clean)

```text
Security-camera footage look: fixed camera, absolutely no camera motion, no zoom, no cuts, slight wide-angle distortion, mild compression noise, muted colours, 720p, timestamp-free. Duration about 18 seconds, real-time speed. No logos, no text, no brand names, no recognisable real people. Camera mounted high in a ceiling corner, looking diagonally down at the desk, the safe and the glass door, all in frame. A small windowless bank-office cash room, about 3 by 4 metres, beige walls, grey vinyl floor. One plain office desk with: a grey electric banknote counting machine, an open silver laptop, a few rubber-banded stacks of generic unbranded paper banknotes, a pen and a paper form. Next to the desk stands a waist-high grey steel safe with a key lock and a single door. On the far wall is a glass office door with a metal frame leading to a corridor. Realistic cool-white fluorescent office lighting, slightly flat. A female cashier in her thirties, dark office blouse, hair tied back, sits at the desk. Action timeline: 0-5 s: she counts banknotes at the desk, the glass door is closed. 5-8 s: a man in the corridor walks past the glass door without stopping or opening it. 8-18 s: she continues counting alone; nobody enters the room.
```

## 6. Cash left on desk when cashier leaves (`cashleft`, T1)

**cashleft_A_viol** (label ≈ 11 s)

```text
Security-camera footage look: fixed camera, absolutely no camera motion, no zoom, no cuts, slight wide-angle distortion, mild compression noise, muted colours, 720p, timestamp-free. Duration about 16 seconds, real-time speed. No logos, no text, no brand names, no recognisable real people. Camera mounted high in a ceiling corner, looking diagonally down at the desk, the safe and the glass door, all in frame. A small windowless bank-office cash room, about 3 by 4 metres, beige walls, grey vinyl floor. One plain office desk with: a grey electric banknote counting machine, an open silver laptop, a few rubber-banded stacks of generic unbranded paper banknotes, a pen and a paper form. Next to the desk stands a waist-high grey steel safe with a key lock and a single door. On the far wall is a glass office door with a metal frame leading to a corridor. Realistic cool-white fluorescent office lighting, slightly flat. A female cashier in her thirties, dark office blouse, hair tied back, sits at the desk. Action timeline: 0-5 s: she counts banknotes at the desk. 5-8 s: she stands up, leaving three banded stacks of banknotes and loose notes spread on the desk, the safe door shut. 8-11 s: she walks out through the glass door and closes it behind her. 11-16 s: the room is empty; the cash stays on the desk.
```

**cashleft_B_viol** (label ≈ 11 s)

```text
Security-camera footage look: fixed camera, absolutely no camera motion, no zoom, no cuts, slight wide-angle distortion, mild compression noise, muted colours, 720p, timestamp-free. Duration about 16 seconds, real-time speed. No logos, no text, no brand names, no recognisable real people. Camera mounted on the ceiling directly above the desk, looking straight down (top-down view); the desk surface, the cashier's chair, the top of the safe with its door, and the door threshold are visible. A small windowless bank-office cash room, about 3 by 4 metres, beige walls, grey vinyl floor. One plain office desk with: a grey electric banknote counting machine, an open silver laptop, a few rubber-banded stacks of generic unbranded paper banknotes, a pen and a paper form. Next to the desk stands a waist-high grey steel safe with a key lock and a single door. On the far wall is a glass office door with a metal frame leading to a corridor. Realistic cool-white fluorescent office lighting, slightly flat. A female cashier in her thirties, dark office blouse, hair tied back, sits at the desk. Action timeline: 0-5 s: she counts banknotes at the desk. 5-8 s: she stands up, leaving three banded stacks of banknotes and loose notes spread on the desk, the safe door shut. 8-11 s: she walks out through the glass door and closes it behind her. 11-16 s: the room is empty; the cash stays on the desk.
```

**cashleft_A_clean** (label: clean)

```text
Security-camera footage look: fixed camera, absolutely no camera motion, no zoom, no cuts, slight wide-angle distortion, mild compression noise, muted colours, 720p, timestamp-free. Duration about 16 seconds, real-time speed. No logos, no text, no brand names, no recognisable real people. Camera mounted high in a ceiling corner, looking diagonally down at the desk, the safe and the glass door, all in frame. A small windowless bank-office cash room, about 3 by 4 metres, beige walls, grey vinyl floor. One plain office desk with: a grey electric banknote counting machine, an open silver laptop, a few rubber-banded stacks of generic unbranded paper banknotes, a pen and a paper form. Next to the desk stands a waist-high grey steel safe with a key lock and a single door. On the far wall is a glass office door with a metal frame leading to a corridor. Realistic cool-white fluorescent office lighting, slightly flat. A female cashier in her thirties, dark office blouse, hair tied back, sits at the desk. Action timeline: 0-5 s: she counts banknotes at the desk. 5-8 s: she puts all stacks into the safe, closes and locks it, the desk is left with only the laptop and counting machine. 8-11 s: she walks out through the glass door and closes it. 11-16 s: the room is empty, the desk holds no cash.
```

## 7. Safe left open (`safeopen`, T3)

**safeopen_A_viol** (label ≈ 7 s)

```text
Security-camera footage look: fixed camera, absolutely no camera motion, no zoom, no cuts, slight wide-angle distortion, mild compression noise, muted colours, 720p, timestamp-free. Duration about 16 seconds, real-time speed. No logos, no text, no brand names, no recognisable real people. Camera mounted high in a ceiling corner, looking diagonally down at the desk, the safe and the glass door, all in frame. A small windowless bank-office cash room, about 3 by 4 metres, beige walls, grey vinyl floor. One plain office desk with: a grey electric banknote counting machine, an open silver laptop, a few rubber-banded stacks of generic unbranded paper banknotes, a pen and a paper form. Next to the desk stands a waist-high grey steel safe with a key lock and a single door. On the far wall is a glass office door with a metal frame leading to a corridor. Realistic cool-white fluorescent office lighting, slightly flat. A female cashier in her thirties, dark office blouse, hair tied back, sits at the desk. Action timeline: 0-4 s: she kneels at the grey safe, the safe door is open, and she takes out a banded stack of banknotes; other stacks remain visible on the shelf inside. 4-7 s: she walks back to the desk leaving the safe door wide open. 7-16 s: she sits at the desk and works on the laptop with her back to the safe; the safe stays wide open the whole time.
```

**safeopen_B_viol** (label ≈ 7 s)

```text
Security-camera footage look: fixed camera, absolutely no camera motion, no zoom, no cuts, slight wide-angle distortion, mild compression noise, muted colours, 720p, timestamp-free. Duration about 16 seconds, real-time speed. No logos, no text, no brand names, no recognisable real people. Camera mounted on the ceiling directly above the desk, looking straight down (top-down view); the desk surface, the cashier's chair, the top of the safe with its door, and the door threshold are visible. A small windowless bank-office cash room, about 3 by 4 metres, beige walls, grey vinyl floor. One plain office desk with: a grey electric banknote counting machine, an open silver laptop, a few rubber-banded stacks of generic unbranded paper banknotes, a pen and a paper form. Next to the desk stands a waist-high grey steel safe with a key lock and a single door. On the far wall is a glass office door with a metal frame leading to a corridor. Realistic cool-white fluorescent office lighting, slightly flat. A female cashier in her thirties, dark office blouse, hair tied back, sits at the desk. Action timeline: 0-4 s: she kneels at the grey safe, the safe door is open, and she takes out a banded stack of banknotes; other stacks remain visible on the shelf inside. 4-7 s: she walks back to the desk leaving the safe door wide open. 7-16 s: she sits at the desk and works on the laptop with her back to the safe; the safe stays wide open the whole time.
```

**safeopen_A_clean** (label: clean)

```text
Security-camera footage look: fixed camera, absolutely no camera motion, no zoom, no cuts, slight wide-angle distortion, mild compression noise, muted colours, 720p, timestamp-free. Duration about 16 seconds, real-time speed. No logos, no text, no brand names, no recognisable real people. Camera mounted high in a ceiling corner, looking diagonally down at the desk, the safe and the glass door, all in frame. A small windowless bank-office cash room, about 3 by 4 metres, beige walls, grey vinyl floor. One plain office desk with: a grey electric banknote counting machine, an open silver laptop, a few rubber-banded stacks of generic unbranded paper banknotes, a pen and a paper form. Next to the desk stands a waist-high grey steel safe with a key lock and a single door. On the far wall is a glass office door with a metal frame leading to a corridor. Realistic cool-white fluorescent office lighting, slightly flat. A female cashier in her thirties, dark office blouse, hair tied back, sits at the desk. Action timeline: 0-4 s: she kneels at the grey safe, the safe door is open, and she takes out a banded stack of banknotes. 4-6 s: she shuts the safe door and turns the key, removing the key. 6-16 s: she sits at the desk and works on the laptop; the safe stays closed.
```

## 8. Safe key left in the lock, cashier leaves (`safekey`, T3)

**safekey_A_viol** (label ≈ 8 s)

```text
Security-camera footage look: fixed camera, absolutely no camera motion, no zoom, no cuts, slight wide-angle distortion, mild compression noise, muted colours, 720p, timestamp-free. Duration about 15 seconds, real-time speed. No logos, no text, no brand names, no recognisable real people. Camera mounted high in a ceiling corner, looking diagonally down at the desk, the safe and the glass door, all in frame. A small windowless bank-office cash room, about 3 by 4 metres, beige walls, grey vinyl floor. One plain office desk with: a grey electric banknote counting machine, an open silver laptop, a few rubber-banded stacks of generic unbranded paper banknotes, a pen and a paper form. Next to the desk stands a waist-high grey steel safe with a key lock and a single door. On the far wall is a glass office door with a metal frame leading to a corridor. Realistic cool-white fluorescent office lighting, slightly flat. A female cashier in her thirties, dark office blouse, hair tied back, sits at the desk. Action timeline: The safe key has a large bright red plastic tag on a ring. 0-4 s: she closes the safe door and turns the key, but leaves the key with the red tag hanging in the lock. 4-8 s: she stands, walks out through the glass door and closes it. 8-15 s: the room is empty; the red key tag is clearly visible hanging from the safe lock.
```

**safekey_B_viol** (label ≈ 8 s)

```text
Security-camera footage look: fixed camera, absolutely no camera motion, no zoom, no cuts, slight wide-angle distortion, mild compression noise, muted colours, 720p, timestamp-free. Duration about 15 seconds, real-time speed. No logos, no text, no brand names, no recognisable real people. Camera mounted on the ceiling directly above the desk, looking straight down (top-down view); the desk surface, the cashier's chair, the top of the safe with its door, and the door threshold are visible. A small windowless bank-office cash room, about 3 by 4 metres, beige walls, grey vinyl floor. One plain office desk with: a grey electric banknote counting machine, an open silver laptop, a few rubber-banded stacks of generic unbranded paper banknotes, a pen and a paper form. Next to the desk stands a waist-high grey steel safe with a key lock and a single door. On the far wall is a glass office door with a metal frame leading to a corridor. Realistic cool-white fluorescent office lighting, slightly flat. A female cashier in her thirties, dark office blouse, hair tied back, sits at the desk. Action timeline: The safe key has a large bright red plastic tag on a ring. 0-4 s: she closes the safe door and turns the key, but leaves the key with the red tag hanging in the lock. 4-8 s: she stands, walks out through the glass door and closes it. 8-15 s: the room is empty; the red key tag is clearly visible hanging from the safe lock.
```

**safekey_A_clean** (label: clean)

```text
Security-camera footage look: fixed camera, absolutely no camera motion, no zoom, no cuts, slight wide-angle distortion, mild compression noise, muted colours, 720p, timestamp-free. Duration about 15 seconds, real-time speed. No logos, no text, no brand names, no recognisable real people. Camera mounted high in a ceiling corner, looking diagonally down at the desk, the safe and the glass door, all in frame. A small windowless bank-office cash room, about 3 by 4 metres, beige walls, grey vinyl floor. One plain office desk with: a grey electric banknote counting machine, an open silver laptop, a few rubber-banded stacks of generic unbranded paper banknotes, a pen and a paper form. Next to the desk stands a waist-high grey steel safe with a key lock and a single door. On the far wall is a glass office door with a metal frame leading to a corridor. Realistic cool-white fluorescent office lighting, slightly flat. A female cashier in her thirties, dark office blouse, hair tied back, sits at the desk. Action timeline: The safe key has a large bright red plastic tag on a ring. 0-4 s: she closes the safe door, turns the key, pulls the key out and puts it in her pocket. 4-8 s: she stands, walks out through the glass door and closes it. 8-15 s: the room is empty; the safe lock has no key in it.
```
