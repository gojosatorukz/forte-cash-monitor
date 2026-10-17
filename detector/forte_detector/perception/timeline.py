"""Turn person tracks into a JSON-able timeline: roles, zones, per-sample series, checkpoints."""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from forte_detector.perception import zones as Z
from forte_detector.perception.tracker import Track, box_area


@dataclass(frozen=True)
class TimelineParams:
    min_track_s: float = 1.0  # shorter tracks never count for enter/exit/n_persons
    min_track_score: float = 0.0  # median detection score of a valid track
    max_hole_s: float = 2.0  # a track counts as present inside internal gaps up to this long
    min_state_s: float = 1.0  # debouncing: a state must persist this long to count
    median_window_s: float = 1.5  # running median over n_persons / cashier states before debouncing
    seat_frac: float = 0.4  # at seat: centre within seat_frac x seat diagonal
    edge_s: float = 0.5  # births/deaths within this of the video start/end are not entries/exits
    stationary_disp: float = 0.1  # visitor "stopped": centre moves < this x box diag per second
    min_stationary_s: float = 2.0  # min stationary presence for a cashier seat
    visitor_seat_dist: float = 1.0  # a non-cashier track must come within this x seat diag of the seat
    min_visitor_s: float = 1.0  # ... and last at least this long; otherwise it is "background"
    visitor_min_rel_h: float = 0.6  # median box height >= this x cashier's (smaller = far behind glass)
    visitor_static_rel_h: float = 0.7  # a static track present from the start needs at least this
    visitor_min_move: float = 0.5  # "moved": centre travels >= this x box diagonal
    visitor_near_dist: float = 0.7  # within this x seat diag of the seat ...
    visitor_near_rel_h: float = 0.45  # ... a visitor may be this small (client seated across the desk)
    visitor_min_score: float = 0.5  # median detection score; mirror images in the glass score ~0.3-0.45
    outside_min_dist: float = 0.5  # door-crossing point must be >= this x seat diag from the seat
    outside_door_overlap: float = 0.15  # crossing box overlaps door/glass evidence (share of its area)
    outside_edge_margin: float = 0.02  # ... or touches the left/right/top frame edge (normalized margin)
    outside_scale_drop: float = 0.7  # ... or is seen this much smaller afterwards (farther, behind glass)
    outside_score_drop: float = 0.75  # ... or this much weaker (detection score) afterwards
    outside_baseline_s: float = 2.0  # "before" = the walk just before the crossing (within the episode)
    # the three evidence-free cues above only count for an episode that never comes back
    outside_return_frac: float = 0.3  # back inside when closer than this x the crossing distance
    # (people walking along the glass outside come back to ~0.5x in 2D; a real return reaches ~0.2x)
    outside_min_after_s: float = 1.0  # ... followed by >= this long of track (seen behind the glass)
    idswitch_s: float = 1.0  # a cashier fragment born within this after another one died ...
    idswitch_dist: float = 0.5  # ... closer than this x the dead box diagonal is a tracker id switch

    def to_dict(self) -> dict:
        return asdict(self)


def _box_at(tr: Track, t: float, half: float, max_hole: float):
    """Box of `tr` at sample time t (nearest sample within half a step, or the previous sample when
    t lies inside an internal gap <= max_hole); None if absent."""
    j = int(_box_index(np.asarray(tr.t, float), np.asarray([t], float), half, max_hole)[0])
    return tr.boxes[j] if j >= 0 else None


def _box_index(ts: np.ndarray, times: np.ndarray, half: float, max_hole: float) -> np.ndarray:
    """Vectorized _box_at: for each of `times`, the index into the track times `ts` of the box
    present then, or -1. O((n + m) log n), no per-sample Python loop."""
    out = np.full(len(times), -1, dtype=np.int64)
    if len(ts) == 0 or len(times) == 0:
        return out
    i = np.searchsorted(ts, times)
    lo = np.clip(i - 1, 0, len(ts) - 1)
    hi = np.clip(i, 0, len(ts) - 1)
    j = np.where(np.abs(ts[lo] - times) <= np.abs(ts[hi] - times), lo, hi)
    # tie-break like min(): the earlier candidate when equally close (lo comes first)
    inside = (times >= ts[0] - half) & (times <= ts[-1] + half)
    near = np.abs(ts[j] - times) <= half + 1e-6
    hole = (i > 0) & (i < len(ts))
    gap = np.where(hole, ts[np.clip(i, 0, len(ts) - 1)] - ts[np.clip(i - 1, 0, len(ts) - 1)], np.inf)
    in_hole = hole & (gap <= max_hole + 1e-6)
    out = np.where(inside & near, j, np.where(inside & in_hole, i - 1, -1))
    return out


