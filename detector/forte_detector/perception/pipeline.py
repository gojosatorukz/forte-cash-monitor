"""Perception stage entry point: video -> detections -> tracks -> timeline (cached)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from forte_detector import config
from forte_detector.concurrency import SingletonCache, atomic_write_text
from forte_detector.labels import normalize_id
from forte_detector.perception.timeline import TimelineParams, build_timeline
from forte_detector.perception.tracker import (
    confirmed,
    dedupe_concurrent,
    merge_tracks,
    run_bytetrack,
    suppress_contained,
)
from forte_detector.schema import ProgressCb, Transform
from forte_detector.video_io import iter_frames, video_duration

log = logging.getLogger(__name__)
CACHE_VERSION = 20  # bump whenever tracking/timeline logic changes (timeline cache key)
DET_CACHE_VERSION = 3  # bump when decoding/detection output changes (detections cache key)


@dataclass(frozen=True)
class PerceptionConfig:
    fps: float = 4.0
    # long uploads: sampling fps = min(fps, max_samples / duration), so tracking/timeline/probes stay
    # bounded (~2400 samples: 145 s at 4 fps is 580; 1 h drops to ~0.67 fps)
    max_samples: int = 2400
    max_side: int = 960  # frames are downscaled to this before detection (1080p sources stay fast)
    model_id: str = field(default_factory=config.det_model)
    input_size: int = 640
    person_thr: float = 0.25
    object_thr: float = 0.3
    batch_size: int = 4
    merge_gap_s: float = 2.0
    merge_dist: float = 0.6
    contain_thr: float = 0.8  # drop part-detections mostly inside a stronger person box
    auto_orient: bool = True  # container rotation tag, else 0/90/270 from person scores (never 180)
    orient_margin: float = 1.5  # 90/270 must beat the unrotated summed confidence by this factor
    orient_min_frames: int = 3  # ... and score higher on at least this many of the probe frames
    start_thr: float = 0.35  # detection score that may start/confirm a track (person_thr continues one)
    timeline: TimelineParams = field(default_factory=TimelineParams)

    def hash(self) -> str:
        blob = json.dumps({"v": CACHE_VERSION, **asdict(self)}, sort_keys=True)
        return hashlib.sha1(blob.encode()).hexdigest()[:10]


def sampling_fps(fps: float, duration: float, max_samples: int) -> float:
    """min(fps, max_samples / duration); `fps` unchanged when the duration is unknown (<= 0)."""
    if duration and duration > 0 and max_samples > 0 and fps * duration > max_samples:
        return round(max_samples / duration, 4)
    return fps


_DETECTORS = SingletonCache()  # shared across concurrent analyze() threads (one model in VRAM)


def get_detector(cfg: PerceptionConfig, device: str | None):
    from forte_detector.perception.detector import Detector

    key = (cfg.model_id, cfg.input_size, cfg.person_thr, cfg.object_thr, cfg.batch_size, device)
    return _DETECTORS.get_or_create(key, lambda: Detector(
        cfg.model_id, device=device, input_size=cfg.input_size, person_thr=cfg.person_thr,
        object_thr=cfg.object_thr, batch_size=cfg.batch_size,
    ))


_SHA_MEMO: dict = {}


def content_key(video_path: str) -> str:
    """sha1 of the full file bytes (streamed in 1 MB chunks); never the file name. Memoized per
    (path, size, mtime) within the process."""
    st = os.stat(video_path)
    memo_key = (os.path.abspath(video_path), st.st_size, st.st_mtime_ns)
    if memo_key not in _SHA_MEMO:
        h = hashlib.sha1()
        with open(video_path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _SHA_MEMO[memo_key] = h.hexdigest()
    return _SHA_MEMO[memo_key]


def cache_path(video_path: str, variant: str, cfg: PerceptionConfig) -> Path:
    return config.cache_dir() / "perception" / f"{content_key(video_path)[:20]}__{variant}__{cfg.hash()}.json"


def det_hash(cfg: PerceptionConfig) -> str:
    keys = ("fps", "max_side", "model_id", "input_size", "person_thr", "object_thr", "auto_orient", "orient_margin",
            "orient_min_frames", "start_thr")
    blob = json.dumps({"v": DET_CACHE_VERSION, **{k: getattr(cfg, k) for k in keys}}, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:10]


_ROT = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


def rotate_cw(frame: np.ndarray, deg: int) -> np.ndarray:
    return frame if deg % 360 == 0 else cv2.rotate(frame, _ROT[deg % 360])


def _compose(transform: Transform | None, deg: int) -> Transform | None:
    if deg % 360 == 0:
        return transform
    if transform is None:
        return lambda f: rotate_cw(f, deg)
    return lambda f: rotate_cw(transform(f), deg)


def container_rotation_cw(video_path: str) -> int:
    """Clockwise degrees that put the stored frames upright according to the container's display
    matrix (phone videos). PyAV reports the CCW display rotation; 0 when absent."""
    import av

    try:
        with av.open(str(video_path)) as c:
            for frame in c.decode(video=0):
                return int(round(-float(getattr(frame, "rotation", 0) or 0))) % 360
    except Exception:  # noqa: BLE001 - metadata is best effort
        log.warning("cannot read rotation metadata of %s", video_path)
    return 0


def _spread_frames(video_path: str, n: int, transform: Transform | None, max_side: int) -> list[np.ndarray]:
    import av

    from forte_detector.video_io import read_frame_at

    with av.open(str(video_path)) as c:
        st = c.streams.video[0]
        dur = float(st.duration * st.time_base) if st.duration else float(c.duration or 0) / 1e6
    frames = []
    for i in range(n):
        f = read_frame_at(video_path, dur * (i + 0.5) / n, transform=transform)
        sc = max_side / max(f.shape[:2])
        if sc < 1:
            f = cv2.resize(f, (round(f.shape[1] * sc), round(f.shape[0] * sc)), interpolation=cv2.INTER_AREA)
        frames.append(f)
    return frames


def choose_orientation(per_frame: dict[int, list[float]], margin: float, min_frames: int) -> int:
    """Pick 0 / 90 / 270 (never 180) from per-frame summed person confidence. Default 0: a rotation
    is chosen only if its total beats 0 deg by `margin`x AND it scores higher on >= min_frames frames."""
    s0 = sum(per_frame[0])
    best, best_s = 0, s0
    for deg in (90, 270):
        sd = sum(per_frame[deg])
        wins = sum(a > b for a, b in zip(per_frame[deg], per_frame[0]))
        if sd >= margin * s0 and sd > 1.0 and wins >= min_frames and sd > best_s:
            best, best_s = deg, sd
    return best


def effective_transform(timeline: dict, transform: Transform | None = None) -> Transform | None:
    """The frame transform perception actually used for `timeline`: container rotation, then the
    caller's transform, then the auto-rotation. Use it for every later frame read (probes, VLM crops,
    evidence frames) so pixels line up with the timeline's normalized boxes:

        read_frame_at(path, t, transform=effective_transform(tl, transform))

    (perception additionally downscales to max_side; boxes are normalized, so that does not matter).
    """
    o = timeline.get("orientation") or {}
    return _compose(_compose_pre(int(o.get("container_rot_cw_deg") or 0), transform), int(o.get("rot_cw_deg") or 0))


def detect_orientation(det, video_path: str, transform: Transform | None, cfg: PerceptionConfig,
                       n_frames: int = 6) -> dict:
    """Container rotation tag first; otherwise 0 deg unless 90/270 clearly wins on person scores
    over `n_frames` frames spread across the clip (see choose_orientation). All scores are logged."""
    tag = container_rotation_cw(video_path)
    base = _compose_pre(tag, transform)
    if tag:
        return {"rot_cw_deg": 0, "container_rot_cw_deg": tag, "source": "container", "scores": None, "pre": base}
    frames = _spread_frames(video_path, n_frames, transform, cfg.max_side)
    per_frame = {}
    for deg in (0, 90, 180, 270):  # 180 is scored for the log only, never chosen
        res = det.detect([rotate_cw(f, deg) for f in frames])
        per_frame[deg] = [round(sum(p[4] for p in r["persons"] if p[4] >= cfg.start_thr), 3) for r in res]
    best = choose_orientation(per_frame, cfg.orient_margin, cfg.orient_min_frames)
    return {
        "rot_cw_deg": int(best), "container_rot_cw_deg": 0, "source": "detector" if best else "default",
        "scores": {str(k): round(sum(v), 3) for k, v in per_frame.items()},
        "per_frame": {str(k): v for k, v in per_frame.items()}, "pre": base,
    }


def _compose_pre(deg: int, transform: Transform | None) -> Transform | None:
    """Rotate first (container orientation), then apply the caller's transform."""
    if deg % 360 == 0:
        return transform
    if transform is None:
        return lambda f: rotate_cw(f, deg)
    return lambda f: transform(rotate_cw(f, deg))


