# Batch C: X-24 … X-33

Method: I looked at the 1 fps contact sheets for every video, then at 2 fps full-frame montages (and crops enlarged 2–3x) around the labeled second and around any other notable event. Durations come from the 2 fps frame counts.

## Common scene notes

There are two camera setups, both high-angle corner cameras looking at a glass partition wall with a glass door on the upper left.

- **Setup A** (X-24 … X-29): a wooden desk with a black counting machine (left), a desk phone, a laptop (right) and a teal/green small item. A grey-blue metal **safe / cabinet** stands behind the desk on the right, next to the glass wall. A white pedestal cabinet is at the lower left. The glass door is behind the desk on the left.
- **Setup B** (X-30 … X-33): the desk is rotated. The phone is on the left and the counting machine is on the right of the desk, with the grey safe immediately behind it, next to the wall. The glass door is on the upper left. In X-33 there is also a laptop on the left, a blue client chair and a second grey chair (furniture variant).
- Actors: a cashier with long hair in a **pink cardigan and black trousers** (X-24–X-32), a cashier in a **white blouse and black trousers** (X-33), and male "outsiders" in a **dark jacket** (X-30) or a **light/white shirt** (X-33). None of them wears a uniform and none carries a bag (they are not cash collectors).
- Door state cue: when the glass door is **ajar**, its frame is visibly rotated inward and a dark vertical gap/band appears in the left glass panel. When it is closed, the left panel shows the office behind it (chairs, cabinet) just as in frame 0. This contrast is clear when the first frame is compared with later frames.

---

## X-24: violation, label 8 s (duration ≈ 14.5 s)

**Scene:** Setup A, one cashier (pink cardigan).

**Timeline**
- 0–3 s: The cashier sits at the desk and handles a white booklet/papers. A dark flat rectangular object (cash tray / tablet / folder) and the teal item are on the desk.
- 3–4.5 s: She puts things down on the desk and starts to stand.
- 5–6 s: She walks around the right end of the desk, past the safe. **She does not open the safe.**
- 6.5–8 s: She walks to the glass door. **At 8 s she is at the door with her back to the desk.**
- 9–11 s: She opens the door and leaves the room. 11–14 s: the room is empty. The **dark object and the teal item stay on the desk** and the safe is closed and untouched.

**Hypothesis:** The cashier **leaves the cash room / workstation without putting valuables (cash tray/box, teal item, possibly a stamp or seal) into the safe**. Valuables are left unattended on the desk. Confidence: **high**. X-25 is the same scene performed correctly; there the same items go into the safe first.

**Detection cues:** The person track moves from the desk zone to the door zone and out of frame. Items remain in the desk zone (dark rectangle + teal object) after the person leaves. There is no "safe-door-open" event between "stand up" and "exit". The rule is: room empty AND objects on desk → violation.

## X-25: clean (≈ 31 s)

**Scene:** Setup A, same cashier, same starting state as X-24.

**Timeline (correct procedure: secure valuables before leaving)**
- 0–4 s: She handles the booklet at the desk.
- 4.5–7 s: She stands up and walks to the safe.
- 7.5–9 s: She **opens the safe door** with her hand at the lock/handle.
- 8.5–11 s: She returns to the desk, picks up the **dark rectangular object** and places it inside the safe.
- 12–15 s: She picks up the **teal item(s)** from the desk and puts them in the safe.
- 16–19 s: She **closes the safe door**.
- 19–23 s: She works on the front of the safe with both hands for several seconds (locking: key or code).
- 24–28 s: She walks to the glass door and exits. The door closes behind her.
- 28–31 s: Empty room. **The desk holds only fixed equipment** (counting machine, phone, laptop) and the safe is closed.

**Near-misses for a naive detector**
- The safe door is open for about 10 s (8–18 s) while the cashier walks back and forth to the desk. That is legitimate because she stays next to it. Do not flag "safe open" while a person is within reach.
- The empty room at the end is legitimate. Only empty room + leftover valuables (or an open safe) should be flagged.
- A long "fiddling" period at the safe front (locking) is normal.

