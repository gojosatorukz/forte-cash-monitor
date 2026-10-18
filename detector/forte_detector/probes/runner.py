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
import threading
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

_CLIENT: VLMClient | None = None  # the HTTP client only (per-job state is thread-local inside it)
_CLIENT_LOCK = threading.Lock()


def get_client() -> VLMClient:
    global _CLIENT
    if _CLIENT is None:
        with _CLIENT_LOCK:  # double-checked: concurrent analyze() threads share one client
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


def _lighting_ok(img, ref) -> bool:
    ratio = cheap.global_brightness(img) / max(cheap.global_brightness(ref), 1.0)
    return 1 - DOOR_ILLUM_MAX_SHIFT <= ratio <= 1 / (1 - DOOR_ILLUM_MAX_SHIFT)


def _same_lighting(imgs, ref) -> list[int]:
    """Indices of imgs lit like ref (whole-frame brightness within DOOR_ILLUM_MAX_SHIFT)."""
    return [i for i, im in enumerate(imgs) if _lighting_ok(im, ref)]


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
    imgs = [read(t) for t in pool]
    # lighting guard: frames after a lights-off/on (whole-frame brightness off by > 30 % from the
    # earliest candidate) form spurious "door states" -> not candidates
    keep = _same_lighting(imgs, imgs[0])
    pool, imgs = [pool[i] for i in keep], [imgs[i] for i in keep]
    n = len(pool)
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


DOOR_PERSON_MIN_H = 0.6  # a person "in the doorway" is at least this tall relative to the cashier standing height
DOOR_ILLUM_MAX_SHIFT = 0.3  # whole-frame brightness change vs the reference beyond which door CV abstains
DOOR_PERSON_IN_ROI = 0.3  # ... with at least this share of the box over the door leaf (X-33: 0.41 at the
# handle edge while stepping in; X-32: 1.0)


def _in_roi(box, roi) -> float:
    """Fraction of `box` lying inside `roi`."""
    ix = max(0.0, min(box[2], roi[2]) - max(box[0], roi[0]))
    iy = max(0.0, min(box[3], roi[3]) - max(box[1], roi[1]))
    a = max((box[2] - box[0]) * (box[3] - box[1]), 1e-9)
    return ix * iy / a


def cashier_height(timeline: dict) -> float:
    """Cashier's standing height (normalized): 75th percentile of the cashier-role box heights;
    else the seat box height; else 0.2 of the frame."""
    hs = [p[2][3] - p[2][1] for row in ((timeline.get("series") or {}).get("persons") or [])
          for p in row if len(p) > 2 and p[1] == "cashier"]
    if len(hs) >= 5:
        return float(np.percentile(hs, 75))
    seat = (timeline.get("zones") or {}).get("seat")
    return float(seat[3] - seat[1]) if seat else 0.2


