"""Concurrent analyze() calls in one process (service JOB_WORKERS > 1): CPU-only, no model, no VLM.

3 threads x full.analyze on short synthetic clips share ONE detector (the real Detector class with a
mocked torch whose forward checks the weights did not move mid-forward and sometimes hits a CUDA OOM
-> in-place CPU fallback, moved back by ensure_device() at the next perceive) and ONE real VLMClient
(fake HTTP; slow answers; every request and health check of job A fails). Asserts: no exceptions /
device mismatch, job A's VLM failures do not degrade jobs B and C, results equal single-thread runs.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import types

import numpy as np
import pytest

from forte_detector.analyzers import full
from forte_detector.perception import detector as D
from forte_detector.perception import pipeline
from forte_detector.probes import openvocab, runner, vlm
from test_perception_smoke import _write_clip

JOB = threading.local()  # which test job the current thread runs ("A" = the failing one)


class OOM(RuntimeError):
    pass


class FakeModel:
    def to(self, device=None, dtype=None):
        time.sleep(0.002)  # a move takes a while: overlapping forwards would see it
        self.device = device
        return self


def make_shared_detector():
    torch = types.SimpleNamespace(float16="f16", float32="f32", OutOfMemoryError=OOM,
                                  inference_mode=lambda: _Null())
    torch.cuda = types.SimpleNamespace(is_available=lambda: True, empty_cache=lambda: None,
                                       reset_peak_memory_stats=lambda *a: None, max_memory_allocated=lambda *a: 0)
    det = D.Detector.__new__(D.Detector)
    det._lock = threading.RLock()
    det._torch, det._model, det.batch_size = torch, FakeModel(), 4
    det.model_id = "fake-detr"
    det.preferred_device = "cuda"
    det._to_device("cuda")
    det.n_forward = 0
    det.errors = []

    def forward(chunk):
        dev, dtype = det.device, det.dtype
        det.n_forward += 1
        if dev == "cuda" and det.n_forward % 5 == 0:
            raise OOM("CUDA out of memory (forward)")  # -> detect() moves the shared model to CPU
        out = []
        for f in chunk:
            time.sleep(0.001)
            if (det.device, det.dtype, det._model.device) != (dev, dtype, dev):
                det.errors.append("device mismatch")
                raise RuntimeError(f"device mismatch: forward on {dev}, weights now on {det._model.device}")
            ys, xs = np.nonzero(f[:, :, 1] > 200)
            h, w = f.shape[:2]
            out.append([[xs.min() / w, ys.min() / h, (xs.max() + 1) / w, (ys.max() + 1) / h, 0.9]] if len(xs) else [])
        return out, [None] * len(out)

    det._forward = forward
    det._decode = lambda persons, _bx: {"persons": persons, "objects": []}
    return det


class _Null:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOV:
    model_id, device = "fake-ov", "cpu"

    def detect(self, image, queries):
        h, w = image.shape[:2]
        return {"safe": (0.6, [0.7 * w, 0.2 * h, 0.9 * w, 0.5 * h]),
                "laptop": (0.5, [0.4 * w, 0.5 * h, 0.6 * w, 0.65 * h]),
                "desk": (0.5, [0.2 * w, 0.55 * h, 0.8 * w, 0.9 * h])}


class FakeServer:
    """llama-server stand-in: deterministic answer per request body, slow, job A always fails."""

    def __init__(self):
        self.lock = threading.Lock()
        self.calls: dict[str, int] = {}
        self.in_flight = self.max_in_flight = 0

    def post(self, url, body, timeout):
        job = getattr(JOB, "name", "?")
        with self.lock:
            self.calls[job] = self.calls.get(job, 0) + 1
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            time.sleep(0.01)
            if job == "A":
                JOB.failed_post = True
                raise TimeoutError("timed out")
            h = hashlib.sha1(json.dumps(body["messages"], sort_keys=True).encode()).digest()
            if "num_people" in body["grammar"]:
                return {"choices": [{"message": {"content": '{"num_people": %d}' % (h[0] % 3)}}]}
            ans = "yes" if h[0] % 2 else "no"
            return {"choices": [{"message": {"content": '{"answer": "%s"}' % ans}}]}
        finally:
            with self.lock:
                self.in_flight -= 1

    def get(self, url, timeout):
        # job A: the first availability check passes, health checks after its failed POSTs fail
        if getattr(JOB, "name", "?") == "A" and getattr(JOB, "failed_post", False):
            raise OSError("connection refused")
        return {"data": [{"id": "fake-vlm"}]}


def _walk(door_x):
    seat = np.array([0.45, 0.40, 0.55, 0.70])
    door = np.array([door_x, 0.30, door_x + 0.08, 0.60])

    def box_at(t):
        if t < 4:
            return seat
        if t < 6:
            return seat + (t - 4) / 2 * (door - seat)
        return np.array([0, 0, 0, 0])
    return box_at


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("FORTE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("FORTE_EVAL_CACHE", raising=False)
    clips = {}
    for name, door_x in (("A", 0.02), ("B", 0.85), ("C", 0.40)):
        p = tmp_path / f"{name}.mp4"
        _write_clip(p, 320, 240, 5, 9, _walk(door_x))
        clips[name] = str(p)
    det = make_shared_detector()
    server = FakeServer()
    client = vlm.VLMClient(url="http://fake/v1/chat/completions", post=server.post, get=server.get,
                           timeout=5.0, max_failures=2)
    import torch  # perceive() reads CUDA memory stats when the detector reports a CUDA device

    monkeypatch.setattr(torch.cuda, "reset_peak_memory_stats", lambda *a: None)
    monkeypatch.setattr(torch.cuda, "max_memory_allocated", lambda *a: 0)
    real_perceive = pipeline.perceive
    # no perception cache: every run (also the concurrent ones) really runs the shared detector
    monkeypatch.setattr(full, "perceive", lambda *a, **k: real_perceive(*a, **{**k, "use_cache": False}))
    monkeypatch.setattr(pipeline, "get_detector", lambda cfg, device: det)
    monkeypatch.setattr(openvocab, "get_detector", lambda: FakeOV())
    monkeypatch.setattr(runner, "get_client", lambda: client)
    return clips, det, server


def _run(name, path):
    JOB.name, JOB.failed_post = name, False
    r = full.analyze(path, deadline=time.monotonic() + 120)
    d = r.to_dict()
    cal = {k: v for k, v in (d["calibration"] or {}).items() if k != "meta"}
    return {"verdict": d["verdict"], "degraded": d["degraded"], "probes": d["probes"], "calibration": cal,
            "events": [(e["type"], e["timecode_sec"], e["confidence"]) for e in d["events"]],
            "probes_status": d["timeline"]["probes_status"]}


def test_three_concurrent_jobs_equal_single_thread_and_independent_vlm_state(setup):
    clips, det, server = setup
    single = {}
    for name, path in clips.items():  # sequential reference runs (fresh perception each time: no cache key reuse issue)
        single[name] = _run(name, path)
    assert server.calls.get("B", 0) > 0, "the synthetic clips must exercise the VLM"

    results, errors = {}, []

    def work(name):
        try:
            results[name] = _run(name, clips[name])
        except BaseException as e:  # noqa: BLE001
            errors.append((name, repr(e)))

    for _ in range(2):  # two rounds: one with a warm perception cache, one racing the same entries
        threads = [threading.Thread(target=work, args=(n,)) for n in clips]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        assert not errors, errors
        assert not det.errors
        for name in clips:
            assert results[name] == single[name], name

    # job A's VLM is down for job A only; B and C got their answers
    a, b, c = (single[n]["probes_status"] for n in "ABC")
    assert a["requested"] > 0 and a["answered"] == 0 and a["unavailable"]
    assert results["A"]["degraded"]
    for st, n in ((b, "B"), (c, "C")):
        if st["requested"]:
            assert st["answered"] > 0 and st["vlm_unavailable"] == 0 and not st["unavailable"], n
        assert not results[n]["degraded"], n
    assert det.n_forward > 0


def test_vlm_failure_state_is_per_thread():
    """A thread whose calls fail marks the VLM down for itself only."""
    server = FakeServer()
    c = vlm.VLMClient(url="http://fake/v1/chat/completions", post=server.post, get=server.get,
                      timeout=5.0, max_failures=2)
    img = np.zeros((32, 32, 3), np.uint8)
    out = {}
    barrier = threading.Barrier(2)

    def job(name):
        JOB.name, JOB.failed_post = name, False
        c.begin_batch()
        barrier.wait()
        out[name] = [c.yesno([img], f"q{i}")[0] for i in range(4)]
        out[name + "_avail"] = c.available()

    ts = [threading.Thread(target=job, args=(n,)) for n in "AB"]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert out["A"] == [None] * 4 and out["A_avail"] is False
    assert all(v is not None for v in out["B"]) and out["B_avail"] is True
    assert server.calls["A"] == 2  # down after 2 failures + a failed health check: no further POSTs


def test_vlm_slots_bound_concurrency_and_timeout_starts_after_slot(monkeypatch):
    monkeypatch.setenv("FORTE_VLM_SLOTS", "2")
    timeouts = []
    lock = threading.Lock()
    state = {"n": 0, "max": 0}

    def post(url, body, timeout):
        with lock:
            state["n"] += 1
            state["max"] = max(state["max"], state["n"])
            timeouts.append(timeout)
        time.sleep(0.2)
        with lock:
            state["n"] -= 1
        return {"choices": [{"message": {"content": '{"answer": "yes"}'}}]}

    c = vlm.VLMClient(url="http://fake/v1/chat/completions", post=post,
                      get=lambda u, t: {"data": [{"id": "m"}]}, timeout=5.0)
    img = np.zeros((32, 32, 3), np.uint8)
    deadline = time.monotonic() + 30
    ts = [threading.Thread(target=lambda: c.yesno([img], "q", deadline=deadline)) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert state["max"] == 2
    assert len(timeouts) == 6 and all(t == pytest.approx(5.0) for t in timeouts)  # waiting did not eat it


def test_vlm_slot_wait_is_bounded_by_the_deadline(monkeypatch):
    monkeypatch.setenv("FORTE_VLM_SLOTS", "1")
    release = threading.Event()

    def post(url, body, timeout):
        release.wait(5)
        return {"choices": [{"message": {"content": '{"answer": "yes"}'}}]}

    c = vlm.VLMClient(url="http://fake/v1/chat/completions", post=post,
                      get=lambda u, t: {"data": [{"id": "m"}]}, timeout=5.0)
    img = np.zeros((32, 32, 3), np.uint8)
    holder = threading.Thread(target=lambda: c.yesno([img], "q"))
    holder.start()
    time.sleep(0.05)
    t0 = time.monotonic()
    v, conf, raw = c.yesno([img], "q", deadline=time.monotonic() + 3.0)
    assert v is None and raw.get("skipped") == "deadline"
    assert time.monotonic() - t0 < 1.5  # gave up once less than min_call_s would be left
    release.set()
    holder.join(5)
