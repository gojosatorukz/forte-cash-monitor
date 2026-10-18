# Status: external footage set 2 (robust/external2)

**Where:** `robust/external2/` — `videos/*.mov` (27 clips, 152 MB, no audio, native res, ≤90 s), `manifest.csv`
(source URL, author, licence, segment, camera angle, scene type), `labels.csv`
(`file,violation,type,label_sec,angle,certainty,evidence`; several events per row are separated by `;`). Per-collector parts are in `parts/`.
LOCAL ONLY: do not commit, upload or push. These clips are for robustness testing only, not training.

How it was made: two collector subagents, A (RU/KZ YouTube) and B (EN YouTube + Pexels), labelled clips from 1–2 fps frame
sheets. The orchestrator re-checked 9 of the 27 against frames (e2_02, 04, 06, 10, 22, 23, 28, 31, 34), and all held.
External set 1 (`robust/external/`) is not duplicated here.

## Counts
- **Verdict:** 16 violation / 11 clean.
- **T-code (clips containing it):**
  - T6 outsider ×8
  - T1 unattended ×5
  - T10 concealment ×4
  - T12 food/drink ×4
  - T13 phone ×2 (stock only)
  - T3 ×1 (deposit vault, a stretch)
  - T4 ×1
  - T9 ×1 (weak)
  - T14 ×1 (non-cash scene)
  - **None:** T2, T5, T7, T8, T11, T15.
- **Angle:**
  - C side/high corner ~45° ×10
  - D top-down ×9
  - E eye-level ×4
  - W wide room ×3
  - A behind-shoulder ×2
  - **None:** F front low counter cam.
- **Scene:**
  - bank_teller ×9
  - shop_register ×6
  - cash_office ×5
  - exchange_booth ×3
  - safe, counting_room, cash_collection, other ×1 each
- **Source:** YouTube ×23 ("YouTube standard (educational use)"), Pexels ×4 (Pexels License).
- **Certainty:** 9 of the 27 clips have every event "certain". The weakest labels are e2_01@7 and e2_02@22, both uncertain.

## Best 10 for robustness testing
| # | Clip | Why |
|---|---|---|
| 1 | e2_04 exchange booth intruder (T6@10, certain) | Real KZ exchange-booth CCTV and a clear entry into the cash zone. The source mixes several cameras. |
| 2 | e2_34 wide, behind the teller line (T4@42 + T12) | Fisheye wide view, a teller leaves with the screen on, and a second teller stays, so T1 must NOT fire. |
| 3 | e2_02 teller booth, behind the shoulder (T6@22, uncertain) | Closest to training angle A. A second person enters; checks whether a colleague is wrongly flagged as an outsider. |
| 4 | e2_22 unattended exchange booth, arm reaching in (T1+T6@0) | Inside-booth fisheye; tests detection when the state already holds at t=0. |
| 5 | e2_11 office at night, intruder at the safe (T6@1) | Wide room, infrared/night, a new lighting domain. |
| 6 | e2_31 shop counter left unattended, drawer theft (T1/T6/T10) | Real CCTV with strong backlight and three events in sequence. |
| 7 | e2_23 teller counter top-down (clean) | False-alarm test: lots of cash handling with no violation. |
| 8 | e2_24 teller window, behind the teller (clean) | False-alarm test. Includes a paper cup on a side shelf that could trigger a false T12. |
| 9 | e2_33 wide bank lobby (clean) | False-alarm test on a wide view with many people (T6 must not fire on customers in the lobby). |
| 10 | e2_28 camera spray-painted (T14@1.5) | The only real camera-blocking clip. |

Also useful: e2_25 (casino cage, clean, fisheye) and e2_10 (deposit vault, T3/T10).

## Caveats
- **Pexels clips are not CCTV.** e2_26, e2_27, e2_29 and e2_30 are 10–26 s close-ups. Treat them as object tests (T12/T13/T1).
- **Overlays and recapture:**
  - e2_01 and e2_21 are partly filmed off a monitor.
  - e2_02 is a time-lapse.
  - e2_07 is pillarboxed.
  - Several clips have logos or timestamp OSD (noted in the manifest).
