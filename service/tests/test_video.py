import base64

import cv2
import numpy as np
import pytest

from app.models import VideoMeta
from app.video import VideoError, frame_at, iter_frames, jpeg_b64, probe


def test_probe_basic(test_video):
    meta = probe(test_video)
    assert isinstance(meta, VideoMeta)
    assert meta.duration_sec == pytest.approx(3.0, abs=0.1)
    assert meta.fps == pytest.approx(25.0, abs=0.01)
    assert (meta.width, meta.height) == (320, 240)
    assert meta.rotation == 0
    assert meta.codec == "h264"
    assert meta.frame_count == 75


def test_probe_cyrillic(cyrillic_video):
    meta = probe(cyrillic_video)
    assert (meta.width, meta.height) == (320, 240)


def test_probe_missing_file(tmp_path):
    with pytest.raises(VideoError):
        probe(tmp_path / "nope.mov")


def test_probe_not_a_video(tmp_path):
    p = tmp_path / "junk.mov"
    p.write_bytes(b"not a video at all")
    with pytest.raises(VideoError):
        probe(p)


def test_frame_at_shape(test_video):
    frame = frame_at(test_video, 1.0)
    assert frame.dtype == np.uint8
    assert frame.shape == (240, 320, 3)
    assert frame.std() > 10  # testsrc is colourful, not a blank frame


def test_frame_at_cyrillic(cyrillic_video):
    frame = frame_at(cyrillic_video, 1.5)
    assert frame.shape == (240, 320, 3)


def test_frame_at_downscales(test_video):
    frame = frame_at(test_video, 0.5, max_side=160)
    assert frame.shape == (120, 160, 3)


def test_frame_at_clamps_past_end(test_video):
    frame = frame_at(test_video, 99.0)
    assert frame.shape == (240, 320, 3)


def test_rotated_probe_and_frame(rotated_video):
    meta = probe(rotated_video)
    assert abs(meta.rotation) == 90
    assert (meta.width, meta.height) == (240, 320)
    frame = frame_at(rotated_video, 1.0)
    assert frame.shape == (320, 240, 3)
    frames = list(iter_frames(rotated_video, fps=1))
    assert frames and frames[0][1].shape == (320, 240, 3)


def test_iter_frames(test_video):
    frames = list(iter_frames(test_video, fps=2))
    assert 5 <= len(frames) <= 7
    ts = [t for t, _ in frames]
    assert ts == sorted(ts)
    assert ts[0] == pytest.approx(0.0)
    assert ts[1] == pytest.approx(0.5)
    assert all(f.shape == (240, 320, 3) for _, f in frames)


def test_iter_frames_early_close(cyrillic_video):
    gen = iter_frames(cyrillic_video, fps=5, max_side=100)
    t, f = next(gen)
    assert max(f.shape[:2]) == 100
    gen.close()  # must not hang or leak


def _fake_meta() -> VideoMeta:
    return VideoMeta(duration_sec=3.0, fps=25.0, width=320, height=240, rotation=0, codec="h264", frame_count=75)


@pytest.mark.parametrize("kind", ["garbage", "truncated"])
def test_iter_frames_undecodable_raises(tmp_path, test_video, kind):
    """ffprobe may or may not accept such a file; iter_frames itself must not silently
    yield zero frames when ffmpeg fails."""
    bad = tmp_path / f"{kind}.mov"
    if kind == "garbage":
        bad.write_bytes(b"\x00\x00\x00\x18ftypqt  garbage" + b"\xab" * 20000)
    else:
        data = test_video.read_bytes()
        bad.write_bytes(data[: len(data) // 3])
    with pytest.raises(VideoError):
        list(iter_frames(bad, fps=1, meta=_fake_meta()))


def test_iter_frames_early_close_after_error_free_start_does_not_raise(test_video):
    gen = iter_frames(test_video, fps=25)
    next(gen)
    gen.close()  # ffmpeg is killed (non-zero rc) but that must not raise


def test_jpeg_b64(test_video):
    s = jpeg_b64(frame_at(test_video, 1.0))
    assert s.startswith("/9j/")
    raw = base64.b64decode(s)
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape == (240, 320, 3)


def test_probe_timeout_raises_video_error(test_video, monkeypatch):
    import subprocess as sp

    import app.video as video

    def fake_run(*a, **kw):
        assert kw["timeout"] == 0.01
        raise sp.TimeoutExpired(cmd="ffprobe", timeout=kw["timeout"])

    monkeypatch.setattr(video.subprocess, "run", fake_run)
    with pytest.raises(video.VideoError):
        video.probe(test_video, timeout=0.01)
