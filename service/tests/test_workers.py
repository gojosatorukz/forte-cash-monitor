"""JOB_WORKERS: several job threads on one FIFO queue, one analyzer instance per thread.

Timing is generous (the test machine is often loaded); parallelism is asserted from recorded
analyzer intervals, not from wall-clock tightness alone.
"""

from __future__ import annotations

import contextlib
import itertools
import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.clips as clips
from app.config import Settings
from app.jobs import JobRecord
from app.main import create_app
from app.models import AnalysisResult, Violation
from app.rules import BUNDLED_RULES_PATH

KEY = "test-key-0123456789abcdef"
HDR = {"X-API-Key": KEY}


def make_settings(tmp_path: Path, **kw) -> Settings:
    base = dict(api_key=KEY, data_dir=tmp_path / "data", job_timeout_sec=30.0, stub_debug=False,
                rules_path=str(BUNDLED_RULES_PATH), ui_password="test-ui-pass")
    base.update(kw)
    return Settings(**base)


@pytest.fixture(autouse=True)
def _reset_clip_parallel():
    yield
    clips.configure_parallel(clips.MAX_WORKERS)  # the clip semaphore is process-wide


@contextlib.contextmanager
def client_for(tmp_path: Path, factory, **kw):
    app = create_app(make_settings(tmp_path, **kw), analyzer_factory=factory)
    with TestClient(app) as c:
        yield c


def upload(c: TestClient, video: Path, name: str) -> str:
    with open(video, "rb") as f:
        r = c.post("/api/v1/analyze", files={"file": (name, f, "video/quicktime")}, headers=HDR)
    assert r.status_code == 200, r.text
    return r.json()["job_id"]


def wait_all(c: TestClient, job_ids: list[str], timeout: float) -> dict[str, tuple[float, dict]]:
    """job_id -> (monotonic time first seen finished, body)."""
    done: dict[str, tuple[float, dict]] = {}
    end = time.monotonic() + timeout
    while len(done) < len(job_ids) and time.monotonic() < end:
        for jid in job_ids:
            if jid not in done:
                b = c.get(f"/api/v1/jobs/{jid}", headers=HDR).json()
                if b["status"] != "processing":
                    done[jid] = (time.monotonic(), b)
        time.sleep(0.02)
    assert len(done) == len(job_ids), f"{len(job_ids) - len(done)} job(s) still processing after {timeout}s"
    return done