## X-26: violation, label 9 s (≈ 19.5 s)

**Scene:** Setup A, same cashier.

**Timeline**
- 0–7 s: She sits at the desk and **counts banknotes by hand** (a stack of light-coloured notes; enlarged crops show note-flipping motions).
- 7.5–8.5 s: She **puts the banknote stack on the desk** (a small white/beige rectangle in the centre of the desk).
- 9 s: **She stands up and walks away from the desk** (the labeled second). The cash stays on the desk.
- 10–15 s: She walks past the safe without opening it, to the door, and exits.
- 15–19.5 s: Empty room. **The banknote stack is clearly visible on the desk.**

**Hypothesis:** **Cash left unattended on the desk**: the cashier leaves the workstation and the room without securing cash in the safe. Confidence: **high**. X-27 is the paired clean version.

**Detection cues:** A banknote-coloured blob on the desk (cash detector / "paper on desk" change detection versus the empty-desk reference), together with no person in the desk zone or in the room. The cash first appears on the desk when the person stands up, and it is still there when the person exits through the door.

## X-27: clean (≈ 26 s)

**Scene:** Setup A, same cashier, same start as X-26.

**Timeline**
- 0–8 s: She counts banknotes by hand at the desk.
- 8.5–9.5 s: She stands up **holding the cash** and walks to the safe.
- 10–11.5 s: She unlocks and opens the safe.
- 12–13 s: She places the cash inside.
- 13–17 s: She closes the safe, then spends a few seconds on the lock.
- 18–23 s: She walks to the door and exits. After that the room is empty and the **desk is empty** (fixed equipment only).

**Correct procedure shown:** Cash goes from hand straight to the safe, the safe is locked, and only then does she leave.

**Near-misses:** Cash in hand while walking (8.5–12 s) is legitimate and is not "cash left". Track whether the cash moves with the person.

## X-28: violation, label 7 s (≈ 16 s)

**Scene:** Setup A, same cashier. **Laptop open, with a spreadsheet/application window visible** (white screen with table rows).

**Timeline**
- 0–5 s: She sits and types on the laptop.
- 5.5–6.5 s: She stands up, with one hand still on the desk/laptop.
- 7 s: **She walks away from the desk** (the labeled second). The laptop screen is still lit with the application open.
- 8–12 s: She goes to the door and exits. 13–16 s: Empty room. **The laptop still shows the bright spreadsheet** (confirmed in enlarged crops at 2 s, 6 s and 15 s).

**Hypothesis:** **The cashier leaves the workstation without locking the computer** (no Win+L / screen lock), which leaves the banking session accessible. Confidence: **medium-high**. The paired clean video X-29 shows the lock explicitly. No cash or safe is involved.

**Detection cues:** The laptop screen region stays bright / content-unchanged (a white table UI) after the person has left the desk zone. The correct state is a dark screen or the lock-screen wallpaper (dark blue landscape). A simple classifier on the screen ROI (bright white UI versus dark/lock-screen) plus a "person left desk" event is enough.

## X-29: clean (≈ 17 s)

**Scene:** Setup A, same cashier, same start as X-28.

**Timeline**
- 0–5 s: She types on the laptop (spreadsheet visible).
- ~6 s: She presses a key combination. The **screen goes black (6 s) and then shows the Windows lock screen** (a dark landscape wallpaper with a clock) from about 6.5 s onward.
- 6.5–12 s: She stands up, walks to the door and exits. 12–17 s: Empty room, with the lock screen still visible.

**Correct procedure shown:** Lock the PC before leaving the workplace.

**Near-misses:** The laptop screen is not dark. The lock screen is a coloured wallpaper, so "screen on" alone must not be flagged. The detector must tell a **lock-screen wallpaper** from a **white application UI**.

## X-30: violation, label 13 s (≈ 18.5 s)

**Scene:** Setup B, the cashier (pink) and a male outsider (dark jacket, dark trousers, no bag, not in uniform).