def _debounce(values: list, k: int) -> list:
    """Runs shorter than k samples take the value of the previous accepted run (or the next one when
    nothing before is accepted). If no run reaches k samples, values are returned unchanged."""
    if not values:
        return []
    runs = []  # [value, start, length]
    for i, v in enumerate(values):
        if runs and runs[-1][0] == v:
            runs[-1][2] += 1
        else:
            runs.append([v, i, 1])
    accepted = [r for r in runs if r[2] >= k]
    if not accepted:
        return list(values)
    out = list(values)
    stable = accepted[0][0]
    for v, s, n in runs:
        if n >= k:
            stable = v
        for i in range(s, s + n):
            out[i] = stable
    return out


def _median_filter(values: list[int], w: int) -> list[int]:
    """Running median over an odd window w (edges use the available samples)."""
    if w <= 1 or not values:
        return list(values)
    h = w // 2
    arr = np.asarray(values)
    return [int(np.median(arr[max(0, i - h) : i + h + 1])) for i in range(len(arr))]


def _transitions(times: list[float], values: list) -> list[tuple[float, object, object]]:
    return [(times[i], values[i - 1], values[i]) for i in range(1, len(values)) if values[i] != values[i - 1]]


def _visitor_arrival(tr: Track, seat, aspect: float, p: TimelineParams, step: float) -> float | None:
    """First moment the visitor stops (>= min_state_s) closer to the seat than where they appeared."""
    if seat is None or len(tr.t) < 3:
        return None
    c = Z.iso_centers(tr.boxes, aspect)
    s = Z.iso_centers([seat], aspect)[0]
    d_seat = np.linalg.norm(c - s, axis=1)
    diag = float(np.median([Z.iso_diag(b, aspect) for b in tr.boxes]))
    win = max(1, int(round(p.min_state_s / step)))
    for i in range(len(tr.t) - win):
        j = i + win
        if tr.t[j] - tr.t[i] > p.min_state_s + 2 * step:
            continue
        disp = float(np.max(np.linalg.norm(c[i : j + 1] - c[i], axis=1)))
        if disp < p.stationary_disp * diag and d_seat[i] < d_seat[0] - 0.25 * diag:
            return float(tr.t[i])
    return None