def wait_for(pred, timeout: float = 15.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


def max_overlap(intervals: list[tuple[float, float]]) -> int:
    events = sorted([(s, 1) for s, _ in intervals] + [(e, -1) for _, e in intervals],
                    key=lambda x: (x[0], x[1]))  # an end at the same instant closes first
    cur = best = 0
    for _, d in events:
        cur += d
        best = max(best, cur)
    return best


class Recorder:
    def __init__(self):
        self.lock = threading.Lock()
        self.created: list[tuple[int, str]] = []  # (id(analyzer), creating thread name)
        self.runs: list[dict] = []
        self.process_calls: list[tuple[str, str]] = []  # (job_id, worker thread) in dequeue order


class SleepyAnalyzer:
    """Sleeps `sleep` s, returns one violation tagged with the video's file name."""

    name = "sleepy"

    def __init__(self, rec: Recorder, sleep: float = 1.0, n_violations: int = 1):
        self.rec, self.sleep, self.n = rec, sleep, n_violations
        with rec.lock:
            rec.created.append((id(self), threading.current_thread().name))

    def analyze(self, video_path, progress_cb, deadline, partial):
        start = time.monotonic()
        progress_cb(0.5, "sleepy")
        time.sleep(self.sleep)
        end = time.monotonic()
        with self.rec.lock:
            self.rec.runs.append({"file": Path(video_path).name, "analyzer": id(self), "start": start, "end": end})
        return AnalysisResult(violations=[
            Violation(type="X_TEST", timecode_sec=0.5 + k, description=f"from {Path(video_path).name}")
            for k in range(self.n)])


def record_process(worker, rec: Recorder) -> None:
    """Wrap the worker's _process to log dequeue order and the worker thread per job."""
    orig = worker._process

    def wrapped(job_id, submitted, slot):
        with rec.lock:
            rec.process_calls.append((job_id, threading.current_thread().name))
        return orig(job_id, submitted, slot)

    worker._process = wrapped


def test_job_workers_setting_clamped(monkeypatch):
    for raw, want in (("", 1), ("3", 3), ("0", 1), ("-2", 1), ("99", 8), ("abc", 1)):
        monkeypatch.setenv("JOB_WORKERS", raw)
        assert Settings.from_env().job_workers == want, raw
    monkeypatch.delenv("JOB_WORKERS")
    assert Settings.from_env().job_workers == 1
    assert Settings.from_env().clip_max_parallel == 4
    monkeypatch.setenv("CLIP_MAX_PARALLEL", "0")
    assert Settings.from_env().clip_max_parallel == 1
    assert Settings.from_env().analyzer_load_timeout_sec == 240.0
    monkeypatch.setenv("ANALYZER_LOAD_TIMEOUT_SEC", "90")
    assert Settings.from_env().analyzer_load_timeout_sec == 90.0
    monkeypatch.setenv("ANALYZER_LOAD_TIMEOUT_SEC", "-1")
    assert Settings.from_env().analyzer_load_timeout_sec == 240.0


def test_three_workers_run_jobs_in_parallel(tmp_path, test_video):
    rec = Recorder()
    with client_for(tmp_path, lambda: SleepyAnalyzer(rec, 1.0), job_workers=3) as c:
        worker = c.app.state.worker
        wait_for(lambda: worker.status()["ready"] == 3)
        assert worker.status() == {"configured": 3, "ready": 3, "busy": 0}
        record_process(worker, rec)
        names = [f"job{i}.mov" for i in range(6)]
        t0 = time.monotonic()
        ids = [upload(c, test_video, n) for n in names]
        done = wait_all(c, ids, timeout=60)
        wall = max(t for t, _ in done.values()) - t0
        store = c.app.state.store
        jobs_dir = c.app.state.settings.jobs_dir
        health = c.get("/healthz").json()

    # all done, each with its own filename and its own violation (no cross-contamination)
    for jid, name in zip(ids, names):
        b = done[jid][1]
        assert b["status"] == "done" and b["verdict"] == "violation", b
        assert b["filename"] == name
        assert [v["description"] for v in b["violations"]] == [f"from {name}"]
    # dequeue (start) order == submit order
    assert [j for j, _ in rec.process_calls] == ids
    # parallelism from recorded analyzer intervals
    intervals = [(r["start"], r["end"]) for r in rec.runs]
    assert len(intervals) == 6
    assert 2 <= max_overlap(intervals) <= 3, intervals
    span = max(e for _, e in intervals) - min(s for s, _ in intervals)
    busy = sum(e - s for s, e in intervals)
    assert span < 0.8 * busy, (span, busy)  # serial execution would give span >= busy
    assert wall < 6 * 1.0 + 10, wall  # generous; the overlap assertions carry the real check
    # one analyzer per worker thread, each created in that thread, never shared
    assert len(rec.created) == 3
    assert len({a for a, _ in rec.created}) == 3
    assert sorted(t for _, t in rec.created) == ["job-worker-0", "job-worker-1", "job-worker-2"]
    owner = {a: t for a, t in rec.created}
    thread_of_job = dict(rec.process_calls)
    file_to_job = dict(zip(names, ids))
    for r in rec.runs:
        assert owner[r["analyzer"]] == thread_of_job[file_to_job[r["file"]]]
    assert len({t for _, t in rec.process_calls}) >= 2
    # healthz
    assert health["analyzer_ready"] is True and health["analyzer_error"] is None
    assert health["workers"]["configured"] == 3 and health["workers"]["ready"] == 3
    # store safety: every job file is valid JSON matching memory, no temp leftovers
    assert not list(jobs_dir.glob("*.tmp")) and not list(jobs_dir.glob(".*"))
    for jid in ids:
        raw = json.loads((jobs_dir / f"{jid}.json").read_text(encoding="utf-8"))
        raw.pop("seq", None)
        disk = JobRecord.model_validate(raw)
        assert disk.model_dump() == store.get(jid).model_dump()


def test_one_analyzer_fails_others_serve_jobs(tmp_path, test_video):
    rec = Recorder()
    calls = itertools.count()

    def factory():
        if next(calls) == 1:
            raise RuntimeError("CUDA out of memory (slot 1)")
        return SleepyAnalyzer(rec, 0.3)

    with client_for(tmp_path, factory, job_workers=3) as c:
        worker = c.app.state.worker
        wait_for(lambda: worker.status()["ready"] == 2 and worker.load_error is not None)
        record_process(worker, rec)
        ids = [upload(c, test_video, f"v{i}.mov") for i in range(4)]
        done = wait_all(c, ids, timeout=60)
        health = c.get("/healthz").json()
    assert all(b["status"] == "done" for _, b in done.values()), done
    assert health["workers"] == {"configured": 3, "ready": 2, "busy": 0}
    assert health["analyzer_ready"] is True
    assert "CUDA out of memory" in health["analyzer_error"]
    assert "job-worker-1" not in {t for _, t in rec.process_calls}  # the failed slot took no job


def test_all_analyzers_fail_jobs_fail_fast(tmp_path, test_video):
    def factory():
        raise RuntimeError("no gpu here")

    with client_for(tmp_path, factory, job_workers=3) as c:
        worker = c.app.state.worker
        assert worker.ready.wait(15)
        t0 = time.monotonic()
        ids = [upload(c, test_video, f"v{i}.mov") for i in range(3)]
        done = wait_all(c, ids, timeout=20)
        elapsed = time.monotonic() - t0
        health = c.get("/healthz").json()
    for _, b in done.values():
        assert b["status"] == "failed"
        assert "Анализатор недоступен" in b["error"] and "no gpu here" in b["error"]
    assert elapsed < 15, elapsed
    assert health["analyzer_ready"] is False
    assert health["workers"]["ready"] == 0 and health["workers"]["configured"] == 3
    assert "no gpu here" in health["analyzer_error"]


class DeadlineIgnoringAnalyzer:
    name = "ignorer"

    def analyze(self, video_path, progress_cb, deadline, partial):
        partial.add(Violation(type="T1", timecode_sec=1.0))
        time.sleep(4.0)
        return AnalysisResult()


def test_deadline_holds_under_contention(tmp_path, test_video):
    cap = 2.5
    with client_for(tmp_path, DeadlineIgnoringAnalyzer, job_workers=2,
                    job_timeout_sec=1.0, job_hard_cap_sec=cap) as c:
        worker = c.app.state.worker
        wait_for(lambda: worker.status()["ready"] == 2)
        submitted = {}
        for i in range(3):
            jid = upload(c, test_video, f"v{i}.mov")
            submitted[jid] = time.monotonic()
        done = wait_all(c, list(submitted), timeout=20)
    for jid, (t, b) in done.items():
        assert t - submitted[jid] <= cap + 1.5, (jid, t - submitted[jid])
        assert b["status"] == "done" and b["partial"] is True


def test_clip_semaphore_bounds_ffmpeg_across_workers(tmp_path, test_video, monkeypatch):
    rec = Recorder()
    lock = threading.Lock()
    cuts: list[tuple[float, float]] = []

    def fake_cut(src, dst, start, length, rotation_deg, deadline):
        t = time.monotonic()
        time.sleep(0.2)
        Path(dst).write_bytes(b"fake mp4")
        with lock:
            cuts.append((t, time.monotonic()))

    monkeypatch.setattr(clips, "cut_clip", fake_cut)
    with client_for(tmp_path, lambda: SleepyAnalyzer(rec, 0.2, n_violations=3),
                    job_workers=2, clip_max_parallel=1) as c:
        worker = c.app.state.worker
        wait_for(lambda: worker.status()["ready"] == 2)
        assert clips.max_parallel() == 1
        ids = [upload(c, test_video, f"v{i}.mov") for i in range(2)]
        done = wait_all(c, ids, timeout=60)
    assert len(cuts) == 6
    assert max_overlap(cuts) == 1, cuts
    for _, b in done.values():
        assert b["status"] == "done" and all(v.get("clip_url") for v in b["violations"]), b


def test_hung_analyzer_load_times_out_then_recovers(tmp_path, test_video):
    """M-1: a load that never returns must not leave jobs `processing` forever."""
    rec = Recorder()
    release = threading.Event()

    def factory():
        assert release.wait(60), "test never released the loader"
        return SleepyAnalyzer(rec, 0.2)

    timeout = 1.5
    try:
        with client_for(tmp_path, factory, job_workers=2, analyzer_load_timeout_sec=timeout) as c:
            worker = c.app.state.worker
            t0 = time.monotonic()
            jid = upload(c, test_video, "early.mov")
            done = wait_all(c, [jid], timeout=timeout + 8)
            body = done[jid][1]
            assert done[jid][0] - t0 < timeout + 5, done[jid][0] - t0
            assert body["status"] == "failed", body
            assert body["error"] == "Анализатор недоступен: загрузка модели не завершилась за 1.5 с"
            health = c.get("/healthz").json()
            assert health["analyzer_ready"] is False
            assert "загрузка модели не завершилась за 1.5 с" in health["analyzer_error"]
            assert worker.ready.is_set()

            release.set()
            wait_for(lambda: worker.status()["ready"] == 2)
            ids = [upload(c, test_video, f"late{i}.mov") for i in range(3)]
            done = wait_all(c, ids, timeout=30)
            health = c.get("/healthz").json()
    finally:
        release.set()
    for _, b in done.values():
        assert b["status"] == "done" and b["verdict"] == "violation", b
    assert health["analyzer_ready"] is True and health["analyzer_error"] is None


def test_shutdown_during_loading_leaves_queued_jobs_for_recovery(tmp_path, test_video):
    """M-2: a slot that skips loading because the pool is stopping must not fail queued jobs."""
    from app.jobs import JobStore
    from app.rules import load_rules
    from app.worker import Worker

    release = threading.Event()
    calls = itertools.count()

    def factory():
        n = next(calls)
        if n == 0:
            release.wait(30)
            raise RuntimeError("load interrupted")
        return SleepyAnalyzer(Recorder(), 0.1)

    settings = make_settings(tmp_path, job_workers=2, analyzer_load_timeout_sec=60.0)
    store = JobStore(settings.jobs_dir)
    worker = Worker(store, load_rules(BUNDLED_RULES_PATH), settings, analyzer_factory=factory)
    worker.start()
    try:
        job = store.create("v.mov", str(test_video))
        worker.submit(job.job_id)
        stopper = threading.Thread(target=worker.stop, kwargs={"timeout": 15.0})
        stopper.start()
        wait_for(worker._stopping.is_set)
        release.set()  # slot 0 fails while stopping; slot 1 (last, none ready) skips loading
        stopper.join(20)
    finally:
        release.set()
    assert not stopper.is_alive()
    assert all(not s.thread.is_alive() for s in worker._slots)
    assert next(calls) == 1  # only slot 0 ever tried to load
    rec = store.get(job.job_id)
    assert rec.status == "processing" and rec.error is None, rec


def test_failed_job_error_hides_absolute_paths():
    from app.worker import sanitize_paths
    assert sanitize_paths("No such file: '/home/kanat/forte/service/data/uploads/abc/Х-17.mov'") == \
        "No such file: 'Х-17.mov'"
    assert sanitize_paths(r"open C:\Users\k\data\x.mov failed") == "open x.mov failed"
    assert sanitize_paths("see http://host/a/b") == "see http://host/a/b"
