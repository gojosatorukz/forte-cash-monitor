# Batch D: video analysis (X-34, X-35, X-37, X-38, X-39, X-40, X-43, X-44, X-45, X-46)

Method: I viewed the 1 fps contact sheets for every video, then made zoomed montages of the 2 fps frames around the labelled second (±5 s) and around other events (safe opening, handovers, entries and exits). Durations come from the 2 fps frame count. X-47 and X-48 are left out because they duplicate other videos.

## Two camera set-ups appear in this batch

- **Layout A, "client service"** (X-34, X-35, X-37, X-38, X-39): the camera is high in a corner. The cashier sits **with her back to the camera** at a wooden desk. On the desk are a laptop and a phone (left) and a black counting machine (right/back). A grey **safe** stands on a white drawer unit to the right of the desk, and its door opens toward the cashier. A glass wall with a **glass door** is at the back left, and clients come in through it. The client sits on the far side of the desk, facing the camera. The cashier is a woman with long dark hair in a white blouse, and the clients are men in white shirts. A large white table occupies the lower left foreground and is never used.
- **Layout B, "back office / solo cashier"** (X-40, X-43, X-44, X-45, X-46): the desk is rotated. The cashier sits on the far side **facing the camera**, with the glass door behind her on the left. On the desk are a laptop and phone (left), the counting machine (right), and a small black **banknote detector** with a green screen in front of the machine. A grey safe or cabinet stands behind her to the right. An empty **turquoise visitor chair** is in the foreground. The cashier is a woman with long dark hair in a white blouse. No clients appear.

---

## X-34: 17.5 s, VIOLATION @ 13 s (Layout A)

**Participants:** the cashier (back to camera) and one client, a man in a white shirt and dark trousers. Outside the glass wall, background people walk by, including a man in a light-green shirt at about 1–2 s and 15–16 s.

**Timeline**
- 0–2 s: the cashier is alone at the desk and someone passes behind the glass.
- 3–5 s: the client opens the glass door, enters and walks to the desk.
- 6.5–7.5 s: the client leans over the desk and puts a **stack of banknotes** in front of the cashier.
- 8–10.5 s: the client crouches or sits opposite her while the cashier takes the stack.
- 11–11.5 s: **the client stands up and turns toward the door**.
- 12–14.5 s: the client walks out through the glass door. **At the same moment (12.5–15 s) the cashier raises her arm and feeds the cash into the counting machine.**
- 15–17 s: the client is gone and the cashier keeps working on the machine alone.

**What happens at 13 s:** the cash is counted on the machine **only after the client has left, or while he is leaving**. The client never sees the count.

**Hypothesis:** *cash accepted and counted without the depositor present.* Recounting in the client's presence was skipped and the client left before the amount was confirmed. **Confidence: medium.** The contrast with X-35, X-37 and X-39 is strong: in those clean videos the client **stays seated** through the machine count, and in X-35 and X-39 also until the cash is in the safe.

**Detectable cues**
- A "cash handover" event: a client's hand reaches across the desk and leaves an object on it.
- The client's track ends (he exits through the door ROI) **before or while** the cashier interacts with the counting machine.
- Rule: if the counting-machine interaction starts while no client is at the client seat, and a handover happened less than N s earlier, flag it.

---

## X-35: 56.5 s, CLEAN (Layout A)

**Participants:** the same cashier and a client in a white shirt.

**Timeline (correct procedure)**
- 1–5 s: the client enters through the glass door.
- 6–7 s: he puts banknotes on the desk.
- 8 s onward: he **sits down opposite the cashier and stays seated**.
- 11–13 s: the cashier counts the cash in the machine with the client present.
- 17–21 s: she runs the cash through the machine a second time, then takes it out.
- 22–38 s: she works at the desk, apparently counting or processing by hand. The client is still seated.
- 40–47 s: she **opens the safe** door to her right, puts the cash inside and **closes the safe**. The safe is open for about 6 s and she never leaves her chair.
- 48–53 s: only after the safe is closed does the client stand up and leave through the glass door.
- 54–56 s: the cashier is alone and the desk is clear.

**Correct procedure:** the client stays through the whole count. Cash goes from the desk to the machine to the safe. The safe is opened briefly and closed again, the cashier never turns away from open cash, and the desk is clear before the client leaves.

**Near-misses a naive detector could flag**
- The **safe is opened while a client is in the room** (40–47 s). This is normal here, so "safe open with client present" must not be a rule on its own.
- A client sits close to the cash and the machine. That is also normal.
- People moving behind the glass. They are outside the room.
- The client approaches and bends over the desk during the handover.

---

## X-37: 15.5 s, CLEAN (Layout A)

