"""Video helpers built on ffmpeg/ffprobe subprocesses (argv lists -> safe with Cyrillic paths).

Never use cv2.VideoCapture/imread on file paths here: ffmpeg handles unicode paths and
rotation metadata reliably. ffmpeg autorotates by default, so decoded frames are upright
and their size matches the *displayed* width/height reported by `probe`.
"""

from __future__ import annotations

import base64
import json
import logging
import subprocess
import tempfile
from collections.abc import Iterator
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np

from app.models import VideoMeta

log = logging.getLogger(__name__)

FFMPEG = "ffmpeg"
STDERR_TAIL_BYTES = 2000
FFPROBE = "ffprobe"


class VideoError(RuntimeError):
    pass


def _parse_rate(value: str | None) -> float:
    if not value or value in ("0/0", "0"):
        return 0.0
    try:
        return float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return 0.0


def _rotation(stream: dict) -> int:
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                return int(round(float(sd["rotation"])))
            except (TypeError, ValueError):
                pass
    tag = (stream.get("tags") or {}).get("rotate")
    if tag is not None:
        try:
            return int(round(float(tag)))
        except ValueError:
            pass
    return 0


def probe(path: Path | str, timeout: float = 60.0) -> VideoMeta:
    path = Path(path)
    if not path.is_file():
        raise VideoError(f"file not found: {path}")
    cmd = [
        FFPROBE, "-v", "error", "-select_streams", "v:0",
        "-show_streams", "-show_format", "-of", "json", str(path.resolve()),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise VideoError(f"ffprobe timed out after {timeout:.1f} s") from e
    if proc.returncode != 0:
        raise VideoError(f"ffprobe failed: {proc.stderr.decode('utf-8', 'replace').strip()}")
    try:
        info = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise VideoError(f"ffprobe returned invalid JSON: {e}") from e
    streams = info.get("streams") or []
    if not streams:
        raise VideoError("no video stream found")
    st = streams[0]
    fmt = info.get("format") or {}

    fps = _parse_rate(st.get("avg_frame_rate")) or _parse_rate(st.get("r_frame_rate"))
    duration = 0.0
    for src in (st.get("duration"), fmt.get("duration")):
        try:
            duration = float(src)
            if duration > 0:
                break
        except (TypeError, ValueError):
            continue
    rotation = _rotation(st)
    w, h = int(st.get("width") or 0), int(st.get("height") or 0)
    if w <= 0 or h <= 0:
        raise VideoError("video stream has no dimensions")
    if abs(rotation) % 180 == 90:
        w, h = h, w
    frame_count: int | None
    try:
        frame_count = int(st["nb_frames"])
    except (KeyError, TypeError, ValueError):
        frame_count = int(round(duration * fps)) if duration and fps else None
    return VideoMeta(
        duration_sec=duration,
        fps=fps,
        width=w,
        height=h,
        rotation=rotation,
        codec=st.get("codec_name"),
        frame_count=frame_count,
    )


def _out_size(meta: VideoMeta, max_side: int | None) -> tuple[int, int]:
    w, h = meta.width, meta.height
    if max_side and max(w, h) > max_side:
        scale = max_side / max(w, h)
        w, h = max(1, round(w * scale)), max(1, round(h * scale))
    return w, h


def frame_at(
    path: Path | str, t_sec: float, max_side: int | None = 960, meta: VideoMeta | None = None
) -> np.ndarray:
    """Decode one upright BGR frame at `t_sec` (clamped into the video's duration)."""
    path = Path(path)
    meta = meta or probe(path)
    w, h = _out_size(meta, max_side)
    t = max(0.0, float(t_sec))
    if meta.duration_sec > 0:
        last = max(0.0, meta.duration_sec - (1.0 / meta.fps if meta.fps else 0.1))
        t = min(t, last)

    def _grab(ts: float) -> bytes:
        cmd = [
            FFMPEG, "-nostdin", "-v", "error", "-ss", f"{ts:.3f}", "-i", str(path.resolve()),
            "-frames:v", "1", "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-",
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=60)
        if proc.returncode != 0:
            raise VideoError(f"ffmpeg failed: {proc.stderr.decode('utf-8', 'replace').strip()}")
        return proc.stdout

    need = w * h * 3
    data = _grab(t)
    if len(data) < need and t > 0:
        # Seeking right at the tail can yield nothing; back off once.
        data = _grab(max(0.0, t - 1.0))
    if len(data) < need:
        raise VideoError(f"no frame decoded at t={t_sec}")
    return np.frombuffer(data[:need], np.uint8).reshape(h, w, 3).copy()


def iter_frames(
    path: Path | str, fps: float = 1.0, max_side: int | None = 960, meta: VideoMeta | None = None
) -> Iterator[tuple[float, np.ndarray]]:
    """Yield `(t_sec, upright BGR frame)` sampled at `fps`, streamed from an ffmpeg pipe.

    Closing the generator early terminates ffmpeg. If ffmpeg exits with an error at the
    end of the stream and not a single frame was decoded, raises `VideoError` (so an
    undecodable file is an error, not a silent "nothing found"); an error after some
    frames (e.g. a truncated tail) is only logged.
    """
    if fps <= 0:
        raise ValueError("fps must be > 0")
    path = Path(path)
    meta = meta or probe(path)
    w, h = _out_size(meta, max_side)
    need = w * h * 3
    cmd = [
        FFMPEG, "-nostdin", "-v", "error", "-i", str(path.resolve()),
        "-vf", f"fps={fps},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-",
    ]
    # stderr goes to an unbounded temp file (no pipe -> no deadlock); only its tail is read.
    err = tempfile.TemporaryFile()
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err)
    except BaseException:
        err.close()
        raise
    assert proc.stdout is not None
    i = 0
    try:
        while True:
            buf = proc.stdout.read(need)
            if not buf or len(buf) < need:
                break
            yield i / fps, np.frombuffer(buf, np.uint8).reshape(h, w, 3).copy()
            i += 1
        # natural EOF (not an early close): ffmpeg's exit code is meaningful here
        try:
            rc = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            rc = proc.wait()
        if rc != 0:
            err.seek(0, 2)
            err.seek(max(0, err.tell() - STDERR_TAIL_BYTES))
            tail = err.read().decode("utf-8", "replace").strip()
            if i == 0:
                raise VideoError(f"ffmpeg could not decode the video (exit code {rc}): {tail}")
            log.warning("ffmpeg exited with code %s after %d frame(s) of %s: %s", rc, i, path.name, tail)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdout.close()
        proc.wait()
        err.close()


def jpeg_b64(frame: np.ndarray, quality: int = 85) -> str:
    """Encode a BGR frame to base64 JPEG (no data: prefix)."""
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    if not ok:
        raise VideoError("JPEG encoding failed")
    return base64.b64encode(buf.tobytes()).decode("ascii")
