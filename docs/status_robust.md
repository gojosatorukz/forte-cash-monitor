# Robustness status (forte-robust)

Updated: 2026-10-17

## Deliverables
| # | Item | State |
|---|---|---|
| 1 | Augmented set `robust/<variant>/videos/*.mov` + `robust/gen_augment.py`, `robust/README.md`, `robust/manifest.json` | **done**: 25 variants x 42 = 1050 files, 2.8 GB, durations verified (ffprobe), contact sheet `robust/previews/contact.jpg` checked |
| 2 | Recording guide `docs/recording_guide.md` (RU, 12 scenes / ~22 takes, angles A–E, T13/T14 priority) | **done** |
| 3 | Per-augmentation recall/FP of the detector | waiting for a non-stub analyzer (`perception_only`, then `full`) |

## How to eval
From the detector worktree (`detector/.claude/worktrees/phase1`):
`FORTE_DATA_DIR=/home/datamata/Projects/datathon2026/robust/<variant> uv run python -m eval.run_eval --analyzer <mod:fn> --variant orig`
(Filenames are identical to the originals, so labels.json applies unchanged; `--variant` would stack an in-memory transform on top.)

## Variants (details in robust/README.md)
reencode (control: same pipeline, no transform) · hflip · yaw+20 · yaw-25 · pitch+20 · pitch-30 (actually -27°, area guard) · yawpitch+25+15 · zoom130 · crop_offset · wide_pad · rot90cw · landscape1080 · bright · dark · lowcontrast · blur · jpeg_q15 · lowbitrate · fps10 · fps5 · landscape_pad (upright 16:9 pillarbox) · landscape_crop (upright 16:9 desk band, 2.76× upscale) · newcam_full (same view change as newcam_combo, no crop, whole room kept, scene 0.66×) · newcam_full_l (yaw+25/pitch+10, upright 1280x720, scene strip 0.61×, only 25% of the frame is real content: small-target test) · newcam_combo (yaw-20/pitch+15, zoom1.15, -20 bright, blur1, jpeg30, 15 fps).
Pinhole f=665.5 px (65° on the long side); crop is folded into the homography, so there are no black wedges.

## Notes
- **Limits of the proxy:** a homography simulates camera *rotation* only, not a different camera *position* (no parallax, no new occlusions, same room layout). Real new-angle footage (recording_guide.md → robust/own/) is still needed.
- Warps and crops push scene edges out of frame (for example in X-2 pitch-30 the man at the top-left is lost). A miss there can mean the event is off-screen, so check misses on the warp/crop variants per video before blaming the model.
- Timings here are measured on the dev RTX 3060 Laptop (6 GB, shared with other sessions); prod is RTX 5080 16 GB (decisions.md), so 5-min-budget checks (landscape1080) are pessimistic.

## Results

### Sweep perc1: perception_only, indicative only (mixed code: orig/reencode at config a23a692e51, later variants at 130c00a313)
Tool: `robust/.venv/bin/python -I robust/compare_runs.py robust/runs/perc1` → `robust/runs/perc1/compare.md`.

**Findings (checked against result.json):**
1. **Re-encoding alone is a big perturbation.** `reencode` (crf18, no transform) vs orig: verdict agreement 83%, per-sample n_persons agreement 74%, checkpoint recall 70% (±2 s, same type), 27% extra checkpoints. X-44 and X-1 flip from violation to clean. Cause: person detections near person_thr=0.3 appear or vanish and the change cascades into tracks and roles. Detection is deterministic, so this is not noise from nondeterminism. The jury's video is always a different encode, so this is a **real risk** → the timeline needs hysteresis (sent to forte-detector).
2. **Auto-orient misfires** on landscape1080 (correct undo is 270° clockwise): right on only 15/24 videos, 180° on 5, 0° on 5, often by tiny margins. Risk: a natively upright jury landscape clip could get rotated wrongly → added `landscape_pad`/`landscape_crop` upright variants to test that 0° is kept. Suggested fixes: container rotation tag first, default 0° with a large margin required, 180° almost never.
3. Runtime of landscape1080: ~10 s mean / 33 s max per clip (2.2× orig) on the 3060. OK for the budget so far.
4. Prod bug found and fixed by forte-detector: the perception cache was keyed by filename, not content (stale results for same-named uploads).