def _transition_gap(read, timeline, roi, pool, label, imgs, occ, events):
    """Ambiguity guard for a minority earliest cluster: (gap, explained_by | None).

    The earliest-cluster rule is a time prior (videos start with the door closed). When the earliest
    state is a minority, trust it only if the first state change is pinned to a short gap
    (<= DOOR_GAP_MAX) and someone really used the door in it. The pool is sparse (<= 10 candidates),
    so the gap is first narrowed with the frames between the two candidates (each compared with both
    sides; frames lit differently from the earlier side are skipped). Door use = a door event whose
    activity window overlaps the gap, or in a gap sample: with a trusted doorway zone (>= 3 points) a
    person box covering >= DOOR_ZONE_PRESENCE of it; without one, a person occluding >=
    DOOR_REF_MAX_OCCLUSION of the leaf ROI, or a full-size person (height >= DOOR_PERSON_MIN_H x the
    cashier's standing height) with >= DOOR_PERSON_IN_ROI of the box over the (leaf-side) ROI - not
    any overlap of the padded ROI (someone behind the glass).

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
        if not _lighting_ok(img, imgs[i0]):  # lights switched in the gap: no state evidence
            continue
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
    zones = timeline.get("zones") or {}
    # same trust rule as door_roi(): a doorway zone from 1-2 births/deaths may be somewhere else
    dp = zones.get("door_points")  # None (older timelines / synthetic) = trusted
    zone = zones.get("door") if (dp is None or dp >= DOOR_ZONE_MIN_POINTS) else None
    h_ref = cashier_height(timeline)
    for t in ts:
        if not (a + lo <= t <= b - lo):
            continue
        boxes = cheap.persons_at(timeline, t)
        if zone is not None:
            hit = any(_zone_cover(zone, bx) >= DOOR_ZONE_PRESENCE for bx in boxes)
        else:
            # no doorway zone (X-32 at HEAD): a full-size person over the door leaf is crossing the
            # doorway (the cashier walks in at 2.5-4.3 s, box in the leaf ROI but covering only ~0.2
            # of it). "Full-size" is relative to the cashier's own standing height, not to the ROI,
            # so small figures behind the glass fail whatever the ROI size
            hit = occ.get(t, 0.0) >= DOOR_REF_MAX_OCCLUSION or any(
                _in_roi(bx, roi) >= DOOR_PERSON_IN_ROI and (bx[3] - bx[1]) >= DOOR_PERSON_MIN_H * h_ref
                for bx in boxes)
        if hit:
            return gap, f"person@{round(t, 3)}"
    return gap, None


# ----------------------------------------------------------------------------- desk ROI

DESK_MIN_AREA = 0.08  # floor on the desk ROI area (fraction of the frame), also >= the seat box
CLOSE_UP_MAX_AREA = 0.2  # ROIs larger than this get the neutral (full-frame) prompt, not "close-up"


def _area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _gap(a, b) -> float:
    """Largest axis gap between two boxes (0 when they overlap on that axis)."""
    gx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    gy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return max(gx, gy)


def plausible_machine(lap, cm) -> bool:
    """A counting machine sits on the same desk as the laptop and is of comparable size; an
    open-vocab "black machine" far away or huge/tiny (monitor, printer, safe) is rejected."""
    if not lap or not cm:
        return False
    lw = lap[2] - lap[0]
    ratio = _area(cm) / max(_area(lap), 1e-6)
    return _gap(lap, cm) < 2.5 * lw and 0.15 <= ratio <= 3.0  # opposite desk ends: ~1.5-2.1 lw (X-38, X-16, X-4)


def hands_roi(timeline: dict, t: float):
    """Hand-over area at t: the box spanning the visitor(s) and the cashier in the nearest timeline
    sample (a hand-over happens between them), or the visitor alone; None without a visitor."""
    ser = timeline.get("series") or {}
    ts = ser.get("t") or []
    if not ts:
        return None
    i = int(np.argmin([abs(x - t) for x in ts]))
    if abs(ts[i] - t) > 1.0:
        return None
    rows = (ser.get("persons") or [[]] * len(ts))[i]
    vis = [p[2] for p in rows if len(p) > 2 and p[1] != "cashier"]
    if not vis:
        return None
    boxes = vis + [p[2] for p in rows if len(p) > 2 and p[1] == "cashier"]
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _coco_laptop_video(timeline: dict):
    """Median box of strong COCO laptop detections over the whole video (None if < 3)."""
    ser = timeline.get("series") or {}
    boxes = [o[1:5] for row in (ser.get("objects") or []) for o in row if o[0] == "laptop" and o[5] >= 0.6]
    if len(boxes) < 3:
        return None
    return [float(v) for v in np.median(np.asarray(boxes, float), axis=0)]


def desk_roi(lap, cm, coco_lap, ov_desk, seat) -> tuple[list[float] | None, str | None]:
    """Cash work-surface ROI (normalized) and its source.

    Order: union of laptop + counting machine (machine only if plausible next to the laptop) ->
    one padded anchor object (laptop, machine, or the COCO laptop from the timeline) -> the
    open-vocab desk box only if it is near the cashier seat -> None (caller uses the full frame).
    Anchored boxes are padded, extended toward the seat (items lie between the cashier and the
    equipment; on low/oblique views in front of the laptop) and grown to a minimum area.
    """
    if lap and cm and plausible_machine(lap, cm):
        u = [min(lap[0], cm[0]), min(lap[1], cm[1]), max(lap[2], cm[2]), max(lap[3], cm[3])]
        w, h = u[2] - u[0], u[3] - u[1]
        box, src = [u[0] - 0.1 * w, u[1] - 0.5 * h, u[2] + 0.1 * w, u[3] + 0.5 * h], "laptop+counting_machine"
    else:
        anchor, src = ((lap, "laptop") if lap else (cm, "counting_machine") if cm
                       else (coco_lap, "coco_laptop") if coco_lap else (None, None))
        if anchor is None:
            if ov_desk and seat:
                sc = ((seat[0] + seat[2]) / 2, (seat[1] + seat[3]) / 2)
                dc = ((ov_desk[0] + ov_desk[2]) / 2, (ov_desk[1] + ov_desk[3]) / 2)
                sd = float(np.hypot(seat[2] - seat[0], seat[3] - seat[1]))
                if float(np.hypot(sc[0] - dc[0], sc[1] - dc[1])) <= 1.5 * sd:
                    return [round(float(v), 4) for v in ov_desk], "openvocab_near_seat"
            return None, None
        w, h = anchor[2] - anchor[0], anchor[3] - anchor[1]
        box = [anchor[0] - 1.0 * w, anchor[1] - 0.75 * h, anchor[2] + 1.0 * w, anchor[3] + 0.75 * h]
    if seat:  # extend toward the seat centre, halfway
        sx, sy = (seat[0] + seat[2]) / 2, (seat[1] + seat[3]) / 2
        box[0] = min(box[0], box[0] + 0.5 * (sx - box[0])) if sx < box[0] else box[0]
        box[2] = max(box[2], box[2] + 0.5 * (sx - box[2])) if sx > box[2] else box[2]
        box[1] = min(box[1], box[1] + 0.5 * (sy - box[1])) if sy < box[1] else box[1]
        box[3] = max(box[3], box[3] + 0.5 * (sy - box[3])) if sy > box[3] else box[3]
    min_area = max(DESK_MIN_AREA, _area(seat) if seat else 0.0)
    a = _area(box)
    if a < min_area:  # grow about the centre
        k = float(np.sqrt(min_area / max(a, 1e-6)))
        cx, cy, w, h = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2, box[2] - box[0], box[3] - box[1]
        box = [cx - k * w / 2, cy - k * h / 2, cx + k * w / 2, cy + k * h / 2]
    box = [max(0.0, box[0]), max(0.0, box[1]), min(1.0, box[2]), min(1.0, box[3])]
    return [round(float(v), 4) for v in box], src


# ----------------------------------------------------------------------------- door ROI

DOOR_ZONE_MIN_POINTS = 3  # a doorway zone from fewer births/deaths is a guess (one track can be anything)
DOOR_ROI_MIN_H = 0.1  # a door-leaf ROI thinner than this (fraction of frame height) is degenerate


def door_roi(zone, door_points: int | None, ov_door, seat) -> tuple[list[float] | None, str | None]:
    """Door-leaf ROI (normalized) and its source.

    1. doorway zone from >= 3 track births/deaths -> door_roi_from_zone (person-sized box at the door);
    2. open-vocab box: a large one ("glass door") is used as is (width-capped); a small one is the
       handle -> a person-sized pseudo zone centred on it (size of the seat box, the cashier's
       box) -> door_roi_from_zone. Padding the handle box itself gives a ~0.03-high strip that
       misses the door leaf (X-32 after perception rounds 4-5: no door zone, ROI 0.03 high).
    3. None = door absent. A doorway zone from only 1-2 births/deaths is NOT used on its own: on an
       external top-down table clip (ext12) two track ends at the frame corner made a phantom door
       [0, 0, 0.44, 0.47] and the door probe then "saw" it open (T5).
    Any result thinner than DOOR_ROI_MIN_H is rebuilt as a person-sized pseudo zone around its centre.
    """
    def person_zone(c):
        if seat:
            w, h = seat[2] - seat[0], seat[3] - seat[1]
        else:
            w, h = 0.12, 0.2
        return [c[0] - w / 2, c[1] - h / 2, c[0] + w / 2, c[1] + h / 2]

    def centre(b):
        return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)

    if zone and (door_points is None or door_points >= DOOR_ZONE_MIN_POINTS):
        roi, src = cheap.door_roi_from_zone(zone), "timeline"
    elif ov_door:
        if _area(ov_door) >= 0.03:
            cx = centre(ov_door)[0]
            half = min((ov_door[2] - ov_door[0]) / 2, 0.225)
            roi, src = [cx - half, ov_door[1], cx + half, ov_door[3]], "openvocab"
        else:
            roi, src = cheap.door_roi_from_zone(person_zone(centre(ov_door))), "openvocab_handle"
    else:
        return None, None
    if roi[3] - roi[1] < DOOR_ROI_MIN_H:
        roi, src = cheap.door_roi_from_zone(person_zone(centre(roi))), f"{src}+min_height"
    roi = [max(0.0, roi[0]), max(0.0, roi[1]), min(1.0, roi[2]), min(1.0, roi[3])]
    return [round(float(v), 4) for v in roi], src


def _door_activity(read, timeline, roi, n: int = 8) -> float:
    """Largest pairwise door_state change over n low-occupancy frames spread over the video: how much
    this ROI's appearance varies (a moving door leaf varies, a wall or desk does not)."""
    ts = [float(t) for t in ((timeline.get("series") or {}).get("t") or [0.0])]
    ok = [t for t in ts if _overlap_frac(roi, cheap.persons_at(timeline, t)) <= DOOR_REF_MAX_OCCLUSION] or ts[:1]
    if len(ok) > n:
        ok = [ok[i] for i in sorted(set(np.linspace(0, len(ok) - 1, n).round().astype(int).tolist()))]
    imgs = [read(t) for t in ok]
    # lighting guard: compare only frames lit like the first one (a lights-off would make any
    # half "move"); `ok` is already filtered for persons occluding THIS half
    keep = _same_lighting(imgs, imgs[0])
    ok, imgs = [ok[i] for i in keep], [imgs[i] for i in keep]
    best = 0.0
    for i in range(len(ok)):
        for j in range(i + 1, len(ok)):
            persons = cheap.persons_at(timeline, ok[i]) + cheap.persons_at(timeline, ok[j])
            v, _, raw = cheap.door_state(imgs[i], imgs[j], roi, persons)
            if v is not None:
                best = max(best, raw.get("change_frac", 0.0))
    return best