def build_timeline(
    tracks: list[Track],
    times: list[float],
    duration: float,
    frame_wh: tuple[int, int],
    params: TimelineParams | None = None,
) -> dict:
    p = params or TimelineParams()
    w, h = frame_wh
    aspect = w / h
    step = float(np.median(np.diff(times))) if len(times) > 1 else 0.25
    k = max(1, int(round(p.min_state_s / step)))
    t_first = times[0] if times else 0.0
    t_last = times[-1] if times else duration

    valid = [
        tr for tr in tracks
        if tr.end - tr.start >= p.min_track_s - 1e-6 and float(np.median(tr.scores)) >= p.min_track_score
    ]
    cashier_ids, seat = Z.find_cashier(valid, aspect, step, p.min_stationary_s)
    cashier = [tr for tr in valid if tr.id in cashier_ids]
    others = [tr for tr in valid if tr.id not in cashier_ids]
    ref_h = float(np.median([b[3] - b[1] for tr in cashier for b in tr.boxes])) if cashier else 0.0
    visitors = [tr for tr in others if _is_visitor(tr, seat, aspect, p, ref_h, t_first)]
    background = [tr for tr in others if tr not in visitors]
    valid = cashier + visitors  # background tracks (glass reflections, people outside) are not counted

    # ---- people that left through the door but stay visible behind the glass wall
    # door/glass evidence: where people were seen behind the glass (background tracks) and where
    # tracks were born/died away from the seat (entries/exits)
    def _away(b):
        return not Z.at_seat(b, seat, aspect, p.seat_frac)

    births = {tr.id: tr.boxes[0] for tr in valid if tr.start > t_first + p.edge_s and _away(tr.boxes[0])}
    deaths = {tr.id: tr.boxes[-1] for tr in valid if tr.end < t_last - p.edge_s and _away(tr.boxes[-1])}
    glass = [Z.median_box(tr.boxes) for tr in background]
    # times the cashier is at the seat in any of her tracks (she may sit again as a new track id)
    seat_times = sorted(float(t) for tr in cashier for t, b in zip(tr.t, tr.boxes)
                        if Z.at_seat(b, seat, aspect, p.seat_frac)) if seat is not None else []
    outside_iv: dict[int, list[tuple[float, float, bool]]] = {}
    # tracker id switches of the cashier: a fragment born right after (and where) another one died.
    # Such a birth/death is not door evidence for her; a re-entry after a real absence still is.
    sw_births, sw_deaths = set(), set()
    for a in cashier:
        for b in cashier:
            if a is b or not (-1e-6 <= b.start - a.end <= p.idswitch_s + 1e-6):
                continue
            ca, cb = Z.iso_centers([a.boxes[-1], b.boxes[0]], aspect)
            if float(np.linalg.norm(ca - cb)) <= p.idswitch_dist * Z.iso_diag(a.boxes[-1], aspect):
                sw_births.add(b.id)
                sw_deaths.add(a.id)
    for tr in valid:
        is_c = tr.id in cashier_ids
        skip_b, skip_d = (sw_births, sw_deaths) if is_c else (set(), set())
        evidence = glass + [b for i, b in births.items() if i not in skip_b] + \
            [b for i, b in deaths.items() if i != tr.id and i not in skip_d]
        iv = _outside_intervals(tr, is_c, seat, aspect, p, evidence, seat_times if is_c else [])
        if iv:
            outside_iv[tr.id] = iv

    # ---- per-sample series (outside samples are kept, flagged, and not counted)
    half = step / 2
    present: list[list[tuple[int, list[float]]]] = []
    flagged: list[list[tuple[int, list[float], bool]]] = []
    times_arr = np.asarray(times, float)
    box_idx = [_box_index(np.asarray(tr.t, float), times_arr, half, p.max_hole_s) for tr in valid]
    for q, t in enumerate(times):
        row, frow = [], []
        for tr, bi in zip(valid, box_idx):
            b = tr.boxes[bi[q]] if bi[q] >= 0 else None
            if b is not None:
                out = any(a < t + 1e-6 and (t < b - 1e-6 or not back) and t > a + 1e-6
                          for a, b, back in outside_iv.get(tr.id, []))
                frow.append((tr.id, b, out))
                if not out:
                    row.append((tr.id, b))
        present.append(row)
        flagged.append(frow)
    n_raw = [len(r) for r in present]
    mw = 2 * max(1, int(round(p.median_window_s / step / 2))) + 1  # odd window, ~median_window_s
    n_persons = _debounce(_median_filter(n_raw, mw), k)
    c_present_raw = [any(tid in cashier_ids for tid, _ in r) for r in present]
    c_seat_raw = [any(tid in cashier_ids and Z.at_seat(b, seat, aspect, p.seat_frac) for tid, b in r) for r in present]
    c_present = _debounce([bool(v) for v in _median_filter([int(x) for x in c_present_raw], mw)], k)
    c_seat = _debounce([bool(v) for v in _median_filter([int(x) for x in c_seat_raw], mw)], k)

    cps: list[dict] = []

    def add(kind, t, ids=(), **extra):
        cps.append({"type": kind, "t": round(float(t), 3), "track_ids": sorted(int(i) for i in ids), **extra})

    # ---- cashier seat transitions
    was_seated = bool(c_seat[0]) if c_seat else False
    for t, a, b in _transitions(times, c_seat):
        if a and not b:
            add("cashier_leaves_seat", t, cashier_ids)
            was_seated = True
        elif b and not a and was_seated:
            add("cashier_returns_seat", t, cashier_ids)
        elif b and not a:
            add("cashier_arrives_seat", t, cashier_ids)
            was_seated = True

    # ---- farthest point of each away-from-seat episode (door-crossing candidate for probes)
    if seat is not None:
        sc = Z.iso_centers([seat], aspect)[0]
        # only episodes that start after the cashier was first seated (walking in at the start is not one)
        i = next((k for k, v in enumerate(c_seat) if v), len(times))
        while i < len(times):
            if c_seat[i]:
                i += 1
                continue
            j = i
            while j < len(times) and not c_seat[j]:
                j += 1
            best = None
            for q in range(i, j):
                for tid, b in present[q]:
                    if tid in cashier_ids:
                        d = float(np.hypot(*(Z.iso_centers([b], aspect)[0] - sc)))
                        if best is None or d > best[0]:
                            best = (d, times[q], tid)
            if best is not None:
                add("cashier_farthest_from_seat", best[1], [best[2]],
                    dist_seat_diags=round(best[0] / Z.iso_diag(seat, aspect), 2))
            i = j

    # ---- births / deaths
    door_pts = []
    for tr in valid:
        is_c = tr.id in cashier_ids
        born_inside = tr.start > t_first + p.edge_s
        dies_inside = tr.end < t_last - p.edge_s
        born_at_seat = is_c and Z.at_seat(tr.boxes[0], seat, aspect, p.seat_frac)
        dies_at_seat = is_c and Z.at_seat(tr.boxes[-1], seat, aspect, p.seat_frac)
        for a, b, back in outside_iv.get(tr.id, []):  # exit = the door crossing, not the track end
            door_pts.append(_box_at(tr, a, half, p.max_hole_s) or tr.boxes[-1])
            add("cashier_exits_room" if is_c else "person_exits", a, [tr.id], seen_behind_glass=True)
            if back:  # came back in after being seen behind the glass
                add("cashier_enters_room" if is_c else "person_enters", b, [tr.id], seen_behind_glass=True)
            else:
                dies_inside = False
        if born_inside and not born_at_seat:
            door_pts.append(tr.boxes[0])
            if is_c:
                if not _present_between(cashier, tr, tr.start - p.max_hole_s, tr.start - 1e-6):
                    add("cashier_enters_room", tr.start, [tr.id])
            else:
                add("person_enters", tr.start, [tr.id])
        if dies_inside and not dies_at_seat:
            if is_c:
                if not _present_between(cashier, tr, tr.end + 1e-6, tr.end + p.min_state_s):
                    door_pts.append(tr.boxes[-1])
                    add("cashier_exits_room", tr.end, [tr.id])
            else:
                door_pts.append(tr.boxes[-1])
                add("person_exits", tr.end, [tr.id])
    for tr in visitors:
        ta = _visitor_arrival(tr, seat, aspect, p, step)
        if ta is not None:
            add("visitor_arrives_at_desk", ta, [tr.id])

    # ---- person count
    for j in range(1, len(n_persons)):
        if n_persons[j] == n_persons[j - 1]:
            continue
        t, a, b = times[j], n_persons[j - 1], n_persons[j]
        add("n_persons_change", t, [tid for tid, _ in present[j]], **{"from": int(a), "to": int(b)})
    empty_prev = None
    for i, t in enumerate(times):
        e = n_persons[i] == 0
        if e and empty_prev is not True:
            add("room_empty_start", t)
        elif not e and empty_prev is True:
            add("room_empty_end", t)
        empty_prev = e

    cps.sort(key=lambda c: (c["t"], c["type"]))
    by_id = {tr.id: tr for tr in valid}
    roles = {tid: "cashier" for tid in cashier_ids} | {tr.id: "visitor" for tr in visitors}
    return {
        "frame_wh": [int(w), int(h)],
        "duration": round(float(duration), 3),
        "step": round(step, 4),
        "params": p.to_dict(),
        "roles": {
            "cashier_tracks": sorted(cashier_ids),
            "visitor_tracks": sorted(tr.id for tr in visitors),
            "background_tracks": sorted(tr.id for tr in background),
        },
        "zones": {
            "seat": [round(v, 4) for v in seat] if seat else None,
            "door": [round(v, 4) for v in d] if (d := Z.door_zone(door_pts)) else None,
            "door_points": len(door_pts),
        },
        "series": {
            "t": [round(float(t), 3) for t in times],
            "n_persons": [int(n) for n in n_persons],
            "n_persons_raw": [int(n) for n in n_raw],
            "cashier_present": [bool(x) for x in c_present],
            "cashier_at_seat": [bool(x) for x in c_seat],
            "persons": [  # [track id, role, box, outside(behind the glass)]
                [[int(tid), roles.get(tid, "visitor"), [round(float(v), 4) for v in b], bool(o)] for tid, b, o in r]
                for r in flagged
            ],
        },
        "outside": [
            {"track_id": int(tid), "from_t": round(float(a), 3), "to_t": round(float(b), 3), "came_back": bool(back)}
            for tid, ivs in sorted(outside_iv.items()) for a, b, back in ivs
        ],
        "tracks": [tr.to_dict() for tr in valid],
        "background_tracks": [tr.to_dict() for tr in background],
        "checkpoints": cps,
    }