### Sweep full_c5ef2b6: full analyzer (VLM), pinned export robust/det_c5ef2b6, deduped to 40 clips. Paused on orchestrator call until the retune commit
`python3 -I robust/recall_table.py robust/runs/full_c5ef2b6` (worst-first):

| variant | recall | FP | lost vs orig | gained | FP clips | hit@5s | type-hit | mean/max s | flag |
|---|---|---|---|---|---|---|---|---|---|
| newcam_combo | 8/21 | 1/19 | 6: X-11, X-13, X-17, X-18, X-30, X-4 | X-22 (as T11), X-24 (as T12), X-26 | X-35 (T15) | 38% | 75% | 16.6/28.7 | **DROP >3** |
| orig | 11/21 | 0/19 | — | | | 48% | 82% | 14.1/29.7 | |
| reencode | 12/21 | 3/19 | 1: X-13 | X-24, X-33 | X-14 (T2), X-19 (T15), X-35 (T2) | 57% | 100% | 15.8/29.1 | |

**Reading:**
- **reencode:** 6/40 verdicts flip from the encode alone, so the decision boundary sits on noise; T2 is the most fragile. → forte-detector is retuning on orig+reencode jointly.
- **newcam_combo:** frame check at the label second (orig vs variant). In X-17 (T9 collector), X-30 and X-18 (T6 second person) the key person stands at the left/door edge and **is cut out of frame by the warp-crop**, so those are artefacts of the variant (it keeps only 58% of pixels). **X-11 (T4) is a real miss**: the laptop screen is clearly visible but not flagged. X-4/X-13 (T2) are not frame-checked yet (T2 already flips under re-encode). Two of the gains carry the wrong type (lucky hits).
- → Added `newcam_full` / `newcam_full_l`: the same viewpoint change without crop (the whole room stays in frame, blurred fill), so model failures can be told apart from off-screen events.

### Final detector 38f9946 (= 2a508df): full analyzer, PC, all 42 clips, 25 variants (FINAL)
Source: `robust/runs/final_2a508df/<variant>` (orig/reencode/newcam_combo copied from `work/pc_runs/2a508df0b9ff-*`, the rest from the overnight `robust/run_sweep_pc.sh` run, 00:34–04:22). Table: `python3 -I robust/recall_table.py robust/runs/final_2a508df`. Worst-first; flag = more than 3 orig TPs lost. 42 clips with no dedupe, so X-47/X-48 (duplicates) are included. "frame kept" = share of the original frame still visible after the warp/crop (from `robust/manifest.json`; fit/pad variants keep 100% with fill around it).

**Summary:** orig recall 20/22, FP 4/20. Across the 25 variants: recall ≥18/22 on 9, ≥16/22 on 18, ≥15/22 on 22. Median recall 16/22, median FP 7/20 (max 13/20). The DROP >3 flag fires on 16/25 variants.

Run: `robust/runs/final_2a508df` · baseline orig recall 20/22, FP 4/20

