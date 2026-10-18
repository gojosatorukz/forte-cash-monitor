# Service status (forte-service)

_Updated 2026-10-17 ~19:00 (after Tasks 1–8). Owner: the service-builder session._

**Code:** `service/` repo, branch **`worktree-service-shell`** (head `c5fce60`; worktree at `service/.claude/worktrees/service-shell`). The branch is a fast-forward of `main` (441bdd4, which already contains the forte-deploy commits). To publish: `cd service && git merge --ff-only worktree-service-shell`.

**Verified:** `uv run pytest -q` → 218 passed. Live stub smoke tests ran on `Х-10.mov` and on its rot90 variant. `docker build` succeeded locally (no GPU here). `docker compose config`, `caddy validate` and `nginx -t` pass on the deploy files.

## Evidence boxes and clips (Task 8)
- **Boxes (analyzer side).** `EvidenceFrame.boxes=[{x,y,w,h,label?,kind}]`:
  - Coordinates are normalized 0..1 on the upright JPEG the analyzer sends.
  - `kind` is `person` (red outline) or `object`/`region` (green).
  - The service draws a 3 px outline and a Cyrillic label chip, using the bundled DejaVuSans font.
- **Output frames.** `evidence_frames` = [annotated, raw at the same timestamp, the rest], max 3 frames.
  - Extra keys: `annotated`, `boxes`.
  - If drawing fails, the raw frames are returned.
- **Clips.** One H.264 MP4 per violation.
  - Window: start−2 s … end+2 s, clamped to 3–8 s.
  - Format: ≤480p, no audio, faststart.
  - Rotation: `AnalysisResult.meta["applied_rotation_deg"]` (0/90/180/270, extra clockwise rotation beyond the container tag) is applied.
- **Clip URL.** Extra field `clip_url` → `GET /api/v1/jobs/{id}/clips/{n}.mp4` (key required; Range and HEAD supported). The UI route needs no key.
- **Clip budget.** Clips have their own deadline: submit + JOB_HARD_CAP_SEC − 2 s (≈278 s), so the analyzer keeps its full ≈270 s.
  - Up to 4 clips are cut in parallel, highest confidence first.
  - Clips that run out of time get the warning «не хватило времени на нарезку клипов № …»; real failures get «не удалось создать клипы № …».
  - A clip never fails a job, and the job stays `processing` until its clips are done.
- **UI.** Each violation card has a looping muted clip whose poster is the annotated frame, plus a «Разметка» badge on that frame.

## API (jury contract + extras)
- `POST /api/v1/analyze` (multipart `file`, ≤200 MB) → `{job_id, status}`.
- `GET /api/v1/jobs/{id}` → `{job_id, status: processing|done|failed, verdict: violation|no_violation|null, violations:[…]}`.
- Both require `X-API-Key` (env `API_KEY`); otherwise 401. HEAD is supported.
- Each violation has:
  - Contract fields: `type` (= rules `title_ru`), `timecode_sec`, `description`, `evidence_frames[{timestamp_sec, image_base64}]`.
  - Extras: `type_id` (T-id), `title_ru`, `rule {document, point, quote, url, legal_conf…}` or null, `rules[]`, `start_sec`, `end_sec`, `confidence`, `fact_conf`, `legal_conf`, `legal_strength` (`"high"|"medium"|"low"|null`, the citation-strength label; `legal_conf` from the detector is its ordinal encoding 0.9/0.7/0.4, not a probability; the UI shows «Сила правовой нормы: …»), and `analyzer_description` (only when it differs from the public description).
- `warnings: [str]` on every job (any status, `[]` by default): `AnalysisResult.warnings` + analyzer `meta["notes_ru"]`, deduped, ≤10 × ≤300 chars. `notes_ru` is extracted before the 8 KB meta size check, so it survives a dropped meta. Every `partial: true` job also gets a service note appended, which always survives the cap:
  - «анализ прерван по времени, результат может быть неполным» for a deadline overrun, analyzer `stopped_early`/`partial` meta, or a queue-exhausted budget;
  - «анализ прерван из-за ошибки, результат может быть неполным» for a crash after findings. The UI shows them as a «Предупреждения» block above the violations.
