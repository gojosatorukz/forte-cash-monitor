"""Probe runner: routes each ProbeRequest to a CV probe or one narrow VLM question.

    run_probes(video_path, requests, *, timeline, transform=None, calibration=None) -> list[ProbeResult]
    calibrate(video_path, timeline, transform=None, detector=None) -> dict

Routing (binding, docs/plan_phase1.md Task 3): door_open and camera_blocked are CV only; all other
cues are VLM questions (questions.SPECS) on the keyframe (or keyframe + up to 2 extra frames for
short actions), on the full frame or an upscaled ROI crop. COCO detections from the perception
timeline are attached as evidence and are the fallback when the VLM is unavailable.

Never raises: any failure gives value=None with raw["error"]. One result per request, same order.

No global memos: state lives in the calibration dict (computed once per analyze() and passed in)
and in a per-call context. Call `calibrate()` once per video/variant and pass the result as
`calibration=` to every `run_probes` call for that video; without it, run_probes calibrates lazily
for that call only (slow when called repeatedly).

Calibration dict (normalized x0,y0,x1,y1 boxes, None when not found):
    {"safe", "door", "laptop", "counting_machine", "desk": box | None, "door_openvocab": box | None,
     "door_ref": {"t", "roi", "candidates", "cluster", "why"} | None,   # closed-door reference frame
     "scores": {key: float | None}, "meta": {"model", "device", "frames_t", "best_t", "runtime_s",
     "door_source": "openvocab" | "timeline" | None, "error"?}}

Env: FORTE_VLM_URL, FORTE_VLM_TIMEOUT (s per call, default 20; capped by the deadline), FORTE_PROBE_BUDGET_S (total VLM
seconds per run_probes call, default 150), FORTE_VLM_MAX_SIDE (full-frame downscale, default 896),
FORTE_OV_MODEL / FORTE_OV_DEVICE (calibration model).
"""
from __future__ import annotations

import bisect
import logging
import os
import time
from typing import Any, Callable

import cv2
import numpy as np

from forte_detector.probes import cheap, openvocab, questions
from forte_detector.probes.base import CUES, ProbeRequest, ProbeResult
from forte_detector.probes.vlm import VLMClient
from forte_detector.video_io import read_frame_at

log = logging.getLogger(__name__)

CV_CUES = ("door_open", "camera_blocked")
VLM_CUES = tuple(c for c in CUES if c not in CV_CUES)
COCO_MAP = {
    "cup_or_food": ("cup", "bottle"),
    "phone_visible": ("cell phone",),
    "handbag_or_coat_on_desk": ("handbag", "backpack", "suitcase"),
}
COCO_STRONG = 0.6  # a COCO class counts as present at this score in >= 2 samples near t
DOOR_EVENTS = ("person_enters", "person_exits", "cashier_enters_room", "cashier_exits_room")
# door likely moving around an entry/exit: no reference frames there. Only 0.5 s before: a person is
# often detected (through the glass) ~1 s before stepping in while the door is still closed - X-32:
# track born at 1.0 s, door closed at 0 s, then left open for the rest of the clip (T5). A wider
# pre-window drops the only closed frame and inverts every decision on that video.
DOOR_EVENT_WINDOW = (-0.5, 4.0)
DOOR_REF_CANDIDATES = 10
DOOR_REF_MAX_OCCLUSION = 0.35  # persons are masked anyway; skip frames where they cover more of the ROI
# An explained minority earliest cluster (see door_reference) needs the state change pinned to a short
# gap: wider -> the door use found there may be unrelated to the change -> abstain. X-30/X-33: 1.2-1.5 s.
DOOR_GAP_MAX = 4.0
DOOR_GAP_SCAN = 32  # max frames read inside the gap to narrow it down
# "someone used the door": a person box covering >= this share of the doorway zone (the tracks'
# doorway, not the padded ROI, which also spans the glass panels a person may stand behind)
DOOR_ZONE_PRESENCE = 0.5

_CLIENT: VLMClient | None = None  # the HTTP client only (no per-video state besides the down-flag)


def get_client() -> VLMClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = VLMClient()
    return _CLIENT


def budget_s() -> float:
    return float(os.environ.get("FORTE_PROBE_BUDGET_S", "150"))


def vlm_max_side() -> int:
    return int(os.environ.get("FORTE_VLM_MAX_SIDE", "896"))