| variant | recall | FP | lost vs orig | gained | FP clips | hit@5s | type-hit | mean/max s | err/degr | flag | frame kept |
|---|---|---|---|---|---|---|---|---|---|---|---|
| pitch-30 | 8/22 | 9/20 | 13: X-1, X-13, X-18, X-2, X-20, X-24, X-30, X-32, X-33, X-34, X-38, X-4, X-47 | X-44 | X-10, X-12, X-14, X-16, X-19, X-3, X-35, X-39, X-43 | 32% | 75% | 23.7/56.3 | 0/0 | **DROP >3** | 49% |
| landscape_crop | 10/22 | 5/20 | 10: X-1, X-13, X-18, X-2, X-32, X-33, X-34, X-38, X-4, X-47 |  | X-10, X-19, X-31, X-45, X-8 | 32% | 80% | 23.7/44.3 | 0/0 | **DROP >3** | 31% |
| crop_offset | 14/22 | 5/20 | 6: X-13, X-18, X-2, X-30, X-32, X-33 |  | X-16, X-19, X-35, X-37, X-39 | 59% | 79% | 28.5/64.3 | 0/0 | **DROP >3** | 56% |
| fps5 | 15/22 | 5/20 | 5: X-1, X-18, X-2, X-4, X-47 |  | X-16, X-19, X-35, X-37, X-39 | 64% | 80% | 29.5/82.3 | 0/0 | **DROP >3** | 100% |
| lowbitrate | 15/22 | 10/20 | 6: X-11, X-13, X-17, X-26, X-32, X-40 | X-44 | X-16, X-19, X-21, X-23, X-35, X-37, X-39, X-45, X-48, X-5 | 45% | 47% | 31.0/75.2 | 0/0 | **DROP >3** | 100% |
| newcam_full | 15/22 | 11/20 | 5: X-13, X-26, X-32, X-33, X-34 |  | X-16, X-19, X-25, X-29, X-3, X-35, X-37, X-39, X-43, X-48, X-5 | 55% | 47% | 34.2/79.4 | 0/0 | **DROP >3** | 100% |
| newcam_full_l | 15/22 | 10/20 | 5: X-13, X-20, X-32, X-33, X-38 |  | X-14, X-16, X-19, X-3, X-35, X-37, X-39, X-45, X-48, X-5 | 59% | 60% | 33.3/80.0 | 0/0 | **DROP >3** | 100% |
| blur | 16/22 | 13/20 | 5: X-11, X-13, X-26, X-28, X-32 | X-44 | X-12, X-16, X-19, X-21, X-25, X-29, X-3, X-35, X-37, X-39, X-48, X-5, X-8 | 55% | 38% | 30.5/74.1 | 0/0 | **DROP >3** | 100% |
| dark | 16/22 | 5/20 | 4: X-13, X-2, X-32, X-4 |  | X-19, X-35, X-37, X-39, X-48 | 68% | 88% | 31.6/74.1 | 0/0 | **DROP >3** | 100% |
| jpeg_q15 | 16/22 | 13/20 | 4: X-11, X-28, X-32, X-40 |  | X-16, X-19, X-21, X-25, X-27, X-3, X-31, X-35, X-37, X-39, X-48, X-5, X-8 | 64% | 62% | 31.9/72.5 | 0/0 | **DROP >3** | 100% |
| newcam_combo | 16/22 | 11/20 | 5: X-13, X-18, X-2, X-32, X-33 | X-22 | X-16, X-21, X-23, X-25, X-29, X-3, X-35, X-37, X-39, X-48, X-5 | 68% | 56% | 32.5/68.6 | 0/0 | **DROP >3** | 58% |
| pitch+20 | 16/22 | 7/20 | 4: X-13, X-2, X-24, X-32 |  | X-16, X-19, X-25, X-3, X-35, X-37, X-39 | 68% | 88% | 33.8/84.2 | 0/0 | **DROP >3** | 61% |
| rot90cw | 16/22 | 7/20 | 4: X-18, X-20, X-24, X-26 |  | X-16, X-19, X-3, X-35, X-37, X-39, X-8 | 64% | 69% | 35.5/79.6 | 0/0 | **DROP >3** | 100% |
| yawpitch+25+15 | 16/22 | 5/20 | 4: X-13, X-2, X-4, X-47 |  | X-16, X-19, X-35, X-37, X-39 | 64% | 88% | 33.8/71.2 | 0/0 | **DROP >3** | 74% |
| landscape1080 | 17/22 | 8/20 | 3: X-18, X-20, X-30 |  | X-14, X-16, X-19, X-35, X-37, X-39, X-48, X-5 | 64% | 65% | 40.8/85.0 | 0/0 |  | 100% |
| yaw-25 | 17/22 | 6/20 | 4: X-13, X-2, X-30, X-33 | X-44 | X-12, X-19, X-31, X-35, X-37, X-39 | 68% | 71% | 35.7/80.8 | 0/0 | **DROP >3** | 67% |
| bright | 18/22 | 8/20 | 2: X-13, X-2 |  | X-16, X-19, X-3, X-35, X-37, X-39, X-48, X-5 | 77% | 89% | 31.5/78.6 | 0/0 |  | 100% |
| fps10 | 18/22 | 6/20 | 3: X-2, X-4, X-47 | X-22 | X-16, X-19, X-25, X-35, X-37, X-39 | 73% | 78% | 31.5/75.1 | 0/0 |  | 100% |
| landscape_pad | 18/22 | 8/20 | 2: X-30, X-32 |  | X-14, X-19, X-3, X-35, X-37, X-39, X-48, X-5 | 59% | 50% | 31.0/74.6 | 0/0 |  | 100% |
| reencode | 18/22 | 6/20 | 2: X-13, X-2 |  | X-14, X-16, X-19, X-35, X-37, X-39 | 77% | 100% | 34.6/77.4 | 0/0 |  | 100% |
| yaw+20 | 18/22 | 6/20 | 3: X-1, X-13, X-18 | X-22 | X-16, X-19, X-35, X-37, X-39, X-8 | 82% | 72% | 29.9/68.7 | 0/0 |  | 74% |
| hflip | 19/22 | 6/20 | 1: X-2 |  | X-19, X-3, X-31, X-35, X-37, X-39 | 82% | 84% | 34.0/75.4 | 0/0 |  | 100% |
| wide_pad | 19/22 | 8/20 | 1: X-32 |  | X-16, X-19, X-25, X-3, X-37, X-39, X-48, X-5 | 82% | 84% | 30.9/71.1 | 0/0 |  | 100% |
| zoom130 | 19/22 | 5/20 | 1: X-18 |  | X-16, X-19, X-35, X-37, X-39 | 73% | 79% | 33.1/86.0 | 0/0 |  | 59% |
| lowcontrast | 20/22 | 6/20 | 0:  |  | X-16, X-19, X-3, X-35, X-37, X-39 | 82% | 90% | 32.5/70.3 | 0/0 |  | 100% |
| orig | 20/22 | 4/20 | 0:  |  | X-19, X-35, X-37, X-39 | 82% | 85% | 31.8/79.7 | 0/0 |  | 100% |