def pick_leaf_side(read, timeline, roi, handle) -> tuple[list[float], str]:
    """A handle-centred ROI spans both sides of the handle, but the leaf (hinged on one side) lies on
    only one of them; the other half (wall, desk, cashier area) dilutes the change and the open and
    closed states blur into one cluster (X-33 in full.analyze). Keep the half whose appearance varies
    most over the video; keep the full ROI when neither varies (door never moves) or they tie."""
    hx = (handle[0] + handle[2]) / 2
    left = [roi[0], roi[1], hx, roi[3]]
    right = [hx, roi[1], roi[2], roi[3]]
    if left[2] - left[0] < 0.05 or right[2] - right[0] < 0.05:
        return roi, "both"
    al, ar = _door_activity(read, timeline, left), _door_activity(read, timeline, right)
    if max(al, ar) <= cheap.DOOR_THR or abs(al - ar) < 0.25 * max(al, ar):
        return roi, "both"
    return ([round(v, 4) for v in left], "left") if al > ar else ([round(v, 4) for v in right], "right")


DOOR_ROI_VERSION = 5  # bump when the door ROI chain changes: older calibrations get it recomputed


def select_door_roi(read, timeline: dict, ov_door) -> tuple[list[float] | None, str | None, str | None]:
    """The full door ROI chain: door_roi() source selection, then the leaf-side choice for a
    handle-centred ROI. Returns (roi, source, leaf_side)."""
    zones = timeline.get("zones") or {}
    roi, src = door_roi(zones.get("door"), zones.get("door_points"), ov_door, zones.get("seat"))
    side = None
    if roi is not None and src and src.startswith("openvocab_handle"):
        try:
            roi, side = pick_leaf_side(read, timeline, roi, ov_door)
        except Exception as e:  # noqa: BLE001
            log.warning("door leaf side failed: %s", e)
    return roi, src, side


