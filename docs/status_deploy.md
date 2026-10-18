# Deploy status (forte-deploy)

_Updated 2026-10-17. Owner: forte-deploy session._

## Target
- RTX 5080 PC: `ssh kanat@10.13.0.14`
- Ubuntu **22.04** (glibc 2.35), 28 cores, 62 GB RAM, RTX 5080 16 GB, driver 610.57
- No Docker and no passwordless sudo, so everything runs **user-level**: `~/forte/*` with `systemctl --user` units
- Linger enabled, sleep targets masked (both done by the user)

## Milestone 1: VLM on the PC (done)
| Piece | Where |
|---|---|
| llama.cpp b11707 (cuda-12.8 prebuilt, has sm_120) + Qwen3-VL-4B Q4_K_M + mmproj F16 | PC `~/forte/spike/{bin,models}` (sha256 checked against the laptop) |
| Launcher | PC `~/forte/spike/run_server_pc.sh`: `-ngl 99 -c 8192 -ctk/-ctv q8_0 -np 1 -fa on --temp 0 --image-min-tokens 1024`, bound to `127.0.0.1:8090` |
| Unit | PC `~/.config/systemd/user/forte-vlm.service`: Restart=always, StartLimitIntervalSec=0, ExecStartPost self-test (unit goes active only once `/health` returns ok, retried for up to 120 s) |
| Laptop forward | laptop `~/.config/systemd/user/forte-vlm-tunnel.service`: `ssh -N -L 127.0.0.1:8090:127.0.0.1:8090 kanat@10.13.0.14`, ExitOnForwardFailure, ServerAlive 10 s×3, Restart=always (RestartSec 3) |

VRAM: llama-server uses about **4.5 GB** (5.0 GB total on the GPU including the desktop).

### Known fragility: side-loaded glibc
The b11707 prebuilt is built on Ubuntu 24.04 and needs GLIBC_2.38 and GLIBCXX_3.4.32. On 22.04 it is therefore started through a user-level copy of the Ubuntu 24.04 libraries:
- libraries: `libc6 2.39-0ubuntu8.9`, `libstdc++6 14.2.0-4ubuntu2~24.04.1` and `libgcc-s1`, extracted with `dpkg-deb -x` into `~/forte/glibc239/root`
- loader: `~/forte/glibc239/root/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2 --library-path …`
- `cd` into the bin dir: ggml looks for `libggml-cuda.so` next to the exe, which here resolves to the loader, or in the cwd

If it ever crashes under load, the fallback is to build llama.cpp from source on the PC, with a user-level CUDA 12.8 toolkit (pip `nvidia-cuda-nvcc-cu12`) and gcc 11. Not done for now (orchestrator decision).

Operations:
- status: `systemctl --user status forte-vlm`
- logs: `journalctl --user -u forte-vlm -f`
- laptop side: `systemctl --user status forte-vlm-tunnel`

### Latency: laptop → SSH tunnel → 5080, end to end
Measured with `spike/test_single.py both` on real 464×848 keyframes and ROI crops, `--image-min-tokens 1024` (about 1100 prompt tokens per image). Raw data: `spike/results/single_{full,crop}_pc5080_tunnel.jsonl`.

| | n | p50 | p95 | max |
|---|---|---|---|---|
| full frame | 32 | 0.81 s | 1.10 s | 1.11 s |
| ROI crop | 24 | 0.83 s | 0.93 s | 0.95 s |
| all | 56 | **0.81 s** | **1.10 s** | 1.11 s |

- Breakdown: about 0.30 s prefill (1100 tokens) plus about 0.33 s decode (~55 output tokens of `reason`). The tunnel adds about 3 ms.
- Compared with the laptop 3060 (1.2–2 s): roughly 2× faster.
- At 60 calls per video that comes to about 50–65 s. Dropping or capping `reason` should bring calls to about 0.4–0.5 s.
- Accuracy matches the laptop:

  | Cue | Full frame | Crop |
  |---|---|---|
  | cash | 7/7 | 7/7 |
  | count | 8/8 | n/a |
  | laptop | 7/8 | 8/8 |
  | door | 5/9 | 6/9 (still CV-only) |

## Milestone 2: full service on the PC (done)
- Code on the PC:
  - `~/forte/service` at service main 441bdd4
  - `~/forte/detector` at detector main c5ef2b6
  - Both are rsynced from the laptop with `--exclude .venv .claude __pycache__ data/ models/ .env`.
- Toolchain (all user-level):
  - uv 0.13.1 in `~/.local/bin`
  - Python 3.12 via uv
  - static ffmpeg/ffprobe 8.1 (BtbN) symlinked into `~/.local/bin`
  - `uv sync --frozen --inexact`: torch 2.11.0+cu128 with arch list up to sm_120, `cuda.is_available()` True, opencv-headless 5.0
  - detector installed editable through service pyproject (`../detector`)
