# Detector — Phase 1 plan

Repo: detector git repo; work happens in worktree `WT=/home/datamata/Projects/datathon2026/detector/.claude/worktrees/phase1` (branch `worktree-phase1`). All paths below are relative to WT unless absolute. `ROOT=/home/datamata/Projects/datathon2026`.
Spec: `ROOT/docs/00_case_brief.md`, `ROOT/docs/01_findings.md` (taxonomy T1–T14, T99 + design — binding), `ROOT/docs/tech_options.md` (license list — binding).

## Global Constraints (apply to every task)

- **Licenses:** only Apache-2.0 / MIT / BSD weights and code. NEVER install or import `ultralytics`, YOLO-World, YOLOv7/v9, YOLO-NAS, VideoMAE, V-JEPA, Qwen2.5-VL-3B, DINOv3, RF-DETR XL/2XL. Record every model used in `LICENSES.md` (name, license, URL).
- **No cloud AI**, no network at analysis time once weights are cached. **No face recognition / identification**; track IDs are anonymous. **No audio.**
- **GPU:** another process (Qwen3-VL llama-server) will need ~4.5 GB of the 6 GB RTX 3060. Our CV models must stay ≤ ~1.2 GB VRAM peak, and must run on CPU too (`device` param, auto-fallback). Until `ROOT/docs/spike_vlm.md` exists, another session is benchmarking on the GPU: small models only, no long heavy GPU jobs.
- **Prod hardware (orchestrator update):** final inference on RTX 5080 16 GB (Blackwell sm_120) + 64 GB RAM; VLM ~8B (6–10 GB) + CV ≤ ~2 GB together. torch from the cu128 index (≥2.7), versions pinned. Model sizes configurable (laptop default small, prod larger); device-agnostic code.
- **Runtime:** total analysis ≤ ~180 s for a 145 s video incl. VLM later; perception budget ≤ 60 s.
- **No fixed pixel zones.** Any zone is derived per video (from tracks or open-vocab detection) and expressed in normalized coordinates. Must work on flipped / rotated / cropped input.
- **Python env:** `uv` project in WT, Python 3.12 (`uv python install 3.12`), venv at `WT/.venv` (git-ignored). torch CUDA wheels (cu12x). Package code in `forte_detector/`. Tests with `pytest` in `tests/` (fast, no GPU, no real videos — synthetic data/fixtures; real-video runs belong to the harness, not unit tests).
- **Data (read-only, never modify):** videos `/home/datamata/Downloads/Datathon26-20261017T071335Z-1-001/Datathon26/open/videos/*.mov`, labels `.../Datathon26/open/open_labels.xlsx`. Pre-extracted frames at `ROOT/work/frames/<VID>/f_KKKK.jpg` (2 fps, t=(KKKK-1)/2 s) exist for prototyping/eyeballing only — the analyzer itself must read the .mov.
- **Video ID normalization:** Cyrillic `Х` (U+0425) → Latin `X`; canonical id like `X-4`. X-4 ≡ X-47 (identical files), X-5 ≡ X-48.
- **Data path config:** `FORTE_DATA_DIR` env var, default `/home/datamata/Downloads/Datathon26-20261017T071335Z-1-001/Datathon26/open`.
- **Caches** under `cache/` (git-ignored); harness outputs under `runs/<timestamp>/` (git-ignored).
- **Commit often** with clear messages. Subagents must NOT dispatch subagents.

## Shared data model (`forte_detector/schema.py`, created in Task 1, used by all)

```python
@dataclass Event:  type: str  # "T1".."T14","T99"
                   timecode_sec: float; confidence: float  # 0..1
                   description: str  # RU, what is visible + why
                   evidence_ts: list[float]  # 1–3 timestamps (s) for evidence frames
                   source: str  # which rule/probe produced it
                   extra: dict
@dataclass AnalysisResult: video: str; verdict: str  # "violation"|"no_violation"
                   events: list[Event]; timeline: dict  # perception summary (JSON-able)
                   calibration: dict; runtime_sec: float; stage_times: dict
Analyzer: analyze(video_path: str, progress_cb: Callable[[float, str], None] | None = None, *, transform=None) -> AnalysisResult
```
Everything JSON-serializable via `to_dict()`. `transform` (frame→frame callable) is for robustness eval only; the service never passes it.