**Reading it (FN-first):**
- **Crop artefacts dominate the worst rows.** pitch-30 (8/22, 49% of the frame kept) and landscape_crop (10/22, 31% kept) cut away the desk, the door or the people. Most of their losses are content off-screen, not a detector failure; treat them as a lower bound. crop_offset (56% kept) likewise. Fit/pad variants that keep the whole scene (newcam_full, newcam_full_l, landscape_pad, wide_pad) are the realistic "new camera" proxies: 15–19/22.
- **Three clips account for most losses.** X-13 is lost in 15/25 variants, X-2 in 13/25, X-32 in 13/25.
  - X-13: the orig TP is itself fragile (T2 at 6.2 s vs a T8 label at 47 s, no type-hit). It flips under any re-encode, including plain reencode and bright.
  - X-2: a T10 hit that also flips on plain re-encode. Encode-fragile.
  - **X-32 and X-33: T5 (door open), real viewpoint weakness.** Lost on most new-angle variants (newcam_*, crop, pitch, yaw-25) and on landscape_pad/wide_pad. Door detection depends on the calibrated door ROI, and a moved/smaller door is not re-found.
- **Encode/quality:** lowbitrate (15/22) and blur/jpeg_q15 (16/22) lose a different set (X-11 T4, X-26, X-28, X-40) and raise FP to 10–13/20, the noisiest variants for FPs.
- **Temporal:** fps10 18/22, fps5 15/22. At 5 fps short events are missed (X-1, X-4, X-18, X-2).
- **Orientation:** rot90cw 16/22 and landscape1080 17/22. Auto-orient works, with mid-level losses. hflip 19/22.
- **FPs** rise on every variant (4 → 5–13/20). That's acceptable under the "FN worse than FP" rule, but expect more false alarms on a new camera.

## External clips (production detector cb77b44, qualitative)
14 clips from `robust/external/` (other scenes and cameras, CC BY / PD / Pexels / Mixkit; local only), run on the PC via `work/pc_eval.sh cb77b44 --robust external_alias` (clips hard-linked under training filenames, mapping in `robust/external_alias/alias_map.json`; the harness metrics are meaningless here). Results: `robust/runs/external_cb77b44/`. **0 crashes, 0 timeouts, 0 degraded; orientation 0° on every clip (correct); wall time 16–60 s per clip** (3 parallel workers; the VLM probes take most of it; 4K clips 43–60 s).