**Timeline**
- 0–3 s: The cashier approaches from outside and opens the glass door.
- 3.5–4 s: She steps in. Her hand is on the handle while she passes, but **she does not turn back to close/lock the door**.
- 4–10 s: She walks to the desk and sits. From about 5 s on, **the door is ajar**: its frame is rotated inward and a dark band shows in the left glass panel (clearly visible at 12 s compared with 0 s).
- 10–12 s: She stands and works at the counting machine / safe behind it.
- **12–13 s: The man walks in through the open door** without being let in and stands about 1 m behind and to the left of the cashier (13 s = labeled second).
- 13–18.5 s: He stays inside the cash room near the desk while the cashier keeps working at the machine/safe, then turns to talk to him (16–18 s).

**Hypothesis:** **An unauthorized person enters the cash room** because the door was left open/unlocked. Main type: outsider in cash room; contributing type: door not closed. The label (13 s) matches the moment he enters, not the door event. Confidence: **high** (outsider), medium (whether the label targets the outsider or the door).

**Detection cues:** A second person appears in the door zone and enters the room. A person count of 2 or more inside the cash room is suspicious when the second person is not a uniformed cash collector with a bag. The door-ajar state before that (rotated frame / dark band) is a strong precursor.

## X-31: clean (≈ 23.5 s)

**Scene:** Setup B, the cashier alone.

**Timeline**
- 0–6 s: She approaches, opens the door and enters.
- 6.5–7.5 s: She starts toward the desk.
- **8–10 s: She turns back to the door, takes the handle and closes it (likely locking it)**. From then on the door looks exactly as in frame 0 (closed, office visible through the left panel).
- 11–13 s: She walks to the desk and sits. 13–15 s: Seated.
- 16–23.5 s: She stands at the counting machine and handles the machine and the area of the safe behind it (loading or unloading).

**Correct procedure shown:** **Close/lock the cash-room door immediately after entering**, and only then start working with cash and equipment. Nobody else enters.

**Near-misses:** The door is legitimately open for a few seconds while she enters (2–8 s). Do not flag a door that is open while the person is passing through or is within about 1 m of it. Flag it only when the door stays open after the person has moved to the workstation (more than ~2–3 s).

## X-32: violation, label 5 s (≈ 21 s)

**Scene:** Setup B, the cashier alone (no outsider seen in the video).

**Timeline**
- 0–2 s: She approaches and opens the door.
- 3–4 s: She enters, with her hand on the handle while passing.
- **4.5–5 s: She walks off toward the desk without closing the door** (the labeled second). Through the rest of the video the door stays **ajar** (rotated frame, dark band in the left panel; compared with frame 0 at 15 s).
- 6–9 s: She arrives at the desk and sits. 11–20 s: She stands and works at the counting machine / safe.

**Hypothesis:** **The cash-room door is left open/unlocked after entering, while the cashier works with cash/equipment.** Confidence: **medium-high**. The door-ajar state is clearly visible, the label sits exactly at the moment she turns away from the open door, and it contrasts with X-31 (same setup, door closed at 8–10 s).

**Detection cues:** The door state (open/closed) is taken from the door-frame position, the dark gap or optical flow in the door ROI. The rule: door open AND the person has left the door zone AND more than N seconds have passed → violation.

## X-33: violation, label 6 s (≈ 14 s)

**Scene:** Setup B variant (laptop on the left, blue client chair). Cashier in a white blouse; male outsider in a light/white shirt.

**Timeline**
- 0–3 s: The cashier opens the glass door and enters.
- 4–5 s: She is inside, glances back at the door but **does not close it**.
- **6 s: She walks toward the desk with the door left ajar** (the labeled second).
- 7–9 s: She sits and starts working at the counting machine (hands on the machine and its tray).
- 9.5–10 s: The man appears behind the glass door.
- 10.5–14 s: He **pushes into the open doorway and leans in, looking toward the cashier/counting machine**. He partially enters (upper body and one leg inside) while the cashier keeps counting, apparently without reacting.