def _overlap_rel(person, other) -> float:
    """Intersection relative to the person's own box (a small box inside it gives a small value)."""
    ix = max(0.0, min(person[2], other[2]) - max(person[0], other[0]))
    iy = max(0.0, min(person[3], other[3]) - max(person[1], other[1]))
    return ix * iy / max(box_area(person), 1e-9)


def _left_cue(tr: Track, f: int, start: int, end: int, seated, p: TimelineParams) -> bool:
    """Evidence-free cue that the person at sample f went out of the room: the box touches the
    left/right/top frame edge (door at the image border; the bottom edge is not a cue, since in
    ceiling-corner views the feet of anybody walking towards the camera are cut there), or
    afterwards the person is seen clearly smaller / weaker than during the walk just before the
    crossing (samples start..f-1 of the same episode, last outside_baseline_s). For the cashier the
    height baseline is also capped by her seated height, so crouching (at the safe) is not
    "smaller". Only meant for episodes that never come back (see _outside_intervals)."""
    b = tr.boxes[f]
    m = p.outside_edge_margin
    if b[0] <= m or b[1] <= m or b[2] >= 1 - m:
        return True
    step = float(np.median(np.diff(tr.t))) if len(tr.t) > 1 else 0.25
    lo = max(start, f - max(2, int(round(p.outside_baseline_s / step))))
    if f - lo < 2 or end - f < 2:
        return False
    h = np.asarray([x[3] - x[1] for x in tr.boxes])
    sc = np.asarray(tr.scores)
    base_h = float(np.median(h[lo:f]))
    if seated is not None and seated.any():
        base_h = min(base_h, float(np.median(h[seated])))
    if np.median(h[f + 1 : end + 1]) <= p.outside_scale_drop * base_h:
        return True
    return bool(np.median(sc[f + 1 : end + 1]) <= p.outside_score_drop * np.median(sc[lo:f]))


