"""Evidence clips: one short H.264 MP4 per violation, cut with ffmpeg before a clip deadline.

Window = [start_sec - 2, end_sec + 2] (start defaults to timecode_sec, end to start), its
length clamped to CLIP_MIN_SEC..CLIP_MAX_SEC (a longer window is re-centred on timecode_sec),
then shifted/clamped into the video. ffmpeg autorotates by the container tag; the analyzer's
extra clockwise `applied_rotation_deg` (0/90/180/270) is applied on top so the clips match
the evidence frames. Clips are written to a temp name, checked with ffprobe (at least one
video frame) and only then renamed, so a killed or broken encode never leaves a clip behind.

The caller passes the deadline (the worker uses submit + JOB_HARD_CAP_SEC - 2 s). Clips are
cut most-important-first (fact_conf / confidence, then timecode) with up to MAX_WORKERS
parallel ffmpeg processes of FFMPEG_THREADS threads each.
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"
FFMPEG_THREADS = 2
PAD_SEC = 2.0
CLIP_MIN_SEC = 3.0
CLIP_MAX_SEC = 8.0
CLIP_HEIGHT = 480
MAX_WORKERS = 4
MIN_REMAINING_SEC = 2.0  # less than this left before the clip deadline -> skip all clips
KILL_MARGIN_SEC = 1.0  # each ffmpeg's timeout = time to the clip deadline - this
MIN_CLIP_TIMEOUT_SEC = 1.5  # don't start an ffmpeg whose timeout would be shorter
PROBE_TIMEOUT_MAX_SEC = 5.0
PROBE_TIMEOUT_MIN_SEC = 0.2

ROTATION_FILTERS = {90: "transpose=1", 270: "transpose=2", 180: "hflip,vflip"}

WARN_CLIPS_TIME = "не хватило времени на нарезку клипов № {nums}"
WARN_CLIPS_FAILED = "не удалось создать клипы № {nums}"


def _f(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def clip_window(timecode_sec: float, start_sec: float | None = None, end_sec: float | None = None,
                duration_sec: float | None = None) -> tuple[float, float] | None:
    """(start, length) of the clip in seconds, or None if nothing can be cut."""
    tc = _f(timecode_sec)
    if tc is None:
        return None
    start = _f(start_sec)
    start = tc if start is None else start
    end = _f(end_sec)
    end = start if end is None or end < start else end
    a, b = start - PAD_SEC, end + PAD_SEC
    length = b - a
    if length > CLIP_MAX_SEC:
        length = CLIP_MAX_SEC
        a = tc - length / 2
    elif length < CLIP_MIN_SEC:
        a -= (CLIP_MIN_SEC - length) / 2
        length = CLIP_MIN_SEC
    dur = _f(duration_sec)
    if dur is not None and dur > 0:
        length = min(length, dur)
        a = min(max(0.0, a), dur - length)
    else:
        a = max(0.0, a)
    if length <= 0:
        return None
    return round(a, 3), round(length, 3)


def normalize_rotation(v: Any) -> int:
    """Analyzer `applied_rotation_deg` -> 0/90/180/270 (anything else -> 0)."""
    f = _f(v)
    if f is None:
        return 0
    deg = int(round(f)) % 360
    return deg if deg in (0, 90, 180, 270) else 0


def video_filter(rotation_deg: int = 0) -> str:
    # Height 480 (never upscaled), even dimensions, aspect kept; rotation first so the
    # height applies to the upright picture.
    scale = f"scale=-2:trunc(min({CLIP_HEIGHT}\\,ih)/2)*2"
    rot = ROTATION_FILTERS.get(rotation_deg)
    return f"{rot},{scale}" if rot else scale


class ClipTimeout(RuntimeError):
    """Not enough time left before the clip deadline (skipped or killed)."""


def _remaining(deadline: float) -> float:
    return deadline - time.monotonic()


def count_frames(path: Path, timeout: float) -> int:
    """Number of video packets in `path` (ffprobe -count_packets); raises on ffprobe failure."""
    cmd = [FFPROBE, "-v", "error", "-select_streams", "v:0", "-count_packets",
           "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)]
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe exit {proc.returncode}: {proc.stderr.decode('utf-8', 'replace').strip()[-300:]}")
    lines = proc.stdout.decode("ascii", "replace").split()
    first = lines[0].strip(",") if lines else ""
    return int(first) if first.isdigit() else 0


def cut_clip(src: Path, dst: Path, start: float, length: float, rotation_deg: int, deadline: float) -> None:
    """Encode one clip so that everything finishes before `deadline` (time.monotonic()).

    ffmpeg gets `deadline - KILL_MARGIN_SEC - now` (not started below MIN_CLIP_TIMEOUT_SEC;
    killed by subprocess.run on timeout); the result must then pass an ffprobe frame count
    within the time that is left. Raises ClipTimeout for lack of time, anything else for a
    real failure; a failed clip leaves no file behind."""
    timeout = _remaining(deadline) - KILL_MARGIN_SEC
    if timeout < MIN_CLIP_TIMEOUT_SEC:
        raise ClipTimeout(f"only {timeout:.1f} s left for ffmpeg")
    tmp = dst.with_name(f".{dst.stem}.tmp.mp4")
    cmd = [
        FFMPEG, "-nostdin", "-y", "-v", "error",
        "-ss", f"{start:.3f}", "-i", str(Path(src).resolve()), "-t", f"{length:.3f}",
        "-vf", video_filter(rotation_deg),
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
        "-threads", str(FFMPEG_THREADS), "-movflags", "+faststart", "-f", "mp4", str(tmp),
    ]
    try:
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as e:
            raise ClipTimeout(f"ffmpeg killed after {timeout:.1f} s") from e
        if proc.returncode != 0:
            tail = proc.stderr.decode("utf-8", "replace").strip()[-500:]
            raise RuntimeError(f"ffmpeg exit {proc.returncode}: {tail}")
        if not tmp.is_file() or tmp.stat().st_size == 0:
            raise RuntimeError("ffmpeg produced no output")
        probe_timeout = min(PROBE_TIMEOUT_MAX_SEC, _remaining(deadline))
        if probe_timeout < PROBE_TIMEOUT_MIN_SEC:
            raise ClipTimeout("no time left to check the clip")
        try:
            frames = count_frames(tmp, probe_timeout)
        except subprocess.TimeoutExpired as e:
            raise ClipTimeout("ffprobe check timed out") from e
        if frames <= 0:
            raise RuntimeError("clip has no video frames")
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)


def _priority(v: dict[str, Any]) -> float:
    for k in ("fact_conf", "confidence"):
        f = _f(v.get(k))
        if f is not None:
            return f
    return 0.0


def clip_order(violations: list[dict[str, Any]]) -> list[int]:
    """1-based violation numbers in cutting order: highest fact_conf (else confidence) first,
    then earliest timecode. The numbers stay the violations' positions in the final list."""
    def key(n: int) -> tuple[float, float, int]:
        v = violations[n - 1]
        tc = _f(v.get("timecode_sec"))
        return (-_priority(v), tc if tc is not None else math.inf, n)
    return sorted(range(1, len(violations) + 1), key=key)