**Hypothesis:** **Door left open → an outsider gets access to / observes the cash-counting area.** The label (6 s) marks the moment the door is left open, so the primary type is "door not closed/locked". The intruder at 10–14 s shows the consequence. Confidence: **high** for door-left-open. Confidence that the intruder is also part of the intended violation: medium.

**Detection cues:** Door-ajar state after the cashier has passed (as in X-32). A second person in the door zone or entering. The cashier does not react while cash/the machine is being handled.

---

## Summary table

| Video | Label | Hypothesized violation type | Key visual cue | Confidence |
|---|---|---|---|---|
| X-24 | V @ 8 s | Leaves room without securing valuables in safe (items left on desk) | Person exits; dark tray + teal item remain on desk; safe never opened | High |
| X-25 | clean | Correct: items → safe → lock → exit | Safe opened, items moved in, safe closed/locked, empty desk at exit | n/a |
| X-26 | V @ 9 s | Cash (counted banknotes) left unattended on desk | Banknote stack on desk while room is empty | High |
| X-27 | clean | Correct: counted cash → safe → lock → exit | Cash carried in hand to safe; empty desk at exit | n/a |
| X-28 | V @ 7 s | Workstation left with PC unlocked | Laptop screen shows bright app UI after person leaves | Medium-high |
| X-29 | clean | Correct: PC locked (Win+L) before leaving | Screen turns black → lock-screen wallpaper | n/a |
| X-30 | V @ 13 s | Unauthorized outsider enters cash room (door left open) | 2nd person (no uniform/bag) enters through ajar door | High |
| X-31 | clean | Correct: door closed/locked right after entering | Cashier turns back and closes the door; door ROI matches frame 0 | n/a |
| X-32 | V @ 5 s | Cash-room door left open/unlocked after entering | Door frame rotated / dark gap persists while cashier at desk | Medium-high |
| X-33 | V @ 6 s | Door left open → outsider leans in / observes cash counting | Door ajar from ~5 s; man enters doorway at 10.5–14 s | High (door), medium (intruder part) |

## Distinct violation types in this batch

1. **Valuables/cash left unattended on desk when the cashier leaves** (X-24, X-26).
   Cues: room or desk zone empty of people, plus non-fixed objects on the desk (banknote-coloured stack, dark cash tray, small teal item) compared with an empty-desk reference. The "open safe" step is missing between standing up and exiting. Correct counterpart (X-25, X-27): open the safe, move the items in, close and lock, and only then exit.
2. **Workstation computer left unlocked** (X-28).
   Cues: the laptop screen ROI stays bright with application UI (white table) after the person has left. The correct state is a black screen or the lock-screen wallpaper (X-29).
3. **Cash-room door not closed/locked after entering** (X-32, X-33, and a precursor in X-30).
   Cues: the door frame stays rotated inward, with a dark gap in the left glass panel, for more than a few seconds after the person has moved away from the door. Correct counterpart (X-31): the cashier turns back and closes the door within ~2–3 s of entering.
4. **Unauthorized person entering / reaching into the cash room** (X-30, and the consequence in X-33).
   Cues: a second person appears at or enters through the door zone. They wear plain clothes (dark jacket or light shirt), with no bag and no cash-collector uniform. The person count inside the room is 2 or more while the cashier handles the counting machine or safe.

**General notes for the detector**
- Several videos are paired twins (X-24/X-25, X-26/X-27, X-28/X-29, X-30/X-32 versus X-31). The violation is often the **absence** of a step (safe not opened, PC not locked, door not closed). Change-detection on fixed ROIs (desk surface, laptop screen, door frame, safe door) against frame 0 plus a person tracker covers all four types.
- Label seconds mark the moment the cashier **turns away from / leaves** the item or door (X-24, X-26, X-28, X-32, X-33), or the moment the outsider **enters** (X-30). They do not mark when the room becomes empty.
- Legitimate near-misses: the safe open while the cashier stands next to it (X-25 8–18 s), the door open while the cashier passes through it (X-31 2–8 s), cash carried in hand (X-27), the lock-screen wallpaper visible on the laptop (X-29), and an empty room after correct securing (X-25, X-27, X-29).