**Timeline**
- 2–5 s: the client enters. Behind the frosted glass he briefly looks greenish.
- 7–8 s: he puts cash on the desk.
- 9 s onward: he sits opposite the cashier.
- 11–14 s: the cashier counts it in the machine **while the client is seated and watching**.
- The video ends at 15.5 s with the client still seated.

**Correct procedure:** this is the same scenario as X-34 up to the handover, but the client **stays** for the count. It is the direct negative counterpart of X-34. The only visible difference is whether the client is present when the machine is used.

**Near-misses:** same as X-35. A detector that only sees "client brings cash, cashier uses machine" cannot separate X-37 from X-34; it has to check whether the client is present.

---

## X-38: 58.5 s, VIOLATION @ 33 s (Layout A)

**Participants:** the cashier and two different clients. Client 1 is a man in a white shirt with short hair. Client 2 is a man in a white long-sleeve shirt with glasses and wavy hair.

**Timeline**
- 2–5 s: client 1 enters.
- 6–7 s: he hands over cash and sits.
- 10–11 s and 20–22 s: the cashier uses the counting machine with client 1 present.
- 22–24 s: client 1 stands up and leaves through the door at 24–27 s.
- **25–26 s:** with client 1 leaving, the cashier puts a **stack of banknotes openly on the desk** between the laptop and her position. She does not put it in the safe.
- 27–32 s: the stack stays on the desk. The cashier sits idle and nobody else is in the room.
- **32–34 s:** client 2 opens the glass door and walks in. **At 33 s the next client enters while the previous client's cash is still lying on the desk.**
- 35–37 s: client 2 sits down directly in front of the open banknote stack.
- 38–43 s: client 2's cash is put into the machine. The first stack is still on the desk.
- 49–54 s: client 2 leaves. A small item, apparently the cash stack, is still visible on the desk until the end at 58 s.
- **The safe is never opened in this video.**

**Hypothesis:** *cash left openly on the desk and not secured in the safe before the next client was served.* Cash from different operations gets mixed, and an outsider has access to and sight of cash that is not his. **Confidence: medium-high.** In X-39, the clean counterpart, the cashier puts each client's cash **into the safe before that client leaves**, and the desk is empty when the next client arrives.

**Detectable cues**
- A "cash on desk" object (a small rectangular green or beige patch) stays in the desk ROI across a client change, that is, after the first client's track ends and a new person track enters through the door.
- No safe-open event happens between one client leaving and the next arriving.
- Rule: if a new person enters the room while a cash object is on the desk and the safe was not opened since the last handover, flag it.

---

## X-39: 78 s, CLEAN (Layout A); the clean counterpart of X-38

**Timeline**
- 2–5 s: client 1 enters.
- 6–7 s: he hands over cash and sits.
- 10–19 s: the cash is counted in the machine with client 1 present.
- 20–27 s: the cashier handles it at the desk.
- **28–34 s: the safe is opened, the cash goes in and the safe door is closed.** Client 1 is still seated.
- 34–39 s: client 1 leaves after the safe is closed. The cashier's hand is on the safe while he walks out, closing or locking it.
- 40–46 s: the cashier is alone and the **desk is empty**.
- 45–49 s: client 2 (glasses) enters and sits.
- 56–63 s: the machine count happens with client 2 present.
- **64–71 s: the safe is opened, the cash deposited and the safe closed.**
- 72–76 s: client 2 leaves.

**Correct procedure:** every transaction is a complete cycle of handover, count in the client's presence, safe and close, and only then does the client leave. The desk is clear between clients.

**Near-misses**
- The safe is open while a client sits about a metre away. This is normal.
- The cashier's arm is still on the safe door as client 1 walks out, around 35–37 s. This is a brief overlap between the safe being open and a person moving near the door, and it is legitimate.
- The second client's entry is the same as X-38's at 33 s. In X-39 it is legitimate because the desk is empty.

---

## X-40: 25 s, VIOLATION @ 17 s (Layout B)

**Participants:** one woman, the cashier arriving for work. She has long dark hair and wears a **beige jacket** over a white blouse. She carries a **patterned checkered handbag**. Nobody else enters.

**Timeline**
- 0–2 s: the room is empty and the desk is clear: laptop, phone, counting machine and detector.
- 2–4 s: she opens the glass door and enters.
- 5–8 s: she walks behind the desk holding her handbag.
- **8.5–9.5 s:** she puts the **handbag on the cash desk** next to the counting machine.
- 10–13 s: she takes off her jacket.
- **14–15 s:** she puts the **jacket on the desk** over the area next to the laptop.
- 16–17 s: she pulls out the chair and sits down at **17–18 s**.
- 18–24 s: she starts working at the machine, with her **coat and handbag lying on the cash desk** next to her and the cash equipment.

