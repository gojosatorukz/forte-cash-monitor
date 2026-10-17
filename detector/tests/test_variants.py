import numpy as np
import pytest

from eval.variants import ROBUST, VARIANTS, resolve_variants

H, W = 848, 464  # dataset frames are portrait 464x848


def _rand():
    return np.random.default_rng(0).integers(0, 256, (H, W, 3), dtype=np.uint8)


def test_registry_names():
    assert list(VARIANTS) == ["orig", "hflip", "rot90cw", "rot+12", "rot-12", "crop80", "crop_tl", "dark"]
    assert VARIANTS["orig"] is None  # the analyzer gets transform=None, like the service
    assert ROBUST == ["orig", "hflip", "rot+12", "crop80", "rot90cw"]


def test_resolve_variants():
    assert resolve_variants("orig") == ["orig"]
    assert resolve_variants("robust") == ROBUST
    assert resolve_variants("all") == list(VARIANTS)
    assert resolve_variants("hflip,dark") == ["hflip", "dark"]
    with pytest.raises(ValueError):
        resolve_variants("nope")


def test_hflip():
    f = _rand()
    assert np.array_equal(VARIANTS["hflip"](f), f[:, ::-1])


def test_rot90cw():
    f = _rand()
    out = VARIANTS["rot90cw"](f)
    assert out.shape == (W, H, 3)
    assert np.array_equal(out[0, 0], f[H - 1, 0])  # bottom-left goes to top-left
    assert np.array_equal(out[0, H - 1], f[0, 0])  # top-left goes to top-right


@pytest.mark.parametrize("name", ["rot+12", "rot-12"])
def test_rotation_has_no_black_borders(name):
    white = np.full((H, W, 3), 255, np.uint8)
    out = VARIANTS[name](white)
    assert out.min() >= 250
    # largest inscribed rectangle of a 464x848 frame rotated 12 deg is ~303.9x802.4, minus 4 px margin
    assert out.shape[:2] == (798, 299)


@pytest.mark.parametrize("name, right_side_higher", [("rot+12", True), ("rot-12", False)])
def test_rotation_direction(name, right_side_higher):
    # horizontal dark line through the centre; +12 = counter-clockwise (OpenCV convention)
    img = np.full((H, W, 3), 255, np.uint8)
    img[H // 2 - 3 : H // 2 + 3] = 0
    out = VARIANTS[name](img)[..., 0]
    y_left = np.argmin(out[:, 2])
    y_right = np.argmin(out[:, -3])
    assert (y_right < y_left) == right_side_higher
    assert abs(y_right - y_left) > 10


def test_crop80_centre():
    f = _rand()
    out = VARIANTS["crop80"](f)
    h, w = round(H * 0.8), round(W * 0.8)
    y0, x0 = (H - h) // 2, (W - w) // 2
    assert np.array_equal(out, f[y0 : y0 + h, x0 : x0 + w])


def test_crop_tl():
    f = _rand()
    out = VARIANTS["crop_tl"](f)
    assert np.array_equal(out, f[: round(H * 0.8), : round(W * 0.8)])


def test_dark_gamma():
    f = np.array([[[0, 128, 255]]], np.uint8)
    out = VARIANTS["dark"](f)
    assert out.dtype == np.uint8
    assert out[0, 0].tolist() == [0, round(255 * (128 / 255) ** 1.8), 255]


@pytest.mark.parametrize("name", [n for n in VARIANTS if n != "orig"])
def test_outputs_are_contiguous_uint8_bgr(name):
    out = VARIANTS[name](_rand())
    assert out.dtype == np.uint8 and out.ndim == 3 and out.shape[2] == 3
    assert out.flags["C_CONTIGUOUS"]
