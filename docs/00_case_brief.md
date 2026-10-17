# ForteBank Datathon 26 — Case brief

Source: `~/Downloads/Datathon26-20261017T071335Z-1-001/Datathon26/Кейс_ForteBank.docx`
Data: `.../Datathon26/open/videos/*.mov` + `open/open_labels.xlsx`
Workspace: `work/frames/<VID>/f_KKKK.jpg` (2 fps, t=(KKKK-1)/2 s), `work/sheets/<VID>_NN.jpg` (1 fps, 16 s per sheet)

## Task
Local AI service (web UI + REST API) that takes a cash-room video (.mov) and returns:
- verdict: `violation` / `no_violation`
- per violation: type, timecode_sec, description (what is visible + why it may be a violation), 1–3 evidence JPEG frames, rule + normative source (doc name, point).

API (jury tests automatically, ≤5 min per video, else counted unprocessed):
- `POST /api/v1/analyze` (multipart field `file`, header `X-API-Key`) → `job_id`
- `GET /api/v1/jobs/{job_id}` → `{job_id, status: processing|done|failed, verdict, violations:[{type, timecode_sec, description, evidence_frames:[{timestamp_sec, image_base64}]}]}`

## Scoring (100)
Detection 25 (per-video verdict) · Localization 15 (timecode + frame) · Classification 15 (type) · Rule grounding 15 (no invented refs) · Robustness 10 (unseen videos, **different camera angle in final**) · Product 10 · Tech 10 (incl. locality).

## Hard constraints
- Fully local models; no cloud AI APIs at all (disqualification). Offline after weights download.
- Open-source, commercial-use licenses only (watch Ultralytics AGPL).
- No face recognition/identification. No audio.
- Don't claim violations that need invisible data (ledger, reconciliation).
- Public URL required. Deadline 12:00.
- FPs minimal, but **misses unacceptable** → bias toward recall.
- Unknown violation types may appear in test; no closed list.
- Only RK normative acts (NBRK, adilet.zan.kz); other banks' open docs OK; refs without exact point OK.

## "Команда самостоятельно исследует ... нормативные источники" — what it means
Organizers deliberately do NOT give the internal cashier regulation or a list of violations. We must:
1. Find public RK acts (NBRK resolutions on cash operations, cash collection/инкассация, cash-room security requirements) on adilet.zan.kz.
2. Decide which rules are *visually checkable* from a single overhead camera.
3. Build our own taxonomy of violation types, each mapped to a concrete document + point.
4. Show the chain: source point → violation type → observed video fragment. Graded under "Обоснованность правил" (15 pts); hallucinated citations are penalized. FAQ #32: every dataset violation has an open normative basis.
→ See `regulations.md`.

## Dataset facts
- 42 clips, all 464x848 portrait, 30 fps, 11.6–142.5 s. 22 violation / 20 clean.
- Labels: only yes/no + one second. No type.
- Duplicates: `Х-4` ≡ `Х-47` (labels 57 s vs 68 s → label noise / possibly two events), `Х-5` ≡ `Х-48` (both clean).
- Filenames mix Latin `X` and Cyrillic `Х` — normalize.
- Scene: overhead, cashier at desk (counting machine, laptop), grey safe/cabinet, glass door; collectors bring bags (e.g. X-17).

## Docs index
- `videos/batch_[A-D].md` — per-video analysis
- `regulations.md` — normative sources & mapping
- `tech_options.md` — model/stack research
- `01_findings.md` — synthesized violation taxonomy & plan (orchestrator)

## Team sessions (Claude)
- `forte-service` (bg id 13d98288) — builds FastAPI + UI + job queue shell with stub analyzer in `service/`; status → `docs/status_service.md`. `claude attach 13d98288` to watch.
- `forte-vlm-spike` — tests whether Qwen3-VL-4B on 6GB can see the cues; results → `docs/spike_vlm.md`.
- `forte-detector` — builds eval harness + perception + rule engine in `detector/`; status → `docs/status_detector.md`.
- `forte-deploy` (bg e90083b9) — deploys VLM + service on the 5080 PC (kanat@10.13.0.14); status → `docs/status_deploy.md`.
