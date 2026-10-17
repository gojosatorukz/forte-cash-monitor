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
- Limits: at most **3 eval workers across all concurrent invocations** (flock on `~/forte/evals/slot.{0,1,2}.lock`; extra workers wait, and a waiting run's wall time includes the wait). Workers run at `nice 10`. Before each video a worker waits while GPU memory used is above 12 GB. A run refuses to start until VLM `:8090/health` is ok (5 min max).
- **Kill switch:** `ssh kanat@10.13.0.14 touch ~/forte/evals/STOP`. New runs are refused, and running workers exit before their next video (the partial run is merged; never-run clips are listed and not scored). `rm` it to re-enable.
- Measured (17ddd4e, 42 orig clips, 3 workers, 2026-10-17): wall **414 s**, against 563 s for the sequential c5ef2b6 robust run. Verdicts and types are identical on all 40 shared clips (12/22, FP 0/20). The gain is small because the VLM still had **1 slot** (`/props total_slots=1`): probe time was 1150 s summed against 395 s sequential. Expect more once llama-server runs `-np 4`. Per worker: torch peak 819–836 MiB; process peak 1.17 GB with `expandable_segments` (1.42 GB without). GPU total peak 10.6 GB. During that run a production request (Х-17) took **29.2 s** (9 s idle). Set STOP before demos.

### Eval cut-off and STOP (PC, systemd --user)
- `forte-evals-cutoff.timer` fires once at `2026-10-18 03:00:00 UTC` (= **08:00 Asia/Almaty**; the PC clock is UTC; `Persistent=true`). It runs `forte-evals-cutoff.service`, which creates `~/forte/evals/STOP`.
- `forte-evals-stop.path` (`PathExists=~/forte/evals/STOP`) starts `forte-evals-kill.service` as soon as STOP appears, whether the timer or a manual `touch` created it.
  - The kill service sends SIGTERM to every `eval_worker.py` and `run_eval_sha.sh`, then SIGKILL to any worker still alive after 10 s.
  - Tested with dummy processes: all killed within seconds. While STOP exists, `run_eval_sha.sh` refuses to start.
- Manual stop: `ssh kanat@10.13.0.14 touch ~/forte/evals/STOP`
- Re-enable evals: `ssh kanat@10.13.0.14 'rm ~/forte/evals/STOP && systemctl --user stop forte-evals-kill.service'`. Stopping the kill service re-arms the path unit.
- Check: `systemctl --user list-timers forte-evals-cutoff.timer`