- Models: `scripts/fetch_models.py` cached RT-DETRv2-r18 and OWLv2-base (671 MB in `~/.cache/huggingface`), and the offline load check passed.
- Env: PC `~/forte/.env` (chmod 600). It contains:
  - `API_KEY`: generated on the PC, never printed. To read it: `ssh kanat@10.13.0.14 'sed -n "s/^API_KEY=//p" ~/forte/.env'`
  - `HOST=0.0.0.0`, `PORT=8000`
  - `ANALYZER=forte_detector.service_adapter:ForteAnalyzer`
  - `RULES_PATH=/home/kanat/forte/detector/taxonomy/rules.json`
  - `FORTE_VLM_URL=http://127.0.0.1:8090/v1/chat/completions`, `FORTE_DEVICE=cuda`
  - `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `UV_OFFLINE=1`
  - `FORTE_VLM_SLOTS=4`: must equal llama-server `-np` (NP in `run_server_pc.sh`), per forte-detector; added 2026-10-17 for the thread-safety release
- Unit `~/.config/systemd/user/forte-service.service`:
  - `Wants=`/`After=forte-vlm.service`, so the VLM gets VRAM first
  - EnvironmentFile `~/forte/.env`, ExecStart `run.sh`, Restart=always
- VRAM: llama-server 4.7 GB plus the service (RT-DETR + OWLv2, warm) 1.0 GB, about 6.2 GB of 16 GB in total.
- `/healthz` returns `analyzer_ready: true`, `vlm_ok: true`, `rules_count: 17`. It shows `rules_path` relative (display only).
- The service starts fully offline: HF, transformers and uv are all set offline.

### End to end from the laptop (`curl http://10.13.0.14:8000`, port open on the LAN)
Script: `e2e.sh` (submit, then poll every 1 s); the API key is read over SSH.

| Video | Upload | Submit → done | Verdict | Violations | VLM calls |
|---|---|---|---|---|---|
| Х-26 | 1.2 s | **12.6 s** | no_violation | none | 22 |
| Х-43 | 1.8 s | **12.1 s** | no_violation | none (clean clip ✓) | 13 |
| Х-17 | 0.9 s | **12.3 s** | violation | T9 @ 7.27 s, legal_strength=high, 3 evidence frames | 17 |

- No warnings, `degraded: false`. An unauthenticated request returns 401.
- About 12 s per 20–30 s clip, far under the 270 s budget.
- Detection quality is not a deploy matter: Х-26 is labelled a violation but came back no_violation. This is for forte-detector.

## Milestone 3: service gaps (done, service main)
- `b8e7750`:
  - job `warnings: list[str]`, taken from `result.warnings` and analyzer meta `notes_ru`; notes_ru is read before the 8 KB meta drop
  - UI block «Предупреждения»
  - Violation `legal_strength` (high/medium/low or null); the UI shows «Сила правовой нормы: высокая/средняя/низкая»
  - `/healthz` gains `vlm_ok` and `vlm_url` (1.5 s probe, 5 s cache; `ok` unchanged)
- `b06b58e`: service depends on the editable `../detector`, plus the opencv-python override. This breaks the Docker build, because `../detector` is outside the build context; Docker is not used on the PC.
- `441bdd4`: every `partial: true` job gets a service warning: «анализ прерван по времени, результат может быть неполным», or «…из-за ошибки…» after a crash with findings.
- 174 tests pass.

## Milestone 4: jury-day hardening (PC part done; server relay pending the user)
- **GPU headroom**: peak 6.6 GB of 16.3 GB, sampled every 200 ms during Х-14 (142 s, the longest clip). That is the VLM at 4.7 GB plus the service at 1.0–1.4 GB, leaving about 9.7 GB free.
  - Х-14 takes 20–27 s end to end, of which 8–10 s is the upload over the LAN.
- **Restart safety** (all via systemd `Restart=always`, user units, linger on):

  | Test | Recovery | Notes |
  |---|---|---|
  | `kill -9` forte-service | restarted after 3 s, uvicorn up after 4 s | analyzer warm, then an Х-17 job gave the same result |
  | `systemctl --user kill -s KILL forte-vlm` | `/health` ok after 4.6 s | service `vlm_ok` back to true; the laptop tunnel did not even reconnect |
  | laptop tunnel kill (`forte-vlm-tunnel`) | 127.0.0.1:8090 answers again after 5.9s | ssh reconnected by Restart=always (RestartSec 3) |

  The VLM and tunnel tests ran under the shared `work/gpu.lock`, the same lock the detector and robust evals take, so no eval was hit.
- **Reboot test**: not done; it needs the user's go-ahead. Everything is enabled for `default.target` with linger, so it should come up on boot: forte-vlm, then forte-service (After/Wants).
- **Relay to forte.incode.kz**:
  - PC: key `~/.ssh/forte_relay_ed25519` and unit `forte-relay.service` are staged but **not enabled**.
  - Server: `docs/forte_relay_server_setup.sh` is for the user to run as root; my session is not permitted to SSH to 89.126.193.108.
  - After the server step, on the PC: `systemctl --user enable --now forte-relay`, set `PUBLIC_HOSTNAME=forte.incode.kz` and `HOST=127.0.0.1` in `~/forte/.env`, then `systemctl --user restart forte-service`.

### Server files changed by the relay script (for revert)
- created: user `forte-relay` (`/home/forte-relay/.ssh/authorized_keys`)
- created: `/etc/ssh/sshd_config.d/50-forte-relay.conf`
- appended: a `forte.incode.kz` block to `/home/ubuntu/agileai/Caddyfile`, with backup `Caddyfile.bak-before-forte`
- Revert steps are at the bottom of the script.

