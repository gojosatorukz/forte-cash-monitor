"""Video decoding via PyAV: sequential sampling at a target fps + single-frame reads."""
from __future__ import annotations

from collections.abc import Iterator

import av
import cv2
import numpy as np

from forte_detector.schema import Transform


def _frame_times(container, stream, t0: float = 0.0) -> Iterator[tuple[float, av.VideoFrame]]:
    """Yield (t_sec relative to stream start, frame) for every decoded frame.

    Frames without pts fall back to t0 + i / rate, where t0 is where decoding started (0 for a
    sequential decode, the seek target after a seek - the keyframe actually reached is unknown, so
    the result is accurate to one keyframe interval)."""
    start = stream.start_time or 0
    tb = stream.time_base
    rate = float(stream.average_rate or 30)
    for i, frame in enumerate(container.decode(stream)):
        t = float((frame.pts - start) * tb) if frame.pts is not None else t0 + i / rate
        yield t, frame


def _to_bgr(frame: av.VideoFrame, max_side: int | None, transform: Transform | None) -> np.ndarray:
    img = frame.to_ndarray(format="bgr24")
    if transform is not None:
        img = transform(img)
    if max_side:
        h, w = img.shape[:2]
        scale = max_side / max(h, w)
        if scale < 1:
            img = cv2.resize(img, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(img)


def iter_frames(
    path: str,
    fps: float,
    max_side: int | None = None,
    transform: Transform | None = None,
) -> Iterator[tuple[float, np.ndarray]]:
    """Decode sequentially, yielding (t_sec, frame_bgr) for the frame nearest each k/fps.

    No seeking; frames that are not kept are never converted to ndarray. A source frame is
    yielded at most once (when fps exceeds the source rate). `transform` is applied to each
    kept full-resolution frame, then the result is downscaled so max(h, w) <= max_side.
    """
    step = 1.0 / fps
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        k = 0
        prev: tuple[float, av.VideoFrame] | None = None
        last_emitted = None
        for t, frame in _frame_times(container, stream):
            while t >= k * step - 1e-9:
                target = k * step
                k += 1
                cand_t, cand = t, frame
                if prev is not None and target - prev[0] < t - target:
                    cand_t, cand = prev
                if cand is last_emitted:
                    continue
                last_emitted = cand
                yield cand_t, _to_bgr(cand, max_side, transform)
            prev = (t, frame)


def video_duration(path: str) -> float:
    """Container/stream duration in seconds (0.0 when unknown or unreadable; never raises)."""
    try:
        with av.open(str(path)) as c:
            st = c.streams.video[0]
            if st.duration:
                return float(st.duration * st.time_base)
            return float(c.duration or 0) / 1e6
    except Exception:  # noqa: BLE001 - best effort
        return 0.0


def read_frame_at(path: str, t: float, transform: Transform | None = None) -> np.ndarray:
    """Return the BGR frame nearest to t seconds (last frame if t is past the end)."""
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        start = stream.start_time or 0
        container.seek(start + int(max(t, 0) / stream.time_base), stream=stream, backward=True)
        prev: tuple[float, av.VideoFrame] | None = None
        for ft, frame in _frame_times(container, stream, t0=max(t, 0.0)):
            if ft >= t:
                if prev is not None and t - prev[0] < ft - t:
                    frame = prev[1]
                return _to_bgr(frame, None, transform)
            prev = (ft, frame)
        if prev is None:
            raise ValueError(f"no frames decoded from {path}")
        return _to_bgr(prev[1], None, transform)
