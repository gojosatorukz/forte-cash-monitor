"""End-to-end perception smoke test on synthetic clips with a fake detector (no GPU, no model):
landscape 1920x1080 input and a low-fps (2 fps) source."""
import av
import numpy as np
import pytest

from forte_detector.perception import pipeline
from forte_detector.perception.pipeline import PerceptionConfig, perceive


class FakeDetector:
    """'Person' = bounding box of bright pixels."""

    device = "cpu"

    def detect(self, frames):
        out = []
        for f in frames:
            ys, xs = np.nonzero(f[:, :, 1] > 200)
            h, w = f.shape[:2]
            persons = []
            if len(xs):
                persons = [[xs.min() / w, ys.min() / h, (xs.max() + 1) / w, (ys.max() + 1) / h, 0.9]]
            out.append({"persons": persons, "objects": []})
        return out


def _write_clip(path, w, h, fps, seconds, box_at, display_ccw=0):
    with av.open(str(path), "w") as c:
        s = c.add_stream("libx264", rate=fps)
        s.width, s.height, s.pix_fmt = w, h, "yuv420p"
        if display_ccw:
            s.set_display_rotation(display_ccw)
        for i in range(int(seconds * fps)):
            img = np.zeros((h, w, 3), np.uint8)
            x1, y1, x2, y2 = box_at(i / fps)
            img[int(y1 * h) : int(y2 * h), int(x1 * w) : int(x2 * w)] = 255
            for p in s.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
                c.mux(p)
        for p in s.encode():
            c.mux(p)


def _cashier_walks_out(t):
    # seated 0-5 s at the centre, walks to the left edge 5-7 s, gone afterwards (box off-screen)
    seat = np.array([0.45, 0.40, 0.55, 0.70])
    door = np.array([0.02, 0.30, 0.10, 0.60])
    if t < 5:
        return seat
    if t < 7:
        a = (t - 5) / 2
        return seat + a * (door - seat)
    return np.array([0, 0, 0, 0])


@pytest.fixture(autouse=True)
def fake_detector(monkeypatch):
    monkeypatch.setattr(pipeline, "get_detector", lambda cfg, device: FakeDetector())


def test_landscape_1080p_is_downscaled_and_tracked(tmp_path):
    clip = tmp_path / "land.mp4"
    _write_clip(clip, 1920, 1080, 5, 10, _cashier_walks_out)
    tl = perceive(str(clip), use_cache=False, cfg=PerceptionConfig(fps=4.0, max_side=960))
    assert tl["frame_wh"] == [960, 540]
    assert tl["meta"]["n_samples"] >= 35  # ~4 fps sampled from a 5 fps source by timestamp
    assert tl["roles"]["cashier_tracks"]
    types = [c["type"] for c in tl["checkpoints"]]
    assert "cashier_leaves_seat" in types and "cashier_exits_room" in types and "room_empty_start" in types
    ex = next(c for c in tl["checkpoints"] if c["type"] == "cashier_exits_room")
    assert 6.0 <= ex["t"] <= 7.5


def test_low_fps_source_degrades_gracefully(tmp_path):
    clip = tmp_path / "low.mp4"
    _write_clip(clip, 464, 848, 2, 10, _cashier_walks_out)
    tl = perceive(str(clip), use_cache=False, cfg=PerceptionConfig(fps=4.0))
    assert tl["frame_wh"] == [464, 848]
    assert 18 <= tl["meta"]["n_samples"] <= 21  # each 2 fps source frame used once, no duplicates
    assert abs(tl["meta"]["effective_fps"] - 2.0) < 0.2
    assert tl["step"] == pytest.approx(0.5, abs=0.01)
    types = [c["type"] for c in tl["checkpoints"]]
    assert "cashier_exits_room" in types and "room_empty_start" in types


def _seated(t):
    return np.array([0.45, 0.40, 0.55, 0.70])


def test_cache_is_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("FORTE_EVAL_CACHE", raising=False)
    monkeypatch.setenv("FORTE_CACHE_DIR", str(tmp_path / "cache"))
    clip = tmp_path / "X-1.mp4"
    _write_clip(clip, 464, 848, 4, 4, _seated)
    tl = perceive(str(clip))
    assert tl["meta"]["cached"] is False
    assert not (tmp_path / "cache").exists()


