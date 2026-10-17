"""Named in-memory frame transforms for robustness evaluation (passed as `transform=`)."""
from __future__ import annotations

import math

import cv2
import numpy as np

from forte_detector.schema import Transform


def _inscribed_size(w: int, h: int, angle_deg: float) -> tuple[float, float]:
    """Largest axis-aligned rectangle inside a w x h rectangle rotated by angle_deg."""
    a = math.radians(angle_deg)
    sin_a, cos_a = abs(math.sin(a)), abs(math.cos(a))
    long_side, short_side = max(w, h), min(w, h)
    if short_side <= 2 * sin_a * cos_a * long_side or abs(sin_a - cos_a) < 1e-10:
        x = 0.5 * short_side
        return (x / sin_a, x / cos_a) if w >= h else (x / cos_a, x / sin_a)
    cos_2a = cos_a * cos_a - sin_a * sin_a
    return (w * cos_a - h * sin_a) / cos_2a, (h * cos_a - w * sin_a) / cos_2a


def rotate_crop(angle_deg: float) -> Transform:
    """Rotate counter-clockwise by angle_deg (OpenCV convention), crop to no black borders."""

    def tf(frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        m = cv2.getRotationMatrix2D((w / 2, h / 2), angle_deg, 1.0)
        rot = cv2.warpAffine(frame, m, (w, h), flags=cv2.INTER_LINEAR)
        cw, ch = _inscribed_size(w, h, angle_deg)
        cw, ch = int(cw) - 4, int(ch) - 4  # margin against interpolated border pixels
        x0, y0 = (w - cw) // 2, (h - ch) // 2
        return np.ascontiguousarray(rot[y0 : y0 + ch, x0 : x0 + cw])

    return tf


def crop(frac: float, anchor: str) -> Transform:
    def tf(frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        ch, cw = round(h * frac), round(w * frac)
        y0, x0 = ((h - ch) // 2, (w - cw) // 2) if anchor == "centre" else (0, 0)
        return np.ascontiguousarray(frame[y0 : y0 + ch, x0 : x0 + cw])

    return tf


def gamma(g: float) -> Transform:
    lut = np.array([round(255 * (i / 255) ** g) for i in range(256)], np.uint8)
    return lambda frame: cv2.LUT(frame, lut)


VARIANTS: dict[str, Transform | None] = {
    "orig": None,
    "hflip": lambda f: np.ascontiguousarray(f[:, ::-1]),
    "rot90cw": lambda f: cv2.rotate(f, cv2.ROTATE_90_CLOCKWISE),
    "rot+12": rotate_crop(12),
    "rot-12": rotate_crop(-12),
    "crop80": crop(0.8, "centre"),
    "crop_tl": crop(0.8, "top_left"),
    "dark": gamma(1.8),
}
ROBUST = ["orig", "hflip", "rot+12", "crop80", "rot90cw"]


def resolve_variants(spec: str) -> list[str]:
    if spec == "all":
        return list(VARIANTS)
    if spec == "robust":
        return list(ROBUST)
    names = [s.strip() for s in spec.split(",") if s.strip()]
    unknown = [n for n in names if n not in VARIANTS]
    if unknown or not names:
        raise ValueError(f"unknown variant(s) {unknown}; choose from {list(VARIANTS)}, all, robust")
    return names