def _outside_intervals(tr: Track, is_cashier: bool, seat, aspect: float, p: TimelineParams,
                       door_evidence: list, seat_times: list[float] = ()) -> list[tuple[float, float, bool]]:
    """Intervals [(t_cross, t_back_or_end, came_back)] when the track is outside the room but still
    detected behind the glass wall.

    In these corner views "just behind the glass" and "just inside" often look the same in 2D, so
    the cue is the trajectory plus a door cue. In each away-episode (cashier: between seated runs,
    after first sitting; visitor: after the closest approach to the desk) the crossing candidate is
    the farthest point from the seat. It counts only if all hold:
      (a) it is >= outside_min_dist seat diagonals from the seat;
      (b) a door cue: its box overlaps door/glass evidence (people seen behind the glass, other
          tracks' births/deaths, the track's own birth) by >= outside_door_overlap of its own area,
          OR (no evidence needed, only when the episode never comes back: no return toward the
          seat, the cashier does not sit again later in any of her tracks) it touches the
          left/right/top frame edge, or afterwards the person is seen clearly smaller
          (<= outside_scale_drop x) or weaker (<= outside_score_drop x) than on the walk just
          before (see _left_cue);
      (c) the track continues >= outside_min_after_s afterwards;
      (d) a visitor gets back about as far as where they first appeared (a client seated at the
          desk is not leaving).
    The interval ends (reversibly, came_back=True) at the first later sample within
    outside_return_frac x the crossing distance of the seat, or when the cashier sits down again.
    """
    if seat is None or len(tr.t) < 3:
        return []
    c = Z.iso_centers(tr.boxes, aspect)
    sc = Z.iso_centers([seat], aspect)[0]
    dist = np.linalg.norm(c - sc, axis=1) / Z.iso_diag(seat, aspect)
    seated = np.array([Z.at_seat(b, seat, aspect, p.seat_frac) for b in tr.boxes])
    if is_cashier:
        if not seated.any():
            return []
        episodes, i = [], int(np.argmax(seated))
        while i < len(tr.t):
            if seated[i]:
                i += 1
                continue
            j = i
            while j < len(tr.t) and not seated[j]:
                j += 1
            episodes.append((i, j))
            i = j
    else:
        episodes = [(int(np.argmin(dist)), len(tr.t))]
    out = []
    for i0, i1 in episodes:
        k = i0
        while k < i1 - 1:
            seg = dist[k:i1]
            dmax = float(seg.max())
            if dmax < p.outside_min_dist:
                break
            if not is_cashier and dmax < 0.8 * float(dist[0]):  # a visitor leaves the way they came
                break
            f = k + int(np.argmax(seg))
            back = next((q for q in range(f + 1, i1) if dist[q] < p.outside_return_frac * dmax), None)
            seg_end = (back - 1) if back is not None else i1 - 1
            came_back = back is not None or i1 < len(tr.t)  # sat down again = came back as well
            door_cue = any(_overlap_rel(tr.boxes[f], e) >= p.outside_door_overlap for e in door_evidence)
            # a return (or sitting again, even as another track) makes an evidence-free cue
            # meaningless: crouching / occluded / feet cut at the safe and back is not an exit
            returns = came_back or any(t > tr.t[f] + 1e-6 for t in seat_times)
            if not (door_cue or (not returns and _left_cue(tr, f, i0, seg_end, seated if is_cashier else None, p))):
                break
            t_end = tr.t[back] if back is not None else (tr.t[i1] if i1 < len(tr.t) else tr.t[-1])
            if t_end - tr.t[f] >= p.outside_min_after_s - 1e-6:
                out.append((float(tr.t[f]), float(t_end), bool(came_back)))
            if back is None:
                break
            k = back
    return out


