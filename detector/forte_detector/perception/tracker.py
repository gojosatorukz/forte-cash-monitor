"""Person tracking: ByteTrack (supervision, MIT) + merging of fragmented tracks.

All boxes are normalized xyxy in [0, 1] (relative to the frame the detector saw), so the logic is
independent of resolution / orientation of the input.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np


@dataclass
class Track:
    id: int
    t: list[float]
    boxes: list[list[float]]  # normalized xyxy per sample
    scores: list[float]
    members: list[int] = field(default_factory=list)  # raw tracker ids merged into this track

    def __post_init__(self):
        if not self.members:
            self.members = [self.id]

    @property
    def start(self) -> float:
        return self.t[0]

    @property
    def end(self) -> float:
        return self.t[-1]

    def centers(self) -> np.ndarray:
        b = np.asarray(self.boxes, float).reshape(-1, 4)
        return np.stack([(b[:, 0] + b[:, 2]) / 2, (b[:, 1] + b[:, 3]) / 2], axis=1)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["t"] = [round(float(x), 3) for x in self.t]
        d["boxes"] = [[round(float(v), 4) for v in b] for b in self.boxes]
        d["scores"] = [round(float(s), 3) for s in self.scores]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Track":
        return cls(**d)


def box_diag(b) -> float:
    return float(np.hypot(b[2] - b[0], b[3] - b[1]))


def box_area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = box_area(a) + box_area(b) - inter
    return inter / union if union > 0 else 0.0


def _end_velocity(tr: Track, window: float = 1.0) -> np.ndarray:
    """Centre velocity (per second) over the last `window` seconds of the track."""
    c = tr.centers()
    t = np.asarray(tr.t)
    i0 = int(np.searchsorted(t, t[-1] - window))
    if t[-1] - t[i0] < 1e-6:
        return np.zeros(2)
    return (c[-1] - c[i0]) / (t[-1] - t[i0])


def _link_cost(a: Track, b: Track, max_gap: float, dist_factor: float) -> float | None:
    gap = b.start - a.end
    if gap <= 0 or gap > max_gap:
        return None
    ea, sb = a.boxes[-1], b.boxes[0]
    ra = box_area(ea) / max(box_area(sb), 1e-9)
    if not (1 / 3 <= ra <= 3):
        return None
    # extrapolate a's motion over the gap (capped: tracker velocities are noisy)
    v = _end_velocity(a)
    speed = float(np.hypot(*v))
    if speed > 0.25:
        v = v * (0.25 / speed)
    pred = a.centers()[-1] + v * gap
    dist = float(np.hypot(*(b.centers()[0] - pred)))
    scale = (box_diag(ea) + box_diag(sb)) / 2
    if dist > dist_factor * scale:
        return None
    return dist / max(scale, 1e-9) + 0.1 * gap


def _concat(chain: list[Track]) -> Track:
    t, boxes, scores, members = [], [], [], []
    for tr in chain:
        t += list(tr.t)
        boxes += [list(b) for b in tr.boxes]
        scores += list(tr.scores)
        members += list(tr.members)
    return Track(chain[0].id, t, boxes, scores, members)


def merge_tracks(tracks: list[Track], max_gap: float = 2.0, dist_factor: float = 0.6) -> list[Track]:
    """Link fragments: b follows a if b starts 0 < gap <= max_gap after a ends, b's first centre is
    within dist_factor x box diagonal of a's (velocity-extrapolated) last centre, and box areas are
    within 3x. Greedy by cost; each track gets at most one successor/predecessor. Chains are
    concatenated; the merged track keeps the id of its earliest fragment."""
    trs = sorted(tracks, key=lambda x: (x.start, x.id))
    cands = []
    for i, a in enumerate(trs):
        for j, b in enumerate(trs):
            if i != j:
                c = _link_cost(a, b, max_gap, dist_factor)
                if c is not None:
                    cands.append((c, i, j))
    succ: dict[int, int] = {}
    pred: dict[int, int] = {}
    for _, i, j in sorted(cands):
        if i not in succ and j not in pred:
            succ[i], pred[j] = j, i
    out = []
    for i in range(len(trs)):
        if i in pred:
            continue
        chain = [trs[i]]
        while i in succ:
            i = succ[i]
            chain.append(trs[i])
        out.append(_concat(chain))
    return sorted(out, key=lambda x: (x.start, x.id))


MAX_MISSES = 3  # a ByteTrack track is dropped after this many consecutive missed samples


def run_bytetrack(
    times: list[float],
    person_dets: list[list[list[float]]],
    frame_rate: float,
    frame_wh: tuple[int, int],
    start_thr: float = 0.35,
    max_misses: int = MAX_MISSES,
) -> list[Track]:
    """Track per-frame person detections ([x1, y1, x2, y2, score] normalized) with ByteTrack.

    Detections ByteTrack leaves unassigned (e.g. fast motion at low fps: no IoU overlap, so a new
    track is never confirmed) are returned as single-sample tracks with fresh negative ids, so that
    `merge_tracks` can still link them. Returns raw tracks (no merging, no length filter).
    """
    import supervision as sv

    w, h = frame_wh
    tracker = sv.ByteTrack(
        # supervision starts a track only from a detection >= activation + 0.1 (= start_thr); weaker
        # detections (>= the detector's person_thr) can only continue an existing track
        track_activation_threshold=start_thr - 0.1,
        # supervision: max_time_lost = int(frame_rate / 30 * lost_track_buffer) -> N misses in 30 fps units
        lost_track_buffer=int(np.ceil(max_misses * 30 / max(1, int(round(frame_rate))))),
        minimum_matching_threshold=0.8,
        frame_rate=max(1, int(round(frame_rate))),
        minimum_consecutive_frames=1,
    )
    scale = np.array([w, h, w, h], float)
    acc: dict[int, Track] = {}
    next_orphan = -1
    for t, dets in zip(times, person_dets):
        if dets:
            arr = np.asarray(dets, float)
            d = sv.Detections(
                xyxy=arr[:, :4] * scale,
                confidence=arr[:, 4],
                class_id=np.zeros(len(arr), int),
            )
        else:
            arr = np.zeros((0, 5))
            d = sv.Detections.empty()
        out = tracker.update_with_detections(d)
        used = np.zeros(len(arr), bool)
        confs = out.confidence if out.confidence is not None else np.full(len(out), 0.5)
        for box, conf, tid in zip(out.xyxy, confs, out.tracker_id):
            nb = (np.asarray(box) / scale).clip(0, 1).tolist()
            for k in range(len(arr)):
                if not used[k] and iou(nb, arr[k, :4].tolist()) > 0.9:
                    used[k] = True
                    break
            tid = int(tid)
            tr = acc.get(tid)
            if tr is None:
                acc[tid] = Track(tid, [t], [nb], [float(conf)])
            else:
                tr.t.append(t)
                tr.boxes.append(nb)
                tr.scores.append(float(conf))
        for k in np.nonzero(~used)[0]:
            if arr[k, 4] >= start_thr:
                acc[next_orphan] = Track(next_orphan, [t], [arr[k, :4].tolist()], [float(arr[k, 4])])
                next_orphan -= 1
    return list(acc.values())


def suppress_contained(dets: list[list[float]], thr: float = 0.8) -> list[list[float]]:
    """Drop a detection ([x1,y1,x2,y2,score]) when >= thr of its area lies inside a higher-scoring
    detection (DETR part-detections: an arm, a head + torso split). Order of survivors is kept."""
    order = sorted(range(len(dets)), key=lambda i: -dets[i][4])
    keep: list[int] = []
    for i in order:
        d = dets[i]
        a = box_area(d)
        contained = False
        for j in keep:
            k = dets[j]
            ix = max(0.0, min(d[2], k[2]) - max(d[0], k[0]))
            iy = max(0.0, min(d[3], k[3]) - max(d[1], k[1]))
            if a > 0 and ix * iy / a >= thr:
                contained = True
                break
        if not contained:
            keep.append(i)
    return [dets[i] for i in sorted(keep)]


def confirmed(tr: Track, times: list[float], need: int = 3, window: int = 5) -> bool:
    """Track confirmation: present in >= `need` of some `window` consecutive sample times."""
    idx = {round(t, 3): i for i, t in enumerate(times)}
    pos = sorted(idx[round(t, 3)] for t in tr.t if round(t, 3) in idx)
    j = 0
    for i in range(len(pos)):
        while pos[i] - pos[j] >= window:
            j += 1
        if i - j + 1 >= need:
            return True
    return False


def dedupe_concurrent(tracks: list[Track], iou_thr: float = 0.5, min_common: float = 0.5,
                      max_h_ratio: float = 1.25) -> list[Track]:
    """Merge tracks that coexist on the same person (ByteTrack id switch with overlap, duplicate
    queries): if two tracks share >= min_common of the shorter one's sample times and their boxes
    have mean IoU >= iou_thr there and similar heights (median ratio <= max_h_ratio; a client seated
    right behind/in front of the cashier overlaps her box but is much smaller), the shorter is
    absorbed into the longer (longer's boxes win on common times). Keeps the longer track's id."""
    trs = sorted(tracks, key=lambda x: -len(x.t))
    alive = [True] * len(trs)
    for i, a in enumerate(trs):
        if not alive[i]:
            continue
        for j in range(i + 1, len(trs)):
            b = trs[j]
            if not alive[j] or b.end < a.start or b.start > a.end:
                continue
            amap = {round(t, 3): k for k, t in enumerate(a.t)}
            common = [(amap[round(t, 3)], k) for k, t in enumerate(b.t) if round(t, 3) in amap]
            if len(common) < min_common * len(b.t) or not common:
                continue
            if np.mean([iou(a.boxes[ka], b.boxes[kb]) for ka, kb in common]) < iou_thr:
                continue
            ha = np.median([a.boxes[ka][3] - a.boxes[ka][1] for ka, _ in common])
            hb = np.median([b.boxes[kb][3] - b.boxes[kb][1] for _, kb in common])
            if max(ha, hb) > max_h_ratio * max(min(ha, hb), 1e-6):
                continue
            merged = {round(t, 3): (t, bx, sc) for t, bx, sc in zip(b.t, b.boxes, b.scores)}
            merged.update({round(t, 3): (t, bx, sc) for t, bx, sc in zip(a.t, a.boxes, a.scores)})
            items = [merged[k] for k in sorted(merged)]
            trs[i] = a = Track(a.id, [x[0] for x in items], [list(x[1]) for x in items], [x[2] for x in items],
                               sorted(a.members + b.members))
            alive[j] = False
    return sorted((t for t, ok in zip(trs, alive) if ok), key=lambda x: (x.start, x.id))
