# Detector status (forte-detector)

Updated: 2026-10-17 (phase 1 start)

## Merge state
- ea2e4ca reencode: 19/22, FP 4/16 excl. team, **0 TP→FN flips** (all 19 positives fire on both encodes), type-hit 95%, hit@5s 82% → passes refined gate.
- ea2e4ca orig (PC, fresh): 19/22, FP 2/16 excl. team-T15 (X-5/X-48 T15, ID shown after the window), team T15 2/4, hard-neg 4/19, hit@5s 82%, runtime max 74 s. X-19 T15 root cause: one unsure ID "yes" (0.63) during the banknote hand-over with notes in hand (no cash-on-desk veto). Next candidate 2e873d1: ID window to arrival+4 s, single unsure late yes ignored, VLM plan 70 / cap 90 (+20 follow-up reserve), T7 strong ≥ weak.
- **Release candidate 9f8d3b9** (branch `release-ba2f4df`, tree = ba2f4df): orig 19/22, FP 0/16 excl. team-T15, hit@5s 82%; reencode 18/22, TP→FN 1, FP 4/16 excl.; **newcam 15/22**, FP 9/20. Smoke identical. Sent for ff. Known: T15 veto not frame-aligned (fix next candidate).
- **Release candidate 378b74f** (branch `release-cb4da16` = cb4da16 + merge of main; tree identical to cb4da16). Gate (recall-first, refined): orig 18/22, FP 0/20, hard-neg 0/19, hit@5s 77%; reencode TP→FN flips 2/42, FP 2/16 excl. team-T15; newcam 12/22, FP 7/20. Suite 339 pass; PC smoke on X-17/26/32/43 identical to cb4da16. Sent to orchestrator for ff + rollout.
- detector `main` = **17ddd4e** (= c5ef2b6 + adapter evidence boxes 6a: raw frames + person boxes + applied_rotation_deg; detection identical to c5ef2b6). Previously c5ef2b6 (all 5 milestones + final-review fixes: long-upload safety, deadline-clamped VLM, GPU retry, offline by default, truncation → partial; 303 tests pass). forte-deploy deploys this commit. Further work continues on `worktree-phase1`; orchestrator ffs on request.
- VLM moving to RTX 5080 via SSH forward to the same `http://127.0.0.1:8090` (forte-deploy) — VLM-dependent eval numbers resume when it's up.

- VLM via tunnel to RTX 5080 (forte-deploy measurement, spike prompts with `reason`): p50 0.81 s, p95 1.10 s per single-image call; detector's GBNF yes/no grammar has no `reason` field, so expect ~0.4–0.5 s/call → ≤60 calls ≈ 25–35 s/video.

## Where
- Repo: `detector/` (git). Active work on branch `worktree-phase1` in worktree `detector/.claude/worktrees/phase1/` (background-session isolation). `main` holds only the init commit until phase 1 lands.
- Plan: `detector/.claude/worktrees/phase1/docs/plan_phase1.md`

## Phase 1 milestones
| # | Milestone | State |
|---|---|---|
| 1 | Eval harness + labels.json + stub analyzer + robustness variants (hflip, rot±12, rot90, crop80, crop_tl, dark) | **done** (f7c3b72, reviewed) |
| 2 | Perception: RT-DETRv2 persons (Apache) + ByteTrack (supervision, MIT) → checkpoint timeline, data-derived seat/door zones, hysteresis, auto-orientation (safe default 0°), behind-glass exit logic, `effective_transform` | **done** (…35ced20, reviewed, 5 fix rounds) |
| 3 | State probes: CV door/camera-blocked, OWLv2 calibration, VLM single-cue client (GBNF), desk-crop prompts, deadline-aware | **done** (…95e5aef, reviewed, 4 fix rounds); live VLM re-validation pending server |
| 4 | Rule engine + full analyzer (localization, obs, degraded flag, on_timeline) | fix round 1 in re-review (6638596); VLM numbers pending server |
| 5 | Service adapter `ForteAnalyzer` | **done** (0419e68..425e85b, reviewed); VLM-up smoke on X-26 pending server |
| 3 | State probes (cheap CV + open-vocab zones + VLM client to llama-server once spike_vlm.md lands) | queued |
| 4 | Rule engine T1–T14/T99 + tuning (recall first) | queued |
| 5 | `analyze(video_path, progress_cb)` adapter for service/ | queued |