def _detect_video(video_path, transform, variant, cfg: PerceptionConfig, device, progress_cb,
                  stop: Callable[[], str | None] | None = None) -> dict:
    """Decode + detect every sample (cached separately from tracking/timeline so that timeline
    parameters can be tuned without re-running the detector). `stop()` is checked before the
    orientation probe and between batches; a non-empty reason ends decoding early ("truncated",
    never cached)."""
    cp = (
        config.cache_dir() / "perception" / "dets" / f"{content_key(video_path)[:20]}__{variant}__{det_hash(cfg)}.json"
        if variant else None
    )
    if cp is not None and cp.exists():
        d = json.loads(cp.read_text())
        d["cached"] = True
        return d
    det = get_detector(cfg, device or config.det_device())
    start_device = str(det.device)
    on_cuda = start_device.startswith("cuda")
    if on_cuda:
        import torch

        torch.cuda.reset_peak_memory_stats(start_device)
    if hasattr(det, "ensure_device"):  # back on CUDA after an earlier OOM fallback, when possible
        det.ensure_device()
    orientation = {"rot_cw_deg": 0, "container_rot_cw_deg": 0, "source": "off", "scores": None}
    truncated = stop() if stop else None
    if cfg.auto_orient and not truncated:
        orientation = detect_orientation(det, video_path, transform, cfg)
        transform = _compose(orientation.pop("pre"), orientation["rot_cw_deg"])
    elif cfg.auto_orient:
        orientation["source"] = "skipped"
    times, frame_dets, frame_wh = [], [], None
    batch: list = []
    batch_t: list[float] = []
    t_detect = 0.0
    t0 = time.perf_counter()

    def flush():
        nonlocal t_detect
        if not batch:
            return
        td = time.perf_counter()
        frame_dets.extend(det.detect(batch))
        t_detect += time.perf_counter() - td
        times.extend(batch_t)
        batch.clear()
        batch_t.clear()

    for t, frame in iter_frames(video_path, cfg.fps, max_side=cfg.max_side, transform=transform):
        if frame_wh is None:
            frame_wh = (frame.shape[1], frame.shape[0])
        batch.append(frame)
        batch_t.append(round(t, 3))
        if len(batch) >= cfg.batch_size:
            flush()
            if progress_cb:
                progress_cb(min(0.95, len(times) / 600), "perception: detect")
            truncated = stop() if stop else None
            if truncated:
                log.warning("perception of %s stopped early (%s) after %d samples", video_path, truncated,
                            len(times))
                break
    flush()
    vram = torch.cuda.max_memory_allocated(start_device) / 2**20 if on_cuda else 0.0
    frames = [
        {
            "persons": [[round(float(v), 4) for v in p] for p in d["persons"]],
            "objects": [[o[0], *[round(float(v), 4) for v in o[1:5]], round(float(o[5]), 3)] for o in d["objects"]],
        }
        for d in frame_dets
    ]
    out = {
        "times": times, "frames": frames, "frame_wh": list(frame_wh or (1, 1)),
        "device": str(det.device), "start_device": start_device,
        "decode_detect_s": round(time.perf_counter() - t0, 3), "detect_s": round(t_detect, 3),
        "vram_peak_mb": round(vram, 1), "orientation": orientation, "truncated": truncated or None,
    }
    if cp is not None and not truncated:
        cp.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(cp, json.dumps(out))
    out["cached"] = False
    return out


