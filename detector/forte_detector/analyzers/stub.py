"""Stub analyzer: exercises the decode path at 1 fps, always reports one T99 at mid-video."""
from __future__ import annotations

import time
from pathlib import Path

from forte_detector.schema import VERDICT_VIOLATION, AnalysisResult, Event, ProgressCb, Transform
from forte_detector.video_io import iter_frames


def analyze(
    video_path: str,
    progress_cb: ProgressCb | None = None,
    *,
    transform: Transform | None = None,
) -> AnalysisResult:
    t0 = time.perf_counter()
    if progress_cb:
        progress_cb(0.0, "decode")
    n, last_t, shape = 0, 0.0, None
    for t, frame in iter_frames(video_path, fps=1, transform=transform):
        n, last_t, shape = n + 1, t, frame.shape
    decode_sec = time.perf_counter() - t0
    mid = round(last_t / 2, 2)
    event = Event(
        type="T99",
        timecode_sec=mid,
        confidence=0.5,
        description="Заглушка: событие в середине видео (анализ не выполнялся).",
        evidence_ts=[mid],
        source="stub",
    )
    if progress_cb:
        progress_cb(1.0, "done")
    return AnalysisResult(
        video=Path(video_path).name,
        verdict=VERDICT_VIOLATION,
        events=[event],
        timeline={"n_frames": n, "last_t": last_t, "frame_shape": list(shape) if shape else None},
        runtime_sec=round(time.perf_counter() - t0, 3),
        stage_times={"decode": round(decode_sec, 3)},
    )
