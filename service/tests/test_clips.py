"""Evidence clips: window math, ffmpeg encoding/rotation, budget, routes."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from app import clips
from app.clips import (WARN_CLIPS_FAILED, WARN_CLIPS_TIME, clip_order, clip_window, generate_clips,
                       normalize_rotation, video_filter)
from app.models import AnalysisResult, Violation
from tests.test_api import HDR, client_for, upload, wait_done


def ffprobe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, check=True).stdout
    return json.loads(out)


def video_streams(info: dict) -> list[dict]:
    return [s for s in info["streams"] if s["codec_type"] == "video"]


# ---------- window math ----------

def test_window_pads_two_seconds():
    assert clip_window(10.0, None, None, 60.0) == (8.0, 4.0)  # [tc-2, tc+2]
    assert clip_window(10.0, 9.0, 11.0, 60.0) == (7.0, 6.0)


def test_window_long_event_is_centred_on_timecode():
    assert clip_window(30.0, 10.0, 50.0, 100.0) == (26.0, 8.0)


def test_window_min_length():
    # a [s-2, e+2] window is >= 4 s anyway; a reversed end falls back to start
    assert clip_window(5.0, 5.0, 1.0, 60.0) == (3.0, 4.0)
    a, length = clip_window(5.0, 5.0, 5.0, 60.0)
    assert length >= clips.CLIP_MIN_SEC


def test_window_clamped_to_video_bounds():
    assert clip_window(0.5, None, None, 60.0) == (0.0, 4.0)  # shifted right, length kept
    assert clip_window(59.5, None, None, 60.0) == (56.0, 4.0)  # shifted left
    assert clip_window(1.0, None, None, 2.5) == (0.0, 2.5)  # shorter video: whole video
    assert clip_window(100.0, None, None, 60.0) == (56.0, 4.0)
    assert clip_window(1.0, None, None, None) == (0.0, 4.0)  # unknown duration: only >= 0


def test_window_rejects_bad_timecode():
    assert clip_window(float("nan")) is None
    assert clip_window(None) is None  # type: ignore[arg-type]


def test_rotation_normalization_and_filters():
    assert [normalize_rotation(v) for v in (None, 0, 90, "180", 270, -90, 450, 45, "x", float("nan"))] == \
        [0, 0, 90, 180, 270, 270, 90, 0, 0, 0]
    assert video_filter(0).startswith("scale=")
    assert video_filter(90).startswith("transpose=1,")
    assert video_filter(270).startswith("transpose=2,")
    assert video_filter(180).startswith("hflip,vflip,")


# ---------- encoding ----------

def test_generate_clip_h264_no_audio(tmp_path, test_video):
    v = {"timecode_sec": 1.5, "start_sec": 1.5, "end_sec": None}
    made, notes = generate_clips(test_video, [v], tmp_path / "c", time.monotonic() + 60, duration_sec=3.0)
    assert notes == [] and set(made) == {1}
    info = ffprobe(made[1])
    vs = video_streams(info)
    assert len(vs) == 1 and vs[0]["codec_name"] == "h264" and vs[0]["pix_fmt"] == "yuv420p"
    assert not [s for s in info["streams"] if s["codec_type"] == "audio"]
    assert (vs[0]["width"], vs[0]["height"]) == (320, 240)  # never upscaled
    assert 2.5 <= float(info["format"]["duration"]) <= 3.5  # whole 3 s video
    assert not list((tmp_path / "c").glob(".*"))  # no temp files left


def test_rotation_90_swaps_dimensions(tmp_path, test_video):
    v = {"timecode_sec": 1.0}
    made, _ = generate_clips(test_video, [v], tmp_path / "c", time.monotonic() + 60, duration_sec=3.0,
                             rotation_deg=90)
    vs = video_streams(ffprobe(made[1]))[0]
    assert (vs["width"], vs["height"]) == (240, 320)


def test_tiny_budget_skips_all_clips(tmp_path, test_video):
    t0 = time.monotonic()
    made, notes = generate_clips(test_video, [{"timecode_sec": 1.0}] * 3, tmp_path / "c", t0 + 1.0)
    assert made == {} and notes == [WARN_CLIPS_TIME.format(nums="1, 2, 3")]
    assert time.monotonic() - t0 < 1.0


def test_failed_clip_warns_and_never_raises(tmp_path):
    missing = tmp_path / "nope.mov"
    made, notes = generate_clips(missing, [{"timecode_sec": 1.0}, {"timecode_sec": 2.0}], tmp_path / "c",
                                 time.monotonic() + 30)
    assert made == {} and notes == [WARN_CLIPS_FAILED.format(nums="1, 2")]


def fake_tool(tmp_path: Path, name: str, body: str) -> str:
    p = tmp_path / name
    p.write_text("#!/usr/bin/env bash\n" + body + "\n")
    p.chmod(0o755)
    return str(p)


def test_hung_ffmpeg_killed_before_deadline(tmp_path, test_video, monkeypatch):
    monkeypatch.setattr(clips, "FFMPEG", fake_tool(tmp_path, "ffmpeg", "exec sleep 30"))
    t0 = time.monotonic()
    made, notes = generate_clips(test_video, [{"timecode_sec": 1.0}] * 2, tmp_path / "c", t0 + 3.5)
    assert made == {} and notes == [WARN_CLIPS_TIME.format(nums="1, 2")]
    assert time.monotonic() - t0 < 3.5 + 0.5  # killed at deadline - 1 s; generous on a loaded machine


def test_clip_not_started_below_min_timeout(tmp_path, test_video):
    t0 = time.monotonic()
    # 2.4 s left: passes the 2 s skip-all gate, but ffmpeg would get < 1.5 s -> not started
    made, notes = generate_clips(test_video, [{"timecode_sec": 1.0}], tmp_path / "c", t0 + 2.4)
    assert made == {} and notes == [WARN_CLIPS_TIME.format(nums="1")]
    assert time.monotonic() - t0 < 1.0


def test_zero_frame_clip_is_dropped_as_failed(tmp_path, test_video, monkeypatch):
    # "ffmpeg" exits 0 but writes junk: the ffprobe frame check must reject it
    monkeypatch.setattr(clips, "FFMPEG", fake_tool(
        tmp_path, "ffmpeg", 'for a; do last="$a"; done; echo junk > "$last"'))
    out = tmp_path / "c"
    made, notes = generate_clips(test_video, [{"timecode_sec": 1.0}], out, time.monotonic() + 30)
    assert made == {} and notes == [WARN_CLIPS_FAILED.format(nums="1")]
    assert list(out.iterdir()) == []


def test_clip_dir_cleared_even_without_violations(tmp_path, test_video):
    out = tmp_path / "c"
    out.mkdir()
    (out / "1.mp4").write_bytes(b"stale")
    assert generate_clips(test_video, [], out, time.monotonic() + 30) == ({}, [])
    assert not out.exists()


def test_clip_order_by_priority_then_timecode():
    vs = [{"timecode_sec": 5.0, "fact_conf": 0.4},
          {"timecode_sec": 9.0, "fact_conf": 0.9},
          {"timecode_sec": 1.0, "confidence": 0.4},
          {"timecode_sec": 0.5},
          {"timecode_sec": 7.0, "fact_conf": 0.9}]
    assert clip_order(vs) == [5, 2, 3, 1, 4]


# ---------- through the API ----------

def test_stub_debug_job_produces_clip(tmp_path, test_video):
    with client_for(tmp_path, stub_debug=True) as c:
        jid = upload(c, test_video).json()["job_id"]
        body = wait_done(c, jid, route="/api/v1/jobs/", timeout=60)
        assert body["status"] == "done"
        v = body["violations"][0]
        assert v["clip_url"] == f"/api/v1/jobs/{jid}/clips/1.mp4"
        path = tmp_path / "data" / "clips" / jid / "1.mp4"
        info = ffprobe(path)
        vs = video_streams(info)
        assert vs[0]["codec_name"] == "h264" and vs[0]["height"] <= 480
        assert not [s for s in info["streams"] if s["codec_type"] == "audio"]
        assert 3.0 - 0.1 <= float(info["format"]["duration"]) <= 8.0 + 0.1
        assert body["warnings"] == []
        # a stray file without a clip_url on the job record is never served
        (path.parent / "2.mp4").write_bytes(path.read_bytes())

        # routes: key, Range, HEAD, 404s, traversal
        url = v["clip_url"]
        assert c.get(url).status_code == 401
        r = c.get(url, headers=HDR)
        assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
        assert r.content == path.read_bytes()
        r = c.get(url, headers={**HDR, "Range": "bytes=0-99"})
        assert r.status_code == 206 and len(r.content) == 100
        assert r.headers["content-range"].startswith("bytes 0-99/")
        assert c.head(url, headers=HDR).status_code == 200
        ui = c.get(f"/ui-api/jobs/{jid}").json()["violations"][0]["clip_url"]
        assert ui == f"/ui-api/jobs/{jid}/clips/1.mp4"
        r = c.get(ui, headers={"Range": "bytes=10-19"})
        assert r.status_code == 206 and len(r.content) == 10
        for bad in ("2.mp4", "0.mp4", "-1.mp4", "01.mp4", "1.mp4x", "1.MP4", "x.mp4",
                    "..%2F..%2Fjobs%2F" + jid + ".json", "%2E%2E%2F1.mp4"):
            assert c.get(f"/api/v1/jobs/{jid}/clips/{bad}", headers=HDR).status_code == 404, bad
            assert c.get(f"/ui-api/jobs/{jid}/clips/{bad}").status_code == 404, bad
        assert c.get("/api/v1/jobs/nope/clips/1.mp4", headers=HDR).status_code == 404
        assert c.get("/ui-api/jobs/nope/clips/1.mp4").status_code == 404
        assert c.get("/api/v1/jobs/nope/clips/1.mp4").status_code == 401


class RotatedAnalyzer:
    name = "rotated"

    def analyze(self, video_path, progress_cb, deadline, partial):
        return AnalysisResult(violations=[Violation(type="T1", timecode_sec=1.0)],
                              meta={"applied_rotation_deg": 90})


def test_applied_rotation_meta_rotates_clip(tmp_path, test_video):
    with client_for(tmp_path, analyzer=RotatedAnalyzer()) as c:
        jid = upload(c, test_video).json()["job_id"]
        body = wait_done(c, jid, timeout=60)
        assert body["violations"][0]["clip_url"]
        vs = video_streams(ffprobe(tmp_path / "data" / "clips" / jid / "1.mp4"))[0]
        assert (vs["width"], vs["height"]) == (240, 320)


def test_tiny_budget_job_done_with_clip_warning(tmp_path, test_video):
    cap = 1.5
    with client_for(tmp_path, stub_debug=True, job_timeout_sec=1.0, job_hard_cap_sec=cap) as c:
        t0 = time.monotonic()
        jid = upload(c, test_video).json()["job_id"]
        body = wait_done(c, jid, route="/api/v1/jobs/", timeout=30)
        elapsed = time.monotonic() - t0
        assert body["status"] == "done" and body["verdict"] == "violation"
        assert "clip_url" not in body["violations"][0]
        assert WARN_CLIPS_TIME.format(nums="1") in body["warnings"]
        assert elapsed <= cap + 2.0, elapsed  # generous: loaded machine
        assert not (tmp_path / "data" / "clips" / jid / "1.mp4").exists()


class DeadlineAnalyzer:
    """Real-detector-like timing: works until its deadline, then returns (or overruns)."""
    name = "deadline"

    def __init__(self, overrun: float = 0.0):
        self.overrun = overrun

    def analyze(self, video_path, progress_cb, deadline, partial):
        time.sleep(max(0.0, deadline - time.monotonic()) + self.overrun)
        return AnalysisResult(violations=[Violation(type="T1", timecode_sec=1.0, fact_conf=0.6),
                                          Violation(type="T2", timecode_sec=2.0, fact_conf=0.9)])


def test_analyzer_using_whole_budget_still_gets_clips(tmp_path, test_video):
    # timeout 3 s -> the analyzer returns right at its deadline (~2.7 s, 0.3 s before the hard
    # deadline, which alone would be too little for clips); clip deadline = 8 - 2 = 6 s
    cap = 8.0
    with client_for(tmp_path, analyzer=DeadlineAnalyzer(), job_timeout_sec=3.0, job_hard_cap_sec=cap) as c:
        t0 = time.monotonic()
        jid = upload(c, test_video).json()["job_id"]
        body = wait_done(c, jid, route="/api/v1/jobs/", timeout=30)
        elapsed = time.monotonic() - t0
        assert body["status"] == "done" and body["verdict"] == "violation"
        assert elapsed <= cap + 1.0, elapsed  # never past submit + cap (slack: polling, loaded machine)
        assert [v.get("clip_url") for v in body["violations"]] == [
            f"/api/v1/jobs/{jid}/clips/1.mp4", f"/api/v1/jobs/{jid}/clips/2.mp4"]
        assert body["warnings"] == []


class PartialOverrunAnalyzer:
    """Adds a violation to the partial sink, then ignores the deadline entirely."""
    name = "partial-overrun"

    def analyze(self, video_path, progress_cb, deadline, partial):
        partial.add(Violation(type="T1", timecode_sec=1.0))
        time.sleep(30)


def test_overrunning_analyzer_clips_within_cap(tmp_path, test_video):
    cap = 7.0
    with client_for(tmp_path, analyzer=PartialOverrunAnalyzer(), job_timeout_sec=2.0, job_hard_cap_sec=cap) as c:
        t0 = time.monotonic()
        jid = upload(c, test_video).json()["job_id"]
        body = wait_done(c, jid, route="/api/v1/jobs/", timeout=30)
        elapsed = time.monotonic() - t0
        assert body["status"] == "done" and body["partial"] is True
        assert elapsed <= cap + 1.0, elapsed
        # hard deadline ~2 s, clip deadline 5 s: the clip is cut after the overrun
        assert body["violations"][0]["clip_url"] == f"/api/v1/jobs/{jid}/clips/1.mp4"


def test_zero_violation_job_has_no_clip_warning(tmp_path, test_video):
    with client_for(tmp_path, job_timeout_sec=1.0, job_hard_cap_sec=1.5) as c:
        body = wait_done(c, upload(c, test_video).json()["job_id"], route="/api/v1/jobs/", timeout=30)
        assert body["status"] == "done" and body["violations"] == []
        assert not [w for w in body["warnings"] if "клип" in w]


def test_global_slot_wait_counts_against_the_clip_deadline(tmp_path, test_video, monkeypatch):
    """All ffmpeg slots busy (another job): the clip waits only while it could still be cut,
    then is reported as skipped for time - never past the deadline."""
    called = []
    monkeypatch.setattr(clips, "cut_clip", lambda *a, **k: called.append(a))
    clips.configure_parallel(1)
    try:
        assert clips._slots.acquire(timeout=1)  # held by "another job"
        try:
            deadline = time.monotonic() + 4.0
            vs = [{"timecode_sec": 1.0}, {"timecode_sec": 2.0}]
            made, notes = generate_clips(test_video, vs, tmp_path / "c", deadline, duration_sec=3.0)
            assert time.monotonic() <= deadline
        finally:
            clips._slots.release()
        assert made == {} and called == []
        assert notes == [WARN_CLIPS_TIME.format(nums="1, 2")]
    finally:
        clips.configure_parallel(clips.MAX_WORKERS)