- **The clean set is mostly not real bank CCTV.** e2_08 is TV footage, e2_09 is staged, and e2_12/13/29 are shop registers.
- **Short or partial clips.** e2_32 (T9) is only 6 s, and the bag handover is not clearly in frame.
- **Still uncovered:**
  - No footage found for T11.
  - No real-CCTV footage for T13 or an инкассация handover.
  - No T3 on a cash-room safe.
  - No front low counter angle.
  - Many searches were tried (see the collector reports). Our own recordings (`recording_guide.md` scenes 11–12, 8, 3) remain the only source for these.

## Round 2 — robust/external3 (unseen violation types)

**Where:** `robust/external3/` — `videos/*.mov`, `manifest.csv`, `labels.csv` (same format as external2). Collector brief + per-collector parts/notes in `parts/`. Extra tags for steps with no T-code: P31 payout without recount, P59 suspicious note handed back, P42 counting alone, P15 cash passed between cashiers, P107 bag to collector without seal check, ABS cashier asleep/absent. LOCAL ONLY.

### Batch 1 (02:30) — 11 clips, 97 MB, all YouTube real CCTV
- T10 pocketing/hiding ×4 (e3_01 central-bank counting room, e3_02 exchange safe, e3_06 shop till top-down fisheye, e3_10 bakery till) — all *probable* (pocket entry occluded by body).
- T6/T1 outsider in empty teller booth ×1 (e3_03, certain — Turkish vezne CCTV, high corner).
- ABS clerk lies down behind open counter ×1 (e3_05), P59 notes handed back to client ×1 (e3_09), P31 payout without joint recount ×1 (e3_11, news overlay).
- Clean ×3: e3_04 drive-up teller behind-high, e3_07 teller counter top-down payout with ID, e3_08 wide behind teller line.
- Lead re-checked frames of e3_01, 03, 06, 09: labels hold.
- **Not found yet:** T13 at a bank/exchange counter (only c-store "Cctv Officer" clips, same genre as robust/phone), T14 in shop/bank/office, T3, indoor инкассация handover. Search dead-ends listed in `parts/a_notes.md`.

### Batch 2 (03:30) — 24 clips e3_31–e3_54, 155 MB → also in `robust/external3b2/` (hard links, own manifest/labels)
- T13 phone ×2: **e3_49 real CCTV, Indian bank-kiosk operator using smartphone at the service desk (certain)** — first real-CCTV T13 at a bank desk; e3_36 c-store cashier phone at ear (filmed off DVR).
- T14 camera covered ×2 (e3_31 hotel floor taped/hand, e3_44 home cam turned to ceiling then covered) — indoor but not cash rooms.
- T6 outsider/second person ×11 (post-office/teller/casino cage/back office/bank kiosk), T1 unattended ×5, T10 ×4, T11 handbag on cash desk ×1 (e3_52, uncertain — 8 s, heavy overlays), T12 ×1, T5 ×1, T3 ×1 (e3_46 burglar forcing safe, uncertain — not "left open"), P31 ×1, T99 ×2 (teller filling robber's bag / reaching through window).
- Clean ×4: e3_33 behind cashier service counter, e3_40 exchange office behind two windows, e3_51 fisheye teller payout (panning), e3_54 register top-down.
- Re-filmed monitor/DVR: e3_32, 36, 37, 43, 47, 48. Lead re-checked frames of e3_31, 39, 49, 52 (e3_52 downgraded to uncertain).

### Round 2 totals (35 clips, 251 MB)
- 28 violation / 7 clean. Clips containing: T6 ×13, T10 ×9, T1 ×6, T11 ×2, T13 ×2, T14 ×2, T99 ×2, P31 ×2, T3/T5/T12/ABS/P59 ×1.
- Scene: bank_teller 13, shop_register 10, exchange_booth 5, cash_office 4, counting_room 1, other 2. Angle: D 10, A 9, W 8, C 4, B/E 1 each (+2 mixed).
- **Best for unseen-type testing:** e3_49 (T13 real), e3_03 (T1→T6 empty booth), e3_42 (T6 post-office clerk zone), e3_01 (T10 counting room), e3_06 (T10 till top-down), e3_31 (T14), e3_05 (ABS), e3_11/e3_53 (P31), e3_09 (P59); clean FP checks e3_04, e3_07, e3_40.
- **Still not found anywhere (two collectors, ~10 languages):** T3 safe *left open*/key in lock, indoor инкассация handover (incl. P107), T14 in a bank/shop/office, P15 cash between cashiers, P42 counting alone after hours, T4 unlocked screen. Only our own staged recordings / synthetic clips can cover these. Dead-end queries: `robust/external3/parts/a_notes.md`, `b_notes.md`.