class _Frames:
    """Per-call frame cache (keyed by t) for one video + transform."""

    def __init__(self, video_path, transform):
        self.video_path, self.transform = video_path, transform
        self._cache: dict[float, np.ndarray] = {}

    def __call__(self, t: float) -> np.ndarray:
        k = round(float(t), 3)
        if k not in self._cache:
            self._cache[k] = read_frame_at(self.video_path, k, self.transform)
        return self._cache[k]


# ----------------------------------------------------------------------------- door reference


def _overlap_frac(roi, boxes) -> float:
    """Fraction of roi area covered by the union of boxes (coarse 32x32 raster)."""
    g = np.zeros((32, 32), bool)
    rx0, ry0, rx1, ry1 = roi
    for b in boxes:
        x0 = int(np.clip((b[0] - rx0) / max(rx1 - rx0, 1e-6) * 32, 0, 32))
        x1 = int(np.clip(np.ceil((b[2] - rx0) / max(rx1 - rx0, 1e-6) * 32), 0, 32))
        y0 = int(np.clip((b[1] - ry0) / max(ry1 - ry0, 1e-6) * 32, 0, 32))
        y1 = int(np.clip(np.ceil((b[3] - ry0) / max(ry1 - ry0, 1e-6) * 32), 0, 32))
        g[y0:y1, x0:x1] = True
    return float(g.mean())


def door_events(timeline: dict, roi) -> list[float]:
    """Moments the door is likely moving: entry/exit checkpoints plus births/deaths of any track
    (even ones not promoted to checkpoints) whose first/last box centre lies near the door ROI.
    Births at the very start and deaths at the very end of the video are not events."""
    ts = (timeline.get("series") or {}).get("t") or [0.0]
    t_first, t_last = ts[0], ts[-1]
    ev = [c["t"] for c in timeline.get("checkpoints", []) if c.get("type") in DOOR_EVENTS]
    x0, y0, x1, y1 = cheap.expand(roi, 0.15)

    def near(b):
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        return x0 <= cx <= x1 and y0 <= cy <= y1

    for tr in timeline.get("tracks") or []:
        tt, bb = tr.get("t") or [], tr.get("boxes") or []
        if not tt or not bb:
            continue
        if tt[0] > t_first + 0.5 and near(bb[0]):
            ev.append(float(tt[0]))
        if tt[-1] < t_last - 0.5 and near(bb[-1]):
            ev.append(float(tt[-1]))
    return sorted(ev)


def door_reference(read: Callable[[float], np.ndarray], timeline: dict, roi) -> dict:
    """Closed-door reference: consensus over clean samples instead of "the first clear frame".

    Candidates: samples with the ROI mostly free of persons and outside door-activity windows
    (-1..+4 s around entries/exits and around track births/deaths near the door), 10 spread over the
    video. Candidates whose door ROIs match each other (door_state change <= threshold) form
    clusters; the reference is the medoid of the cluster holding the earliest candidate (time prior:
    with door activity excluded, the start of the video shows the room closed - a visitor entering at
    t~0-3 s is excluded by its own birth window). Isolated noisy frames are outvoted inside the
    cluster. Returns {"t", "roi", "candidates", "cluster", "events", "why"}.
    """
    ser = timeline.get("series", {})
    ts = [float(t) for t in (ser.get("t") or [0.0])]
    events = door_events(timeline, roi)
    lo, hi = DOOR_EVENT_WINDOW
    occ = {t: _overlap_frac(roi, cheap.persons_at(timeline, t)) for t in ts}  # O(n log n)

    def in_window(t: float) -> bool:  # some event e with e + lo <= t <= e + hi (events sorted)
        k = bisect.bisect_left(events, t - hi - 1e-9)
        return k < len(events) and events[k] <= t - lo + 1e-9

    pool = [t for t in ts if occ[t] <= DOOR_REF_MAX_OCCLUSION and not in_window(t)]
    why = "consensus"
    if not pool:
        pool = [min(ts, key=lambda t: (occ[t], t))]
        why = "fallback_least_occluded"
    if len(pool) > DOOR_REF_CANDIDATES:
        idx = np.linspace(0, len(pool) - 1, DOOR_REF_CANDIDATES).round().astype(int)
        pool = [pool[i] for i in sorted(set(idx.tolist()))]
    n = len(pool)
    imgs = [read(t) for t in pool]
    D = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            persons = cheap.persons_at(timeline, pool[i]) + cheap.persons_at(timeline, pool[j])
            v, _, raw = cheap.door_state(imgs[i], imgs[j], roi, persons)
            D[i, j] = D[j, i] = raw.get("change_frac", 1.0) if v is not None else 1.0
    label = [-1] * n  # connected components of the "same door state" graph
    for i in range(n):
        if label[i] >= 0:
            continue
        stack, label[i] = [i], i
        while stack:
            a = stack.pop()
            for b in range(n):
                if label[b] < 0 and D[a, b] <= cheap.DOOR_THR:
                    label[b] = i
                    stack.append(b)
    n_clusters = len(set(label))
    best = [m for m in range(n) if label[m] == label[0]]  # pool is time-sorted: index 0 = earliest
    medoid = min(best, key=lambda m: float(np.mean([D[m, o] for o in best])))
    # Ambiguity guard: the earliest-cluster rule is a time prior. When the clean frames disagree, trust
    # it only if the earliest state is a strict majority, or the first state change is pinned to a
    # short gap with real door use in it (_transition_gap). Otherwise the door probe abstains: an
    # inverted reference would invert every decision on the video.
    ambiguous, gap, explained_by = False, None, None
    if why == "consensus" and n_clusters > 1:
        why = f"earliest_of_{n_clusters}_clusters"
        sizes = {}
        for l in label:
            sizes[l] = sizes.get(l, 0) + 1
        others = [s for l, s in sizes.items() if l != label[0]]
        is_largest = len(best) > max(others)  # a tie is not a majority
        if not is_largest:
            gap, explained_by = _transition_gap(read, timeline, roi, pool, label, imgs, occ, events)
            ambiguous = explained_by is None
        if ambiguous:
            why = f"ambiguous_{n_clusters}_clusters"
    return {"t": pool[medoid], "roi": [round(float(v), 4) for v in roi], "candidates": pool,
            "cluster": [pool[m] for m in best], "events": [round(e, 3) for e in events], "why": why,
            "ambiguous": ambiguous, "gap": gap, "explained_by": explained_by}


