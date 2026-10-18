"""Cheap CV probes (CPU, milliseconds): camera blocked (per frame + a whole-clip low-res series),
door open/closed by appearance change, COCO-detection evidence from the perception timeline.

All geometry is normalized (x0, y0, x1, y1) in the (possibly transformed) frame; nothing assumes
fixed pixels. The burned-in timestamp (yellow box) is found by colour and masked out.
"""
from __future__ import annotations

import bisect
import time

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


def global_brightness(img: np.ndarray) -> float:
    """Mean luminance of the whole (downscaled) frame."""
    return float(_gray_small(img, 96).mean())


# Structure comparison against the reference frame (the clip's first sampled frame).
BLOCK_W = 64              # both frames are compared at ~64 px width
BLOCK_SIGMA = 1.0         # Gaussian blur at that size (kills sensor noise / JPEG blocks)
BLOCK_SHIFT = 2           # correlation is maximised over +-2 px shifts (camera jitter, re-crops)
BLOCK_CORR = 0.30         # structure lost: corr below this ...
BLOCK_ENERGY = 0.50       # ... AND gradient energy below this x the reference's (cover, hand, tape)
BLOCK_CORR_VEIL = 0.70    # veiled lens: faint structure left (corr below this) ...
BLOCK_ENERGY_VEIL = 0.35  # ... AND weak (energy below this): spray haze, translucent film
BLOCK_CONTRAST = 0.30     # washed out: energy/brightness ratio below this (with weak energy) ...
BLOCK_CORR_WASH = 0.85    # ... AND corr below this: bright milky tape over a still-guessable scene
BLOCK_UNIFORM = 0.05      # mean gradient (gray levels/px at 64 px) below this: uniform frame
BLOCK_BAND = 0.15         # top/bottom fraction of rows searched for OSD/timestamp text
BLOCK_MIN_VALID = 0.5     # less than this fraction of the map left after masking -> undecided
BLOCK_ABS_ENERGY = 15.0   # absolute structure_energy below this: flat frame (no usable reference)
# Absolute (reference-free) cover test: gradient energy relative to brightness, so dimming (edges
# and brightness scale together) keeps it while a cover (black / tape / spray / plain wall) loses it.
BLOCK_REL_ENERGY = 0.10   # structureless: mid-band gradient / max(mean luminance, floor) below this
BLOCK_LUM_FLOOR = 20.0    # luminance floor: near-black frames are judged by their absolute gradient