## Ground truth (Task 1 builds `forte_detector/data/labels.json` from the xlsx)

Hypothesized types per positive video (from 01_findings.md):
T1: X-15, X-24, X-26 · T2: X-4, X-47, X-38 · T3: X-20, X-22 · T4: X-28, X-11, X-13(secondary) · T5: X-32, X-33, X-30 · T6: X-30, X-18, X-33 · T7: X-34 · T8: X-44, X-13 · T9: X-17 · T10: X-1, X-2 · T11: X-40 · T12: X-46.
X-4 and X-47 are the same clip with labels 57 s and 68 s — treat both seconds as valid targets for both ids.

**Revisions (orchestrator, binding — see `ROOT/docs/01_findings.md` §"Revisions after teammate comparison" and `ROOT/docs/02_teammate_comparison.md`):** **Final hyp_types (forte-docs, supersedes the table above where they differ):** X-24 → [T1b] (stamps + journal); X-18 → [T2] (not T6); X-2 → [T15, T10] (human review: T15 primary); X-1 → [T10]; X-13 → [T8, T4]; X-30/X-33 → [T5, T6]. rules.json template placeholders: `{t}` = mm:ss, `{obs}` = short observed-fact phrase (most types: starts with ", " or empty; T10/T99 template "на {t} {obs}." → {obs} is the main sentence, never empty). Never edit citation text in rules.json; never emit a type not in it; citation fixes (T6 cites S2 п.8 + п.16 пп.1 ONLY — no п.75/76, no S2 п.21; verified citations at the bottom of 01_findings.md are the source of truth, no п.41 for T8, п.31 пп.4 payouts only, п.42 never a trigger); events carry a start–end interval and separate `fact_conf` (observed) vs `legal_conf` (rule link); exception clauses п.25, п.32, п.49–50; the hard-negative list from 02 must be in the eval (report FPs on those specifically); X-4/X-47 and X-5/X-48 share a CV fold in any tuning/cross-validation.

---

## Task 1: Eval harness + labels + stub analyzer

Files: `pyproject.toml`, `forte_detector/{__init__,schema,labels,video_io,config}.py`, `forte_detector/data/labels.json`, `forte_detector/analyzers/stub.py`, `eval/run_eval.py`, `eval/variants.py`, `tests/test_labels.py`, `tests/test_metrics.py`, `tests/test_variants.py`, `LICENSES.md`, `README.md`.

1. uv project (Python 3.12), deps: numpy, opencv-python-headless, av (PyAV), pillow, openpyxl, pytest. (torch etc. come in Task 2.)
2. `labels.py`: parse the xlsx (inspect its columns first), normalize ids, produce `labels.json`: `{id: {"file": <original filename>, "violation": bool, "label_sec": [floats] (or []), "hyp_types": [...], "duration_sec": float}}`. `hyp_types` from the table above. Duration via PyAV. Commit the JSON. Must contain 42 entries, 22 violations.
3. `video_io.py`: `iter_frames(path, fps, max_side=None, transform=None)` yielding `(t_sec, frame_bgr)` using PyAV, decoding sequentially and keeping only frames nearest each sample time (no per-frame seeking); `transform` is applied to each kept frame. Also `read_frame_at(path, t, transform=None)` for evidence frames.
4. `eval/variants.py`: named transforms `orig, hflip, rot90cw, rot+12, rot-12 (rotate + crop to the largest inscribed axis-aligned rectangle, no black borders), crop80 (centre 80%), crop_tl (top-left 80%), dark (gamma 1.8)`. Applied in-memory through the analyzer's `transform` kwarg.
5. `eval/run_eval.py` CLI: `--analyzer module:callable` (default stub), `--videos all|X-1,X-4`, `--variant orig|hflip|...|all|robust` (robust = orig,hflip,rot+12,crop80,rot90cw), `--out runs/<ts>/`. Skips X-47/X-48 duplicates only if `--dedupe` given (default: run all 42). Saves per-video `result.json`; writes `report.md` + `summary.json`:
   - confusion matrix, verdict accuracy, recall, FP rate (on clean), precision;
   - per positive video: label second(s), best-event |Δt| (min over events × label seconds), hit@3s / hit@5s, mean/median |Δt|;
   - per video: predicted types vs `hyp_types` (type-hit = any predicted type ∈ hyp_types), type-hit rate on TP videos;
   - runtime per video (wall) + stage_times, runtime per video-second, max runtime;
   - with multiple variants: one table row per variant (acc / recall / FP rate / mean |Δt| / type-hit) for robustness comparison.
   Metrics in a pure function `compute_metrics(labels, results) -> dict` covered by `tests/test_metrics.py`. Video runs are sequential (single GPU).