def _is_visitor(tr: Track, seat, aspect: float, p: TimelineParams, ref_h: float, t_first: float) -> bool:
    """A non-cashier track is a person in the room if it lasts >= min_visitor_s and comes near the
    seat at some point. Static detections far from the desk (people behind the glass wall, mirror
    images in the glass) are background. Without a seat every lasting track counts."""
    if tr.end - tr.start < p.min_visitor_s - 1e-6 or float(np.median(tr.scores)) < p.visitor_min_score:
        return False
    if seat is None:
        return True
    c = Z.iso_centers(tr.boxes, aspect)
    s = Z.iso_centers([seat], aspect)[0]
    near = float(np.min(np.linalg.norm(c - s, axis=1))) / Z.iso_diag(seat, aspect)
    if near > p.visitor_seat_dist:
        return False
    # small = far behind the glass / a mirror image; a client seated right across the desk is smaller
    # than the cashier too, so close to the seat a lower size bound applies
    hs = np.asarray(tr.boxes)[:, 3] - np.asarray(tr.boxes)[:, 1]
    rel_h = float(np.median(hs)) / max(ref_h, 1e-6)
    if rel_h < (p.visitor_near_rel_h if near <= p.visitor_near_dist else p.visitor_min_rel_h):
        return False
    diag = float(np.median([Z.iso_diag(b, aspect) for b in tr.boxes]))
    moved = float(np.max(np.linalg.norm(c - c[0], axis=1))) >= p.visitor_min_move * diag
    entered = tr.start > t_first + p.edge_s
    return entered or moved or rel_h >= p.visitor_static_rel_h


def _present_between(group: list[Track], me: Track, t0: float, t1: float) -> bool:
    """Whether any other track of `group` has a sample in [t0, t1]."""
    for tr in group:
        if tr is me:
            continue
        if any(t0 <= t <= t1 for t in tr.t):
            return True
    return False