def _blur_small(img: np.ndarray, width: int = BLOCK_W) -> np.ndarray:
    """The ~64 px wide, Gaussian-blurred gray image (float32)."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    h, w = g.shape[:2]
    size = (width, max(8, round(h * width / w)))
    return cv2.GaussianBlur(cv2.resize(g.astype(np.float32), size, interpolation=cv2.INTER_AREA), (0, 0), BLOCK_SIGMA)


def _grad_of(s: np.ndarray) -> np.ndarray:
    return np.hypot(cv2.Sobel(s, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(s, cv2.CV_32F, 0, 1, ksize=3))


def _grad_map(img: np.ndarray, width: int = BLOCK_W) -> np.ndarray:
    """Gradient-magnitude map of the ~64 px wide, Gaussian-blurred gray image."""
    return _grad_of(_blur_small(img, width))


def osd_band_mask(img: np.ndarray, ref: np.ndarray, ga: np.ndarray, gb: np.ndarray) -> np.ndarray:
    """Bool mask (map size) of burned-in overlays: yellow boxes, plus OSD text in the top/bottom
    BLOCK_BAND rows = strong edges at the same place in BOTH maps (text stays put while the scene
    behind it is covered; changing digits still have edges there), dilated along the text line.
    On an unblocked frame this only drops some band pixels that agree anyway."""
    h, w = gb.shape
    m = np.zeros((h, w), bool)
    for x in (img, ref):
        ov = overlay_mask(x).astype(np.uint8)
        m |= cv2.resize(ov, (w, h), interpolation=cv2.INTER_AREA) > 0
    band = np.zeros((h, w), bool)
    k = max(1, round(h * BLOCK_BAND))
    band[:k], band[-k:] = True, True
    both = (ga > float(np.percentile(ga, 75))) & (gb > float(np.percentile(gb, 75)))
    text = cv2.dilate((band & both).astype(np.uint8), np.ones((3, 7), np.uint8)) > 0
    return m | (text & band)


def _shift_corr(a: np.ndarray, b: np.ndarray, valid: np.ndarray, r: int = BLOCK_SHIFT) -> float:
    """Max Pearson correlation of a and b (on valid pixels) over integer shifts of b by <= r px."""
    h, w = a.shape
    best = -1.0
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            ya, yb = slice(max(0, dy), h + min(0, dy)), slice(max(0, -dy), h + min(0, -dy))
            xa, xb = slice(max(0, dx), w + min(0, dx)), slice(max(0, -dx), w + min(0, -dx))
            v = valid[ya, xa] & valid[yb, xb]
            x, y = a[ya, xa][v], b[yb, xb][v]
            if x.size < 16:
                continue
            x, y = x - x.mean(), y - y.mean()
            den = float(np.sqrt((x * x).sum() * (y * y).sum()))
            best = max(best, float((x * y).sum()) / den if den > 1e-6 else 0.0)
    return best


def structure_energy(img: np.ndarray) -> float:
    """Absolute scene structure: mean gradient magnitude of the 64 px blurred gray map."""
    return float(_grad_map(img).mean())


def relative_structure(img: np.ndarray) -> tuple[float, float, float]:
    """(ratio, energy, luminance) of the frame's middle band (top/bottom BLOCK_BAND rows = OSD text
    and burned-in overlays excluded): ratio = mean gradient of the 64 px blurred gray map /
    max(mean luminance, BLOCK_LUM_FLOOR). Dimming scales gradient and luminance alike, so the ratio
    survives it (real frames 0.45-0.6, also at x0.2; x0.1 ~0.3 via the floor; external2 scenes
    >= 0.15); a cover keeps brightness but no edges (black + noise, tape, spray, white, plain
    wall: 0.02-0.06). The blur keeps sensor / compression noise of a dark frame from counting."""
    s = _blur_small(img)
    g = _grad_of(s)
    h = g.shape[0]
    valid = ~(cv2.resize(overlay_mask(img).astype(np.uint8), g.shape[::-1], interpolation=cv2.INTER_AREA) > 0)
    k = max(1, round(h * BLOCK_BAND))
    valid[:k], valid[-k:] = False, False
    if valid.mean() < 0.25:  # overlays everywhere: fall back to the whole map
        valid[:] = True
    e, lum = float(g[valid].mean()), float(s[valid].mean())
    return e / max(lum, BLOCK_LUM_FLOOR), e, lum


def structureless(img: np.ndarray) -> bool:
    return relative_structure(img)[0] < BLOCK_REL_ENERGY


def _blocked_no_ref(img: np.ndarray) -> tuple[bool, float, dict]:
    """Without a usable reference (none given, or the reference itself is flat): absolute test,
    blocked when the frame is structureless (relative_structure ratio < BLOCK_REL_ENERGY): covers
    measure 0.02-0.06, real scenes >= 0.15 (training >= 0.45), x0.1-dimmed scenes ~0.3. A dim but
    structured room is not blocked (a flat absolute gradient alone cannot tell it from a cover).
    Not caught without a reference: a hand at the lens (light leak + its edge: ratio 0.19-0.26,
    overlaps plain real views) and a camera turned to a textured view. Conf <= 0.8."""
    ratio, energy, lum = relative_structure(img)
    raw = {"energy_abs": round(energy, 3), "lum": round(lum, 1), "rel_energy": round(ratio, 4), "mode": "absolute"}
    if ratio < BLOCK_REL_ENERGY:
        return True, round(float(np.clip(0.6 + (BLOCK_REL_ENERGY - ratio) * 4, 0.6, 0.8)), 3), {**raw, "why": "structureless"}
    return False, round(float(np.clip(0.6 + (ratio - BLOCK_REL_ENERGY) * 2, 0.6, 0.8)), 3), raw


def camera_blocked(img: np.ndarray, ref: np.ndarray | None = None) -> tuple[bool | None, float, dict]:
    """Per-frame verdict: has the scene structure of the reference frame disappeared from `img`?

    Both frames -> ~64 px wide gray + Gaussian blur -> gradient-magnitude maps; burned-in overlays
    (yellow boxes; OSD text in the top/bottom bands) are masked from both. Then
      corr   = max over +-2 px shifts of the Pearson correlation of the two maps. Normalised per
               map, so dimming / lights off (edges kept, only weaker) keeps it high; the blur keeps
               the sensor noise of a dark frame from passing as structure (z-normalising pixels
               instead made a noisy black cover look textured - e2_28 spray-painted lens).
      energy = mean gradient of the probe / mean gradient of the reference.
    Blocked when
      structure_lost: corr < BLOCK_CORR and energy < BLOCK_ENERGY (cover, hand, tape, plain wall);
      veiled:         corr < BLOCK_CORR_VEIL and energy < BLOCK_ENERGY_VEIL (spray haze, film);
      washed_out:     contrast < BLOCK_CONTRAST, energy < BLOCK_ENERGY_VEIL, corr < BLOCK_CORR_WASH,
                      contrast = energy / (probe brightness / reference brightness): dimming scales
                      edges and brightness alike (~1), a light veil adds brightness without edges;
      uniform_frame:  the probe has (almost) no gradient at all.
    The energy condition keeps scene changes that still have full structure (people moving close
    to the camera, a cut in edited footage) from counting; the price is that a camera turned
    towards an equally textured view is not detected.

    This is ONE frame: a person passing right in front of the lens can trip it on that frame.
    Persistence is the rule's job (CameraRule: blocked for >= 2 s on its 1 s grid, one dropout
    allowed). conf (0.6-0.95) grows with the distance from the deciding thresholds. Without a
    reference, or with a flat one, the absolute test applies (_blocked_no_ref, conf <= 0.8).
    Returns (None, 0, raw) when the masks leave too little to compare.
    """
    if ref is None:
        return _blocked_no_ref(img)
    ga, gb = _grad_map(img), _grad_map(ref)
    if ga.shape != gb.shape:  # different aspect: compare at the reference's map size
        ga = cv2.resize(ga, gb.shape[::-1], interpolation=cv2.INTER_AREA)
    osd = osd_band_mask(img, ref, ga, gb)
    valid = ~osd
    raw: dict = {"osd_frac": round(float(osd.mean()), 3)}
    if valid.mean() < BLOCK_MIN_VALID:
        return None, 0.0, {**raw, "why": "masked"}
    eb, ea = float(gb[valid].mean()), float(ga[valid].mean())
    if float(gb.mean()) < BLOCK_ABS_ENERGY:  # the reference itself is flat (covered from the start?)
        v, c, r = _blocked_no_ref(img)
        return v, c, {**raw, **r, "why_ref": "flat_reference"}
    if ea <= BLOCK_UNIFORM:
        return True, 0.95, {**raw, "corr": 0.0, "energy": round(ea / eb, 3), "why": "uniform_frame"}
    energy = ea / eb
    corr = _shift_corr(ga, gb, valid)
    contrast = energy / max(global_brightness(img) / max(global_brightness(ref), 1.0), 0.05)
    raw.update(corr=round(corr, 3), energy=round(energy, 3), contrast=round(contrast, 3), ref_energy=round(eb, 3))

    def conf(m: float) -> float:
        return round(float(np.clip(0.6 + 1.5 * m, 0.6, 0.95)), 3)

    if corr < BLOCK_CORR and energy < BLOCK_ENERGY:
        return True, conf(min(BLOCK_CORR - corr, BLOCK_ENERGY - energy)), {**raw, "why": "structure_lost"}
    if corr < BLOCK_CORR_VEIL and energy < BLOCK_ENERGY_VEIL:
        return True, conf(min(BLOCK_CORR_VEIL - corr, BLOCK_ENERGY_VEIL - energy)), {**raw, "why": "veiled"}
    if contrast < BLOCK_CONTRAST and energy < BLOCK_ENERGY_VEIL and corr < BLOCK_CORR_WASH:
        m = min(BLOCK_CONTRAST - contrast, BLOCK_ENERGY_VEIL - energy, BLOCK_CORR_WASH - corr)
        return True, conf(m), {**raw, "why": "washed_out"}
    # not blocked: distance to the nearest blocked region
    m_lost = max(corr - BLOCK_CORR, energy - BLOCK_ENERGY)
    m_veil = max(corr - BLOCK_CORR_VEIL, energy - BLOCK_ENERGY_VEIL)
    m_wash = max(contrast - BLOCK_CONTRAST, energy - BLOCK_ENERGY_VEIL, corr - BLOCK_CORR_WASH)
    return False, conf(min(m_lost, m_veil, m_wash)), raw


BLOCK_SERIES_FPS = 1.0          # one sample per second of video
BLOCK_DECODE_SIDE = 160         # frames are decoded straight to <= 160 px (maps are 64 px wide)
BLOCK_REF_SAMPLES = 5           # the reference is picked among the first samples
BLOCK_BOUNDED_N = 30            # bounded pass (deadline near/passed): <= this many samples
BLOCK_BOUNDED_SIDE = 96
BLOCK_BOUNDED_S = 5.0           # ... and a hard wall-clock cap
BLOCK_DEADLINE_MARGIN_S = 10.0  # full pass only when more than this is left before the deadline


def coarse_to_fine(n: int) -> list[int]:
    """0..n-1 in bisection order (0, n/2, n/4, 3n/4, ...): any prefix is spread over the range."""
    order, seen, step = [], set(), 1
    while step < n:
        step *= 2
    while step >= 1:
        for k in range(0, n, step):
            if k not in seen:
                seen.add(k)
                order.append(k)
        step //= 2
    return order


def sparse_samples(video_path, ts, *, max_side: int = BLOCK_BOUNDED_SIDE, transform=None,
                   until: float | None = None):
    """Yield (t, frame) for the frame nearest at/after each t in ts (seek to the keyframe before t,
    decode forward; only the kept frame is converted, at <= max_side px), in the order of ts.
    Stops at `until` (time.monotonic()), also inside a long GOP."""
    import av

    from forte_detector.video_io import _to_bgr

    with av.open(str(video_path)) as c:
        st = c.streams.video[0]
        st.thread_type = "AUTO"
        start, tb = st.start_time or 0, st.time_base
        for t in ts:
            if until is not None and time.monotonic() >= until:
                return
            c.seek(start + int(max(t, 0.0) / tb), stream=st, backward=True)
            for f in c.decode(st):
                if until is not None and time.monotonic() >= until:
                    return
                ft = float((f.pts - start) * tb) if f.pts is not None else t
                if ft >= t - 0.02:
                    yield round(ft, 3), _to_bgr(f, max_side, transform)
                    break


def _pick_reference(buf: list) -> tuple[float, np.ndarray] | None:
    """Median-structure sample among those that can be a reference (None if none can)."""
    ok = sorted(((structure_energy(im), t, i) for i, (t, im) in enumerate(buf)), key=lambda x: x[0])
    ok = [x for x in ok if x[0] >= BLOCK_ABS_ENERGY and not structureless(buf[x[2]][1])]
    if not ok:
        return None
    _, t, i = ok[len(ok) // 2]
    return t, buf[i][1]


def camera_blocked_series(video_path, transform=None, ref_t: float = 0.0, *, deadline: float | None = None,
                          duration: float | None = None, fps: float = BLOCK_SERIES_FPS,
                          max_side: int = BLOCK_DECODE_SIDE) -> dict:
    """camera_blocked for the whole clip, without random-access full-res reads.

    Full pass (more than BLOCK_DEADLINE_MARGIN_S before `deadline`, a time.monotonic() value): one
    sequential decode at <= max_side px, one sample per 1/fps s. Bounded pass (deadline near or
    passed - e.g. perception was truncated - or reached during the full pass, for the rest of the
    clip; needs `duration`): <= BLOCK_BOUNDED_N seeks evenly spread, taken coarse-to-fine so that
    whatever fits in BLOCK_BOUNDED_S s wall clock still covers the whole clip. So CV still answers after the deadline, on a coarser grid.

    Reference: the median-structure sample among the first BLOCK_REF_SAMPLES (not just frame 0,
    which may be black or a glitch); flat samples are never a reference. While there is none
    (covered from the start) samples get the absolute test (_blocked_no_ref: structureless =
    brightness-normalised gradient < BLOCK_REL_ENERGY); the first later sample with structure
    (absolute and relative) becomes the reference. If none ever appears (dim from the start, or
    covered throughout) structureless samples stay blocked, all others are None ("no_reference"):
    a dim but structured room is never T14.

    Returns {"t", "res": [(value, conf, raw)], "ref_t", "tol", "mode", "complete", "why",
    "decode_s"}; the caller keeps it per job (nothing module-global)."""
    from forte_detector.video_io import iter_frames

    t_start = time.monotonic()
    out: dict = {"t": [], "res": [], "ref_t": None, "tol": 0.75 / fps, "mode": "full", "complete": False,
                 "why": None, "decode_s": 0.0}
    state: dict = {"ref": None, "buf": []}

    def judge(t, im):
        out["t"].append(round(float(t), 3))
        out["res"].append(camera_blocked(im, state["ref"]))

    def flush():
        buf, state["buf"] = state["buf"] or [], None
        pick = _pick_reference(buf)
        if pick is not None:
            out["ref_t"], state["ref"] = round(float(pick[0]), 3), pick[1]
        for t, im in buf:
            judge(t, im)

    def feed(t, im):
        if t < ref_t - 0.5 / fps:
            return
        if state["buf"] is not None:  # still choosing the reference
            state["buf"].append((t, im))
            if len(state["buf"]) >= BLOCK_REF_SAMPLES:
                flush()
            return
        if state["ref"] is None and structure_energy(im) >= BLOCK_ABS_ENERGY and not structureless(im):
            state["ref"], out["ref_t"] = im, round(float(t), 3)  # first structured view after a cover
        judge(t, im)

    last_t = -1.0
    full = deadline is None or deadline - t_start >= BLOCK_DEADLINE_MARGIN_S
    try:
        if full:
            for t, img in iter_frames(str(video_path), fps, max_side=max_side, transform=transform):
                if deadline is not None and time.monotonic() >= deadline:
                    out["why"] = "deadline"
                    break
                feed(t, img)
                last_t = t
            else:
                out["complete"] = True
        if not out["complete"] and duration:
            out["mode"] = "full+bounded" if full else "bounded"
            lo = max(last_t, 0.0)
            n = max(1, min(BLOCK_BOUNDED_N, int((duration - lo) * fps)))
            step = (duration - lo) / n
            ts = [lo + step * (k + (0.0 if last_t < 0 else 1.0)) for k in range(n)]
            got: list = []  # filled incrementally: a decode error keeps what was decoded
            try:
                for x in sparse_samples(video_path, [ts[k] for k in coarse_to_fine(len(ts))], transform=transform,
                                        until=time.monotonic() + BLOCK_BOUNDED_S):
                    got.append(x)
            finally:
                for t, img in sorted(got, key=lambda x: x[0]):
                    if t > last_t:
                        feed(t, img)
                        last_t = t
                # every probe takes the nearest sample (CameraRule needs >= 2 distinct blocked samples)
                gaps = np.diff([0.0, *out["t"], float(duration)]) if out["t"] else [duration]
                out["tol"] = max(out["tol"], step, float(np.max(gaps)))
    except Exception as e:  # noqa: BLE001 - a broken stream tail keeps the samples decoded so far
        out["why"] = f"decode_error: {type(e).__name__}"
    if state["buf"] is not None:
        flush()
    if state["ref"] is None:
        # no structured view anywhere (dim from the start, or covered throughout): only the
        # absolute structureless verdicts stand (covered from the start); the rest is undecided
        # (nothing to compare a veil or a dim view against), so a dim room is never T14
        out["res"] = [(v, c, raw) if v else (None, 0.0, {**raw, "why": "no_reference"}) for v, c, raw in out["res"]]
    out["decode_s"] = round(time.monotonic() - t_start, 3)
    return out


def series_at(series: dict, t: float, tol: float | None = None) -> tuple[bool | None, float, dict]:
    """The series verdict nearest to t (within tol, default series["tol"]) or None."""
    ts = series.get("t") or []
    tol = series.get("tol", 0.75 / BLOCK_SERIES_FPS) if tol is None else tol
    k = bisect.bisect_left(ts, t)
    cand = [j for j in (k - 1, k) if 0 <= j < len(ts)]
    if not cand:
        return None, 0.0, {"why": series.get("why") or "no_samples", "ref_t": series.get("ref_t")}
    i = min(cand, key=lambda j: abs(ts[j] - t))
    if abs(ts[i] - t) > tol:
        return None, 0.0, {"why": series.get("why") or "no_sample_near", "ref_t": series.get("ref_t")}
    v, c, raw = series["res"][i]
    return v, c, {**raw, "sample_t": ts[i], "ref_t": series.get("ref_t"), "series_mode": series.get("mode")}


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
