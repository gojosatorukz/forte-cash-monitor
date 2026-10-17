"""Cheap CV probes (CPU, milliseconds): camera blocked, door open/closed by appearance change,
COCO-detection evidence from the perception timeline.

All geometry is normalized (x0, y0, x1, y1) in the (possibly transformed) frame; nothing assumes
fixed pixels. The burned-in timestamp (yellow box) is found by colour and masked out.
"""
from __future__ import annotations

import bisect

import cv2
import numpy as np

# ----------------------------------------------------------------------------- helpers


def to_px(box, w: int, h: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    X0, Y0 = int(np.clip(np.floor(x0 * w), 0, w - 1)), int(np.clip(np.floor(y0 * h), 0, h - 1))
    X1, Y1 = int(np.clip(np.ceil(x1 * w), X0 + 1, w)), int(np.clip(np.ceil(y1 * h), Y0 + 1, h))
    return X0, Y0, X1, Y1


def expand(box, margin: float, aspect_x: float = 1.0) -> list[float]:
    """Pad a normalized box by `margin` x its size on each side (x margin scaled by aspect_x)."""
    x0, y0, x1, y1 = box
    dx, dy = (x1 - x0) * margin * aspect_x, (y1 - y0) * margin
    return [max(0.0, x0 - dx), max(0.0, y0 - dy), min(1.0, x1 + dx), min(1.0, y1 + dy)]


def door_roi_from_zone(zone, side: float = 2.5, up: float = 1.0, down: float = 0.5, max_w: float = 0.45) -> list[float]:
    """Door-leaf ROI from the doorway zone (median person box where tracks are born/die).

    The leaf hinges to one side of the doorway (which side depends on the camera/flip), and its
    upper part (glass) is above the person's head, so pad widely sideways and upwards."""
    x0, y0, x1, y1 = zone
    w, h = x1 - x0, y1 - y0
    cx = (x0 + x1) / 2
    half = min(w * (0.5 + side), max_w / 2)
    return [max(0.0, cx - half), max(0.0, y0 - up * h), min(1.0, cx + half), min(1.0, y1 + down * h)]


def overlay_mask(img: np.ndarray, min_area: int = 30, pad: int = 3) -> np.ndarray:
    """Bool mask of burned-in overlays (saturated yellow boxes with black digits), bbox-filled."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    yellow = cv2.inRange(hsv, (22, 150, 170), (38, 255, 255))
    n, _, stats, _ = cv2.connectedComponentsWithStats(yellow, connectivity=8)
    m = np.zeros(img.shape[:2], bool)
    h, w = m.shape
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area >= min_area:
            m[max(0, y - pad): min(h, y + bh + pad), max(0, x - pad): min(w, x + bw + pad)] = True
    return m


def _gray_small(img: np.ndarray, long_side: int = 192) -> np.ndarray:
    h, w = img.shape[:2]
    s = long_side / max(h, w)
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if s < 1:
        g = cv2.resize(g, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)
    return g.astype(np.float32)


# ----------------------------------------------------------------------------- camera blocked


def frame_stats(img: np.ndarray) -> dict:
    g = _gray_small(img)
    mean, std = float(g.mean()), float(g.std())
    z = (g - mean) / max(std, 1.0)
    bs = 8
    h, w = (z.shape[0] // bs) * bs, (z.shape[1] // bs) * bs
    blocks = z[:h, :w].reshape(h // bs, bs, w // bs, bs).std(axis=(1, 3)) if h and w else np.zeros((1, 1))
    # contrast-normalized only (no absolute floors): a dark room keeps its relative texture
    flat = blocks < 0.08
    gx = cv2.Sobel(z, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(z, cv2.CV_32F, 0, 1, ksize=3)
    edge = float((np.hypot(gx, gy) > 0.5).mean())
    return {"mean": round(mean, 2), "std": round(std, 2), "flat_frac": round(float(flat.mean()), 4),
            "edge_frac": round(edge, 4)}


def camera_blocked(img: np.ndarray, ref: np.ndarray | None = None) -> tuple[bool, float, dict]:
    """Blocked = low edge content (relative to the reference frame when given) AND either a
    (near-)uniform frame or a mostly flat frame.

    All measures are contrast-normalized, so lights off (intensity scaled down, structure kept) is
    not "blocked" however dark it gets. Without a reference the edge floor is absolute (0.02) and
    the confidence is lower.
    """
    s = frame_stats(img)
    raw = {"stats": s}
    thr, edge_floor, conf_cap = 0.8, 0.02, 0.75
    if ref is not None:
        r = frame_stats(ref)
        raw["ref_stats"] = r
        thr = float(np.clip(r["flat_frac"] + 0.3, 0.6, 0.8))
        edge_floor, conf_cap = 0.5 * r["edge_frac"], 0.95
    raw["thr"], raw["edge_floor"] = round(thr, 3), round(edge_floor, 4)
    low_edges = s["edge_frac"] < edge_floor
    if low_edges and s["std"] < 4.0:
        return True, conf_cap, {**raw, "why": "uniform_frame"}
    if low_edges and s["flat_frac"] > thr:
        conf = float(np.clip(0.6 + (s["flat_frac"] - thr) * 2, 0.6, conf_cap))
        return True, round(conf, 3), {**raw, "why": "mostly_flat"}
    conf = float(np.clip(0.6 + max(thr - s["flat_frac"], 0.0), 0.6, 0.95))
    return False, round(conf, 3), raw


# ----------------------------------------------------------------------------- door state

DOOR_THR = 0.06  # change fraction: open frames 0.085-0.19, closed 0-0.033 on the probe cases


def door_state(
    img: np.ndarray,
    ref: np.ndarray,
    roi,
    person_boxes: list,
    thr: float | None = None,
    diff_z: float = 0.9,
    person_pad: float = 0.03,
    min_valid: float = 0.25,
) -> tuple[bool | None, float, dict]:
    """Door open if the door ROI differs from the closed reference in > thr of its unmasked pixels.

    Both crops are z-normalized (brightness/exposure invariant) and compared after a blur; persons
    (timeline boxes at both moments, slightly padded) and burned-in overlays are masked. Returns
    None when too little of the ROI is visible.
    """
    thr = DOOR_THR if thr is None else thr
    h, w = img.shape[:2]
    if ref.shape[:2] != (h, w):
        ref = cv2.resize(ref, (w, h), interpolation=cv2.INTER_AREA)
    X0, Y0, X1, Y1 = to_px(roi, w, h)
    mask = np.ones((h, w), bool)
    mask &= ~overlay_mask(img)
    mask &= ~overlay_mask(ref)
    for b in person_boxes:
        bx = expand(b[:4], person_pad)
        px0, py0, px1, py1 = to_px(bx, w, h)
        mask[py0:py1, px0:px1] = False
    m = mask[Y0:Y1, X0:X1]
    valid = float(m.mean()) if m.size else 0.0
    raw = {"roi": [round(float(v), 4) for v in roi], "valid_frac": round(valid, 3), "thr": thr}
    if valid < min_valid:
        return None, 0.0, {**raw, "why": "occluded"}

    def norm(x):
        g = cv2.GaussianBlur(cv2.cvtColor(x[Y0:Y1, X0:X1], cv2.COLOR_BGR2GRAY).astype(np.float32), (5, 5), 0)
        v = g[m]
        return (g - float(v.mean())) / max(float(v.std()), 4.0)

    a, b = norm(img), norm(ref)
    change = ((np.abs(a - b) > diff_z) & m).astype(np.uint8)
    change = cv2.morphologyEx(change, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    frac = float(change[m].mean())
    raw["change_frac"] = round(frac, 4)
    val = frac > thr
    conf = float(np.clip(0.5 + abs(frac - thr) / (2 * thr), 0.5, 0.95))
    return val, round(conf, 3), raw


# ----------------------------------------------------------------------------- COCO evidence


def coco_evidence(
    timeline: dict, t: float, classes, window: float = 1.0, roi=None, strong: float = 0.6
) -> dict:
    """Detections of `classes` in timeline samples within +-window s of t (centre inside roi if given).

    Returns {"max_score", "n_strong" (samples with a detection >= strong), "n_samples", "boxes"}.
    """
    ser = timeline.get("series", {})
    ts, objs = ser.get("t") or [], ser.get("objects") or []
    best, n_strong, n, boxes = 0.0, 0, 0, []
    a, b = bisect.bisect_left(ts, t - window - 1e-9), bisect.bisect_right(ts, t + window + 1e-9)
    for ti, row in zip(ts[a:b], objs[a:b]):  # series t is sorted: only the +-window slice
        if abs(ti - t) > window:
            continue
        n += 1
        hit = False
        for o in row:
            if o[0] not in classes:
                continue
            x0, y0, x1, y1, sc = o[1:6]
            if roi is not None:
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                if not (roi[0] <= cx <= roi[2] and roi[1] <= cy <= roi[3]):
                    continue
            best = max(best, sc)
            if sc >= strong:
                hit = True
                boxes.append([o[0], x0, y0, x1, y1, sc])
        n_strong += hit
    return {"max_score": round(float(best), 4), "n_strong": n_strong, "n_samples": n, "boxes": boxes[:5]}


def persons_at(timeline: dict, t: float, window: float | None = None) -> list[list[float]]:
    """Person boxes of the timeline sample nearest to t (raw detections when available)."""
    ser = timeline.get("series", {})
    ts = ser.get("t") or []
    if not ts:
        return []
    k = bisect.bisect_left(ts, t)  # series t is sorted: O(log n) (was a full argmin per call)
    i = min((j for j in (k - 1, k) if 0 <= j < len(ts)), key=lambda j: abs(ts[j] - t))
    if window is not None and abs(ts[i] - t) > window:
        return []
    persons = ser.get("persons") or []
    boxes = [p[2] for p in persons[i]] if i < len(persons) else []
    dets = ser.get("detections")
    if dets and i < len(dets):
        boxes += [d[:4] for d in dets[i]]
    return boxes