## Service integration (milestone 5, commit 0419e68, in review)
- `ANALYZER=forte_detector.service_adapter:ForteAnalyzer` (implements docs/status_service.md §"Analyzer interface": warm models in `__init__`, `analyze(video_path, progress_cb, deadline, partial)`, `partial.add` per decided event, deadline/cancel aware, `torch.inference_mode()`).
- Env: `FORTE_VLM_URL` (default `http://127.0.0.1:8090/v1/chat/completions`), `FORTE_DET_MODEL`, `FORTE_DEVICE`, `FORTE_VLM_TIMEOUT`. Caches off in prod (`FORTE_EVAL_CACHE` unset).
- Service dependency: `uv add --editable ../detector` + opencv-python override (as in detector pyproject). Detector `main` must first be fast-forwarded: `git -C /home/datamata/Projects/datathon2026/detector merge --ff-only worktree-phase1` (needs user/allowed session).
- Smoke (VLM down → CV-only): warm-up ~6 s, ~3–4 s per 20 s clip; X-43 clean ✓; X-32/X-30 T5 ✓ with mid-run partial.add and 3 upright evidence frames; service models accept all extra fields.

## Interface (planned, final in milestone 5)
`forte_detector.api.analyze(video_path, progress_cb=None) -> dict` with the case-brief job format: `verdict`, `violations: [{type, timecode_sec, description, evidence_frames: [{timestamp_sec, image_base64}], rule_ref...}]`. Will adapt to `docs/status_service.md` once it exists.

## Eval numbers
Harness: `cd detector/.claude/worktrees/phase1 && uv run python -m eval.run_eval --analyzer <module:callable> --videos all --variant orig|robust|all`.
Labels: 42 clips / 22 violations; xlsx seconds are Russian text ("1минута 8 секунда" = 68). X-4 and X-47 both get targets [57, 68].
- **perception_only (naive verdict, 2026-10-17 ~15:10)**: recall **18/22**, FP **14/20**, median |Δt| 6.7 s (mean 12.0 s). Perception 1–15 s/video on GPU (X-14, 142 s → 15 s); RT-DETRv2-S fp16 16 ms/frame, VRAM peak 242 MB. Robust (5 videos × 5 variants): 80% acc on all variants except rot90cw (40%). Naive verdict = room_empty or ≥3 persons → not meaningful beyond plumbing; real verdict comes from Task 4 rules + probes.
  Known perception issues: people seen through the glass door keep being tracked → cashier exit / room-empty fire 2–5 s late (never on X-15); glass reflections make short spurious tracks (noisy n_persons/T6); seat estimate wrong on X-15, oscillates on X-30; CPU fallback ~2.5 s per video-second (over budget, GPU required).
- Stub (always violation, T99 at mid): acc 52%, recall 100%, FP rate 100%, hit@5s 64% — baseline only. 42 videos in 12 s.

