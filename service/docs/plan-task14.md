# Task 14: Parallel job workers (JOB_WORKERS)

Orchestrator calls this Task 13b. It is important for jury day.

**Why.** The jury may submit five or more videos at once, and the 5-minute limit counts from their submit. The detector takes up to 83 s per video (typically 10–30 s). With one sequential worker, the 4th–5th job in a burst risks hitting the 280 s cap and coming back partial.

## Requirements
- **`JOB_WORKERS`** (env/Settings, int, default 1; production sets 3). Clamp to 1..8. Log the value at startup.
- **N worker threads share one FIFO queue.**
  - Each worker owns its own analyzer instance, created via `load_analyzer()` in that worker's thread. Load them **sequentially**: worker k starts loading only after worker k−1 has finished loading or failed. This avoids VRAM spikes.
  - A worker whose analyzer failed to load must NOT take jobs from the queue: it records its error and exits or idles. If every worker failed → keep the current behaviour (jobs fail with the clear «Анализатор недоступен» error) so jobs never hang.
- **Job store.** It is already RLock-protected. Audit every read-modify-write in worker.py for races once several workers run, e.g. counters and the orphan list. The orphan bookkeeping and the join-before-next-job logic are now per worker.
- **Hard caps unchanged.** Per-job deadline stays `min(max(submit+JOB_TIMEOUT_SEC, start+20), submit+JOB_HARD_CAP_SEC)`, and the clip deadline stays submit+cap−2 s.
- **Global ffmpeg limit.** Clip ffmpeg concurrency must stay bounded across workers: a process-wide semaphore of `CLIP_MAX_PARALLEL` (default 4) shared by all workers' clip pools. Each clip still respects its own job's deadline; waiting for the semaphore counts against that deadline (use acquire with a timeout).
- **Recovery and order.**
  - Startup recovery (re-queue `processing` jobs) puts them into the shared queue once.
  - Jobs start in FIFO order (dequeue order = submit order). Completion order may differ.
- **`/healthz`.** Add `workers: {configured: N, ready: k, busy: b}`. `analyzer_ready` is true if at least one worker is ready. `analyzer_error` is the first load error, or null.
- **Shutdown.** The app's shutdown stops all workers cleanly (sentinels on the queue, join with timeout).
- **README.** Document `JOB_WORKERS` and the VRAM rule:
  - N × ~1.2 GB (detector) + VLM ~5.2 GB (llama.cpp on :8090, 4 parallel slots) + headroom ≤ 16 GB on the RTX 5080 → **N ≤ 4**; production uses 3.
  - CPU and ffmpeg load rises with N.
  - Remove the README line about a single FIFO worker and N videos sharing 280 s.
- **`.env.example`.** Add `JOB_WORKERS=1` with a comment, plus `CLIP_MAX_PARALLEL=4`.

## Tests (pytest, stub or fake analyzers; generous timing because the machine is loaded)
- **Concurrency.** JOB_WORKERS=3 with a fake analyzer that sleeps ~1 s and records thread name and start/end times. Submit 6 jobs and assert:
  - at most 3 overlapping and at least 2 overlapping, so they really ran in parallel;
  - all 6 end `done`;
  - start order follows submit order;
  - each worker thread used its own analyzer instance (distinct ids);
  - total wall time is clearly below 6 × 1 s.
- **Store safety.** All job JSON files on disk are valid and match memory; no `.tmp` leftovers; per-job fields are not cross-contaminated (each job's violations and filename are its own).
- **Load failure.** One worker's analyzer fails to load and the others succeed → jobs still complete on the healthy workers, and healthz shows ready=N−1. All fail → jobs fail fast with the error.
- **Deadline under contention.** JOB_WORKERS=2, three slow analyzers that ignore their deadline, a tiny cap → every job finishes ≤ submit + cap + slack.
- **Clip semaphore.** With CLIP_MAX_PARALLEL=1 and 2 workers, ffmpeg (monkeypatched cut) never runs concurrently.
- **Default.** JOB_WORKERS=1 behaves exactly as before; the existing suite stays green.