## Public URL: https://forte.incode.kz (live)
- The user ran `docs/forte_relay_server_setup.sh` on 89.126.193.108: the sshd Match block applies to forte-relay only, Caddy validated and reloaded, incode.kz unchanged (303 → 303).
- PC: `forte-relay.service` is enabled and active (reverse tunnel -R 172.18.0.1:18000 → 127.0.0.1:8000; ExitOnForwardFailure, no restarts).
- PC `~/forte/.env`:
  - `HOST=127.0.0.1`, so the service is no longer reachable on the LAN at :8000
  - `PUBLIC_HOSTNAME=forte.incode.kz`
  - backup at `~/forte/.env.bak-before-relay`
- `/healthz` reports `public_url: https://forte.incode.kz` (source: env). The certificate is Let's Encrypt, valid until 2027-01-15.
- End to end from the laptop over the public HTTPS URL:

  | Video | Upload | Submit → done | Result |
  |---|---|---|---|
  | Х-17 | 0.9 s | **15.6 s** | T9 @ 7.27, 3 frames |
  | Х-14 | 8.3 s | **28.2 s** | clean |

  - The clip `GET /api/v1/jobs/{id}/clips/1.mp4` with the key returns 200 video/mp4 (70 KB, 7.97 s).
  - An unauthenticated request returns 401; UI `/` returns 200.
- **Reboot test: done, 2026-10-17 19:35 laptop time.** The orchestrator triggered the reboot after the user approved it. It ran under gpu.lock, with forte-detector paused.
  - Boot timing (systemd-analyze): firmware 7.3 s, loader 13.3 s, kernel 4.5 s, userspace 9.9 s = 35 s.
  - Readiness, measured from kernel boot (about 20 s after power-on):

    | Component | Ready after |
    |---|---|
    | VLM `/health` | +17 s |
    | service `analyzer_ready` | +22 s |
    | `https://forte.incode.kz/healthz` | +22 s |
    | laptop tunnel `:8090` | +22 s |

  - No one was logged in (`who` shows 0). forte-vlm, forte-service and forte-relay were all active with 0 restarts. VRAM was 5.7 GB.
  - After the reboot, a public end-to-end run of Х-17 took 9.4 s (T9 @ 7.27).

## Detector 17ddd4e (evidence boxes) rolled out
- Synced detector main 17ddd4e and restarted forte-service (no dependency changes).
- Public Х-17: 8.3 s, T9 @ 7.27.
  - `evidence_frames[0].annotated = true`, with red labelled person boxes «кассир» and «посетитель» (checked visually).
  - Frames 1 and 2 carry `boxes` but `annotated=false`.
- Artifacts: `share/x17_boxes/`.

## Sudo list for the user (run once on the PC; the rest is user-level)
Already done by the user: `loginctl enable-linger kanat` and masked sleep/suspend/hibernate/hybrid-sleep targets.
```bash
# 1. No unattended upgrades or apt timers (a driver/kernel update mid-demo would break CUDA until reboot)
sudo systemctl disable --now unattended-upgrades.service apt-daily.timer apt-daily-upgrade.timer
sudo sed -i 's/"1"/"0"/' /etc/apt/apt.conf.d/20auto-upgrades
# 2. Pin the NVIDIA driver and kernel (belt and braces)
sudo apt-mark hold nvidia-driver-610-open libnvidia-compute-610 nvidia-kernel-common-610 nvidia-kernel-source-610-open linux-generic-hwe-22.04 linux-image-generic-hwe-22.04
# 3. Pause snap auto-refresh (snapd restarts snaps in the background)
sudo snap refresh --hold
# 4. (optional) system ffmpeg; a user-level static ffmpeg 8.1 is already in use
sudo apt install -y ffmpeg
```

## Jury-day checklist (deploy)
- [x] Once the incode.kz reverse tunnel (relay) is live, set `HOST=127.0.0.1` (done 2026-10-17) in PC `~/forte/.env` and run `systemctl --user restart forte-service`. `HOST=0.0.0.0` is for LAN testing only; the API key protects `/api`.

## Next
- Public URL via forte.incode.kz relay; milestone 4 hardening.