- **122a728 orig (interim, before 896fe91 ROI fix; 42 clips, live VLM)**: recall 13/22, FP 1/20 (X-19 T15), hard-neg 1/19, type-hit 77%, hit@5s 55%, mean |Δt| 1.98 s, 0 degraded; CV A 6/11 FP 1/10, B 7/11 FP 0/10. Misses X-1, X-2, X-4/X-47 (T2), X-22, X-32/X-33 (T5), X-34, X-44. Bisect e038ce4..c5ef2b6 running.
- **c5ef2b6 orig (forte-robust run, deduped 40 clips)**: recall **11/21**, FP **0/19**, hard-neg FP 0/18, type-hit 82%, hit@5s 48%, mean |Δt| 1.89 s, runtime p50 13.8 s / max 29.7 s, 0 degraded. ⚠️ **Regression** vs e038ce4 (new misses X-26, X-32, X-33, X-2) — root-cause investigation in progress.
- **ba2f4df orig (PC, --fresh, 0 degraded) — next candidate**: recall **19/22**, FP 3/20 (all team-T15 clips X-35/37/39 → **0/16 excl. team-T15**), team T15 **3/4** (X-19 missed), hard-neg 2/19, type-hit 84% (primary 79%), hit@3s 64%, **hit@5s 82%**, mean |Δt| 2.16 s; CV A 9/11 FP 1/10, B 9/11 FP 2/10; runtime p50 24.7 s / max 63.9 s. FN: X-1, X-22, X-44. X-1/X-3 correctly get no T15. Reencode: recall 18/22, FP 7/20 (4/16 excl. team-T15: X-5/X-48 T15, X-14 T2, X-16 T11), team T15 3/4, hard-neg 6/19, type-hit 94%, hit@5s 82%; TP→FN flips 1 (X-13). **Passes the refined gate.** Newcam running; release review in progress.
- **cb4da16 FINAL-GATE candidate, orig (PC runner, --fresh, 0 degraded)**: recall **18/22**, FP **0/20** (0/16 excl. X-19/35/37/39), hard-neg 0/19, type-hit 83%, hit@3s 59%, hit@5s 77%, mean |Δt| 2.2 s; CV A 9/11 FP 0/10, B 9/11 FP 0/10; runtime p50 27.7 s / max 105 s (3 workers sharing VLM). FN: X-1, X-2, X-22, X-44. Team T15 0/4 — ID probe says "yes" on banknote hand-overs; fix in 541f176 (ID "yes" with cash on desk at the same frames counts as banknotes). Reencode + newcam running.
- **4e4861f orig gate eval (clean export, 5080 VLM, 0 degraded) — NEW BEST**: recall **18/22**, FP **6/20**, hard-neg FP 5/19, type-hit 83%, hit@3s 59%, **hit@5s 77%**, mean |Δt| 2.15 s, runtime p50 11.9 s / max 26.9 s; CV A 9/11 FP 4/10, B 9/11 FP 0/10. FN: X-1, X-22, X-33, X-44. FP: X-5/X-48/X-8 (weak T13 phone), X-19, X-37 (weak T15), X-35 (T10+T15). Reencode (interim, same commit): recall 19/22, FP 8/20, hard-neg 7/19, type-hit 89%, hit@5s 82%; flips vs orig 3 (X-14 TN→FP T2, X-16 TN→FP T11+T15 collector visit, X-33 FN→TP); all 18 orig positives also fire on reencode. Follow-ups in progress: T13 weak path gated by COCO phone in a person box (fa45924), procedure-aware T15 (no T15 on collector visits), probe robustness round; final gate pending.
- **Eval runner**: `work/pc_eval.sh <sha> --videos all --workers 3 [--robust <set>] [--fresh]` (forte-deploy; PC, 4-slot VLM, results in work/pc_runs/).
- **Eval policy (orchestrator)**: all numbers from clean `git archive` exports of a commit, 5080 VLM via tunnel, orig + reencode. Gate for ff: recall ≥15/22, FP ≤6/20, reencode verdict flips ≤2/42. Retune adds: T2 on persistent multi-frame cash evidence; X-11 T4 newcam miss under root-cause. **Reproducible baseline = clean-export e038ce4, 5080 VLM: recall 14/22, FP 0/20, hard-neg 0/19, type-hit 86%, hit@5s 50%, mean |Δt| 2.98 s; CV A 7/11, B 6/11, FP 0.** Bisect: X-26 T1 lost from abf1c84 (desk-crop cash prompt cropped the wrong table) — already fixed by the desk-ROI rework 65753b3/896fe91 (HEAD detects X-26). X-32 T5 lost from 9c37025 (door probe abstains, door ROI a 0.03-high strip) — fix in progress. Recall-first retune committed (a0a9275), gate eval pending.
- **⚠️ NON-REPRODUCIBLE — e038ce4 run from the dirty shared worktree + laptop VLM**: recall **15/22**, FP **0/20**, hard-negative FPs 0/19, type-hit 87% (primary 87%), hit@5s 55%, mean |Δt| 2.98 s. **2-fold CV by video pair** (threshold tuned on one fold, scored on the other): fold A 7/11 recall, FP 0/10; fold B 7/11, FP 0/10 → pooled 14/22, FP 0/20.
  Remaining misses: X-1 (hand-to-body cue False), X-22 (key-in-lock False), X-24 (stamps cue False on empty-room frame), X-32 (door CV says closed), X-34 (exit detected late → machine probe after the count), X-38 (cash_on_desk False ×3), X-44 (no T8 cue).
