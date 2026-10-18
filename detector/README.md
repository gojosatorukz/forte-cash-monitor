# forte-detector

Local cash-room video violation detector (ForteBank Datathon 2026): `.mov` in, verdict plus
typed, timecoded events out. Plan: `docs/plan_phase1.md`.

## Setup

```bash
uv python install 3.12
uv sync
```

Data is read from `FORTE_DATA_DIR` (default
`/home/datamata/Downloads/Datathon26-20261017T071335Z-1-001/Datathon26/open`, containing
`videos/*.mov` and `open_labels.xlsx`). Never modified.

## Layout

- `forte_detector/schema.py` — `Event`, `AnalysisResult` (JSON via `to_dict()`), analyzer signature
  `analyze(video_path, progress_cb=None, *, transform=None) -> AnalysisResult`.
- `forte_detector/labels.py` — builds `forte_detector/data/labels.json` from the xlsx
  (`uv run python -m forte_detector.labels`). Ids normalized `Х-15` (Cyrillic) → `X-15`;
  `file` keeps the original filename. X-4 ≡ X-47 share label seconds [57, 68].
- `forte_detector/video_io.py` — `iter_frames(path, fps, max_side, transform)` (sequential PyAV decode,
  nearest frame per sample time) and `read_frame_at(path, t, transform)`.
- `forte_detector/analyzers/stub.py` — baseline: 1 fps decode, one T99 event at mid-video.
- `eval/variants.py` — robustness transforms: orig, hflip, rot90cw, rot+12, rot-12 (CCW positive,
  cropped to the largest inscribed rectangle), crop80, crop_tl, dark (gamma 1.8).
- `eval/run_eval.py` — harness and `compute_metrics`.

## Evaluate

```bash
uv run python -m eval.run_eval                                  # stub, all 42 videos, orig
uv run python -m eval.run_eval --analyzer forte_detector.analyzers.stub:analyze \
    --videos X-15,X-17,X-43 --variant robust --out runs/robust/
```

Video sets: `--videos all` (42), `--videos hard` (hard-negative clean clips), `--videos regress`
(fast regression set: X-26, X-32, X-33, X-2 + anchors X-15, X-17, X-30, X-40), or a comma list.
Gate for every ff (orchestrator): the 42-orig eval of a clean `git archive` export of the candidate
with the prod VLM must reach recall >= best-so-far (15/22) with FP <= 6/20; report hard-negative FP,
2-fold CV and the reencode flips too.

Options: `--variant orig|hflip|rot90cw|rot+12|rot-12|crop80|crop_tl|dark|all|robust` (or a comma list;
robust = orig,hflip,rot+12,crop80,rot90cw), `--dedupe` skips X-47/X-48. Outputs
`runs/<ts>/<variant>/<id>/result.json`, `summary.json`, `report.md`.

## Tests

```bash
uv run pytest -q
```

## Service adapter (FastAPI service in `ROOT/service`)

`ANALYZER=forte_detector.service_adapter:ForteAnalyzer` (`name = "forte-detector"`).

- `ForteAnalyzer()` loads the models once in the service's worker thread: RT-DETR person detector,
  OWLv2 calibration model, and a VLM availability check. Every `analyze()` call reuses those
  module-level singletons.
- `analyze(video_path, progress_cb, deadline, partial)` runs `forte_detector.analyzers.full.analyze`
  inside `torch.inference_mode()` and passes `deadline` through, so VLM probes left after the
  deadline are skipped. Each accepted event goes to `partial.add(Violation(...))` as soon as it is
  decided. When `partial.cancelled` is set, the run stops at the next progress call. Stage texts
  are in Russian.
- Each `Violation` has these fields:
  - `type`: the T-id.
  - `timecode_sec`, `start_sec`, `end_sec`.
  - `confidence`: the combined score.
  - `fact_conf`.
  - `legal_conf`: an **ordinal** encoding of the rule-link strength: high 0.9 / medium 0.7 /
    low 0.4. It is not a calibrated probability. The original label is also sent as the extra
    field `legal_strength`, but the service's `Violation` model ignores unknown fields, so it
    currently does not reach the public JSON.
  - `title_ru`.
  - `obs`: a bare clause.
  - `description`: the detector's rendered text, used as a fallback because the service renders
    its own template from `type` + `obs`.
  - `evidence_frames`: 1–3 JPEGs (max side 960). The first is exactly at `timecode_sec`. Frames
    are read from the .mov with perception's orientation transform, and person boxes are drawn
    as thin lines with no ids.