- Extra job fields: `progress`, `stage`, `partial`, `error`, `meta` (`meta.analyzer` holds the analyzer's meta, `meta.analyzer_error`, `meta.note`), `video_meta`, `filename`, `created_at`, `finished_at`.
- `GET /healthz` → `{ok, analyzer_ready, analyzer_error, rules_path, rules_count, public_url, public_url_source, vlm_ok, vlm_url}`. Check it before jury testing.
  - `vlm_url` = `VLM_HEALTH_URL`, else `scheme://host:port/health` of `FORTE_VLM_URL` (default `http://127.0.0.1:8090/v1/chat/completions`). `VLM_HEALTH_URL=""`/`off` → `vlm_ok: null`, `vlm_url: null`.
  - `vlm_ok` = HTTP 200 with "ok" in the body. The timeout is 1.5 s and the result is cached for 5 s. `ok` does not depend on it.

## Time budget
- The clock starts at **submit**, before the upload body is read.
- Deadline = `min(max(submit+JOB_TIMEOUT_SEC=270, start+20), submit+JOB_HARD_CAP_SEC=280)`.
- At the deadline the job finishes as done with `partial:true` and the violations found so far.
- If the analyzer crashes after finding something → done/partial with `meta.analyzer_error`. If it found nothing → failed.
- There is a single FIFO worker. N simultaneous videos share 280 s each, so per-video analysis must stay well under 280/N.

## Rules / citations (rule grounding)
- Lookup order: `RULES_PATH` → `../detector/taxonomy/rules.json` → `../docs/deliverables/rules_spec.json` → bundled `app/rules.json`, which is a byte-identical copy of rules_spec.json (17 ids).
- Citations come **only** from the loaded rules file; analyzer-supplied citations are dropped. Unknown ids pass through with `rule: null`, and the UI then shows «Норма не определена — требуется проверка специалистом».
- `description` precedence:
  1. The rules `description_template`, rendered with `{t}` = «мм:сс» and `{obs}` = the analyzer's `obs`. The separator is chosen by context: «…сумка, кассир передал…», «на 00:57 кассир…». With no obs, the placeholder is removed cleanly.
  2. The analyzer's `description`.
  3. `"{title}: {obs}"`.
  4. The title.

## Analyzer interface (what the detector implements)
```python
class MyAnalyzer:                      # ANALYZER=package.module:MyAnalyzer
    name = "forte-detector"
    def __init__(self): ...            # load models ONCE (worker thread, at startup)
    def analyze(self, video_path, progress_cb, deadline, partial) -> AnalysisResult:
        # progress_cb(0..1, "этап"); stop when deadline_passed(deadline) or partial.cancelled
        # partial.add(Violation(type="T1", timecode_sec=57.0, start_sec=55.0, end_sec=63.0,
        #     fact_conf=0.9, legal_conf=0.8, obs="кассир вышел из кадра", evidence_frames=[...]))
        # put torch.inference_mode() INSIDE analyze (each call runs in a fresh thread)
```
- Imports: `from app.analyzers.base import PartialSink, deadline_passed`; `from app.models import Violation, EvidenceFrame, AnalysisResult`; `from app.video import probe, iter_frames, frame_at, jpeg_b64`. `iter_frames` raises `VideoError` on an undecodable file.
- `obs`: a short clause, no leading capital, no trailing period.
- Dependencies: add them with `uv add …`. `run.sh` uses `uv sync --inexact`. The cu128 torch index is preconfigured: `uv add torch torchvision` resolves 2.11.0+cu128.
- In Docker the analyzer code must be baked into the image or mounted. Mount the rules file and set `RULES_PATH`; there is an override example in the README.

## Deploy (RTX 5080, Ubuntu 24.04)
- Docker path:
  - Dockerfile: CUDA 12.8 runtime, uv, Python 3.12, ffmpeg, non-root user, HEALTHCHECK.
  - `docker-compose.yml`: `app` (GPU, `restart: unless-stopped`, bound to `${BIND_ADDR:-127.0.0.1}:8000`), plus profiles `caddy` and `tunnel`.
- Bare-metal path: `scripts/setup.sh` plus systemd units in `deploy/systemd/` (`forte-service`, `forte-relay`, `forte-tunnel`, caddy).
- Public URL, the chosen path: **`https://forte.incode.kz`**.
  - Team server 89.126.193.108 runs Caddy or nginx drop-ins (`deploy/relay/`, additive) and forwards to `127.0.0.1:18000`.
  - On the PC, `forte-relay.service` holds `autossh -R 127.0.0.1:18000:127.0.0.1:8000` with a restricted key.
  - **Required** on the server: `deploy/relay/sshd-forte-relay.conf` (ClientAlive 15/3). Without it the relay can't reconnect after a network drop.
  - Set `PUBLIC_HOSTNAME=forte.incode.kz`.
  - Fallbacks: Caddy on the PC (public IP), Tailscale Funnel, cloudflared quick tunnel (`scripts/public_url.sh`). The named tunnel is dormant.
- README «День защиты» checklist:
  - Mask sleep targets, disable the apt timers, `apt-mark hold` the driver, docker and toolkit packages.
  - Don't restart the tunnel or relay.
  - Run a curl check with the key from mobile data.
- **Not yet tested:** the relay end to end against the real team server, and the GPU inside the container (`docker compose exec app nvidia-smi` on the target).

## Run locally
```bash
cd service && API_KEY=... ./run.sh           # :8000, UI at /
STUB_DEBUG=1 ./run.sh                         # stub emits one demo T99 violation with a demo box + clip
```