| clip | scene / likely type (manifest) | persons & roles | calibration (seat/door/safe) | events (type @ s) | verdict judgement |
|---|---|---|---|---|---|
| ext01 | top-down till, cashier pockets cash, leaves (T10 + T1) | 1–2 tracks, sensible | seat ✓, door ✗ | — | **miss** |
| ext02 | outsider inside cashier zone, phone (T6 + T13) | 4 visitor tracks | seat/door ✓ | T15 @25, T11 @30 | verdict ok, **type wrong** (no T6/T13) |
| ext03 | robber reaches over counter (T6) | 3 visitors + 10 background | ✓ | T15 @12, T15 @55 | verdict ok, type wrong |
| ext04 | after-hours intruder behind bar (T6/T1/T5) | ok | ✓ incl. safe | T5 @24 | verdict ok, type partly plausible |
| ext05 | group behind teller counters (T6), news overlay | 5 visitors + **18 background** | ✓ | T7 @6, T2 @18 | verdict ok, type wrong |
| ext06 | restaurant cashier fraud (T99) | 1 cashier | all zones "found" | T12 @27 | verdict ok (food is really on the counter), type off |
| ext07 | counter robberies, two views (T6) | 3 visitors + **15 background** | ✓ | T5 @27 | verdict ok, type wrong |
| ext08 | outsiders force manager from desk (T6/T1) | 5 visitors | ✓ | T12 @26 (0.40) | verdict ok by luck, type wrong |
| ext09 | empty bank lobby (clean) | none | no seat | — | ✓ clean |
| ext10 | staged hooded intruders, bag on desk (T6/T1) | 4 visitors | ✓ | T11 @11, T7 @18 | verdict ok, T11 (bag) plausible, no T6 |
| ext11 | counting machine stock clip (clean) | 1 | seat ✓ | T12 @23 (0.40) | **FP** (weak) |
| ext12 | top-down cash table (clean/unclear) | 1–2 | **door "found" with no door in the scene** (box [0,0,.44,.47], door score None) | T5 @4.2 (0.72) | **FP, hallucinated door** (frame-checked) |
| ext13 | safe close-up, opened and left open (T3) | hands only | safe ✓ | — | **miss** |
| ext14 | desk counting stock clip (clean) | 1 | seat ✓ | — | ✓ clean |

**Top failure modes:**
1. **T6 (outsider in the cash room) never fires on new scenes.** 7 of 14 clips have intruders or outsiders and T6 fired 0 times. Verdicts come out "right" only through other types (T15/T5/T7/T12/T2), which costs classification and grounding. Root-cause hint: the role logic dumps many real persons into `background_tracks` (ext05 18, ext07 15, ext03 10). The behind-glass/background filter and the seat-based role model are fitted to our room.
2. **Calibration hallucinates absent objects.** A door zone is set without any door detection (ext12 → confident T5 "door open" @4.2 s on a table top shot). Zones are "found" for everything in ext06/ext08. There's no "absent" state, so rules run on phantom ROIs, which gives wrong-type events and FPs.
3. **Single-actor violations in unfamiliar framing are missed.** ext01 (pocketing at the till, then leaving) and ext13 (safe left open, close-up) produce no events. T10/T3/T13 probes depend on our room layout (seat, safe position). Phone in hand (ext02) gives no T13. Plus low-confidence T12 (0.40) fires generically (ext08, ext11).

## External set 2 (forte-footage, labelled; production cb77b44)
Scorer: `python3 -I robust/score_external.py robust/external2 robust/external2_alias robust/runs/external2_cb77b44` (all 27 clips).

- **Clean clips: FP 6/11**
- Violations (certain/probable): **recall 10/15**, type-hit 4/10, median |Δt| 7.7 s, hit@5s 4/10 (e2_34: T12 @40.4, T4 not fired, T1 correctly silent)
- Soft (uncertain-only) violations: 1/1 flagged