def _nums(ns: list[int]) -> str:
    return ", ".join(map(str, sorted(ns)))


def generate_clips(src: Path, violations: list[dict[str, Any]], out_dir: Path, deadline: float,
                   duration_sec: float | None = None, rotation_deg: int = 0,
                   job_id: str = "?") -> tuple[dict[int, Path], list[str]]:
    """Cut clips for `violations` (1-based n -> out_dir/<n>.mp4), all finished before
    `deadline` (time.monotonic()). The job's clip dir is always cleared first.

    Returns ({n: path} of clips that exist, RU warnings): WARN_CLIPS_TIME for clips skipped
    or killed for lack of time, WARN_CLIPS_FAILED for real failures; no warnings without
    violations. Never raises."""
    shutil.rmtree(out_dir, ignore_errors=True)
    n_total = len(violations)
    if not n_total:
        return {}, []
    everything = list(range(1, n_total + 1))
    if _remaining(deadline) < MIN_REMAINING_SEC:
        log.warning("Job %s: < %.0f s left before the clip deadline, skipping %d clip(s)",
                    job_id, MIN_REMAINING_SEC, n_total)
        return {}, [WARN_CLIPS_TIME.format(nums=_nums(everything))]
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:  # noqa: BLE001
        log.warning("Job %s: cannot create clip dir %s: %s", job_id, out_dir, e)
        return {}, [WARN_CLIPS_FAILED.format(nums=_nums(everything))]

    def one(n: int) -> str:
        """'ok' | 'time' | 'failed'"""
        v = violations[n - 1]
        try:
            win = clip_window(v.get("timecode_sec"), v.get("start_sec"), v.get("end_sec"), duration_sec)
            if win is None:
                raise ValueError("no clip window")
            cut_clip(src, out_dir / f"{n}.mp4", win[0], win[1], rotation_deg, deadline)
            return "ok"
        except ClipTimeout as e:
            log.warning("Job %s: evidence clip %d not cut (time): %s", job_id, n, e)
            return "time"
        except Exception as e:  # noqa: BLE001 - a clip is polish, never a job failure
            log.warning("Job %s: evidence clip %d failed: %s", job_id, n, e)
            return "failed"

    t0 = time.monotonic()
    outcome: dict[int, str] = {}
    try:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, n_total), thread_name_prefix="clip") as pool:
            # Submission order = start order: the most important clips are cut first.
            futs = {n: pool.submit(one, n) for n in clip_order(violations)}
            for n, fut in futs.items():
                outcome[n] = fut.result()  # bounded: every subprocess is killed before the deadline
    except Exception as e:  # noqa: BLE001
        log.warning("Job %s: clip generation error: %s", job_id, e)
    made = {n: out_dir / f"{n}.mp4" for n, r in outcome.items() if r == "ok"}
    log.info("Job %s: %d/%d evidence clip(s) in %.1f s", job_id, len(made), n_total, time.monotonic() - t0)
    late = [n for n in everything if outcome.get(n) == "time"]
    failed = [n for n in everything if n not in made and n not in late]
    warnings = []
    if late:
        warnings.append(WARN_CLIPS_TIME.format(nums=_nums(late)))
    if failed:
        warnings.append(WARN_CLIPS_FAILED.format(nums=_nums(failed)))
    return made, warnings
