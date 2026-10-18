"""Data-derived roles and zones (no fixed pixels): cashier seat from the dominant stationary track,
door from birth/death points. Geometry is in "isotropic" units: x is scaled by the frame aspect
(w/h) so distances are in units of frame height regardless of orientation.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from forte_detector.perception.tracker import Track, iou


def iso_centers(boxes, aspect: float) -> np.ndarray:
    b = np.asarray(boxes, float).reshape(-1, 4)
    return np.stack([(b[:, 0] + b[:, 2]) / 2 * aspect, (b[:, 1] + b[:, 3]) / 2], axis=1)


def iso_diag(box, aspect: float) -> float:
    return float(np.hypot((box[2] - box[0]) * aspect, box[3] - box[1]))


def median_box(boxes) -> list[float]:
    return [float(v) for v in np.median(np.asarray(boxes, float).reshape(-1, 4), axis=0)]


@dataclass
class Anchor:
    track_id: int
    box: list[float]  # robust median box of the stationary samples
    idx: list[int]  # sample indices of the track that belong to the stationary cluster
    count: int
    n_used: int = 0  # samples actually clustered (<= ANCHOR_MAX_POINTS; long tracks are subsampled)


ANCHOR_MAX_POINTS = 1000  # clustering is O(m^2) in memory/time: 1000 points ~ 8 MB per matrix


def stationary_anchor(tr: Track, aspect: float, radius: float = 0.3, size_tol: float = 0.25,
                      min_frac: float = 0.4, min_rel_h: float = 0.45,
                      max_points: int = ANCHOR_MAX_POINTS) -> Anchor:
    """Seat-like stationary location of a track.

    Samples are clustered greedily (densest first) by centre distance < radius x box diag AND box
    height within size_tol, so sitting and standing at the same spot are different clusters. Among
    clusters holding >= min_frac of the largest one, the one with the smallest boxes wins: a cashier
    who sits briefly and then stands next to the desk for longer still gets the seated box as seat.

    Long tracks (> max_points samples, e.g. an hour-long upload) are clustered on max_points
    samples evenly spaced in index (deterministic); every sample then takes the cluster of its
    nearest subsample in time, so idx/count still refer to the full track.
    """
    b_all = np.asarray(tr.boxes, float).reshape(-1, 4)
    n_all = len(b_all)
    sub = None
    if n_all > max_points:
        sub = np.unique(np.linspace(0, n_all - 1, max_points).round().astype(int))
        b = b_all[sub]
    else:
        b = b_all
    c = iso_centers(b, aspect)
    hgt = np.maximum(b[:, 3] - b[:, 1], 1e-6)
    d = float(np.median(np.hypot((b[:, 2] - b[:, 0]) * aspect, b[:, 3] - b[:, 1])))
    r = radius * max(d, 1e-6)
    dist = np.linalg.norm(c[:, None, :] - c[None, :, :], axis=-1)
    hr = np.abs(np.log(hgt[:, None] / hgt[None, :]))
    adj = (dist < r) & (hr < np.log(1 + size_tol))
    free = np.ones(len(b), bool)
    clusters = []
    while free.any():
        counts = (adj & free[None, :]).sum(1) * free
        best = int(np.argmax(counts))
        if counts[best] == 0:
            break
        idx = [int(i) for i in np.nonzero(adj[best] & free)[0]]
        clusters.append(idx)
        free[idx] = False
    biggest = max(clusters, key=len)
    top_h = float(np.median(hgt[biggest]))
    # seated is ~0.5-0.65x standing; much smaller = somebody far away / behind the glass, not a posture
    cands = [x for x in clusters if len(x) >= min_frac * len(biggest) and np.median(hgt[x]) >= min_rel_h * top_h]
    idx = min(cands, key=lambda x: float(np.median(hgt[x])))
    if sub is None:
        return Anchor(tr.id, median_box(b[idx]), idx, len(idx), n_all)
    label = np.full(len(sub), -1)
    for k, cl in enumerate(clusters):
        label[cl] = k
    chosen = label[idx[0]]
    # nearest subsample (in sample index) of every full-track sample
    pos = np.searchsorted(sub, np.arange(n_all))
    lo, hi = np.clip(pos - 1, 0, len(sub) - 1), np.clip(pos, 0, len(sub) - 1)
    near = np.where(np.abs(sub[lo] - np.arange(n_all)) <= np.abs(sub[hi] - np.arange(n_all)), lo, hi)
    full_idx = [int(i) for i in np.nonzero(label[near] == chosen)[0]]
    return Anchor(tr.id, median_box(b_all[full_idx]), full_idx, len(full_idx), len(sub))


def _overlap(a: Track, b: Track, tol: float) -> bool:
    return min(a.end, b.end) - max(a.start, b.start) > tol


def find_cashier(
    tracks: list[Track], aspect: float, step: float, min_stationary_s: float = 2.0, seat_iou: float = 0.3
) -> tuple[list[int], list[float] | None]:
    """Cashier = the location with the most stationary presence (summed over non-overlapping tracks
    whose own anchor is there). Returns (cashier track ids, seat box) or ([], None)."""
    if not tracks:
        return [], None
    anchors = {tr.id: stationary_anchor(tr, aspect) for tr in tracks}
    by_id = {tr.id: tr for tr in tracks}
    best: tuple[int, list[int]] | None = None
    for seed in sorted(anchors.values(), key=lambda a: -a.count):
        group = [seed.track_id]
        for other in sorted(anchors.values(), key=lambda a: -a.count):
            if other.track_id in group or iou(seed.box, other.box) < seat_iou:
                continue
            if any(_overlap(by_id[other.track_id], by_id[g], tol=2 * step) for g in group):
                continue
            group.append(other.track_id)
        score = sum(anchors[g].count for g in group)
        if best is None or score > best[0]:
            best = (score, group)
    score, group = best
    if score * step < min_stationary_s:
        return [], None
    boxes = [by_id[g].boxes[i] for g in group for i in anchors[g].idx]
    return sorted(group), median_box(boxes)


def at_seat(box, seat, aspect: float, frac: float = 0.4, max_rel_size: float = 1.35) -> bool:
    """Seated at the workplace: centre within frac x seat diagonal and the box not much larger than
    the seated box (standing up makes the box ~1.5-2x taller/longer along the body axis)."""
    if seat is None:
        return False
    if (box[2] - box[0]) > max_rel_size * (seat[2] - seat[0]) or (box[3] - box[1]) > max_rel_size * (seat[3] - seat[1]):
        return False
    c = iso_centers([box], aspect)[0]
    s = iso_centers([seat], aspect)[0]
    return float(np.hypot(*(c - s))) <= frac * iso_diag(seat, aspect)


def door_zone(points: list[list[float]]) -> list[float] | None:
    """Door zone = robust median box of track birth/death boxes away from the seat (None if none)."""
    if not points:
        return None
    return median_box(points)