6. Stub analyzer (`analyzers/stub.py`): decodes the video at 1 fps via `iter_frames` (exercise the I/O path), returns `violation` with one T99 event at mid-video. Run the harness on all 42 videos with the stub (orig) and on 3 videos with `--variant robust`; commit; put the numbers in the task report.

## Task 2: Perception stage (persons + tracking + timeline)

Files: `forte_detector/perception/{detector,tracker,timeline,zones}.py`, `tests/test_timeline.py`, `scripts/perceive.py`, LICENSES.md update.

1. Add torch (CUDA wheel, works on CPU too), transformers, supervision (MIT, ByteTrack). Person detector: an Apache-licensed DETR-family model in HF transformers — RT-DETRv2-S (`PekingU/rtdetr_v2_r18vd`) or D-FINE-S; pick by speed/quality on our frames, record choice. fp16 on GPU, ≤1.2 GB VRAM, batched. Also keep COCO detections handbag, backpack, suitcase, cell phone, cup, bottle, laptop, book, keyboard per frame (probe evidence later).
2. Sample at 4 fps (configurable). Track persons with ByteTrack (supervision). Merge fragmented tracks (gap ≤ 2 s, centre proximity / IoU) — overhead views fragment.
3. Derive roles and zones **from the data**, normalized coords:
   - **cashier**: track(s) with longest presence near one stationary location (the seat); seat zone = robust median box of the cashier while stationary.
   - **entry/exit**: tracks born/dying away from the seat; door zone = cluster of birth/death points (may be null). Detect leaving the field of view.
   - series: `n_persons(t)`, `cashier_at_seat(t)`, `cashier_present(t)`.
4. Timeline (JSON-able) with checkpoints: `cashier_leaves_seat`, `cashier_returns_seat`, `cashier_exits_room`, `room_empty_start/end`, `person_enters`, `person_exits`, `visitor_arrives_at_desk`, `n_persons_change`, each with t and track ids; plus per-sample series (t, n_persons, cashier_at_seat, person boxes, object detections). Cache to `cache/perception/<vid>__<variant>__<cfghash>.json`.
5. `scripts/perceive.py X-15 [--variant hflip]` dumps the timeline and a debug contact sheet (frames with boxes/ids/zones, timestamps) to `cache/debug/`. Validate by eye against `ROOT/docs/videos/batch_*.md` on at least: X-15, X-24, X-26 (cashier leaves), X-17 (collector), X-30, X-18 (extra person), X-4 (client change), X-43 (clean), plus one hflip/rot variant. Report runtime per video and VRAM peak.
6. Unit tests: checkpoint logic and track merging on synthetic tracks.

## Task 3: State probes at checkpoints

Files: `forte_detector/probes/{base,cheap,openvocab,vlm}.py`, `forte_detector/data/probe_cases.json`, `tests/test_probes.py`, `scripts/probe_eval.py`.

Interface: `probe(frames: list[(t, img)], cue: str, context: dict) -> ProbeResult(cue, value: bool|None, conf, t, evidence_ts, raw)`. Cues: `cash_on_desk`, `bag_on_desk`, `safe_open`, `key_in_safe`, `screen_on_unlocked`, `door_open`, `cup_or_food`, `handbag_or_coat_on_desk`, `phone_visible`, `camera_blocked`, `counting_machine_used`.