# ----------------------------------------------------------------------------- calibration


# Zone presence thresholds on the open-vocab score (OWLv2). Training clips (42 open videos, PC run
# cc70a19): min safe 0.22, laptop 0.33, counting machine 0.165, door 0.165, desk 0.41.
ZONE_MIN_SCORE = {"safe": 0.2, "laptop": 0.3, "counting_machine": 0.15, "door": 0.15, "desk": 0.3}
ABSENT_ZONES = ("door", "safe", "laptop")  # desk: None = full frame, never "absent"
ZONE_OF_CUE = {"safe_open": "safe", "key_in_safe": "safe", "screen_on_unlocked": "laptop"}
ZONE_NAMES_RU = {"door": "дверь", "safe": "сейф", "laptop": "ноутбук/экран", "desk": "рабочий стол"}


def room_gate(read, timeline: dict) -> bool | None:
    """Room view (True) vs close-up of a table/objects (False), asked on up to 2 low-occupancy frames.
    False only when confident: one "no" at conf >= 0.8 or two frames agreeing on "no", and no
    confident "yes". None = no usable answer (VLM down/unsure): never treated as close-up."""
    try:
        ts = openvocab.pick_frames(timeline, 2)
        ans = [get_client().yesno([_full(read(t))], questions.DOOR_GATE_PROMPT)[:2] for t in ts]
    except Exception as e:  # noqa: BLE001
        log.warning("room gate failed: %s", e)
        return None
    dec = [(v, c) for v, c in ans if v is not None]
    if not dec:
        return None
    if any(v and c >= 0.8 for v, c in dec):
        return True
    nos = [c for v, c in dec if v is False]
    if nos and (len(nos) >= 2 or max(nos) >= 0.8) and not any(v for v, _ in dec):
        return False
    return True if all(v for v, _ in dec) else None