def test_same_name_different_content_never_hits_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("FORTE_EVAL_CACHE", "1")
    monkeypatch.setenv("FORTE_CACHE_DIR", str(tmp_path / "cache"))
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first, second = tmp_path / "a" / "X-1.mov", tmp_path / "b" / "X-1.mov"
    _write_clip(first, 464, 848, 4, 10, _seated)
    _write_clip(second, 464, 848, 4, 10, _cashier_walks_out)
    tl1 = perceive(str(first))
    again = perceive(str(first))
    assert again["meta"]["cached"] is True  # the cache itself works
    tl2 = perceive(str(second))
    assert tl2["meta"]["cached"] is False
    types1 = {c["type"] for c in tl1["checkpoints"]}
    types2 = {c["type"] for c in tl2["checkpoints"]}
    assert "cashier_exits_room" not in types1 and "cashier_exits_room" in types2


def test_variant_name_without_transform_is_rejected(tmp_path):
    clip = tmp_path / "c.mp4"
    _write_clip(clip, 464, 848, 4, 2, _seated)
    with pytest.raises(ValueError):
        perceive(str(clip), variant="hflip")


class UprightOnlyDetector(FakeDetector):
    """Scores a person high only when its box is taller than wide and in the upper half (a stand-in
    for "upright, head up": the synthetic person sits in the top half of the upright frame)."""

    def detect(self, frames):
        out = super().detect(frames)
        for r in out:
            for p in r["persons"]:
                upright = (p[3] - p[1]) > (p[2] - p[0]) and (p[1] + p[3]) / 2 < 0.5
                p[4] = 0.9 if upright else 0.2
        return out


def test_auto_orientation_undoes_a_90_degree_rotation(tmp_path, monkeypatch):
    import cv2

    monkeypatch.setattr(pipeline, "get_detector", lambda cfg, device: UprightOnlyDetector())
    clip = tmp_path / "r.mp4"
    _write_clip(clip, 464, 848, 4, 4, lambda t: np.array([0.45, 0.10, 0.55, 0.45]))
    rot = lambda f: cv2.rotate(f, cv2.ROTATE_90_CLOCKWISE)  # noqa: E731
    tl = perceive(str(clip), transform=rot, use_cache=False)
    assert tl["orientation"]["rot_cw_deg"] == 270
    assert tl["frame_wh"][1] > tl["frame_wh"][0]  # back to portrait
    up = perceive(str(clip), use_cache=False)
    assert up["orientation"]["rot_cw_deg"] == 0


def test_choose_orientation_defaults_to_upright_and_never_180():
    from forte_detector.perception.pipeline import choose_orientation

    up = {0: [1.0, 1.0, 1.0, 1.0], 90: [1.2, 1.2, 1.2, 1.2], 180: [9.0] * 4, 270: [0.5] * 4}
    assert choose_orientation(up, 1.5, 3) == 0  # 90 better but < 1.5x; 180 never
    side = {0: [0.3, 0.2, 0.0, 0.4], 90: [0.1] * 4, 180: [0.0] * 4, 270: [1.5, 1.4, 1.6, 0.2]}
    assert choose_orientation(side, 1.5, 3) == 270
    one_frame = {0: [0.5, 0.5, 0.5, 0.5], 90: [6.0, 0.0, 0.0, 0.0], 180: [0.0] * 4, 270: [0.0] * 4}
    assert choose_orientation(one_frame, 1.5, 3) == 0  # wins on only one frame


def test_container_rotation_tag_is_honoured(tmp_path):
    # stored landscape, display matrix says "rotate 90 deg CCW to show" -> analysed as portrait
    clip = tmp_path / "phone.mov"
    _write_clip(clip, 848, 464, 4, 3, lambda t: np.array([0.40, 0.40, 0.70, 0.60]), display_ccw=90)
    tl = perceive(str(clip), use_cache=False)
    assert tl["orientation"]["source"] == "container"
    assert tl["orientation"]["container_rot_cw_deg"] == 270
    assert tl["frame_wh"] == [464, 848]