def perceive(
    video_path: str,
    fps: float | None = None,
    transform: Transform | None = None,
    device: str | None = None,
    progress_cb: ProgressCb | None = None,
    *,
    variant: str | None = None,
    cfg: PerceptionConfig | None = None,
    use_cache: bool | None = None,
    deadline: float | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict:
    """Run the perception stage and return the JSON-able timeline.

    Caching is off unless `use_cache=True` or env FORTE_EVAL_CACHE=1 (the eval harness). Cache key =
    sha1 of the full video bytes + variant + config hash (never the file name). `variant` names the
    transform (e.g. "hflip"); with transform=None it must be None or "orig". A callable transform
    without a variant name is not cached (a callable cannot be keyed reliably).

    Long uploads: the sampling fps drops to max_samples / duration (cfg.max_samples). `deadline`
    (time.monotonic) and `should_stop()` (e.g. the service's cancel flag) are checked between
    detector batches; when either fires, decoding stops and the timeline is built from the samples
    so far (meta["truncated"] = "deadline" | "cancelled"; not cached).
    """
    if use_cache is None:
        use_cache = config.eval_cache_enabled()
    if transform is None and variant not in (None, "orig"):
        raise ValueError(f"variant={variant!r} given without its transform")
    cfg = cfg or PerceptionConfig()
    if fps is not None and fps != cfg.fps:
        cfg = replace(cfg, fps=fps)
    requested_fps = cfg.fps
    video_sec = video_duration(video_path)
    fs = sampling_fps(cfg.fps, video_sec, cfg.max_samples)
    if fs != cfg.fps:
        log.info("long video %s: sampling at %.3f fps instead of %.1f", video_path, fs, cfg.fps)
        cfg = replace(cfg, fps=fs)

    def stop() -> str | None:
        if should_stop is not None:
            try:
                if should_stop():
                    return "cancelled"
            except Exception:  # noqa: BLE001 - a broken cancel probe must not kill perception
                log.debug("should_stop failed", exc_info=True)
        if deadline is not None and time.monotonic() >= deadline:
            return "deadline"
        return None
    if variant is None and transform is None:
        variant = "orig"
    cp = cache_path(video_path, variant, cfg) if (use_cache and variant) else None
    if cp is not None and cp.exists():
        if progress_cb:
            progress_cb(1.0, "perception (cached)")
        tl = json.loads(cp.read_text())
        tl["meta"]["cached"] = True
        return tl

    t0 = time.perf_counter()
    dets = _detect_video(video_path, transform, variant if use_cache else None, cfg, device, progress_cb, stop)
    times, frame_dets, frame_wh = dets["times"], dets["frames"], tuple(dets["frame_wh"])
    duration = times[-1] if times else 0.0

    t1 = time.perf_counter()
    # effective sample rate: a source slower than cfg.fps yields fewer samples (each frame once)
    eff_fps = (len(times) - 1) / (times[-1] - times[0]) if len(times) > 1 and times[-1] > times[0] else cfg.fps
    persons = [suppress_contained(d["persons"], cfg.contain_thr) for d in frame_dets]
    raw = run_bytetrack(times, persons, eff_fps, frame_wh or (1, 1), start_thr=cfg.start_thr)
    merged = merge_tracks(raw, max_gap=cfg.merge_gap_s, dist_factor=cfg.merge_dist)
    merged = [tr for tr in merged if confirmed(tr, times, 3, 5)]  # 3-of-5 confirmation
    merged = dedupe_concurrent(merged)  # one person, two coexisting ids
    next_id = max([tr.id for tr in merged if tr.id > 0], default=0) + 1
    for tr in merged:  # orphan-detection chains (negative ids) get regular ids
        if tr.id < 0:
            tr.id, next_id = next_id, next_id + 1
    tl = build_timeline(merged, times, duration, frame_wh or (1, 1), cfg.timeline)
    t_track = time.perf_counter() - t1

    tl["orientation"] = dets.get("orientation")
    tl["series"]["objects"] = [d["objects"] for d in frame_dets]
    tl["series"]["detections"] = [d["persons"] for d in frame_dets]
    tl["meta"] = {
        "video": Path(video_path).name,
        "video_id": normalize_id(Path(video_path).stem),
        "variant": variant,
        "config_hash": cfg.hash(),
        "config": json.loads(json.dumps(asdict(cfg))),
        "model_id": cfg.model_id,
        "orientation": dets.get("orientation"),
        "device": dets["device"],
        "start_device": dets["start_device"],
        "detections_cached": dets.get("cached", False),
        "n_samples": len(times),
        "requested_fps": requested_fps,
        "sampling_fps": cfg.fps,
        "truncated": dets.get("truncated"),
        "video_duration": round(video_sec, 3) if video_sec else None,
        "effective_fps": round(eff_fps, 3),
        "n_raw_tracks": len(raw),
        "n_merged_tracks": len(merged),
        "stage_times": {
            "decode_detect": dets["decode_detect_s"],
            "detect": dets["detect_s"],
            "track_timeline": round(t_track, 3),
            "total": round(time.perf_counter() - t0, 3),
        },
        "vram_peak_mb": dets["vram_peak_mb"],
        "cached": False,
    }
    if cp is not None and not dets.get("truncated"):
        cp.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(cp, json.dumps(tl, ensure_ascii=False))
    if progress_cb:
        progress_cb(1.0, "perception done")
    return tl