def absent_note_ru(absent: list) -> str:
    if not absent:
        return ""
    names = ", ".join(ZONE_NAMES_RU[z] for z in absent)
    return ("зона не найдена: " if len(absent) == 1 else "зоны не найдены: ") + names


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
    ov_failed = bool((cal.get("meta") or {}).get("error"))
    # room-view gate (one cheap VLM question, <= 2 frames): a confident "close-up" is the second signal
    # needed before any zone is declared absent; None = no answer (VLM down / unsure) = not close-up
    gate = None if ov_failed else room_gate(read, timeline)
    cal["meta"]["room_gate"] = gate
    close_up = gate is False
    # explicit "object absent" needs a low score PLUS a second signal: no box at all, or a box under
    # the zone threshold while the gate says close-up. Weak boxes are KEPT (recall first) and flagged.
    scores = cal.get("scores") or {}
    cal["weak"] = []
    for k, thr in ZONE_MIN_SCORE.items():
        if cal.get(k) is not None and scores.get(k) is not None and scores[k] < thr:
            if close_up:
                cal[k] = None
            else:
                cal["weak"].append(k)
    # door ROI = what the door_open probe uses: doorway zone from tracks first (open-vocab "glass
    # door" often picks the fixed glass panel), open-vocab box only as a fallback
    ov_door = cal.get("door")
    cal["door_openvocab"] = ov_door
    cal["door"], cal["meta"]["door_source"], cal["meta"]["door_leaf_side"] = select_door_roi(read, timeline, ov_door)
    cal["meta"]["door_roi_version"] = DOOR_ROI_VERSION
    if cal["door"] is not None and close_up:
        cal["door"], cal["meta"]["door_source"] = None, "vlm_close_up"
    if cal.get("laptop") is None:  # evidence-based fallback: strong COCO laptop detections
        cl = _coco_laptop_video(timeline)
        if cl is not None:
            cal["laptop"] = [round(v, 4) for v in cl]
            cal["meta"]["laptop_source"] = "coco"
    cal["desk_openvocab"] = cal.get("desk")
    seat = (timeline.get("zones") or {}).get("seat")
    cal["desk"], cal["meta"]["desk_source"] = desk_roi(  # None = use the full frame (not "no desk")
        cal.get("laptop"), cal.get("counting_machine"), _coco_laptop_video(timeline), cal["desk_openvocab"], seat)
    # open-vocab itself failed (OOM, missing model, bad frame): nothing is known to be absent ->
    # cues keep their full-frame fallback
    # door: no ROI = absent (cannot be probed; that is what kills the phantom T5). safe/laptop: absent
    # only with the close-up gate too - with no box in a room view the cues keep the full-frame VLM
    cal["absent"] = [] if ov_failed else [z for z in ABSENT_ZONES
                                          if cal.get(z) is None and (z == "door" or close_up)]
    cal["absent_note_ru"] = absent_note_ru(cal["absent"])
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


