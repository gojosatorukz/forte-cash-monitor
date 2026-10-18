"""iter_frames / read_frame_at on a tiny synthetic video (frame index encoded as brightness)."""
import av
import numpy as np
import pytest

from forte_detector.video_io import iter_frames, read_frame_at

FPS = 10
N = 30  # 3 s


_REF: list[float] = []  # mean brightness of every decoded frame, filled by the fixture


def _idx(frame) -> int:
    """Source frame index, matched by brightness against a plain PyAV decode."""
    return int(np.argmin([abs(float(frame.mean()) - m) for m in _REF]))


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    path = _make_video(tmp_path_factory)
    with av.open(path) as c:
        _REF[:] = [float(f.to_ndarray(format="bgr24").mean()) for f in c.decode(video=0)]
    assert len(_REF) == N and np.all(np.diff(_REF) > 1)
    return path


def _make_video(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("vid") / "synthetic.mp4"
    with av.open(str(path), "w") as c:
        s = c.add_stream("mpeg4", rate=FPS)
        s.width, s.height, s.pix_fmt = 64, 48, "yuv420p"
        s.options = {"qscale": "1"}
        for i in range(N):
            img = np.full((48, 64, 3), i * 8, np.uint8)
            for pkt in s.encode(av.VideoFrame.from_ndarray(img, format="bgr24")):
                c.mux(pkt)
        for pkt in s.encode():
            c.mux(pkt)
    return str(path)


def test_iter_frames_samples_nearest_frames(video):
    out = list(iter_frames(video, fps=2))
    ts = [t for t, _ in out]
    assert ts == pytest.approx([0.0, 0.5, 1.0, 1.5, 2.0, 2.5])
    assert [_idx(f) for _, f in out] == [0, 5, 10, 15, 20, 25]
    assert out[0][1].shape == (48, 64, 3) and out[0][1].dtype == np.uint8


def test_iter_frames_never_duplicates_when_fps_exceeds_source(video):
    out = list(iter_frames(video, fps=25))
    idx = [_idx(f) for _, f in out]
    assert idx == list(range(N))


def test_iter_frames_max_side_and_transform(video):
    calls = []

    def tf(f):
        calls.append(f.shape)
        return f[:, ::-1]

    out = list(iter_frames(video, fps=1, max_side=32, transform=tf))
    assert len(out) == 3
    assert out[0][1].shape == (24, 32, 3)
    assert calls == [(48, 64, 3)] * 3  # transform sees full-res frame, resize after


def test_read_frame_at(video):
    assert _idx(read_frame_at(video, 1.2)) == 12
    assert _idx(read_frame_at(video, 0.0)) == 0
    assert _idx(read_frame_at(video, 99.0)) == N - 1
    f = read_frame_at(video, 1.0, transform=lambda x: x[:10])
    assert f.shape[0] == 10


class _NoPtsFrame:
    pts = None

    def __init__(self, k):
        self.k = k

    def to_ndarray(self, format="bgr24"):
        return np.full((4, 4, 3), self.k, np.uint8)


class _NoPtsContainer:
    """Stream without pts: a backward seek lands on the keyframe at 2.0 s (frame 20 of a 10 fps clip)."""

    def __init__(self, *a, **k):
        from fractions import Fraction

        self.stream = type("S", (), {"start_time": 0, "time_base": Fraction(1, 1000), "average_rate": 10})()
        self.streams = type("SS", (), {"video": [self.stream]})()
        self.first = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def seek(self, offset, stream=None, backward=True):
        self.first = 20  # keyframe at 2.0 s

    def decode(self, stream):
        return (_NoPtsFrame(k) for k in range(self.first, 40))


def test_no_pts_fallback_counts_from_the_seek_target(monkeypatch):
    """M4: without pts the i/rate fallback restarted at 0 after a seek, so asking for t=2.5 returned
    the frame 2.5 s after the keyframe (frame 45 -> past the end -> last frame 39) instead of ~25."""
    from forte_detector import video_io

    monkeypatch.setattr(video_io.av, "open", _NoPtsContainer)
    k = int(read_frame_at("x.mov", 2.5)[0, 0, 0])
    assert abs(k - 25) <= 5  # within the keyframe interval of the target, not frame 39