def _zone_cover(zone, box) -> float:
    """Share of the doorway zone covered by one box."""
    ix = max(0.0, min(zone[2], box[2]) - max(zone[0], box[0]))
    iy = max(0.0, min(zone[3], box[3]) - max(zone[1], box[1]))
    return ix * iy / max((zone[2] - zone[0]) * (zone[3] - zone[1]), 1e-9)


def _transition_gap(read, timeline, roi, pool, label, imgs, occ, events):
    """Ambiguity guard for a minority earliest cluster: (gap, explained_by | None).

    The earliest-cluster rule is a time prior (videos start with the door closed). When the earliest
    state is a minority, trust it only if the first state change is pinned to a short gap
    (<= DOOR_GAP_MAX) and someone really used the door in it. The pool is sparse (<= 10 candidates),
    so the gap is first narrowed with the frames between the two candidates (each compared with both
    sides). Door use = a door event whose activity window overlaps the gap, or a person box covering
    >= DOOR_ZONE_PRESENCE of the doorway zone (no zone: occluding >= DOOR_REF_MAX_OCCLUSION of the
    ROI) in a gap sample - not any overlap of the padded ROI (someone behind the glass).

    Residual limit: door use is direction-blind (it explains open->closed as well as closed->open);
    this only guarantees the change is tied to a real, local door use, and the time prior picks the
    direction. A door open from t=0 that someone walks through and closes within the first seconds
    still inverts; everything looser abstains."""
    ts = [float(t) for t in ((timeline.get("series") or {}).get("t") or [])]
    nxt = next(i for i in range(len(pool)) if label[i] != label[0])
    i0 = max(m for m in range(nxt) if label[m] == label[0])
    a, b = pool[i0], pool[nxt]
    inner = [t for t in ts if a < t < b and occ.get(t, 1.0) <= DOOR_REF_MAX_OCCLUSION]
    if len(inner) > DOOR_GAP_SCAN:
        idx = np.linspace(0, len(inner) - 1, DOOR_GAP_SCAN).round().astype(int)
        inner = [inner[i] for i in sorted(set(idx.tolist()))]
    side = []
    for t in inner:
        img = read(t)
        d = []
        for j in (i0, nxt):
            persons = cheap.persons_at(timeline, t) + cheap.persons_at(timeline, pool[j])
            v, _, raw = cheap.door_state(img, imgs[j], roi, persons)
            d.append(raw.get("change_frac", 1.0) if v is not None else 1.0)
        if d[0] <= cheap.DOOR_THR < d[1]:
            side.append((t, 0))
        elif d[1] <= cheap.DOOR_THR < d[0]:
            side.append((t, 1))
    firsts = [t for t, s_ in side if s_ == 1]
    if firsts:
        b = firsts[0]
    lasts = [t for t, s_ in side if s_ == 0 and t < b]
    if lasts:
        a = lasts[-1]
    gap = [round(a, 3), round(b, 3)]
    if b - a > DOOR_GAP_MAX:
        return gap, None
    lo, hi = DOOR_EVENT_WINDOW
    for e in events:
        if e + lo <= b and e + hi >= a:
            return gap, f"event@{round(e, 3)}"
    zone = (timeline.get("zones") or {}).get("door")
    for t in ts:
        if not (a + lo <= t <= b - lo):
            continue
        boxes = cheap.persons_at(timeline, t)
        if zone is not None:
            hit = any(_zone_cover(zone, bx) >= DOOR_ZONE_PRESENCE for bx in boxes)
        else:
            hit = occ.get(t, 0.0) >= DOOR_REF_MAX_OCCLUSION
        if hit:
            return gap, f"person@{round(t, 3)}"
    return gap, None


