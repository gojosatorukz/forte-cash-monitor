"""Long uploads (I1): 1-hour-length synthetic series must run in bounded time and memory.

At 4 fps one hour is 14 400 samples; any n x n matrix or per-sample O(n) lookup there is ~8 GB or
minutes. These tests use synthetic tracks/timelines only (no model, no real video)."""
from __future__ import annotations

import time
import tracemalloc

import numpy as np

from forte_detector.perception import zones as Z
from forte_detector.perception.timeline import build_timeline
from forte_detector.perception.tracker import Track
from forte_detector.probes import cheap, runner

N = 14_400  # 1 h at 4 fps
STEP = 0.25


def _seated_track(tid=1, n=N, t0=0.0, seat=(0.45, 0.40, 0.55, 0.70), seed=0):
    rng = np.random.default_rng(seed)
    t = [round(t0 + i * STEP, 3) for i in range(n)]
    base = np.asarray(seat)
    boxes = (base + rng.normal(0, 0.003, (n, 4))).tolist()
    return Track(tid, t, boxes, [0.9] * n)


def test_stationary_anchor_long_track_is_subsampled():
    tr = _seated_track()
    tracemalloc.start()
    t0 = time.perf_counter()
    a = Z.stationary_anchor(tr, aspect=16 / 9)
    dt = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert a.n_used <= Z.ANCHOR_MAX_POINTS <= 1000  # clustered on a subsample, not n x n
    assert a.count >= 0.9 * N  # ... but the anchor still covers the whole track
    assert all(0 <= i < N for i in a.idx[:10] + a.idx[-10:])
    assert dt < 2.0
    assert peak < 200 * 2**20  # an n x n float64 matrix alone would be ~1.6 GB


def test_stationary_anchor_short_track_unchanged():
    tr = _seated_track(n=200)
    a = Z.stationary_anchor(tr, aspect=1.0)
    assert a.n_used == 200 and a.count == 200 and a.idx == list(range(200))


def test_stationary_anchor_subsample_is_deterministic():
    tr = _seated_track(n=5000)
    a, b = Z.stationary_anchor(tr, 1.0), Z.stationary_anchor(tr, 1.0)
    assert a.idx == b.idx and a.box == b.box


def test_build_timeline_one_hour_bounded():
    times = [round(i * STEP, 3) for i in range(N)]
    cashier = _seated_track()
    # a visitor coming to the desk every ~10 min for 30 s
    visitors = []
    for k in range(6):
        s = 300 + k * 600
        n = 120
        t = [round(s + i * STEP, 3) for i in range(n)]
        boxes = [[0.60 - 0.2 * i / n, 0.35, 0.72 - 0.2 * i / n, 0.80] for i in range(n)]
        visitors.append(Track(100 + k, t, boxes, [0.9] * n))
    t0 = time.perf_counter()
    tl = build_timeline([cashier, *visitors], times, times[-1], (960, 540))
    dt = time.perf_counter() - t0
    assert tl["roles"]["cashier_tracks"] == [1]
    assert len(tl["series"]["t"]) == N
    assert dt < 10.0, dt  # was O(n^2) (searchsorted on a list per sample) + n x n anchor


def _long_timeline(n=N):
    ts = [round(i * STEP, 3) for i in range(n)]
    person = [0.45, 0.40, 0.55, 0.70]
    return {
        "series": {"t": ts, "persons": [[[1, "cashier", person, False]] for _ in ts],
                   "detections": [[person + [0.9]] for _ in ts], "objects": [[] for _ in ts]},
        "checkpoints": [{"type": "person_enters", "t": float(600 * k), "track_ids": [2]} for k in range(1, 6)],
        "tracks": [], "zones": {"door": [0.05, 0.2, 0.15, 0.6]},
    }


def test_persons_at_and_coco_evidence_are_log_time():
    tl = _long_timeline()
    t0 = time.perf_counter()
    for k in range(2000):
        cheap.persons_at(tl, k * 1.7)
        cheap.coco_evidence(tl, k * 1.7, ("cup",), window=1.0)
    assert time.perf_counter() - t0 < 1.0
    assert cheap.persons_at(tl, 10.1)[0][:4] == [0.45, 0.40, 0.55, 0.70]
    assert cheap.persons_at(tl, 1e9, window=1.0) == []


def test_door_reference_one_hour_bounded():
    tl = _long_timeline()
    img = np.full((54, 96, 3), 120, np.uint8)
    reads = []

    def read(t):
        reads.append(t)
        return img

    t0 = time.perf_counter()
    ref = runner.door_reference(read, tl, [0.0, 0.1, 0.25, 0.8])
    assert time.perf_counter() - t0 < 2.0
    assert len(ref["candidates"]) <= runner.DOOR_REF_CANDIDATES and len(reads) <= runner.DOOR_REF_CANDIDATES