- **Perception fix round (be4d99d, 3e2fe4a, in re-review)**: re-encode stability orig vs crf18 re-encode: verdict agreement **40/42 = 95.2%** (was 83%), checkpoint agreement ±1 s 79.8% (was 70%); remaining diffs X-14, X-38 (T6 from glass mirror images). Orientation: all 42 orig stay 0°; landscape1080 19/42 auto-rotated correctly, 23 stay 0° (below the 1.5× margin), **none rotated wrongly**. Cache: full-file sha1, off unless FORTE_EVAL_CACHE=1. X-15 exit now detected 16.0 s (label 15). Still late exits on X-24 (13 vs ~9–11 s), X-17 (12.8 vs 8–11 s).
- **full analyzer, first real-runner run (c3530a5, 42 orig, VLM live)**: recall **14/22**, FP **3/20** (X-5/X-48 T13 phone, X-35 T10). Misses: X-1, X-18, X-22, X-24, X-32, X-34, X-38, X-44. 15–55 s/video, 13–40 VLM calls. Tuning in progress.
  - **T99**: not emitted as of c3530a5. Policy (orchestrator): no free/holistic VLM pass; T99 only from concrete CV anomalies (e.g. non-cashier track in desk/safe zone while cashier away), low fact_conf, must add 0 FPs on clean set. Being added in Task 4.
  - Known cause behind several T1/T2 misses: `cash_on_desk` VLM answer failed JSON parsing on 20/22 calls (probe-layer bug, fix in progress). Per-miss/FP probable causes (c3530a5, from result.json):
    - X-1 (T10): cash_in_hand_to_body False at 15.6/19.3/23.2 s; split happens 21–22 s, single window, subtle cue → accepted as hard.
    - X-18 (T2): person_count @17.5 s = 2 (second man enters 16–18.5 s → probe too early); no cash/machine cue asked.
    - X-22 (T3): departure confirmed, key_in_safe False (conf 0.995) — key too small for VLM → hard.
    - X-24 (T1b): departure confirmed, stamps_or_journal False, cash_on_desk None (bad_json bug).
    - X-32 (T5): door CV said closed at 4.0/7.8/20.8 s → CV door miss (probe fix in progress).
    - X-34 (T7): client present from t0 (no person_enters) and rule required an entry → fixing (trigger on exit only).
    - X-38 (T2): client change detected @30.8 s, cash_on_desk False @31.8 s (single look, no machine cue).
    - X-44 (T8): no T8 rule / no detector-use cue → accepted miss for now.
    - FP X-5/X-48 (T13): phone_visible True at 28.6 and 50.1 s (2 of 3 probes) → now requires 3/3 + ROI.
    - FP X-35 (T10): single hand cue 0.82 crossed threshold → T10 now needs corroboration.
    Fixes in progress: second looks for desk/cash cues, cash+machine cue at client change, later/repeated person counts for parallel clients, T7 on any visitor exit, stricter T13/T10.
- **Desk ROI for new angles (896fe91, reviewed)**: union laptop+plausible counting machine → single padded anchor → OWL desk only near seat → full frame; ROI extends toward cashier seat; in-machine notes excluded; prompts de-overfitted. Live desk cues 46/46 (cash 16/16, stamps 8/8, bag 7/7, handbag 8/8, cup 7/7), clean cases negative.
- **Probes, live on 5080 VLM after desk-ROI fix (65753b3, 122a728)**: probe_eval 112/127 = 88%, 0 undecided. 100%: door, camera_blocked, person_count (exact 14/14), cup, bag, screen, stamps/journal (12/12 incl. X-24). cash 16/16 tuned set (incl. X-38), 0.88–0.93 cash/handbag/safe_open/counting_machine on full set. Weak: id_document_shown 0.71, phone_visible 0.67, key_in_safe 0.43, cash_in_hand_to_body 0.33 → low weight / gated in rules. Latency via tunnel p50 0.61 s, p95 1.14 s.
- **Probes after fix round (…320afa6)**, live VLM eval *before* desk-crop prompts: door / camera_blocked / bag / screen 100%; person_count exact 9/9, empty room 4/4; safe_open 90%; cash 73%; stamps 50%; key_in_safe 43% (unreliable). VLM median 1.9 s/call. Desk cues (cash/bag/stamps/handbag) now run on a calibrated desk crop upscaled to 672 px — objects visible in dumps; accuracy re-check pending VLM server.
- **Probes (Task 3, b9e216d..e77f061, first version)**: CV door_open 13/13, camera_blocked 7/7 (~0.1 s each); OWLv2 calibration ~2 s GPU — laptop / counting machine / desk found on 13/13 videos, safe is weak (wrong on X-8). VLM cues **unmeasured** (server down).

## Questions for orchestrator
- **BLOCKER**: please start the 4B llama-server (`spike/run_server.sh`, :8090) and leave it running — the detector session's start attempt was denied by the permission system. Without it, VLM cues return None and the analyzer is CV-only.