**Findings (frame-checked where noted):**
1. **FP on clean clips is high: 6/11**, almost all from generic object probes: T11 (personal items) on e2_05/13/23, T12 (cup/food) on e2_05/13/24/29, plus T1/T5 on e2_08 (edited news footage) and T15 on e2_29. On new scenes the "desk" ROI covers counters with customers' belongings, and the VLM answers "yes" to bag/cup readily.
2. **T6 type-hit 0/8 again** (intruders and outsiders). Verdicts carried by T11/T7/T12/T15/T1. Overall type-hit 4/10 TPs; median |Δt| 7.7 s (hit@5s 4/10), 27 clips.
3. **T14 is broken on real footage (e2_28, spray-painted CCTV, frame-checked: fully dark from ~2.5 s).** `cheap.camera_blocked` returned False on all 5 probes. Measured: blocked frame std 2.7 but edge_frac 0.72 after contrast normalisation; the normalisation turns sensor noise into "texture", so dark/noisy covers never count as flat. The OSD band is not the cause (excluding it doesn't help). Proposed fix: decide on *loss of scene structure vs the reference*, e.g. correlation of blurred, normalised low-res gradients with the reference frame (lights-off keeps structure → high correlation; cover/spray/turned-away → low) plus absolute gradient energy dropping. Synthetic labelled T14 set (+ hard negatives) being generated in `robust/t14_synth/`.
4. **Misses with nobody in the scene:** e2_27 (cash plus phone on a desk, no person, close-up): the VLM answered cash_on_desk=False (0.98) and phone=False on a frame full of banknotes (frame-checked). e2_31/e2_32/e2_01 (unattended counter + intruder; impostor guard; hidden theft): no events.

## External set 2 — 2a508df (production detector 38f9946 = 2a508df; same 27 clips)
PC run `work/pc_runs/2a508df0b9ff-external2_alias-*` (copied to `robust/runs/external2_2a508df`). Scorer: `python3 -I robust/score_external.py robust/external2 robust/external2_alias robust/runs/external2_2a508df`. (The earlier `2a508df0b9ff-external2-*` run is invalid: it used the un-aliased folder, so all items hit FileNotFoundError.)

| metric | cb77b44 | **2a508df** |
|---|---|---|
| Clean clips FP | 6/11 | **7/11** |
| Recall (certain/probable) | 10/15 | **10/15** |
| Type-hit (of TPs) | 4/10 | **5/10** |
| hit@5s (of TPs) | 4/10 | **6/10** |
| Median \|Δt\| | 7.7 s | **4.0 s** |
| Soft (uncertain-only) flagged | 1/1 | 1/1 |
| Wall time, 27 clips (sum of per-clip) | — | 1852 s (mean 69 s/clip, range 12–85 s) |

**What changed vs cb77b44:**
- **T14 fixed on real footage:** e2_28 (spray-painted camera) FN → **TP, T14 @ 3.0 s** (label 1.5 s, |Δt| 1.5 s). This is the only new TP and the main reason type-hit and hit@5s went up.
- **T6 type-hit still 0/8.** T6 now fires at all (it never did before), but only on e2_34 (T6 @ 0.0 and 35.5; wide behind-teller shot, labels T4/T12, so no type credit) and as an FP on clean e2_13 (T6 @ 3.0, a client at a top-down shop counter). None of the 8 intruder/outsider clips gets a T6 event.
- **T11/T12 FPs unchanged:** T11 on e2_05/13/23, T12 on e2_05/13/24/29. Same events, same timestamps. The generic object probes are untouched.
- **Other FP moves:** e2_08 lost the T5 door hallucination (T1 @ 10.6 remains, still FP). **New FP e2_12** (clean top-down shop till): T15 @ 4.5.
- **Lost TP: e2_11** (night office intruder, T6 @ 1). cb77b44 caught it only incidentally (T1 @ 38.1); 2a508df emits nothing. Recall stays 10/15 only because e2_28 replaced it.
- Unchanged FNs: e2_01 (T10 safe theft), e2_27 (cash/phone close-up, nobody present), e2_31 (unattended drawer theft), e2_32 (fake CIT guard).

## Phone set (`robust/phone/`, 19 clips: 12 T13, 3 T14, 4 clean; production 2a508df)
Run `robust/runs/phone_2a508df` (alias `robust/phone_alias`). Scorers: `score_external.py robust/phone robust/phone_alias robust/runs/phone_2a508df` (verdicts) and `score_probes.py` (same args; phone cue).

- **Clean FP 2/4:** cl_01 T1 @22.6, cl_03 T10/T2/T6. No phone FPs; the phone cue fires on 0/7 non-phone clips.
- **Recall 11/14** (certain/probable), type-hit 7/11, median |Δt| 6.4 s, hit@5s 5/11. The soft clip (ph_05) is flagged.
- **T13, phone clips:** verdict TP on 11/12 (ph_04 FN). **A T13 event appears on 7/12.** The phone cue (VLM phone_visible) answers True on 9/12. Two gaps:
  - **Cue fired, no T13 event (gating):** ph_02 (Y at 6 s and 11 s, conf 0.97–0.99) and ph_03 (Y at 11 s and 19 s). Both are caught by other types (T12; T5/T2/T1) but not as phone.
  - **Cue silent:** ph_04 (pink phone, held 7–11 s; probes at 4/8/11 s all said no) and ph_06 (dim scene, lit screen; no, with conf down to 0.45 at 11 s).
  - T13 timing: the first T13 event lands 3–12 s after the phone appears (phone probes are sparse, the first at ~3–10 s).
- **T14, real footage: 0/3 T14 events** (t14_03 is a verdict TP only via T11 @7.4). Dense camera probes ran every 1 s (25/22/14 probes) and `cv:blocked` answered False on every one, including t14_02 (uniform cover from 18 s to the end) and t14_01 (hand fills the frame 7–11.5 s). **Frame-checked (one covered frame each):** t14_02 and t14_03 are *screen recordings of desktop VMS software*. The camera view is a small window inside OS window chrome, with an "Events" table below and grey desktop around it, so most of the frame keeps its structure while the lens is covered. These are out of scope for a camera-feed detector, not a real miss. t14_01 is a genuine full-frame hand cover with only a small "Tampering Alarm" OSD. The hand is close but not flat: finger edges, skin shading and a strip of background stay visible, so a structure-loss test does not see it as blocked. That one is a real T14 limitation (partial/close hand). e2_28 (spray, full-frame CCTV) does fire.

### Phone set: before/after, 2a508df → **67d1dbe (final submitted build: T13 PhoneRule + T14 covered-from-start)**
Run `robust/runs/phone_67d1dbe`, same scorers.

| metric | 2a508df | **67d1dbe** |
|---|---|---|
| Clean FP (any type) | 2/4 | 2/4 (same cl_01 T1, cl_03 T10/T2/T6; no T13 on clean) |
| Recall, certain/probable | 11/14 | **12/14** (ph_04 FN → TP) |
| Type-hit (of TPs) | 7/11 | **11/12** |
| hit@5s (of TPs) | 5/11 | **11/12** |
| Median \|Δt\| | 6.4 s | **2.6 s** |
| T13 event on phone clips | 7/12 | **11/12** (only soft ph_05 has none) |
| Phone cue fires (phone / non-phone clips) | 9/12 / 0/7 | **12/12 / 0/7** |
| T14 event on real T14 clips | 0/3 | **0/3** (t14_03 verdict TP only via T11) |

- The T13 gating gaps (ph_02, ph_03) and the silent cues (ph_04, ph_06) are all fixed. T13 now fires 1–4 s after the phone appears (ph_03 still at 13.2 s).
- T14 is unchanged at 0/3. Two of the clips are VMS screen recordings (out of scope); t14_01 is a real partial/close hand cover that is still missed (see the frame check above).
- All other events are identical between the builds, except ph_03 losing a spurious T5 @7.7.

## MEVA probe test (`robust/meva/`, 11 real indoor CCTV clips, CC BY 4.0; 2a508df)
> Superseded for the docs: forte-docs cites a rule-level result on the final build 67d1dbe (T13 on 4/8 phone clips, 0/3 negatives; not run by me). The section below is the pre-fix probe-level diagnosis.

These are hallways/lobbies, not cash desks, so this tests whether the probes fire. **It is not verdict ground truth** (a phone in a corridor is not a cash-discipline violation). Run `robust/runs/meva_2a508df`; `python3 -I robust/score_probes.py robust/meva robust/meva_alias robust/runs/meva_2a508df` → `probe_score.md`.

- **Phone cue (VLM phone_visible) fires on 5/8 phone clips**, silent on meva_02/03/08 (all three probes answered no, conf 1.00). It also fires on 2/3 non-phone clips (meva_09 at 12 s and 28 s, conf 0.99; meva_10 at 21 s, conf 0.69). So on far-field CCTV the cue is noisy in both directions.
- **Phone probes are not anchored to the person.** They are always asked at ~12/21/28 s, while the phone in most clips appears at 1–6 s and people walk through quickly. Some silent clips may simply be probing after the person has left (not frame-verified).
- **Door (CV/VLM door_open):** calibration found a door on 10/11. On the door clip meva_11, door_open = Y at 6 s (door opens ~5–7 s), N at 12 s and 19 s: tracked correctly. One spurious door_open Y on meva_08 (14 s) produced a T5 event.
- For reference only: T13 events appear on meva_01/04 (at 11.9 s) and on clean meva_09.

## External set 3 (forte-footage round 2, 35 labelled clips; final build 67d1dbe)
Batch 1 = e3_01–11 (`robust/runs/ext3b1_67d1dbe`, labels filtered to `robust/external3b1_labels/`), batch 2 = e3_31–54 (`robust/runs/ext3b2_67d1dbe`). Scorer `score_external.py <set> <alias> <run>`. P*/ABS tags (procedure steps without a T-code) count for recall but can never be a type-hit.

| | batch 1 (11) | batch 2 (24) | **total (35)** |
|---|---|---|---|
| Clean FP | 3/3 | 4/4 | **7/7** |
| Recall, certain/probable | 7/8 | 12/15 | **19/23** |
| Type-hit (of TPs) | 0/7 | 7/12 | **7/19** |
| hit@5s (of TPs) | 6/7 | 9/12 | 15/19 |
| Median \|Δt\| | 2.6 s | 3.0 s | — |
| Soft (uncertain-only) flagged | — | 3/5 | 3/5 |

**Findings:**
- **Recall holds (19/23), but mostly through the wrong type.** Verdicts are carried by generic T15/T12/T11/T6/T5 events near the right moment (hit@5s 15/19).
- **T10 (concealment/pocketing) never fires as T10 on any of the 7 T10-labelled clips** (e3_01/02/06/10/32/39/47); its only T10 event is on e3_43 (unlabelled).
- **T6 type-hit 1/11** (only e3_50). Same picture as external set 2.
- **Clean FP 7/7.** Every clean clip on new scenes gets something: T15, T12, T6 (a client or second teller at the window), T4, T3, T7. A new FP type: **T14 @30.0 on clean e3_51** (fisheye teller cam), which could be the new covered-from-start logic.
- **Real T14 wins on the final build:** e3_31 (guard tapes over CCTV) fires T14 @5.0 and @47.0, both within 0.5 s of the labels. Miss: e3_44 (intruder turns the camera to the ceiling), no event.
- **T13 on real CCTV: 2/2** (e3_36 phone at ear @1.7 s, e3_49 bank-kiosk operator @3.8 s).
- FNs: e3_05 (clerk asleep, ABS), e3_32 (fake auditor), e3_44 (camera turned away), e3_45 (masked robber, teller window).

## Synthetic T14 set (`robust/t14_synth/`, generator `robust/gen_t14.py`)
16 clips from clean training clips with a burned-in OSD: 10 positives (cover_black, hand_cover ~86%, spray_haze, defocus_tape, turned_away, ×2 each) and 6 negatives (pass_by ≤1 s, lights_dim, osd_only, ×2 each). labels.csv uses the external2 schema.
**Baseline, current `camera_blocked` + CameraRule (cb77b44/c5ef2b6 logic, `robust/t14_baseline.py`): recall 4/10, FP 0/6.** Misses: spray_haze ×2, defocus_tape ×2 (noise/texture normalisation), cover_black_x35 (onset after the 70% probe: the 5 fixed probes plus the "2 consecutive" rule can't fire on late covers), hand_cover_x8 (flicker). Sent to forte-detector, which is rewriting the probe (structure-loss vs reference, OSD mask, lights-off guard).