def autocontrast(img: np.ndarray, lo: float = 1.0, hi: float = 99.0) -> np.ndarray:
    """Linear stretch of the crop's lo..hi luminance percentiles to 0..255 (same gain on all
    channels, so colours keep their hue). A no-op on already full-range crops."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    a, b = np.percentile(g, [lo, hi])
    if b - a < 8 or (a <= 2 and b >= 253):
        return img
    out = (img.astype(np.float32) - a) * (255.0 / (b - a))
    return np.clip(out, 0, 255).astype(np.uint8)


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
        self._door_roi: list | None = None  # [roi] once computed in this call
        self.t0 = time.perf_counter()
        self.vlm_s = 0.0

    @property
    def cal(self) -> dict:
        if self._cal is None:
            self._cal = self.timeline.get("calibration") or calibrate(self.video_path, self.timeline, self.transform)
        return self._cal

    @property
    def absent(self) -> list:
        """Zones calibration positively found absent (only from a real calibrate() result)."""
        cal = self.cal or {}
        if (cal.get("meta") or {}).get("door_roi_version") != DOOR_ROI_VERSION:
            return []  # older calibration: its absent list used other rules -> not trusted
        return list(cal.get("absent") or [])

    def roi(self, key: str, t: float):
        if key == "door":
            cal = self._cal
            if cal is not None and (cal.get("meta") or {}).get("door_roi_version") == DOOR_ROI_VERSION:
                return cal.get("door")  # calibrate() already ran the current chain (None = no door)
            if self._door_roi is None:  # once per call: same ROI -> one cached reference
                ov = (self.cal or {}).get("door_openvocab")
                roi = select_door_roi(self.frame, self.timeline, ov)[0]
                # nothing better from the current chain: keep a caller-supplied / older door box
                self._door_roi = [roi if roi is not None else (cal or {}).get("door")]
            return self._door_roi[0]
        if key == "hands":  # hand-over area: visitor + cashier boxes at t (people at the desk)
            return hands_roi(self.timeline, t)
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
        why = "zone_absent" if "door" in ctx.absent else "no_door_roi"
        return ProbeResult(req.cue, req.t, None, 0.0, [req.t], "cv:door_diff", {"error": why, "why": why})
    ref_info = ctx.door_ref(roi)
    ref_t = ref_info["t"]
    ref = ctx.frame(ref_t)
    votes, raws = [], []
    ts = [req.t] + list(req.extra_ts[:2])
    for t in ts:
        persons = cheap.persons_at(ctx.timeline, t) + cheap.persons_at(ctx.timeline, ref_t)
        img = ctx.frame(t)
        ratio = cheap.global_brightness(img) / max(cheap.global_brightness(ref), 1.0)
        if not (1 - DOOR_ILLUM_MAX_SHIFT <= ratio <= 1 / (1 - DOOR_ILLUM_MAX_SHIFT)):
            # room lights switched off/on (X-11, clean X-12): the leaf's appearance change is
            # dominated by the lighting, not by the door -> undecidable for this frame
            v, c, raw = None, 0.0, {"why": "illumination_change", "brightness_ratio": round(ratio, 3)}
        else:
            v, c, raw = cheap.door_state(img, ref, roi, persons)
            raw["brightness_ratio"] = round(ratio, 3)
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
    if getattr(ctx, "blocked_series", None) is None:  # one low-res pass per call (bounded past the deadline)
        ctx.blocked_series = cheap.camera_blocked_series(ctx.video_path, ctx.transform, ref_t, deadline=ctx.deadline,
                                                         duration=ctx.timeline.get("duration") or None)
    v, c, raw = cheap.series_at(ctx.blocked_series, req.t)
    return ProbeResult(req.cue, req.t, v, c, [raw.get("sample_t", req.t)], "cv:blocked", raw)


def _probe_vlm(ctx: _Ctx, req: ProbeRequest) -> ProbeResult:
    spec = questions.SPECS[req.cue]
    ts = [req.t] + (list(req.extra_ts[:2]) if spec.multi == "any" else [])
    coco = None
    if req.cue in COCO_MAP:
        coco = cheap.coco_evidence(ctx.timeline, req.t, COCO_MAP[req.cue], window=1.0, strong=COCO_STRONG)

    zone = ZONE_OF_CUE.get(req.cue)
    roi = req.roi or (ctx.roi(spec.roi, req.t) if spec.roi else None)  # laptop: incl. per-frame COCO
    if roi is None and zone is not None and zone in ctx.absent:
        # calibration found no such object (safe/laptop) and nothing else locates it: no answer
        val, conf, raw = None, 0.0, {"why": "zone_absent", "zone": zone}
    elif ctx.vlm_s >= budget_s() or time.perf_counter() - ctx.t0 >= budget_s():
        val, conf, raw = None, 0.0, {"skipped": "budget"}
    else:
        imgs = [(_crop(ctx.frame(t), roi, spec.margin, spec.min_side) if roi else _full(ctx.frame(t))) for t in ts]
        if spec.autocontrast and roi is not None:
            imgs = [autocontrast(im) for im in imgs]
        labels = questions.frame_labels(ts) if len(ts) > 1 else None
        # close-up wording only for genuinely small crops; 0.2-0.5 of the frame gets the neutral prompt
        close_up = roi is not None and _area(cheap.expand(roi, spec.margin)) < CLOSE_UP_MAX_AREA
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
    raw why "deadline" without calling the VLM; CV cues still run when cheap (camera_blocked always:
    its series is built first, bounded past the deadline; door_open only if its ROI and reference
    are already known)."""
    ctx = _Ctx(video_path, timeline, transform, calibration, deadline)
    try:  # camera series first, before any VLM request can eat the time
        next((_probe_blocked(ctx, r) for r in requests if r.cue == "camera_blocked"), None)
    except Exception as e:  # noqa: BLE001
        log.warning("camera_blocked series failed: %s", e)
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