**Hypothesis:** *personal belongings (handbag, outerwear) at the cash workplace / in the cash zone.* Personal bags and clothes are normally banned from the cash area because they could be used to remove cash, and they block the camera's view of the desk. **Confidence: medium-high.** The labelled 17 s is the moment she sits down and starts work with the items on the desk. The same cashier in X-43, X-44, X-45 and X-46 has **no bag or coat** on the desk.

**Detectable cues**
- The object detector finds a "handbag" (a COCO class) and a large beige blob, a coat, on the desk ROI.
- The cashier arrives in outerwear.
- Rule: if a handbag, backpack or coat is in the desk or cash ROI while the cashier is working, flag it. An arrival event (entry through the door, then sitting down) followed by a bag being placed on the desk is a strong pattern.

---

## X-43: 23 s, CLEAN (Layout B)

**Timeline**
- 0–6 s: the cashier alone feeds banknotes into the counting machine and takes them out.
- 7–13 s: she sits with the cash in her hands and then reloads the machine.
- **13–16 s:** she checks banknotes on the **detector** (the black device with the green screen) and **separates a differently coloured (brown/beige) note** into its own small pile.
- 17–22 s: she hand-counts the green notes. **Two separate piles** stay on the desk, one green and one brown.

**Correct procedure:** she counts on the machine, checks on the detector, sorts by denomination or type into separate piles and hand-counts. The desk holds only equipment and the cash being processed. There are no personal items, food or other people.

**Near-misses**
- Cash lying on the desk while the cashier is seated and attending to it. This is normal; it is not "cash left unattended".
- The cashier looks down for long periods, which is just counting.
- Reflections of people outside the glass wall behind her.

---

## X-44: 20.5 s, VIOLATION @ 16 s (Layout B)

**Timeline**
- 0–7 s: the cashier alone puts banknotes into the counting machine at 3–5 s, then sits with her hands folded.
- 9–13 s: she takes notes out of the machine. At 10 s green notes come from the outlet, and at **13 s she takes a brownish or beige bundle** from the machine.
- **13.5–15.5 s:** she holds the **brown notes and the green notes together** and fans or folds them.
- **16–17.5 s:** she **combines them into one stack**, tapping and squaring it, and puts it on the desk. Unlike X-43 and X-45, she **does not use the detector** and **does not keep the brown notes in a separate pile**.
- 18–20 s: one stack lies on the desk and her hands are folded.

**Hypothesis (low confidence):** *improper handling of counted cash.* She mixes denominations, or puts a machine-rejected or suspicious note into the counted bundle without checking it on the detector. The comparison with X-43 and X-45 supports this: in those clean videos the same actor keeps the brown notes in a **separate pile** and uses the detector. I could **not** see a clear theft gesture such as hands to a pocket, sleeve or lap; her hands stay on or above the desk. **Confidence: low. The type is unclear.** "Mixing or not sorting banknotes / skipping the authenticity check" is the best guess.

**Detectable cues (weak)**
- The number of distinct cash piles on the desk: one merged pile here, two in the clean videos.
- No hand interaction with the detector ROI during processing.
- Colour mixing (brown and green) inside a single bundle.
- All of these are fine-grained. A coarse detector will probably not separate X-44 from X-43 or X-45, which makes this a hard case.

---

## X-45: 33.5 s, CLEAN (Layout B)

**Timeline**
- 0–4 s: the cashier loads the counting machine.
- 4–8 s: she waits with her hands folded.
- 8–10 s: she takes the notes out.
- 10–16 s: a **brown/beige pile lies separately** on the desk while she checks the green notes on the **detector** (hand on the detector at 14–18 s).
- 18–31 s: she hand-counts the green notes. The brown pile stays separate, at 22–26 s, until near the end.
- 32 s: she is seated and the cash is in front of her.

**Correct procedure:** as in X-43. She separates by denomination, checks on the detector, hand-counts, and has no personal items on the desk.

**Near-misses:** cash spread in several piles on the desk while she is seated. This is normal. A "cash on desk" rule must take the cashier's presence and attention into account.

---

## X-46: 18.5 s, VIOLATION @ 11 s (Layout B)

**Timeline**
- 0–3 s: the cashier alone works at the counting machine.
- 4–8 s: she sits with a small yellow or beige object, apparently a banknote bundle, which she puts down near the machine at about 6–8 s.
- **9–10 s:** she reaches beside or behind the counting machine and puts a **small light-blue container** (a cup or food pot) on the desk.
- **11 s:** she picks up a **paper coffee cup** (brown and white takeaway cup).
- **13–17 s:** she **drinks from the cup** at the cash desk. The blue container stays on the desk next to the cash and the equipment.
- 18 s: she puts the cup down next to the blue container.