## PC parallel evals
One command from the laptop repo root (the PC does the work and the result comes back to the laptop):
```
work/pc_eval.sh <sha|ref> [--variant orig|hflip|...|robust|all] [--videos all|X-1,X-4|hard] [--workers 3] [--dedupe] [--fresh]
work/pc_eval.sh <sha|ref> --robust <dataset> ...      # forte-robust: robust/<dataset>/videos, scored as orig (same as FORTE_DATA_DIR=robust/<dataset>)
```
- forte-detector: `work/pc_eval.sh main --videos all` (the analyzer defaults to `forte_detector.analyzers.full:analyze`; `--analyzer mod:fn` overrides it). forte-robust: `work/pc_eval.sh <sha> --robust newcam_combo --dedupe`.
- What it does: `git archive <sha>` of `detector/` → PC `~/forte/evals/<sha12>/src` (one clean export per commit; the PC's `~/forte/detector` is not used). It rsyncs `work/pc_evals/` (runner + `eval_worker.py`) and the needed videos (`~/forte/evals/data/open`, `~/forte/evals/data/robust/<dataset>`; a set that is already there is skipped). Then it runs `~/forte/evals/run_eval_sha.sh` (blocking, streams per-video lines) and rsyncs the run back.
- Output: laptop `work/pc_runs/<sha12>-<variant|dataset>-<ts>/` (or `--out DIR`), in the harness format (`summary.json`, `report.md`, `<variant>/<vid>/result.json`). Metrics are recomputed with the harness's own functions from that commit. Extras: `pc_eval.json` (per-worker VRAM peaks) and `.gpu_mem.log`. PC copy: `~/forte/evals/<sha12>/runs/`.
- Env: a shared offline venv `~/forte/evals/venvs/<hash of pyproject+uv.lock>` (`uv sync --frozen`, `UV_OFFLINE=1`; it fails rather than downloads). Each commit has its own cache `~/forte/evals/<sha12>/cache`, so re-runs of a sha replay perception/VLM answers. `--fresh` uses an empty per-run cache, for timing.
- Knobs: every `FORTE_*` set in the laptop shell is forwarded to the PC workers, e.g. `FORTE_VERDICT_THR=0.5 work/pc_eval.sh <sha>`. The runner owns `FORTE_DATA_DIR`, `FORTE_CACHE_DIR`, `FORTE_EVAL_CACHE` and `FORTE_VLM_URL` and drops them; the VLM is PC-local. The variables travel as a NUL-separated file and are exported without eval, so no shell injection. The names are printed at start, and names+values go into the run's `pc_eval.json` (`forwarded_env`). With any knob forwarded, the cache is `<sha12>/cache-env-<hash of the knobs>`, so stale answers are never replayed.
- Limits: at most **3 eval workers across all concurrent invocations** (flock on `~/forte/evals/slot.{0,1,2}.lock`; extra workers wait, and a waiting run's wall time includes the wait). A run starts at most as many workers as it has clips, and a worker still waiting for a slot exits once the other workers have claimed every clip. Workers run at `nice 10`. Before each video a worker waits while GPU memory used is above 12 GB. A run refuses to start until VLM `:8090/health` is ok (5 min max).
- **Kill switch:** `ssh kanat@10.13.0.14 touch ~/forte/evals/STOP`. New runs are refused (exit 3, `REFUSED`). The path unit `forte-evals-stop.path` → `forte-evals-kill.service` SIGTERMs the running `eval_worker.py`/`run_eval_sha.sh` processes (SIGKILL after 10 s). The runner then merges the clips that finished, does not score the unfinished ones (listed in `pc_eval.json` `missing`, `stopped: true`) and exits 6 `STOPPED`. The wrapper still fetches the partial run and prints `STOPPED`. Ctrl-C in the wrapper does the same. The merge step runs as `eval_merge.py`, outside the pkill pattern. Re-enable: `rm ~/forte/evals/STOP && systemctl --user stop forte-evals-kill.service`.
- Measured (17ddd4e, 42 orig clips, 3 workers, 2026-10-17): wall **414 s**, against 563 s for the sequential c5ef2b6 robust run. Verdicts and types are identical on all 40 shared clips (12/22, FP 0/20). The gain is small because the VLM still had **1 slot** (`/props total_slots=1`): probe time was 1150 s summed against 395 s sequential. Expect more once llama-server runs `-np 4`. Per worker: torch peak 819–836 MiB; process peak 1.17 GB with `expandable_segments` (1.42 GB without). GPU total peak 10.6 GB. During that run a production request (Х-17) took **29.2 s** (9 s idle). Set STOP before demos.

### Eval cut-off and STOP (PC, systemd --user)
- `forte-evals-cutoff.timer` fires once at `2026-10-18 03:00:00 UTC` (= **08:00 Asia/Almaty**; the PC clock is UTC; `Persistent=true`). It runs `forte-evals-cutoff.service`, which creates `~/forte/evals/STOP`.
- `forte-evals-stop.path` (`PathExists=~/forte/evals/STOP`) starts `forte-evals-kill.service` as soon as STOP appears, whether the timer or a manual `touch` created it.
  - The kill service sends SIGTERM to every `eval_worker.py` and `run_eval_sha.sh`, then SIGKILL to any worker still alive after 10 s.
  - Tested with dummy processes: all killed within seconds. While STOP exists, `run_eval_sha.sh` refuses to start.
- Manual stop: `ssh kanat@10.13.0.14 touch ~/forte/evals/STOP`
- Re-enable evals: `ssh kanat@10.13.0.14 'rm ~/forte/evals/STOP && systemctl --user stop forte-evals-kill.service'`. Stopping the kill service re-arms the path unit.
- Check: `systemctl --user list-timers forte-evals-cutoff.timer`

### VLM -np 4 (2026-10-17)
- PC launcher `run_server_pc.sh`: `-np ${NP:-4} -c ${CTX:-16384}`, which gives 4 slots of 4096 context each (an image is about 1100 tokens). VLM VRAM is 5.2 GB, up from 4.7 GB. The previous launcher is kept as `run_server_pc.sh.bak-np1`.
- Load test under heavy real load (3 PC eval workers + a laptop-side eval + 4 concurrent test callers + 1 production job):
  - Per-call latency: p50 4.19 s, p95 6.61 s (n=128). Accuracy unchanged.
  - Production Х-17 via https: **41.1 s**, which is under the 60 s target. GPU memory peaked at 10.6 GB (under 13 GB).
- The 5080 is compute-bound on image prefill: extra slots add concurrency, not throughput, so production latency grows with the number of active VLM clients.
- Mitigation:
  - fewer PC eval workers (proposed default 2)
  - `touch ~/forte/evals/STOP` before demos
  - the jury-day 08:00 cut-off timer

## UI password gate (service 0992942, live 2026-10-17)
- PC `~/forte/.env` (chmod 600): `UI_PASSWORD` (24 chars) and `UI_SECRET` (64 chars), generated on the PC and never printed. The previous env is backed up as `.env.bak-before-ui-auth`. To get the password for the team: `ssh kanat@10.13.0.14 'sed -n "s/^UI_PASSWORD=//p" ~/forte/.env'`
- Checked from outside over the public URL:

  | Request | Result |
  |---|---|
  | `/` | 303 → `/login?next=/` |
  | `/act.html` | 303 |
  | `/ui-api/jobs` | 401 |
  | `/ui-api/feedback.jsonl` | 401 |
  | `/api/v1/jobs/x` with the key | 404 |
  | `/api/v1/jobs/x` without the key | 401 |
  | `/healthz` | 200, no job data |

- Headless Chromium login: a wrong password stays on `/login` and `/ui-api` returns 401. The correct password reaches `/` and `/ui-api` returns 200; the UI renders with its history (screenshot `share/act_e612f80/ui_logged_in_0992942.png`).
- Proxy trust: requests arrive over the reverse SSH tunnel, so the socket peer is 127.0.0.1. The default `TRUSTED_PROXIES` therefore covers them, and X-Forwarded-For from Caddy is honoured.

## Idle baseline (PC timer)
- `forte-baseline.timer` fires at `2026-10-18 03:05 UTC` (= 08:05 Almaty), 5 minutes after the eval cut-off. It runs `~/forte/baseline/run_baseline.sh`, which:
  - waits for STOP and for eval workers to finish;
  - runs Х-17, Х-43 and Х-14 over https://forte.incode.kz, two rounds each, uploading from the PC;
  - measures VLM per-call latency over 20 calls with `cache_prompt:false`, so every call does a full image prefill.
- Results go to `~/forte/baseline/<ts>/log.txt` plus the job JSONs.

## Production detector 378b74f (release of cb4da16), live 2026-10-17 ~21:00 Almaty
Public smoke with 3 eval workers running, so the timings are inflated:

| Clip | Total | Result | Evidence |
|---|---|---|---|
| Х-17 | 25.0 s | T9 @ 7.27 (high) | frames [annotated, raw, annotated], boxes «кассир/посетитель», clip |
| Х-26 | 29.9 s | **T1 @ 12.38 (high), now caught** | boxes «кассир» / «за стеклом» |
| Х-32 | 25.7 s | T5 @ 1.0 (medium) | 3 boxed frames |
| Х-43 | 21.3 s | no_violation ✓ | |

No warnings, not degraded.

## Jury-day runbook (2026-10-18, Almaty time; the PC clock is UTC = Almaty − 5 h)
**Scope after 08:00 (orchestrator, final):** only the 08:05 baseline, the 08:20 burst, the 10:02 curate/export and the ~10:30 final external checks. There is no post-08:00 regression run: the 2a508df gate runs are the final numbers for the frozen detector 38f9946, which is deployed 2026-10-18 00:25. Rows below about a late detector release apply only if the orchestrator ships an emergency fix.

| Time | Step | Who/how |
|---|---|---|
| **08:00** | Eval cut-off: `forte-evals-cutoff.timer` creates `~/forte/evals/STOP`, and the path unit kills in-flight eval workers | automatic (PC) |
| **08:05** | Idle baseline: `forte-baseline.timer` runs `~/forte/baseline/run_baseline.sh` (Х-17/43/14 ×2 via https + 20 VLM calls) | automatic (PC) |
| ~08:20 | Copy `~/forte/baseline/<ts>/log.txt` numbers into this doc (deck numbers) and send them to orchestrator + forte-docs | forte-deploy |
| **07:00–08:15** | **The burst must test the FINAL detector.** The improvement-release cut-off is about 07:00. If the orchestrator fast-forwards detector main, deploy it **before 08:15**; evals are stopped from 08:00, so the restart is safe. Run the usual rsync, offline uv sync and restart, plus a smoke test of Х-17/Х-26. If a detector release lands **after** the burst has run, re-run the burst after that deploy and before 10:00: `ssh kanat@10.13.0.14 systemctl --user start forte-burst.service` (logs in a new `~/forte/burst/<ts>/`). | orchestrator → forte-deploy |
| **08:20 (automatic, PC timer `forte-burst.timer`)** | Runs `~/forte/burst/burst_pc.sh` (source `work/burst_pc.sh`). It waits for the baseline and any eval processes to finish, then for JOB_WORKERS in 3, 2, 1: sets it, restarts, waits until all workers are ready, submits 6 videos via https and checks pass/fail. The first passing level stays configured. Log: `~/forte/burst/<ts>/log.txt`. A dry run on 2026-10-17 (1 worker, Х-17 + Х-43) passed. forte-deploy copies the results into this doc. | automatic → forte-deploy |
| (manual alternative) | **Concurrency switch and burst test.** Set `JOB_WORKERS=3` in PC `~/forte/.env`, run `systemctl --user restart forte-service`, check that `/healthz` shows `workers.configured=3, ready=3`. Then run `work/burst.sh` from the laptop: Х-14/17/26/32/43/35 submitted within about 2 s via https. **Pass** = all done, none failed, none `degraded` or carrying a «без VLM» warning; record the max submit→done time and the GPU peak. **If it fails:** set `JOB_WORKERS=2`, restart and re-run; if that fails too, go back to `JOB_WORKERS=1`, which is the current production behaviour. | forte-deploy |
| any time, if the team decides (task #241) | **Brand flip:** set `BRAND=neutral` in PC `~/forte/.env` (the neutral «Cash Monitor» wordmark and CM favicon), run `systemctl --user restart forte-service` (about 20 s of downtime, so not while a job is in flight), and check that `/healthz` shows `"brand":"neutral"`. Revert with `BRAND=forte`. | forte-deploy |
| by ~09:30 | Final ff of detector/service main (orchestrator), then deploy: rsync (excl. .venv .claude data models .env), `UV_OFFLINE=1 uv sync --frozen --inexact`, `systemctl --user restart forte-service`, public smoke Х-17/Х-26/Х-32/Х-43 | orchestrator → forte-deploy |
| **10:00** | Freeze and curate: `ssh kanat@10.13.0.14 ~/forte/curate_jobs.sh` (source `work/curate_jobs.sh`). It stops the service, moves data/{jobs,clips,uploads} to `data.bak-<ts>` and starts the service. It then runs Х-17, Х-26, Х-32 and Х-43 via https and adds a completed review on Х-17 («Демо-проверяющий», all violations confirmed, decision confirmed). It prints the act URL, which then has no ЧЕРНОВИК. Check the act in headless Chromium. | forte-deploy |
| 10:00 (right after curation) | **Export the final artifacts** to `share/final/`, for the four curated jobs (Х-17 reviewed, Х-26, Х-32, Х-43; the records carry `meta.procedure` from detector ≥450e9f6, needed for the «Процедура» screenshots):
- the raw records `data/jobs/<id>.json`, including `review`
- `data/clips/<id>/`
- `versions.txt` with the live BRAND, service SHA (bba8e54 expected) and detector SHA (38f9946 expected)

Command: `rsync -a kanat@10.13.0.14:forte/service/data/jobs/ share/final/jobs/` (then keep only those 3 ids, though after curation the store only holds them) and the same for `data/clips/`. Then message **forte-service**, which captures the final screenshot set from a local replay. | forte-deploy |
| **10:30** | Final external checks (below) | forte-deploy |
| ≤12:00 | Submit (URL https://forte.incode.kz, API key for the jury) | orchestrator / user |

Final external checks at 10:30:
- `https://forte.incode.kz/healthz` returns `analyzer_ready`, `vlm_ok` and `public_url`.
- The gate: `/` → 303 `/login`; `/ui-api/jobs` → 401; `/api/v1/jobs/x` with the key → 404, without it → 401.
- An authenticated end-to-end run of Х-17 via https, with the clip download.
- A headless login with UI_PASSWORD, and the curated act renders.
- On the PC: forte-vlm, forte-service and forte-relay are active, `~/forte/evals/STOP` exists, no `eval_worker.py` is running, and VRAM is about 6 GB.
- Don'ts: do not restart forte-relay or forte-vlm, and do not remove STOP until after judging.

## Service 0a1878a (gate hardening), live 2026-10-17
- Gate re-verified from outside:
  - `/` → 303 `/login`; `/ui-api/jobs` → 401; `/api/v1` with the key → 404 (works)
  - headless login: a wrong password stays on `/login`, the correct one reaches `/` and `/ui-api` returns 200
- `TRUSTED_PROXIES` defaults to loopback, which is correct here: the relay is an SSH reverse tunnel to 127.0.0.1, and Caddy's X-Forwarded-For carries the real client.
- Lockout test (20 failures per 5 min per client IP):

  | Client | Attempt | Result |
  |---|---|---|
  | laptop via the public URL | 20 wrong logins | 401 ×20 |
  | laptop via the public URL | 21st attempt | **429** |
  | laptop via the public URL | correct password | 429 (locked) |
  | laptop via the public URL | correct password + spoofed `X-Forwarded-For: 1.2.3.4` | 429 (the spoof is ignored, because the rightmost XFF entry from Caddy is used) |
  | PC direct `127.0.0.1:8000` | correct password, at the same time | **303 + cookie** |

  So clients are keyed by their real IP, not one shared loopback IP. The lock expired by itself after the window.
  - The venue LAN (laptop and PC) shares one public egress IP, so team members behind the same NAT share one lockout counter.
- The cookie over https is `forte_ui=…; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200; Secure`.

## Production detector 9f8d3b9 (= gated ba2f4df), live 2026-10-17
- Public smoke (idle-ish):

  | Clip | Result | Time | Notes |
  |---|---|---|---|
  | Х-17 | T9 @ 7.27 | 6.2 s | |
  | Х-26 | T1 @ 12.38 | 6.5 s | |
  | Х-32 | T5 @ 1.0 | 5.6 s | |
  | Х-43 | clean | 7.1 s | |
  | Х-35 | **T15** @ 9.0 (low, п. 19 пп. 1) | 52.6 s | under eval load, 37 VLM calls |
  | Х-19 | clean ×2 | | the known 1-of-4 team-T15 miss, matches the gate (forte-detector); artifacts in `share/x19_prod_9f8d3b9/` |

- Gate numbers (orchestrator):

  | Set | Recall | FP | Other |
  |---|---|---|---|
  | orig | 19/22 | 3/20, all team-T15 (0/16 excluding them) | hard-neg 2/19, hit@5s 82% |
  | reencode | 18/22 | 4/16 excl. | |
  | newcam | 15/22 | 9/20 | |

## Service d650940 (live 2026-10-17)
- No default API key: `/api/v1` returns 503 unless `API_KEY` is at least 16 characters (ours is 32). `/healthz` reports `api_key_configured: true` and `workers: {configured: 1, ready: 1}`.
- `JOB_WORKERS=1` is set in `~/forte/.env`, so behaviour is unchanged. Х-17 via https: T9 @ 7.27 in 17.8 s. The gate still redirects (303).

## Production detector cb77b44 (= gated 46b8c29), live 2026-10-17; service still 0a1878a
- Smoke via https, under eval load:

  | Clip | Result | Time |
  |---|---|---|
  | Х-17 | T9 @ 7.27 | 27.2 s |
  | Х-26 | T1 @ 12.38 | 31.8 s |
  | Х-32 | T5 @ 1.0 | 22.5 s |
  | Х-43 | clean | 13.9 s |

  Frames come as [annotated, raw, annotated] plus a clip.
- Gate numbers (orchestrator):

  | Set | Recall | FP | Other |
  |---|---|---|---|
  | orig | 20/22 | 0/16 excluding team-T15 | hard-neg 2/19, hit@5s 82% |
  | reencode | 19/22 | | |
  | newcam | 14/22 | | |

  Max runtime is 83 s.

## Service bba8e54 (BRAND switch + analyzer-load watchdog), live 2026-10-17
- `~/forte/.env`: `BRAND=forte`, `JOB_WORKERS=1`.
- `/healthz` (public): `brand: forte`, `api_key_configured: true`, workers 1/1, analyzer_ready, vlm_ok.
- Х-17 via https: T9 @ 7.27 in 26.0 s, under eval load. The gate redirects (303) to `/login`, and the login title is «Вход — Forte Cash Monitor».

## Detector e018e47 (thread-safety release), live 2026-10-17 23:15 Almaty
- `~/forte/.env` has `FORTE_VLM_SLOTS=4` (= llama-server -np) and `JOB_WORKERS=1`.
- Public smoke: Х-17 T9 @ 7.27 (32.1 s), Х-26 T1 @ 12.38 (26.3 s), not degraded, under eval load.
- The burst at 3, 2 or 1 workers is scheduled on the PC for 08:20 (`forte-burst.timer`).

## Detector 38f9946 (= improvement release 2a508df), live 2026-10-18 00:25 Almaty: FINAL candidate
- `JOB_WORKERS=1`, `FORTE_VLM_SLOTS=4`.
- Public smoke under eval load:

  | Clip | Result | Time |
  |---|---|---|
  | Х-17 | T9 @ 7.27 | 30.3 s |
  | Х-26 | T1 @ 12.38 | 27.3 s |
  | Х-32 | T5 @ 1.0 | 19.0 s |
  | Х-43 | clean | 20.2 s |
  | Х-35 | T15 @ 9.0 (low) + **T10 @ 33.77 (low, new)** | 65.0 s, 41 VLM calls |

  None degraded.
- Gate numbers (orchestrator):

  | Set | Recall | FP |
  |---|---|---|
  | orig | 20/22 | 0/16 excluding team-T15 |
  | reencode | 18/22 (TP→FN 2) | |
  | newcam | 16/22 | |

- The 08:20 burst will test this build.

## FINAL combined rollout, 2026-10-18 07:10 Almaty
- Versions: **detector 67d1dbe** (T13 phone, T14 covered-from-start, meta.procedure), **service b360036** (live replay, 8-item checklist, procedure panel) and **BRAND=neutral**, in one restart. `JOB_WORKERS=1`, `FORTE_VLM_SLOTS=4`. The previous env is backed up as `~/forte/.env.bak-before-final`.
- `/healthz` (public): brand=neutral, api_key_configured, workers 1/1, analyzer_ready, vlm_ok.
- Gate: `/` → 303 `/login`; `/ui-api/jobs` → 401. Login title «Вход — Cash Monitor», with 0 "Forte" occurrences on the login page.
- Public smoke (evals had already finished, so near idle):

  | Clip | Result | Time |
  |---|---|---|
  | Х-17 | T9 @ 7.27 | 12.6 s |
  | Х-26 | T1 @ 12.38 | 12.1 s |
  | Х-32 | T5 @ 1.0 | 11.3 s |
  | Х-43 | clean | 10.0 s |
  | Х-35 | T15 @ 9.0 + T10 @ 33.77 | 29.9 s |

  None degraded.
- Х-17 `meta.analyzer.procedure`: `collection_handover` «Приём сумки от инкассатора», confidence 0.85, with steps (step 1 bag_check = violation T9 @ 7.27).
- Headless logged-in UI: the «Операция» panel renders with 10 steps (1 violation, 1 not checked, 8 compliant), plus the checklist and the violation card. Screenshot: `share/final_rollout/ui_x17_operation_panel_neutral.png`.

## Jury-day results on the FINAL build (detector 67d1dbe, service b360036, BRAND=neutral)
### Idle baseline (PC timer, 2026-10-18 08:05 Almaty; STOP present, 0 eval workers, GPU idle at 6.9 GB)
Uploads go from the PC through https://forte.incode.kz.

| Clip | Round 1 submit→done | Round 2 submit→done | Analyzer runtime (r1 / r2) | Upload | Result |
|---|---|---|---|---|---|
| Х-17 (15 s) | 13.3 s | **7.1 s** | 11.0 / 5.0 s | 1.4–1.6 s | T9 @ 7.27 |
| Х-43 (20 s) | 11.2 s | **7.0 s** | 8.0 / 4.1 s | 2.6 s | clean |
| Х-14 (142 s) | 34.7 s | **27.0 s** | 18.8 / 9.9 s | 15–16 s | clean |

- Round 1 includes first-run warm-up; round 2 is steady state. For Х-14, most of the time is the upload through the PC's uplink, not analysis.
- VLM per call, idle, 1 caller, full image prefill (`cache_prompt` off), short answer: **p50 0.31 s, p95 0.32 s** (n=20).

### Concurrency burst (PC timer, 08:20 Almaty): **PASS with JOB_WORKERS=3, kept**
6 videos submitted in 34.9 s, uploads included.

| Clip | submit→done | Analyzer runtime | Result |
|---|---|---|---|
| Х-14 | 74.7 s | 37.0 s | clean |
| Х-17 | 36.7 s | 23.0 s | T9 @ 7.27 |
| Х-26 | 38.0 s | 23.8 s | T1 @ 12.38 |
| Х-32 | 35.4 s | 19.8 s | T5 @ 1.0 |
| Х-35 | **80.2 s (max)** | 42.8 s | T15 + T10 |
| Х-43 | 51.1 s | 16.7 s | clean |

- None failed, partial or degraded, and no warnings.
- GPU peak 7.1 GB of 16 GB, utilization 99%.
- Production now runs `JOB_WORKERS=3`.

## 09:47 swap: service d6e22c0 + detector 8540d83 (one restart), 2026-10-18 09:48 Almaty: FINAL
- Eval window closed on time: STOP was recreated at 09:45:29 by the PC timer, no eval processes were left, and the GPU was production-only. `FORTE_T3_FALLBACK_PYES` is unset; `JOB_WORKERS=3`, `FORTE_VLM_SLOTS=4`, `BRAND=neutral`.
- Public `/healthz` no longer has `vlm_url` or `rules_path`. Keys: ok, analyzer_ready, analyzer_error, api_key_configured, brand=neutral, public_url(+source), rules_count, vlm_ok, workers (3/3). Gate: `/` → 303, `/ui-api/jobs` → 401.
- Smoke via https:

  | Clip | Result | Time | Notes |
  |---|---|---|---|
  | Х-17 | T9 @ 7.27 | 13.5 s | description ends «… Основание: Постановление Правления НБРК № 231 от 29.11.2019, п. 24 пп. 1), 3), 4).» |
  | Х-22 | **T3 @ 16.77** (medium, conf 0.285) | 19.0 s | «…проверьте сейф: дверца или ключ» |
  | Х-26 | T1 @ 12.38 + **T3 @ 12.38** (new, low conf) | 11.8 s | |
  | Х-43 | clean | 9.7 s | |
  | Х-44 | clean | 9.6 s | |
  | Х-12 | T3 @ 36.77 (conf 0.285) | 22.3 s | |

  None degraded.
- UI card (headless, logged in): the description has no «Основание:» suffix, and the source appears once, in «НАРУШЕННАЯ НОРМА», so there is no duplication.
- Sample response for the docs: `share/final/sample_response_x17.json` (image_base64 cut to 20 characters + «…»), with `share/final/sample_response_x17.meta.txt` alongside it (hashes, time, suffix = yes).

## Curation and freeze, 2026-10-18 09:52–09:55 Almaty
- `~/forte/curate_jobs.sh` ran on the final build. The old store is kept at `~/forte/service/data.bak-20261018T045225Z`.
- The store now holds:

  | Clip | Job id | Result | meta.procedure |
  |---|---|---|---|
  | Х-17 | 7e81f2b9… | T9, reviewed by «Демо-проверяющий» (confirmed) | collection_handover |
  | Х-26 | d1ab3237… | T1 + T3 | unknown |
  | Х-32 | ddce4c18… | T5 | no_client |
  | Х-43 | e806a2b8… | clean | no_client |

- Act https://forte.incode.kz/act.html#job=7e81f2b997fa491b85d0136d0db4b07b: final, no ЧЕРНОВИК, with the «Ход операции» table (checked in headless Chromium).
- Exported to `share/final/`: `jobs/`, `clips/`, `versions.txt`, `act_x17_final.png`, `sample_response_x17.json` and `.meta.txt`.
- **Production is FROZEN through judging** (orchestrator): no restarts and no config changes without the orchestrator.