- A missing or undecodable video raises `VideoError` (the service's class when importable), and
  the job is reported as failed. If a later stage fails after events were emitted, the analyzer
  logs it and returns those events.
- Degraded runs: the service's `AnalysisResult` has no notes field, so the adapter puts the
  following into `meta`, which the worker stores as job `meta.analyzer`:
  - `degraded`
  - `probes_status`
  - `notes_ru`, which is either «анализ выполнен без VLM-проверок, результат может быть неполным»
    or the deadline variant.

  It also logs a warning. Evidence frames are cached per stable event key, not `id()`, because the
  accumulator streams copies.
- Evidence orientation: `full.analyze(on_timeline=...)` fires right after perception, so every
  evidence frame, including those pushed to `partial`, is read through
  `perception.effective_transform(timeline, None)`.
- Concurrency caveat:
  - The models are process-wide singletons (perception detector, OWLv2, VLM client) and are not
    locked.
  - The service runs one job at a time. However, when a job overruns, the worker abandons its
    analyzer thread (an orphan) and starts the next job after a short join.
  - The orphan stops at its next progress / event callback, since `partial.cancelled` is set.
    Until then it can still be inside a detector forward pass or a VLM request (≤ `FORTE_VLM_TIMEOUT`).
  - During that window two threads share the same model objects and GPU memory. Expect slowdowns,
    VRAM pressure, and the VLM client's per-batch down-flag being reset by the other job.
  - The PyTorch modules are inference-only, so this is not a correctness problem for the weights.
    Do not raise the service's worker count above 1.
- Field check without running the models: `uv run --with pydantic python scripts/service_smoke.py --fields`.
- End-to-end check against the real service package (read-only):
  `flock ROOT/work/gpu.lock uv run --with pydantic python scripts/service_smoke.py X-26 X-43`
  (`DUMP_DIR=/tmp/ev` writes the evidence JPEGs).

Environment:

| Variable | Default | Meaning |
|---|---|---|
| `FORTE_VLM_URL` | `http://127.0.0.1:8090/v1/chat/completions` | llama-server (OpenAI-compatible) VLM endpoint |
| `FORTE_VLM_TIMEOUT` | `20` | per-request VLM timeout, s (capped so a request ends 1 s before the deadline) |
| `FORTE_VLM_FAIL_BUDGET_S` | `40` | strict total time failed VLM requests may take per video |
| `FORTE_DET_MODEL` | `PekingU/rtdetr_v2_r18vd` | person detector checkpoint (prod may use `PekingU/rtdetr_v2_r50vd`) |
| `FORTE_DEVICE` | auto (cuda if available) | person detector device |
| `FORTE_OV_MODEL` / `FORTE_OV_DEVICE` | `google/owlv2-base-patch16-ensemble` / `auto` | calibration model; device `cpu`, `cuda` or `auto` (cuda if ≥1.5 GB free at warm-up; a later CUDA OOM falls back to CPU) |
| `FORTE_PROBE_BUDGET_S`, `FORTE_VLM_MAX_SIDE`, `FORTE_VERDICT_THR` | 150, 896, 0.25 | probe budget, VLM image size, verdict threshold (recall-first: weak evidence emits low-fact_conf events above it) |
| `FORTE_EVAL_CACHE`, `FORTE_CACHE_DIR` | off, `cache/` | eval-only caches. **Leave them unset in production.** |
| `FORTE_ALLOW_DOWNLOAD` | unset (offline) | `import forte_detector` sets `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1` (unless already set) and loads weights with `local_files_only`; `1` allows downloads (only `scripts/fetch_models.py` needs it) |

### Deploy

1. Weights (once, with network): `uv run python scripts/fetch_models.py` (add
   `--also PekingU/rtdetr_v2_r50vd` for the prod detector, or set `FORTE_DET_MODEL` first). It
   downloads into the HF cache and then checks that an offline load works. At analysis time
   nothing is downloaded: a missing checkpoint fails fast instead of calling huggingface.co.
2. Start order: start llama-server (the VLM) first and let it load, then the service. The OWLv2
   device is chosen at warm-up from the free VRAM (`FORTE_OV_DEVICE=auto`), so a service started
   before the VLM would put OWLv2 on the GPU and leave the VLM short. If the order cannot be
   guaranteed, set `FORTE_OV_DEVICE=cpu`. The person detector returns to CUDA at the next video
   after a CUDA OOM fallback.
3. Long uploads: perception samples at `min(4, 2400 / duration)` fps and stops at the deadline
   (minus 3 s) or when the job is cancelled; the analysis then decides on the samples it has.

To use the detector from the service, run this in the service repo:
`uv add --editable ../detector`. It adds `forte-detector` with
`[tool.uv.sources] forte-detector = { path = "../detector", editable = true }`. The service also
needs `[tool.uv] override-dependencies = ["opencv-python; sys_platform == 'never'"]`, because uv
does not inherit the detector's override, and without it supervision pulls in GUI opencv next to
the headless build.