class RecordingDetector(UprightOnlyDetector):
    def __init__(self):
        self.seen = []

    def detect(self, frames):
        self.seen.extend(f.copy() for f in frames)
        return super().detect(frames)


@pytest.mark.parametrize("case", ["container_tag+hflip", "auto_rot"])
def test_effective_transform_reproduces_the_frames_perception_saw(tmp_path, monkeypatch, case):
    import cv2

    from forte_detector.perception import effective_transform
    from forte_detector.video_io import read_frame_at

    det = RecordingDetector()
    monkeypatch.setattr(pipeline, "get_detector", lambda cfg, device: det)
    clip = tmp_path / "c.mov"
    if case == "container_tag+hflip":
        _write_clip(clip, 848, 464, 4, 3, lambda t: np.array([0.40, 0.40, 0.70, 0.60]), display_ccw=90)
        user_tf = lambda f: np.ascontiguousarray(f[:, ::-1])  # noqa: E731
    else:
        _write_clip(clip, 464, 848, 4, 3, lambda t: np.array([0.45, 0.10, 0.55, 0.45]))
        user_tf = lambda f: cv2.rotate(f, cv2.ROTATE_90_CLOCKWISE)  # noqa: E731
    tl = perceive(str(clip), transform=user_tf, use_cache=False)
    if case == "auto_rot":
        assert tl["orientation"]["rot_cw_deg"] == 270
    else:
        assert tl["orientation"]["container_rot_cw_deg"] == 270
    eff = effective_transform(tl, user_tf)
    n = len(tl["series"]["t"])
    seen = det.seen[-n:]  # the orientation probe's frames come first
    for t, ref in list(zip(tl["series"]["t"], seen))[::3]:
        img = read_frame_at(str(clip), t, transform=eff)
        assert img.shape == ref.shape
        assert np.abs(img.astype(int) - ref.astype(int)).mean() < 1.0


# ----------------------------------------------------------------------------- long uploads (I1)

def test_long_clip_gets_adaptive_sampling_fps(tmp_path):
    """fps = min(cfg.fps, max_samples / duration): a long upload keeps <= ~max_samples samples."""
    clip = tmp_path / "long.mp4"
    _write_clip(clip, 320, 240, 5, 10, _cashier_walks_out)
    tl = perceive(str(clip), use_cache=False, cfg=PerceptionConfig(fps=4.0, max_samples=20))
    assert tl["meta"]["sampling_fps"] == pytest.approx(2.0, abs=0.05)
    assert tl["meta"]["n_samples"] <= 22
    short = perceive(str(clip), use_cache=False, cfg=PerceptionConfig(fps=4.0))
    assert short["meta"]["sampling_fps"] == 4.0 and short["meta"]["n_samples"] >= 35


def test_max_samples_for_one_hour():
    assert pipeline.sampling_fps(4.0, 3600.0, 2400) == pytest.approx(2400 / 3600, abs=1e-3)
    assert pipeline.sampling_fps(4.0, 145.0, 2400) == 4.0
    assert pipeline.sampling_fps(4.0, 0.0, 2400) == 4.0  # unknown duration: deadline protects


@pytest.mark.parametrize("kind", ["deadline", "cancelled"])
def test_perceive_stops_between_batches(tmp_path, kind):
    import time as _t

    clip = tmp_path / "c.mp4"
    _write_clip(clip, 320, 240, 5, 10, _cashier_walks_out)
    calls = []

    def stop():
        calls.append(1)
        return len(calls) >= 2

    kw = {"deadline": _t.monotonic() - 1} if kind == "deadline" else {"should_stop": stop}
    tl = perceive(str(clip), use_cache=False, cfg=PerceptionConfig(fps=4.0, batch_size=4), **kw)
    assert tl["meta"]["truncated"] == kind
    assert 1 <= tl["meta"]["n_samples"] <= 8  # one or two batches, not the whole clip
    full = perceive(str(clip), use_cache=False, cfg=PerceptionConfig(fps=4.0, batch_size=4))
    assert full["meta"]["truncated"] is None