**Hypothesis:** *eating or drinking at the cash workplace* (food or drink in the cash zone, risk of damaging cash and distraction). **Confidence: high** for what is visible. The cup reaches her mouth at 13–17 s. The label at 11 s matches the moment she picks up the cup.

**Detectable cues**
- The object detector finds a "cup" (COCO class) or a bottle in the desk ROI.
- Pose: the wrist is raised to the mouth while she holds a cup-shaped object, for at least 1 s.
- The cup appears near the cash or counting machine.

---

## Summary table

| Video | Label | Hypothesized type | Key visual cue | Confidence |
|---|---|---|---|---|
| X-34 | V @13 s | Client leaves before the cash is counted; count done without the depositor present | The client's track exits through the door while or before the cashier feeds the handed-over cash into the counting machine | Medium |
| X-35 | clean | Full correct cycle: handover, machine count with client seated, safe opened and closed, then client leaves | Safe opened 40–47 s with the client present (legitimate) | n/a |
| X-37 | clean | Handover and machine count with the client seated | Clean twin of X-34; the client stays | n/a |
| X-38 | V @33 s | Previous client's cash left openly on the desk, not put in the safe, when the next client enters and sits in front of it | Cash object stays on the desk across a client change; no safe-open event | Medium-high |
| X-39 | clean | Two clients, each one's cash goes into the safe before that client leaves; desk empty between clients | Safe open/close at 28–34 s and 64–71 s; empty desk at 40–46 s | n/a |
| X-40 | V @17 s | Personal belongings (handbag and coat) placed on the cash desk; cashier starts work with them there | Handbag and coat on the desk ROI; cashier arrives in outerwear | Medium-high |
| X-43 | clean | Solo counting: machine, detector, sorting into separate piles, hand count | Two separate piles; detector used | n/a |
| X-44 | V @16 s | Unclear. Best guess: brown and green notes (possibly a machine reject) merged into one bundle without a detector check or sorting | One merged pile instead of separate piles; no detector use | Low |
| X-45 | clean | Solo counting with separate brown pile and detector check | Separate piles; hand on detector at 14–18 s | n/a |
| X-46 | V @11 s | Eating or drinking at the cash workplace | Paper coffee cup and food container on the desk; cup raised to mouth at 13–17 s | High |

## Distinct violation types seen in batch D

1. **Count or acceptance without the client present** (X-34). Cue: the client leaves through the door before or while the handed-over cash is machine-counted. This needs person tracking by role (client vs. cashier by position: the client sits opposite or arrives through the door, the cashier stays in the chair by the safe) and time ordering against counting-machine interaction.
2. **Cash left unsecured on the desk, or operations mixed between clients** (X-38). Cue: a cash object persists on the desk through a client change with no safe open/close in between. This needs a cash-on-desk detector, a door-entry event and a safe-door state (open or closed).
3. **Personal belongings in the cash zone** (X-40). Cue: a handbag, bag or coat on the cash desk. Off-the-shelf COCO classes cover this (handbag, backpack; a coat may need a custom class or a large-blob heuristic).
4. **Food or drink at the cash workplace** (X-46). Cue: a cup, bottle or food container on the desk, and hand-to-mouth pose with the object.
5. **Improper banknote handling: no sorting, no detector check, mixed denominations** (X-44, low confidence). Cue: fine-grained only (number of piles, colour mixing, no interaction with the detector ROI). This is hard to detect reliably.

## Notes for building the detector (from the clean videos)

- **Not violations on their own:** the safe open with a client in the room (X-35, X-39); cash on the desk while the cashier is seated and handling it (X-43, X-45); a client bending over the desk during the handover; people moving behind the glass wall, who are outside the room and should be filtered with a room-interior ROI.
- **Normative sequence, Layout A:** door entry, then handover at the desk, then the client seated, then machine count, then the safe opened, cash in and the safe closed, then the client leaves, then the desk is empty. The violations in this batch break this sequence in its order (X-34: the client leaves before the count; X-38: the safe step is skipped before the next entry).
- **Normative scene, Layout B:** the desk holds only the laptop, phone, counting machine, detector and the cash being processed. Any other object class (handbag, coat, cup or food) is a violation candidate.
- The labelled second usually marks the **start of the bad state**, not its end. Examples: the cashier sitting down with the bag (X-40, 17 s), picking up the cup (X-46, 11 s), the next client walking in (X-38, 33 s), and the count starting after the client turned away (X-34, 13 s).
