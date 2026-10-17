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
