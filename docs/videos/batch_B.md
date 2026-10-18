# Batch B: X-14 to X-23, per-video analysis

Sources: 1 fps contact sheets plus 2 fps frames (464x848 portrait). Durations are estimated from the frame count (n/2 s).
People are described by role and clothing only.

## Common scene and camera setups

Every video shows the same small cash room. It has a wooden desk with a black banknote-counting machine on the left, a desk phone, and a black laptop on the right. A light-grey metal **safe** (a small cabinet on a white base) stands to the right of the desk against the wall. Its door faces left, toward the glass partition, and a key goes into a keyhole on the door's left edge. A **glass door/partition** at the back opens to an open-plan office. People outside are visible through the glass but are not in the room. A large white table and chair occupy the foreground, which is dead space.

Two camera setups appear:
- **Angle A (front):** the camera faces the cashier, who sits behind the desk facing it. The safe and laptop are on the right, the door is top-left. Used in X-14 (slightly wider view), X-15, X-20, X-21, X-22, X-23.
- **Angle B (rear/side):** the camera sits behind and to the right of the cashier, so the visitor chair across the desk and the door are visible. Used in X-16, X-17, X-18, X-19 (client and cash-collector scenarios).

Cashier: a woman in a pink cardigan and black trousers (the same actor in every video).
Visitors: (a) a man in a dark jacket over a dark shirt, who acts as client or cash collector depending on the video; (b) a man in a white long-sleeve shirt with glasses, who acts as a second client.
Valuables: green fabric cash bags (инкассаторские сумки) and banknote bundles (light blue/teal notes).

---

## X-14 — ~142 s — CLEAN (angle A, wider)

**Timeline**
- 0–30 s: the cashier stands at the desk facing the camera and handles one or more green cash bags (opening them, pulling tags/seals). Her hands and the bags stay in front of the camera the whole time.
- 31–34 s: she takes banknote bundles out of the bag and lays them on the desk in view.
- 35–39 s: she holds the open bag up and turns it toward the camera, apparently **showing that the bag is empty**.
- 39–42 s: she folds the empty bag and stores it by bending down at the safe/cabinet base.
- 43–115 s: she sits and **counts the cash at the desk** (by hand, piles on the desk) the whole time, in full view.
- ~116–127 s (1:56–2:07): still seated, she opens the safe (door swings open about 1:57–2:01), puts the bundles inside, **closes the door (about 2:02) and turns the key (2:04–2:07)**.
- 2:08–2:22: she works on the laptop. Nothing is left on the desk.

**Correct procedure shown:** bags are opened and emptied in full camera view, the empty bag is shown to the camera, cash is counted on the desk in view, cash goes into the safe, and the safe is closed and locked straight away. The cashier never leaves and no one else enters.

**Near-misses for a naive detector**
- The cashier stands for about 40 s, which can look like "not at her workstation", but she is at the desk.
- Bending down near the safe at 40–42 s partly hides her body.
- The safe is open for about 6–8 s while she sits next to it. Brief opening is fine when she is present and closes it immediately.
- The cash stays on the desk for more than a minute, which is fine while she is seated and counting.

---

## X-15 — ~20.5 s — VIOLATION at 15 s (angle A)

**Timeline**
- 0–5 s: seated, the cashier handles a green cash bag (possibly folding or inserting something; unclear).
- 5–6 s: she lays the **green bag flat on the desk** next to the counting machine.
- 6–8 s: she stands up and walks to the glass door.
- 8.5–9.5 s: she opens the door, then turns back.
- 10–13.5 s: she goes to the safe and works its handle and key. The keyhole zoom shows the **key in the lock at 0–8 s and gone at 14 s+**, so she locked the safe and took the key, which is correct.
- 14–17 s: she walks out through the glass door. The door closes about 17–18 s.
- 17–20 s: she is visible outside, beyond the glass. **The room is empty and the green bag is still on the desk.**

**Hypothesis:** *The cashier left the cash room with valuables (a cash bag) lying open on the desk, unattended.* The safe was locked correctly, so the violation is the bag left out, not the safe.
**Confidence:** high (about 0.8) that the event is "valuables left unattended / cashier leaves the room". The bag's contents are not visible.
**Cues:** a cash-bag or banknote object stays on the desk while no person is in the room (from the moment the cashier passes the door). The labeled second (15 s) is exactly when she crosses the doorway.

---

## X-16 — ~22.5 s — CLEAN (angle B)