# ----------------------------------------------------------------------------- calibration


def calibrate(video_path: str, timeline: dict, transform: Callable[[Any], Any] | None = None,
              detector=None) -> dict:
    """Open-vocab zones on <= 3 low-occupancy frames + the door ROI and its closed reference.

    Not memoized: call once per analyze() and pass the dict to run_probes. Never raises."""
    read = _Frames(video_path, transform)
    try:
        det = detector or openvocab.get_detector()
        n = 3 if str(getattr(det, "device", "cpu")).startswith("cuda") else 2  # CPU: ~4-5 s per frame
        ts = openvocab.pick_frames(timeline, n)
        cal = openvocab.calibrate_frames([(t, read(t)) for t in ts], det)
    except Exception as e:  # noqa: BLE001
        log.warning("calibration failed for %s: %s", video_path, e)
        cal = {**{q: None for q in openvocab.QUERIES}, "scores": {q: None for q in openvocab.QUERIES},
               "meta": {"error": f"{type(e).__name__}: {e}"}}
    # door ROI = what the door_open probe uses: doorway zone from tracks first (open-vocab "glass
    # door" often picks the fixed glass panel), open-vocab box only as a fallback
    ov_door = cal.get("door")
    cal["door_openvocab"] = ov_door
    if ov_door is not None and (ov_door[2] - ov_door[0]) * (ov_door[3] - ov_door[1]) < 0.03:
        cal["door"] = [round(v, 4) for v in cheap.door_roi_from_zone(ov_door)]  # small box = the handle
    cal["meta"]["door_source"] = "openvocab" if cal.get("door") else None
    zone = (timeline.get("zones") or {}).get("door")
    if zone:
        cal["door"] = [round(v, 4) for v in cheap.door_roi_from_zone(zone)]
        cal["meta"]["door_source"] = "timeline"
    cal["door_ref"] = None
    if cal.get("door"):
        try:
            cal["door_ref"] = door_reference(read, timeline, cal["door"])
        except Exception as e:  # noqa: BLE001
            log.warning("door reference failed for %s: %s", video_path, e)
    return cal


# ----------------------------------------------------------------------------- image prep


def _is_large(roi, margin: float) -> bool:
    """A padded ROI covering >= half the frame is treated like the full frame (no close-up)."""
    x0, y0, x1, y1 = cheap.expand(roi, margin)
    return (x1 - x0) * (y1 - y0) >= 0.5


def _crop(img: np.ndarray, roi, margin: float, min_side: int) -> np.ndarray:
    """Pad the ROI by `margin`, crop, upscale so the long side reaches min_side (at most 4x)."""
    h, w = img.shape[:2]
    x0, y0, x1, y1 = cheap.to_px(cheap.expand(roi, margin), w, h)
    c = img[y0:y1, x0:x1]
    if _is_large(roi, margin):  # large ROI: treat like a full frame
        return _full(np.ascontiguousarray(c))
    s = min(4.0, min_side / max(c.shape[:2]))
    if s > 1:
        c = cv2.resize(c, (round(c.shape[1] * s), round(c.shape[0] * s)), interpolation=cv2.INTER_CUBIC)
    return np.ascontiguousarray(c)


