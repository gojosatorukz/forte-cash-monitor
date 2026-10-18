"""T14 camera blocked: the per-frame CV cue (cheap.camera_blocked, structure vs the reference frame)
and the dense CameraRule (>= 2 s blocked, 1 dropout tolerated)."""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from forte_detector.probes import cheap
from forte_detector.probes.base import ProbeResult
from forte_detector.rules import engine as E


def room(seed=0, h=270, w=480):
    """Cash-room-like frame: shaded walls, desk, door, a few objects, mild noise."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.zeros((h, w, 3), np.float32)
    img[:] = (150 + 30 * xx / w - 20 * yy / h)[..., None]
    cv2.rectangle(img, (int(w * .05), int(h * .55)), (int(w * .70), int(h * .80)), (90, 70, 60), -1)   # desk
    cv2.rectangle(img, (int(w * .75), int(h * .10)), (int(w * .95), int(h * .90)), (60, 80, 100), -1)  # door
    cv2.rectangle(img, (int(w * .10), int(h * .10)), (int(w * .35), int(h * .40)), (200, 210, 220), -1)  # window
    for _ in range(6):
        x, y = int(rng.uniform(.1, .6) * w), int(rng.uniform(.45, .6) * h)
        cv2.rectangle(img, (x, y), (x + 20, y + 12), tuple(float(v) for v in rng.uniform(30, 230, 3)), -1)
    cv2.circle(img, (int(w * .45), int(h * .35)), 25, (40, 40, 40), -1)  # a person's head
    img += rng.normal(0, 2, img.shape)
    return np.clip(img, 0, 255).astype(np.uint8)


def with_osd(img, text="2026-10-17 12:34:56"):
    img = img.copy()
    h, w = img.shape[:2]
    cv2.putText(img, text, (int(w * .02), int(h * .07)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 3)
    cv2.putText(img, text, (int(w * .02), int(h * .07)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    cv2.putText(img, "CAM03", (int(w * .85), int(h * .96)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    return img


def noisy(img, s, seed=1):
    rng = np.random.default_rng(seed)
    return np.clip(img.astype(np.float32) + rng.normal(0, s, img.shape), 0, 255).astype(np.uint8)


def blocked(img, ref):
    v, c, raw = cheap.camera_blocked(img, ref)
    return v is True and c >= 0.6, raw


REF = room()


# ----------------------------------------------------------------------------- cue: positives

def test_black_cover_with_sensor_noise_and_osd_is_blocked():
    """The e2_28 failure: a dark noisy frame (std ~3) used to count as textured after z-normalisation."""
    ref = with_osd(REF)
    cover = with_osd(noisy(np.full_like(REF, 12), 3), "2026-10-17 12:35:19")
    assert cover.std() > 2.5
    b, raw = blocked(cover, ref)
    assert b, raw


def test_hand_cover_with_residual_edge_strip_is_blocked():
    h, w = REF.shape[:2]
    m = np.zeros((h, w), np.float32)
    m[:, : int(w * .86)] = 1
    m = cv2.GaussianBlur(m, (0, 0), w * .05)[..., None]
    hand = noisy(REF * (1 - m) + np.array([55, 75, 110], np.float32) * m, 3)
    assert blocked(hand, REF)[0]


def test_spray_haze_is_blocked():
    veil = cv2.GaussianBlur(REF, (0, 0), 25).astype(np.float32) * 0.3 + 0.7 * np.array([118, 122, 149])
    b, raw = blocked(noisy(veil, 3), REF)
    assert b, raw


def test_bright_translucent_tape_is_blocked():
    tape = cv2.GaussianBlur(REF, (0, 0), 18).astype(np.float32) * 0.3 + 0.7 * 210
    assert blocked(noisy(tape, 2), REF)[0]


def test_camera_turned_to_plain_wall_is_blocked():
    h, w = REF.shape[:2]
    xx = np.mgrid[0:h, 0:w][1] / w
    wall = np.repeat((140 + 30 * xx)[..., None], 3, 2)
    assert blocked(noisy(wall, 2.5), REF)[0]


def test_uniform_black_is_blocked_with_high_conf():
    v, c, raw = cheap.camera_blocked(np.zeros_like(REF), REF)
    assert v is True and c >= 0.9 and raw["why"] == "uniform_frame"


# ----------------------------------------------------------------------------- cue: negatives

def test_same_scene_is_not_blocked():
    assert not blocked(noisy(REF, 2, seed=5), REF)[0]


@pytest.mark.parametrize("k,s", [(0.3, 2), (0.1, 2), (0.05, 1.5)])
def test_dimmed_lights_are_not_blocked(k, s):
    b, raw = blocked(noisy(REF.astype(np.float32) * k, s), REF)
    assert not b, raw
    assert raw["corr"] > 0.7


def test_gamma_dimming_is_not_blocked():
    g = 255 * (REF / 255.0) ** 2.4
    assert not blocked(noisy(g, 4), REF)[0]


def test_osd_only_change_is_not_blocked():
    assert not blocked(with_osd(REF, "2026-10-17 12:59:01"), with_osd(REF))[0]


def test_person_passing_close_to_lens_is_not_blocked():
    h, w = REF.shape[:2]
    m = np.zeros((h, w), np.float32)
    m[:, int(w * .55):] = 1  # 45 % of the frame, keeps its own edges
    m = cv2.GaussianBlur(m, (0, 0), w * .03)[..., None]
    passer = noisy(REF * (1 - m) + np.array([40, 45, 50], np.float32) * m, 3)
    assert not blocked(passer, REF)[0]


def test_scene_change_with_full_structure_is_not_blocked():
    """Edited footage cut / people moving close: other structure, same energy -> not a cover."""
    assert not blocked(room(seed=7)[:, ::-1].copy(), REF)[0]


def test_no_reference_only_flags_uniform_frames():
    assert cheap.camera_blocked(np.full_like(REF, 10), None)[0] is True
    assert cheap.camera_blocked(REF, None)[0] is False


# ----------------------------------------------------------------------------- rule


def _rule_events(duration, blocked_at):
    ctx = E.Ctx({"duration": duration, "series": {"t": [0.0]}}, {})
    rule = E.CameraRule()
    reqs = rule.propose(ctx)
    pairs = [(q, ProbeResult(q.cue, q.t, bool(blocked_at(q.t)), 0.9, [q.t], "cv:blocked", {})) for q in reqs]
    return reqs, rule.decide(ctx, pairs)


def test_rule_probes_densely_and_caps_long_clips():
    reqs, _ = _rule_events(30.0, lambda t: False)
    ts = [q.t for q in reqs]
    assert len(ts) >= 29 and max(b - a for a, b in zip(ts, ts[1:])) <= 1.01
    reqs, _ = _rule_events(600.0, lambda t: False)
    assert len(reqs) <= E.CameraRule.MAX_PROBES


def test_rule_fires_on_late_cover_with_onset_timecode():
    _, ev = _rule_events(30.0, lambda t: t >= 26.5)  # after 85 % of the clip
    assert [e.type for e in ev] == ["T14"]
    assert ev[0].timecode_sec == 27.0


def test_rule_tolerates_one_dropout():
    _, ev = _rule_events(30.0, lambda t: t in (16, 17, 19, 20))  # X X . X X
    assert len(ev) == 1 and ev[0].timecode_sec == 16.0


def test_rule_ignores_short_pass_by_and_double_dropout():
    assert _rule_events(30.0, lambda t: 15.5 <= t <= 16.5)[1] == []  # < 2 s
    assert _rule_events(30.0, lambda t: t in (10, 13, 16))[1] == []  # X . . X . . X


def test_rule_needs_two_distinct_blocked_samples():
    """Past the deadline several probes share one (nearest) sample: one blocked frame is not 2 s."""
    ctx = E.Ctx({"duration": 30.0, "series": {"t": [0.0]}}, {})
    rule = E.CameraRule()
    reqs = rule.propose(ctx)
    pairs = [(q, ProbeResult(q.cue, q.t, 12 <= q.t <= 16, 0.9, [14.0], "cv:blocked", {"sample_t": 14.0}))
             for q in reqs]
    assert rule.decide(ctx, pairs) == []


# ----------------------------------------------------------------------------- series (one decode pass)

def _write_clip(path, frames, fps=10):
    import av

    h, w = frames[0].shape[:2]
    with av.open(str(path), "w") as c:
        s = c.add_stream("mpeg4", rate=fps)
        s.width, s.height, s.pix_fmt = w, h, "yuv420p"
        s.options = {"qscale": "2"}
        for img in frames:
            for pkt in s.encode(av.VideoFrame.from_ndarray(img, format="bgr24")):
                c.mux(pkt)
        for pkt in s.encode():
            c.mux(pkt)
    return str(path)


@pytest.fixture(scope="module")
def covered_clip(tmp_path_factory):
    """12 s at 10 fps: clear scene (with OSD) until 6 s, then a noisy black cover (OSD stays)."""
    ref = with_osd(REF)
    cover = with_osd(noisy(np.full_like(REF, 12), 3))
    frames = [ref if i < 60 else cover for i in range(120)]
    return _write_clip(tmp_path_factory.mktemp("t14") / "cover.mp4", frames)


def test_series_one_pass_low_res(covered_clip):
    ser = cheap.camera_blocked_series(covered_clip)
    assert ser["complete"] and ser["mode"] == "full" and 0.0 <= ser["ref_t"] <= 4.0
    assert ser["t"] == pytest.approx([float(k) for k in range(12)])
    flags = [bool(v) for v, _, _ in ser["res"]]
    assert flags == [t >= 6 for t in ser["t"]]
    v, c, raw = cheap.series_at(ser, 8.2)
    assert v is True and raw["sample_t"] == 8.0
    assert cheap.series_at(ser, 30.0)[0] is None  # no sample near: undecided, not "clear"


def test_series_past_deadline_runs_bounded_pass(covered_clip):
    """Truncated perception sets the probe deadline to 'now': CV must still answer (coarser grid)."""
    import time

    t0 = time.monotonic()
    ser = cheap.camera_blocked_series(covered_clip, deadline=t0 - 1.0, duration=12.0)
    assert time.monotonic() - t0 < cheap.BLOCK_BOUNDED_S + 1.0
    assert ser["mode"] == "bounded" and 0 < len(ser["t"]) <= cheap.BLOCK_BOUNDED_N
    assert cheap.series_at(ser, 2.0)[0] is False
    assert cheap.series_at(ser, 10.0)[0] is True


@pytest.fixture(scope="module")
def starts_covered_clip(tmp_path_factory):
    """Covered (noisy black) from the first frame until 8 s, then the scene."""
    cover = noisy(np.full_like(REF, 12), 3)
    frames = [cover if i < 80 else REF for i in range(120)]
    return _write_clip(tmp_path_factory.mktemp("t14") / "starts_covered.mp4", frames)


def test_series_clip_that_starts_covered_is_blocked_from_the_start(starts_covered_clip):
    ser = cheap.camera_blocked_series(starts_covered_clip)
    flags = {t: bool(v) for t, (v, _, _) in zip(ser["t"], ser["res"])}
    assert all(flags[t] for t in flags if t < 8), flags
    assert not any(flags[t] for t in flags if t >= 8), flags
    assert ser["ref_t"] == 8.0  # first structured view becomes the reference
    raw = cheap.series_at(ser, 3.0)[2]
    assert raw["mode"] == "absolute" or raw.get("why_ref") == "flat_reference"


def test_series_black_or_garbage_first_frame_is_not_the_reference(tmp_path_factory):
    rng = np.random.default_rng(3)
    garbage = rng.integers(0, 255, REF.shape, dtype=np.uint8)  # decoder garbage: pure noise
    frames = [np.zeros_like(REF)] * 5 + [garbage] * 5 + [REF] * 110  # 0-0.5 s black, then garbage
    clip = _write_clip(tmp_path_factory.mktemp("t14") / "bad_first.mp4", frames)
    ser = cheap.camera_blocked_series(clip)
    assert ser["ref_t"] >= 1.0
    flags = [bool(v) for t, (v, _, _) in zip(ser["t"], ser["res"]) if t >= 1.0]
    assert flags and not any(flags)


def test_dim_from_start_clip_is_never_t14(tmp_path_factory):
    """Whole clip at x0.2 brightness: may never yield a structured reference -> None, not blocked."""
    dim = (REF.astype(np.float32) * 0.2).astype(np.uint8)
    frames = [noisy(dim, 1.5, seed=i) for i in range(120)]
    clip = _write_clip(tmp_path_factory.mktemp("t14") / "dim.mp4", frames)
    from forte_detector.probes import runner

    tl = {"duration": 12.0, "series": {"t": [0.0]}}
    ctx = E.Ctx(tl, {})
    rule = E.CameraRule()
    reqs = rule.propose(ctx)
    res = runner.run_probes(clip, reqs, timeline=tl, calibration={})
    assert not any(r.value for r in res)
    assert rule.decide(ctx, list(zip(reqs, res))) == []


def _t14_events(clip, duration):
    from forte_detector.probes import runner

    tl = {"duration": duration, "series": {"t": [0.0]}}
    ctx = E.Ctx(tl, {})
    rule = E.CameraRule()
    reqs = rule.propose(ctx)
    res = runner.run_probes(clip, reqs, timeline=tl, calibration={})
    return rule.decide(ctx, list(zip(reqs, res))), res


def test_covered_throughout_is_blocked_without_reference(tmp_path_factory):
    """No structured view ever: the structureless (brightness-normalised) test still flags a cover."""
    clip = _write_clip(tmp_path_factory.mktemp("t14") / "black.mp4", [noisy(np.full_like(REF, 12), 3)] * 60)
    ser = cheap.camera_blocked_series(clip)
    assert ser["ref_t"] is None and ser["res"]
    assert all(v is True and raw["why"] == "structureless" for v, _, raw in ser["res"])


@pytest.mark.parametrize("kind", ["black_osd", "white_tape", "grey_haze"])
def test_covered_from_frame_0_is_t14(tmp_path_factory, kind):
    rng = np.random.default_rng(5)
    if kind == "black_osd":
        frames = [with_osd(noisy(np.full_like(REF, 12), 3, seed=i), f"2026-10-17 12:00:{i // 10:02d}") for i in range(80)]
    elif kind == "white_tape":
        tape = np.clip(215 + cv2.GaussianBlur(rng.normal(0, 25, REF.shape[:2]), (0, 0), 6)[..., None], 0, 255)
        frames = [noisy(tape.astype(np.uint8).repeat(3, 2), 2, seed=i) for i in range(80)]
    else:
        haze = cv2.GaussianBlur(REF, (0, 0), 30).astype(np.float32) * 0.2 + 0.8 * 125  # blur + grey veil
        frames = [with_osd(noisy(haze.astype(np.uint8), 4, seed=i)) for i in range(80)]
    clip = _write_clip(tmp_path_factory.mktemp("t14") / f"cs_{kind}.mp4", frames)
    ev, _ = _t14_events(clip, 8.0)
    assert [e.type for e in ev] == ["T14"] and ev[0].timecode_sec <= 2.0


@pytest.mark.parametrize("scale", [0.2, 0.1])
def test_dim_from_start_is_not_structureless(tmp_path_factory, scale):
    """The reason for the no-reference rule: a dim but structured room (x0.2 has absolute energy
    below BLOCK_ABS_ENERGY) must not look like a cover; the ratio is brightness-normalised."""
    dim = noisy((REF.astype(np.float32) * scale).astype(np.uint8), 1.5)
    assert cheap.structure_energy(dim) < cheap.BLOCK_ABS_ENERGY
    assert not cheap.structureless(dim) and cheap.camera_blocked(dim, None)[0] is False
    clip = _write_clip(tmp_path_factory.mktemp("t14") / f"dim{scale}.mp4",
                       [with_osd(noisy((REF.astype(np.float32) * scale).astype(np.uint8), 1.5, seed=i)) for i in range(80)])
    ev, res = _t14_events(clip, 8.0)
    assert ev == [] and not any(r.value for r in res)


def test_relative_structure_survives_dimming_not_covers():
    r_full = cheap.relative_structure(REF)[0]
    r_dim = cheap.relative_structure((REF.astype(np.float32) * 0.2).astype(np.uint8))[0]
    assert r_dim > 0.7 * r_full > cheap.BLOCK_REL_ENERGY
    for cover in (np.zeros_like(REF), np.full_like(REF, 230), noisy(np.full_like(REF, 12), 3),
                  noisy((cv2.GaussianBlur(REF, (0, 0), 30) * 0.2 + 100).astype(np.uint8), 3)):
        assert cheap.relative_structure(cover)[0] < cheap.BLOCK_REL_ENERGY


def test_no_reference_absolute_test():
    assert cheap.camera_blocked(noisy(np.full_like(REF, 12), 3), None)[0] is True
    assert cheap.camera_blocked(with_osd(noisy(np.full_like(REF, 12), 3)), None)[0] is True  # OSD is not structure
    assert cheap.camera_blocked(REF, None)[0] is False
    assert cheap.structure_energy(REF) > 2 * cheap.BLOCK_ABS_ENERGY


def test_runner_answers_all_camera_probes_from_one_pass(covered_clip, monkeypatch):
    from forte_detector.probes import runner

    calls = []
    real = cheap.camera_blocked_series
    monkeypatch.setattr(cheap, "camera_blocked_series", lambda *a, **k: calls.append(1) or real(*a, **k))
    tl = {"duration": 12.0, "series": {"t": [0.0]}}
    ctx = E.Ctx(tl, {})
    rule = E.CameraRule()
    reqs = rule.propose(ctx)
    res = runner.run_probes(covered_clip, reqs, timeline=tl, calibration={})
    assert len(calls) == 1
    ev = rule.decide(ctx, list(zip(reqs, res)))
    assert [e.type for e in ev] == ["T14"] and ev[0].timecode_sec == 6.0


def test_t14_text_says_covered_from_start_without_reference(tmp_path_factory):
    clip = _write_clip(tmp_path_factory.mktemp("t14") / "cs_text.mp4",
                       [with_osd(noisy(np.full_like(REF, 12), 3, seed=i)) for i in range(80)])
    ev, _ = _t14_events(clip, 8.0)
    assert len(ev) == 1 and ev[0].extra["from_start"] is True
    assert ev[0].obs.endswith("изображение с камеры перекрыто с начала записи")
    assert "по сравнению с началом" not in ev[0].description_ru


def test_t14_text_with_reference_keeps_comparison_wording(covered_clip):
    ev, _ = _t14_events(covered_clip, 12.0)
    assert len(ev) == 1 and ev[0].extra["from_start"] is False
    assert "по сравнению с началом записи" in ev[0].obs and not ev[0].obs.endswith(".")