**Timeline**
- 0–2 s: the cashier is seated with her back to the camera.
- 3–5 s: a man in a dark jacket (cash collector role) enters through the glass door carrying a **green cash bag** and closes the door behind him.
- 5–8 s: he stands at the desk with the bag.
- 8.5–9 s: **he hands the cashier a small white card/document** (ID or pass), and she takes it and examines it.
- 9–13 s: he waits while she checks it.
- 14–15.5 s: he hands over another small slip (it looks like the bag's tag or seal label, or an accompanying document), then places the bag in front of her.
- 16–19 s: the cashier opens and inspects the bag(s) at the desk. Two green bags are visible on the desk. **He stays in the room.**
- 20–22 s: he **sits down on the visitor chair** and waits while she processes the bags.

**Correct procedure shown:** the collector presents an ID/document, the cashier verifies it before accepting the bag, the bag's tag or document is handed over, and the bag is opened and checked in front of the collector, who stays until it is done.

**Near-misses:** a non-cashier man is in the cash room carrying a bag. A naive "outsider in the room" or "bag handover" rule would fire falsely here, so the presence of an authorized collector must not be flagged by itself.

---

## X-17 — ~14.5 s — VIOLATION at 7 s (angle B)

The setup and actors are the same as X-16, which makes it a direct contrast pair.

**Timeline**
- 0–2 s: the cashier is seated alone.
- 2.5–4 s: the collector (dark jacket) opens the door and enters carrying a green cash bag.
- 4–6.5 s: he walks to the desk holding the bag.
- 6.5–8 s: **he puts the bag on the desk and the cashier takes it at once.** No card or document is handed over and nothing is checked. The zooms show no white card, unlike X-16 at 8.5 s.
- 8–11 s: he turns and **leaves immediately** through the door.
- 11–14 s: the cashier opens or handles the bag alone, after he is gone.

**Hypothesis:** *Cash bag accepted from a cash collector without identity/document verification and without a joint check.* The collector leaves before the bag is opened or counted in his presence.
**Confidence:** medium-high (about 0.7) that the violation is the handover itself. The exact sub-rule (no ID check, or no check in the collector's presence) is a best guess based on the contrast with X-16.
**Cues:**
- A visitor carrying a bag enters, the bag is transferred, and the visitor exits within about 8 s.
- No small white object passes between the hands before the bag transfer.
- The visitor never sits down.
- The bag is opened only after the visitor leaves.

A duration rule ("collector dwell time < N s"), combined with "no document exchange detected", separates X-17 from X-16.

---

## X-18 — ~22.5 s — VIOLATION at 20 s (angle B)

**Timeline**
- 0–2 s: the cashier is seated alone.
- 2–4 s: a man in a dark jacket (client) enters and closes the door.
- 5–7.5 s: he takes **banknotes/documents (light-blue items)** from his jacket, places them on the desk, and hands them to the cashier.
- 8–10 s: he sits on the visitor chair. The cashier examines the notes.
- 11–14 s: the cashier turns left and puts the notes into the **counting machine**, then returns.
- 14–16 s: she sits with the items on the desk. The operation is in progress.
- 16–18.5 s: **a second man in a white shirt with glasses opens the door and enters while the first client is still being served.**
- 19–21 s: the second man takes a white card/paper from a black wallet and **hands it to the cashier, who reaches out and takes it.** The cash and documents of client 1 are still on the desk.
- 21–22 s: the second man stands beside the desk. Both visitors are in the room.

**Hypothesis:** *A second outsider is admitted to, or present in, the cash room during an ongoing cash operation, and the cashier serves two clients at once* (one client at a time; third parties must not be present while cash is handled).
**Confidence:** medium-high (about 0.7). The event at 20 s is clearly the handover from the second man. Whether the rule broken is "unauthorized person in the room" or "two operations at once" cannot be told apart, but the visual signature is the same.
**Cues:**
- More than one non-cashier person in the room at the same time.
- A second person enters while the first is still seated or cash is on the desk.
- The cashier takes an item from the second person while the first operation is unfinished.

---

## X-19 — ~57.5 s — CLEAN (angle B)

**Timeline**
- 0–2 s: the cashier is alone.
- 2–5 s: a client in a dark jacket enters and closes the door.
- 6–9 s: he hands over banknotes and **sits down**.
- 9–12 s: the cashier counts by hand.
- 12–15 s: she puts the notes through the counting machine.
- 15–33 s: she processes the notes and works on the laptop. The client sits and waits.
- 35–41 s: the cashier swivels to the safe, **opens it (36.5 s), puts the cash in, closes it (about 40.5 s)** and works the handle/key.
- 41.5–46 s: the client stands up and leaves through the door.
- 46–48 s: the cashier is alone.
- 48–53 s: **a second client (white shirt) enters only after the first has left**, comes to the desk and sits about 53 s.

**Correct procedure shown:** one client at a time. Cash is counted in view (by hand, then by machine), the cash is secured in the safe before the client leaves, the safe is closed immediately, and the next client enters only after the room is clear.

**Near-misses:**
- The safe is opened while a client is seated about 1 m away (36–41 s), so a "safe open while outsider present" rule would fire. Here it is brief and the cashier stays at the safe.
- Two different visitors appear in one video (they are sequential, not overlapping), so the detector must reason about overlap, not just count visitors.
- Cash sits on the desk with a client present, which is normal.

---

## X-20 — ~11.5 s — VIOLATION at 5 s (angle A)

**Timeline**
- 0–1.5 s: the cashier stands at the safe with her hand on the key.
- 1.5–3 s: she **opens the safe door** (it swings toward the glass) and reaches in for cash.
- 3–4.5 s: she turns away holding the cash. **The door stays open, with the key still in it.**
- 4.5–5.5 s: she walks back to the desk and sits.
- 5.5–11.5 s: she counts the cash at the desk. **The safe door stays wide open until the end of the clip.** The zoom confirms the open door, with the dark interior and key visible at 4, 6 and 11 s.

**Hypothesis:** *The safe was left open (unlocked, key in the door) after cash was taken out, while the cashier works elsewhere.*
**Confidence:** high (about 0.85).
**Cues:**
- The safe door is open (a door plane at an angle, dark interior visible) while the cashier is at the desk, not at the safe.
- The duration of "safe open and nobody within reach of it" is over 0 s, and here it lasts from about 4.5 s to the end of the clip.
- The labeled second (5 s) matches the moment she sits down with the door still open.

---

## X-21 — ~19.5 s — CLEAN (angle A), the contrast pair for X-20

**Timeline**
- 0–2 s: the cashier stands at the safe and unlocks it.
- 2–3 s: she opens the door and takes out a cash bundle.
- 3.5–6 s: **she closes the door and turns or removes the key while holding the cash.**
- 6–7.5 s: she walks back to the desk with the cash.
- 8–19 s: she sits and counts the cash (by hand, with the machine) in view. The safe is closed throughout.

**Correct procedure shown:** take the cash out, then close and lock the safe immediately before returning to the desk.

**Near-misses:** the safe is open for about 2–3 s with the cashier standing at it. This is legitimate. A detector must require "open and the cashier away from it" or "open longer than T", not just "safe open".

---

## X-22 — ~20.5 s — VIOLATION at 14 s (angle A)

**Timeline**
- 0–4 s: the cashier sits typing on the laptop, and a spreadsheet is visible on the screen.
- 5–6 s: she reaches for the laptop and the screen changes to a dark lock-screen-like image. She probably locked the PC, which is correct. It looks the same as in X-23.
- 6.5–8 s: she stands and walks toward the safe and door.
- 8.5–13.5 s: she stands at the safe and works its handle and key for several seconds.
- 14–17 s: she walks out through the glass door.
- 17–20 s: she is outside the glass and the room is empty.
- **Keyhole zoom: the key, with a light-coloured key tag, is in the safe lock at 0 s and is STILL in the lock at 15 s and 20.5 s after she has left.** In X-23 the key is removed (see below).

**Hypothesis:** *The cashier left the cash room with the safe key left in the safe lock* (key not removed and taken with her; the safe is effectively unsecured).
**Confidence:** medium (about 0.6). The key difference against the clean twin X-23 can be seen, but the object is tiny (a few pixels at this resolution). An alternative, the safe not actually locked, cannot be ruled out. No cash or bag is left on the desk.
**Cues:**
- A small light object sticking out of the safe keyhole after the cashier leaves the room.
- A "room empty" state combined with "key present in the safe lock".

This needs a high-resolution crop of the safe's lock area. A frame-difference check of the keyhole region before and after the cashier visits the safe would also catch it.

---

## X-23 — ~18.5 s — CLEAN (angle A), the contrast pair for X-22 (and X-15)

**Timeline**
- 0–4 s: the cashier types on the laptop.
- 4–5 s: she reaches for the laptop and the screen switches to the **Windows lock screen** (blue landscape image), so the PC is locked.
- 5–7 s: she stands and walks to the safe.
- 7–11 s: at the safe, she turns and **removes the key**. The keyhole zoom shows the key present at 0 s and only an empty keyhole at 13 s and 18 s.
- 12–15 s: she leaves through the glass door.
- 15–18 s: the room is empty. **The desk has no cash or bags, the laptop is locked, the safe is closed and the key has been taken.**

**Correct procedure shown:** before leaving the workstation, lock the PC, check that the safe is locked and take the key, leave nothing valuable on the desk, then leave.

**Near-misses:** an empty cash room and a cashier leaving are NOT violations by themselves. X-15, X-22 and X-23 all show the same "cashier walks out" motion. Only the state left behind differs (a bag on the desk in X-15, the key in the safe in X-22). A detector that flags "cashier left the room" would produce a false positive here.

---

## Summary table

| Video | Label | Hypothesized type | Key visual cue | Confidence |
|---|---|---|---|---|
| X-14 | clean (~142 s) | Correct bag opening and counting: empty bag shown to camera, cash counted in view, safe closed and locked | — | — |
| X-15 | V @15 s | Cashier leaves the cash room with a cash bag left on the desk, unattended | Green bag on the desk while no person is in the room; cashier crosses the door at 15 s | High (0.8) |
| X-16 | clean | Correct collector handover: ID/document check, collector stays and sits during the check | White card handed over before the bag; collector stays | — |
| X-17 | V @7 s | Cash bag accepted from the collector without ID/document check or joint check; collector leaves at once | Bag put on the desk at 7 s, no card exchanged, collector out by 11 s | Med-high (0.7) |
| X-18 | V @20 s | Second outsider admitted during an ongoing operation; two clients served at once | Two visitors in the room; cashier takes an item from the second man at 20 s while client 1's cash is on the desk | Med-high (0.7) |
| X-19 | clean | One client at a time, cash put in the safe before the client leaves, next client enters after the room is clear | Visitors' presence does not overlap | — |
| X-20 | V @5 s | Safe left open (key in the door) after taking cash; cashier counting at the desk | Safe door open while the cashier sits at the desk, from 4.5 s to the end | High (0.85) |
| X-21 | clean | Take cash, close and lock the safe immediately, then go to the desk | Safe open only about 2 s with the cashier at it | — |
| X-22 | V @14 s | Cashier leaves the room with the safe key left in the safe lock | Light key/tag visible in the safe keyhole after the cashier exits at 14 s | Medium (0.6) |
| X-23 | clean | Correct leave-workstation routine: PC locked, key removed, desk clear, then exit | Empty keyhole and lock screen after exit | — |

## Distinct violation types in this batch and their cues

1. **Valuables left unattended when the cashier leaves the room** (X-15)
   - Cue: a cash bag or banknotes on the desk while the person count in the room is 0.
   - Trigger moment: the cashier passes through the door.
2. **Safe left open (and unlocked) while the cashier is away from it** (X-20)
   - Cue: the safe door's open state, i.e. the door panel at an angle with the dark interior visible.
   - Combined with the cashier's distance from the safe being above a threshold (she is seated at the desk) for more than about 1–2 s.
3. **Safe key left in the lock when the cashier leaves** (X-22)
   - Cue: a small protrusion or key tag at the safe keyhole while the room is empty.
   - Needs a fixed high-resolution ROI on the lock and a before/after comparison against the "key removed" appearance.
4. **Cash-bag handover from a collector without verification or joint check** (X-17)
   - Cue: a visitor with a bag stays in the room for only a short time (about 8 s).
   - No document/card exchange is detected before the bag transfer.
   - The visitor neither sits nor waits, and the bag is opened only after the visitor leaves.
5. **More than one outsider in the cash room / serving two clients at once** (X-18)
   - Cue: two or more non-cashier persons in the room at the same time.
   - The second person enters while the first is still seated or cash is on the desk.
   - The cashier takes an item from the second person.

### Notes for the detector design
- The **contrast pairs** in this batch are almost frame-aligned: X-16 with X-17, X-18 with X-19, X-20 with X-21, X-22 with X-23 (and X-15 with X-23). The violation is a single state difference within an otherwise identical script. Generic activity cues (someone stands up, walks out, opens the safe, a visitor enters, a bag changes hands) occur in both clean and violation videos, so the system must reason about **state** (what is left open or on the desk, who is present, whether a document check happened) rather than about actions alone.
- Useful fixed ROIs per camera angle: the safe door and keyhole, the desk surface (for bags and cash), the door region (for entry and exit events), and the visitor chair.
- Label timing: each labeled second matches the moment the bad state becomes final. Examples are the cashier crossing the door (X-15 at 15 s, X-22 at 14 s), sitting down with the safe still open (X-20 at 5 s), the bag hitting the desk (X-17 at 7 s), and the second client's handover (X-18 at 20 s).
- Resolution caveat: the frames are only 464x848, and the keyhole in X-22 is a few pixels across. The original videos should be used for that cue.