def _full(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    s = vlm_max_side() / max(h, w)
    if s < 1:
        img = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
    return img


def _coco_laptop(timeline: dict, t: float):
    ev = cheap.coco_evidence(timeline, t, ("laptop",), window=2.0, strong=0.5)
    if not ev["boxes"]:
        return None
    best = max(ev["boxes"], key=lambda b: b[5])
    return best[1:5]


# ----------------------------------------------------------------------------- probes


class _Ctx:
    """Everything one run_probes call needs; discarded at the end of the call."""

    def __init__(self, video_path, timeline, transform, calibration, deadline=None):
        self.video_path, self.timeline, self.transform = video_path, timeline, transform
        self.deadline = deadline
        self._cal = calibration
        self.frame = _Frames(video_path, transform)
        self._refs: dict[tuple, dict] = {}
        self.t0 = time.perf_counter()
        self.vlm_s = 0.0

    @property
    def cal(self) -> dict:
        if self._cal is None:
            self._cal = self.timeline.get("calibration") or calibrate(self.video_path, self.timeline, self.transform)
        return self._cal

    def roi(self, key: str, t: float):
        if key == "door":  # data-derived doorway (track births/deaths) beats open-vocab "glass door"
            zone = (self.timeline.get("zones") or {}).get("door")
            if zone:
                return cheap.door_roi_from_zone(zone)
        box = (self.cal or {}).get(key)
        if box is None and key == "laptop":
            box = _coco_laptop(self.timeline, t)
        return box

    def door_ref(self, roi) -> dict:
        rk = tuple(round(float(v), 4) for v in roi)
        if rk not in self._refs:
            pre = (self._cal or {}).get("door_ref") if self._cal is not None else None
            if pre and tuple(pre.get("roi") or ()) == rk:
                self._refs[rk] = pre
            else:
                self._refs[rk] = door_reference(self.frame, self.timeline, roi)
        return self._refs[rk]


def _probe_door(ctx: _Ctx, req: ProbeRequest) -> ProbeResult:
    roi = req.roi or ctx.roi("door", req.t)
    if roi is None:
        return ProbeResult(req.cue, req.t, None, 0.0, [req.t], "cv:door_diff", {"error": "no_door_roi"})
    ref_info = ctx.door_ref(roi)
    ref_t = ref_info["t"]
    ref = ctx.frame(ref_t)
    votes, raws = [], []
    ts = [req.t] + list(req.extra_ts[:2])
    for t in ts:
        persons = cheap.persons_at(ctx.timeline, t) + cheap.persons_at(ctx.timeline, ref_t)
        v, c, raw = cheap.door_state(ctx.frame(t), ref, roi, persons)
        votes.append((v, c))
        raws.append({"t": t, **raw})
    base = {"ref_t": ref_t, "ref_why": ref_info.get("why"), "ref_candidates": ref_info.get("candidates"),
            "ref_cluster": ref_info.get("cluster"), "ref_gap": ref_info.get("gap"),
            "ref_explained_by": ref_info.get("explained_by"), "roi": [round(float(v), 4) for v in roi], "frames": raws}
    if ref_info.get("ambiguous"):  # abstaining beats a possibly inverted reference
        return ProbeResult(req.cue, req.t, None, 0.0, ts, "cv:door_diff", {**base, "why": "ambiguous_reference"})
    decided = [(v, c) for v, c in votes if v is not None]
    n_open = sum(v for v, _ in decided)
    if not decided or n_open * 2 == len(decided) and votes[0][0] is None:
        return ProbeResult(req.cue, req.t, None, 0.0, ts, "cv:door_diff", base)
    val = n_open * 2 > len(decided) if n_open * 2 != len(decided) else bool(votes[0][0])
    conf = float(np.mean([c for v, c in decided if v == val]))
    return ProbeResult(req.cue, req.t, val, round(conf, 3), ts, "cv:door_diff", base)


def _probe_blocked(ctx: _Ctx, req: ProbeRequest) -> ProbeResult:
    ts = (ctx.timeline.get("series") or {}).get("t") or [0.0]
    ref_t = float(ts[0])
    ref = ctx.frame(ref_t) if abs(ref_t - req.t) > 1e-3 else None
    v, c, raw = cheap.camera_blocked(ctx.frame(req.t), ref)
    return ProbeResult(req.cue, req.t, v, c, [req.t], "cv:blocked", {"ref_t": ref_t, **raw})


def _probe_vlm(ctx: _Ctx, req: ProbeRequest) -> ProbeResult:
    spec = questions.SPECS[req.cue]
    ts = [req.t] + (list(req.extra_ts[:2]) if spec.multi == "any" else [])
    coco = None
    if req.cue in COCO_MAP:
        coco = cheap.coco_evidence(ctx.timeline, req.t, COCO_MAP[req.cue], window=1.0, strong=COCO_STRONG)

    if ctx.vlm_s >= budget_s() or time.perf_counter() - ctx.t0 >= budget_s():
        val, conf, raw = None, 0.0, {"skipped": "budget"}
    else:
        roi = req.roi or (ctx.roi(spec.roi, req.t) if spec.roi else None)
        imgs = [(_crop(ctx.frame(t), roi, spec.margin, spec.min_side) if roi else _full(ctx.frame(t))) for t in ts]
        labels = questions.frame_labels(ts) if len(ts) > 1 else None
        close_up = roi is not None and not _is_large(roi, spec.margin)
        base = spec.prompt if (close_up or not spec.full_prompt) else spec.full_prompt
        prompt = base + (questions.MULTI_SUFFIX if len(ts) > 1 else "")
        client = get_client()
        tc = time.perf_counter()
        if spec.kind == "count":
            n, conf, raw = client.count(imgs, prompt, labels=labels, deadline=ctx.deadline)
            val = None if n is None else n >= 2
            raw["count"] = n
        else:
            val, conf, raw = client.yesno(imgs, prompt, labels=labels, deadline=ctx.deadline)
        ctx.vlm_s += time.perf_counter() - tc
        raw["roi"] = [round(float(v), 4) for v in roi] if roi is not None else None
        raw["img_shape"] = list(imgs[0].shape[:2])
    source = "vlm"
    if coco is not None:
        raw["coco"] = coco
        if val is None and coco["n_strong"] >= 2:
            val, conf, source = True, round(min(0.8, coco["max_score"]), 3), "coco"
    return ProbeResult(req.cue, req.t, val, conf, ts, source, raw)


def _door_is_cheap(ctx: _Ctx, req: ProbeRequest) -> bool:
    """Door probe costs ~0.05 s only when its ROI and closed reference are already known (zone in
    the timeline or a passed calibration, reference in calibration["door_ref"] or computed earlier
    in this call); otherwise it would read ~10 frames / run calibration."""
    zone = (ctx.timeline.get("zones") or {}).get("door")
    roi = req.roi or (cheap.door_roi_from_zone(zone) if zone else (ctx._cal or {}).get("door"))
    if roi is None:
        return False
    rk = tuple(round(float(v), 4) for v in roi)
    pre = (ctx._cal or {}).get("door_ref") or {}
    return rk in ctx._refs or tuple(pre.get("roi") or ()) == rk


def _deadline_result(req: ProbeRequest) -> ProbeResult:
    return ProbeResult(req.cue, req.t, None, 0.0, [req.t], "", {"why": "deadline", "skipped": "deadline"})


def run_probes(video_path: str, requests: list[ProbeRequest], *, timeline: dict,
               transform: Callable[[Any], Any] | None = None, calibration: dict | None = None,
               deadline: float | None = None) -> list[ProbeResult]:
    """One ProbeResult per request (same order). Never raises.

    One call = one VLM batch: a server found dead (or failing repeatedly) during this call stays
    marked down for the rest of it; the next call re-checks once.

    `deadline` (time.monotonic() value): once passed, remaining VLM requests return value=None with
    raw why "deadline" without calling the VLM; CV cues still run when cheap (camera_blocked always;
    door_open only if its ROI and reference are already known)."""
    ctx = _Ctx(video_path, timeline, transform, calibration, deadline)
    if any(r.cue in questions.SPECS for r in requests) and not (deadline is not None and time.monotonic() >= deadline):
        try:
            get_client().begin_batch()
        except Exception as e:  # noqa: BLE001
            log.warning("VLM client begin_batch failed: %s", e)
    out: list[ProbeResult] = []
    for req in requests:
        try:
            late = deadline is not None and time.monotonic() >= deadline
            if late and (req.cue in questions.SPECS or (req.cue == "door_open" and not _door_is_cheap(ctx, req))):
                r = _deadline_result(req)
            elif req.cue == "door_open":
                r = _probe_door(ctx, req)
            elif req.cue == "camera_blocked":
                r = _probe_blocked(ctx, req)
            elif req.cue in questions.SPECS:
                r = _probe_vlm(ctx, req)
            else:
                r = ProbeResult(req.cue, req.t, None, 0.0, [req.t], "", {"error": "unknown_cue"})
        except Exception as e:  # noqa: BLE001
            log.warning("probe %s @%.2f failed: %s", req.cue, req.t, e)
            r = ProbeResult(req.cue, req.t, None, 0.0, [req.t], "", {"error": f"{type(e).__name__}: {e}"})
        out.append(r)
    return out
