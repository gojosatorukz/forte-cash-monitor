"""Shared fixtures: tiny generated test videos (never the dataset)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

W, H, FPS, DURATION = 320, 240, 25, 3


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", *args], check=True)


@pytest.fixture(scope="session")
def video_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("videos")


@pytest.fixture(scope="session")
def test_video(video_dir: Path) -> Path:
    """3 s, 320x240, 25 fps H.264 clip in a .mov container."""
    out = video_dir / "clip.mov"
    _ffmpeg(
        "-f", "lavfi", "-i", f"testsrc=size={W}x{H}:rate={FPS}:duration={DURATION}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out),
    )
    return out


@pytest.fixture(scope="session")
def cyrillic_video(video_dir: Path, test_video: Path) -> Path:
    """Same clip under a Cyrillic filename (dataset files use Cyrillic 'Х')."""
    sub = video_dir / "кириллица"
    sub.mkdir(exist_ok=True)
    out = sub / "Х-тест видео.mov"
    shutil.copyfile(test_video, out)
    return out


@pytest.fixture(scope="session")
def rotated_video(video_dir: Path, test_video: Path) -> Path:
    """Clip with a 90-degree display-matrix rotation (stored 320x240, shown 240x320)."""
    out = video_dir / "rotated.mov"
    _ffmpeg("-display_rotation", "90", "-i", str(test_video), "-c", "copy", str(out))
    return out