1. Cheap probes (CPU/small GPU): camera_blocked (brightness/variance/edge drop); COCO objects from Task 2 (cup, phone, handbag, laptop); open-vocab zone discovery (OWLv2 or Grounding-DINO-tiny, Apache) on 2–3 early frames for "safe", "door", "laptop", "money counting machine", "desk" → `calibration` (null if not found); appearance-change state for safe/door/screen region vs reference frame.
**VLM design (orchestrator, binding, from `ROOT/docs/spike_vlm.md`):** CV proposes moments; the VLM answers ONE narrow yes/no cue per call on 1–3 keyframes or ROI crops (proven: laptop locked/unlocked 8/8, cash on desk with nobody 7/7, people count 8/8; ~0.6–1.8 s/call). Never use the VLM for holistic segment classification or multi-field JSON. Door open/closed MUST be CV (VLM always says "open"). T99 catch-all = narrow cues only.
2. VLM probe (`vlm.py`): client for local llama-server OpenAI-compatible endpoint (`FORTE_VLM_URL`, default `http://127.0.0.1:8090/v1/chat/completions`), JSON-schema-constrained answers per cue on 1–4 frames/crops. Use `ROOT/docs/spike_vlm.md` for proven prompts/resolution/latency; reference client `ROOT/spike/vlm.py`. If the server is down → `value=None` (graceful degradation).
3. `scripts/probe_eval.py`: per-cue accuracy + latency on hand-picked cases in `probe_cases.json` (from `ROOT/docs/videos/batch_*.md`, ≥3 pos + ≥3 neg per main cue).

## Task 4: Rule engine + taxonomy mapping

Files: `forte_detector/rules/{engine,taxonomy}.py`, `forte_detector/data/taxonomy.json`, `forte_detector/analyzers/full.py`, `tests/test_rules.py`.

00. **Canonical rules file (orchestrator, binding):** `taxonomy/rules.json` at the detector repo root is the single type/citation file for the whole system (service loads it via `RULES_PATH`). Content = `ROOT/docs/deliverables/rules_spec.json` adopted verbatim when it exists (adds T1b, maybe T15 "обслуживание без предъявления документа"); until then build from 01_findings verified citations. Type ids are strings loaded from rules.json — remove the closed `EVENT_TYPES` assumption. Per-event analyzer output must include: `type_id, title_ru, start_sec, end_sec, timecode_sec, fact_conf, legal_conf, description_ru, evidence_ts` (title_ru/citation from rules.json, never generated).
0. Fold in the Revisions above: extend `Event` with `start_sec`, `end_sec`, `fact_conf`, `legal_conf` (keep `confidence` = combined, backward-compatible), update labels.json hyp_types for X-18, add the hard-negative list to the eval report.
1. `taxonomy.json` (= rules.json): T1–T14 + T99 with `title_ru`, `cue`, `rule_ref` (citations only from `ROOT/docs/regulations.md` / 01_findings), RU description template.
2. Deterministic rules over timeline + probes at checkpoints, each with confidence (T1 room empty ∧ cash/bag on desk; T3 safe open ∧ cashier away; T4 screen on at exit; T5 door open > N s after exit; T6 extra persons; T7 client leaves during count; T9 short collector visit; T11/T12/T13 object on desk; T14 camera blocked; T2/T8/T10 via VLM clip questions best effort; T99 VLM catch-all over coarse windows). Timecode = moment the wrong state becomes final; evidence_ts = checkpoint + before frame.
3. Merge same-type events < 5 s apart, cap ~3/video; verdict = any event conf ≥ threshold (config).
4. Tune on the 42 clips: 100% recall first, then minimize FPs. Report orig + robust variants.

## Task 5: Service adapter

Files: `forte_detector/api.py`, `tests/test_api.py`.

**Settled (orchestrator):** implement `ROOT/docs/status_service.md` §"Analyzer interface": a class (e.g. `forte_detector.service_adapter:ForteAnalyzer`, `name="forte-detector"`) whose `__init__` loads models once; `analyze(video_path, progress_cb, deadline, partial) -> AnalysisResult` (service's pydantic types from `app.analyzers.base`/`app` in `ROOT/service/`; import lazily so detector tests run without the service); `partial.add(Violation)` as each event is confirmed; stop on `deadline` (time.monotonic) or `partial.cancelled`; `torch.inference_mode()` inside analyze. `Violation.type` = T-id; also pass extra fields `start_sec, end_sec, fact_conf, legal_conf, obs` (RU observed-fact phrase for {obs}) and `description` (fallback, from template); evidence_frames 1–3 JPEG base64. progress_cb text in Russian.
Previously: `analyze(video_path, progress_cb)` matching `ROOT/docs/status_service.md` (if absent: case-brief job format — `verdict`, `violations:[{type, timecode_sec, description, evidence_frames:[{timestamp_sec, image_base64}]}]` + rule refs). Evidence JPEGs read from the .mov at evidence_ts with boxes drawn. Models loaded once (warm singleton). progress_cb(0..1, stage).
