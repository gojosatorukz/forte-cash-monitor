"""Evidence boxes: validation/normalization and Pillow drawing."""

from __future__ import annotations

import base64
import io

import numpy as np
import pytest
from PIL import Image

from app import evidence
from app.evidence import FONT_PATH, annotate_frames, draw_boxes
from app.models import EvidenceFrame, Violation, normalize_box


def jpeg_b64(w=320, h=240, color=(40, 60, 80)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()


def decode(b64: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")


# ---------- validation ----------

def test_normalize_box_clamps_and_defaults():
    b = normalize_box({"x": -0.2, "y": 0.5, "w": 0.5, "h": 0.8, "label": "  Сейф  ", "kind": "PERSON"})
    assert b == pytest.approx({"x": 0.0, "y": 0.5, "w": 0.3, "h": 0.5, "label": "Сейф", "kind": "person"})
    b = normalize_box({"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2})
    assert b["kind"] == "object" and b["label"] is None
    assert normalize_box({"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2, "kind": "car", "label": "  "}) == {
        "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2, "label": None, "kind": "object"}
    assert normalize_box({"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2, "kind": "region"})["kind"] == "region"


@pytest.mark.parametrize("raw", [
    {"x": 0.1, "y": 0.1, "w": 0, "h": 0.2},
    {"x": 0.1, "y": 0.1, "w": 0.2, "h": -1},
    {"x": float("nan"), "y": 0.1, "w": 0.2, "h": 0.2},
    {"x": 0.1, "y": float("inf"), "w": 0.2, "h": 0.2},
    {"x": 0.1, "y": 0.1, "w": "wide", "h": 0.2},
    {"x": True, "y": 0.1, "w": 0.2, "h": 0.2},
    {"y": 0.1, "w": 0.2, "h": 0.2},
    {"x": 1.5, "y": 0.1, "w": 0.2, "h": 0.2},  # entirely outside -> no area left
    "box", None, 3,
])
def test_bad_boxes_are_dropped(raw):
    assert normalize_box(raw) is None


def test_frame_drops_bad_boxes_keeps_frame_and_violation():
    fr = EvidenceFrame.model_validate({"timestamp_sec": 1.0, "image_base64": "x", "boxes": [
        {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2, "label": "ok"}, {"x": "bad"}, None, {"x": 0, "y": 0, "w": 0, "h": 1}]})
    assert [b.label for b in fr.boxes] == ["ok"]
    assert EvidenceFrame.model_validate({"timestamp_sec": 1.0, "image_base64": "x", "boxes": "junk"}).boxes == []
    v = Violation.model_validate({"type": "T1", "timecode_sec": 1, "evidence_frames": [
        {"timestamp_sec": 1.0, "image_base64": "x", "boxes": {"not": "a list"}}]})
    assert v.evidence_frames[0].boxes == []


# ---------- drawing ----------

def test_font_is_bundled_with_license():
    assert FONT_PATH.is_file() and FONT_PATH.stat().st_size > 100_000
    lic = FONT_PATH.parent / "LICENSE-DejaVu.txt"
    assert lic.is_file() and "Bitstream" in lic.read_text("utf-8")


def test_draw_boxes_changes_pixels_keeps_size_and_renders_cyrillic():
    src = jpeg_b64(320, 240)
    box = {"x": 0.25, "y": 0.3, "w": 0.5, "h": 0.4, "label": "Зона кассы (демо)", "kind": "region"}
    out = draw_boxes(src, [box])
    assert out != src
    img = np.asarray(decode(out)).astype(int)
    assert img.shape == (240, 320, 3)
    # outline is green (#1DB954) on the box's left edge
    edge = img[120, 80]
    assert edge[1] > 150 and edge[0] < 90 and edge[2] < 140, edge
    # the chip (above the box, filled green) holds white text pixels -> glyphs were rendered
    chip = img[72 - 18:72, 80:200]
    assert (chip.min(axis=2) > 200).sum() > 20
    # no label -> no chip
    plain = np.asarray(decode(draw_boxes(src, [{**box, "label": None}]))).astype(int)
    assert (plain[72 - 18:72, 80:200].min(axis=2) > 200).sum() == 0


def test_cyrillic_glyphs_differ_from_missing_glyph_box():
    # A font without Cyrillic draws identical "tofu" boxes for different letters.
    font = evidence._font(20)
    a = font.getmask("Ж").getbbox()
    b = font.getmask("ы").getbbox()
    assert a and b and a != b


def test_person_box_is_red():
    out = draw_boxes(jpeg_b64(), [{"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5, "label": None, "kind": "person"}])
    px = np.asarray(decode(out)).astype(int)[100, 33]
    assert px[0] > 180 and px[1] < 110, px


def frame(ts, boxes=None, color=(40, 60, 80)):
    d = {"timestamp_sec": ts, "image_base64": jpeg_b64(color=color)}
    if boxes is not None:
        d["boxes"] = boxes
    return d


BOX = {"x": 0.2, "y": 0.2, "w": 0.4, "h": 0.4, "label": "Сейф", "kind": "object"}


def test_annotated_first_then_raw_then_rest():
    frames = [frame(1.0), frame(2.0, [BOX]), frame(3.0)]
    out = annotate_frames(frames)
    assert [f["timestamp_sec"] for f in out] == [2.0, 2.0, 1.0]  # capped at 3
    assert [f["annotated"] for f in out] == [True, False, False]
    assert out[1]["image_base64"] == frames[1]["image_base64"]
    assert out[0]["image_base64"] != frames[1]["image_base64"]
    assert out[0]["boxes"] == [BOX] and out[2]["boxes"] == []


def test_cap_of_three_frames():
    out = annotate_frames([frame(i, [BOX]) for i in range(6)])
    assert len(out) == 3 and [f["timestamp_sec"] for f in out] == [0, 0, 1]


def test_frames_without_boxes_pass_through():
    frames = [frame(i, color=(10 * i, 60, 80)) for i in range(5)]
    out = annotate_frames(frames)
    assert [f["image_base64"] for f in out] == [f["image_base64"] for f in frames[:3]]  # cap always holds
    assert all(f["annotated"] is False and f["boxes"] == [] for f in out)


def test_drawing_failure_falls_back_to_raw():
    frames = [{"timestamp_sec": 1.0, "image_base64": base64.b64encode(b"not a jpeg").decode(), "boxes": [BOX]},
              frame(2.0)]
    out = annotate_frames(frames)
    assert [f["timestamp_sec"] for f in out] == [1.0, 2.0]
    assert all(f["annotated"] is False for f in out)
    assert out[0]["image_base64"] == frames[0]["image_base64"]


def test_every_boxed_frame_is_drawn_within_cap():
    frames = [frame(1.0, [BOX], color=(40, 60, 80)), frame(2.0, [BOX], color=(80, 60, 40)),
              frame(3.0, [BOX], color=(60, 80, 40))]
    out = annotate_frames(frames)
    assert [f["timestamp_sec"] for f in out] == [1.0, 1.0, 2.0]
    assert [f["annotated"] for f in out] == [True, False, True]
    assert out[1]["image_base64"] == frames[0]["image_base64"]
    assert out[0]["image_base64"] != frames[0]["image_base64"]
    assert out[2]["image_base64"] != frames[1]["image_base64"]
    assert all(f["boxes"] == [BOX] for f in out)


def test_unboxed_second_frame_passes_through_raw():
    frames = [frame(1.0, [BOX]), frame(2.0), frame(3.0, [BOX])]
    out = annotate_frames(frames)
    assert [f["timestamp_sec"] for f in out] == [1.0, 1.0, 2.0]
    assert [f["annotated"] for f in out] == [True, False, False]
    assert out[2]["image_base64"] == frames[1]["image_base64"] and out[2]["boxes"] == []


def test_drawing_failure_on_later_frame_keeps_only_that_frame_raw(monkeypatch):
    frames = [frame(1.0, [BOX]), frame(2.0, [BOX], color=(80, 60, 40))]
    real = evidence.draw_boxes

    def flaky(b64, boxes):
        if b64 == frames[1]["image_base64"]:
            raise ValueError("boom")
        return real(b64, boxes)

    monkeypatch.setattr(evidence, "draw_boxes", flaky)
    out = annotate_frames(frames)
    assert [f["timestamp_sec"] for f in out] == [1.0, 1.0, 2.0]
    assert [f["annotated"] for f in out] == [True, False, False]
    assert out[2]["image_base64"] == frames[1]["image_base64"] and out[2]["boxes"] == [BOX]


def test_draw_disabled_near_deadline_passes_through():
    frames = [frame(1.0, [BOX])]
    out = annotate_frames(frames, draw=False)
    assert out[0]["annotated"] is False and out[0]["image_base64"] == frames[0]["image_base64"]


def test_long_label_truncated():
    assert len(evidence._truncate("Д" * 100)) == evidence.LABEL_MAX_CHARS
    assert evidence._truncate("короткая") == "короткая"


def test_later_outline_does_not_cut_earlier_label():
    """Outlines are drawn first, chips on top: box B's edge must not show inside A's chip."""
    src = jpeg_b64(320, 240)
    a = {"x": 0.1, "y": 0.5, "w": 0.4, "h": 0.3, "label": "кассир", "kind": "person"}
    b = {"x": 0.2, "y": 0.3, "w": 0.5, "h": 0.6, "label": "посетитель", "kind": "object"}
    cx0, cy0, cx1, cy1 = evidence.chip_rects([a, b], (320, 240))[0]
    bx = round(b["x"] * 320) + 1  # inside B's 3px left outline
    assert cx0 < bx < cx1, "test setup: B's left edge must cross A's chip"
    assert b["y"] * 240 < cy0  # B's edge spans A's chip vertically
    img = np.asarray(decode(draw_boxes(src, [a, b]))).astype(int)
    # every pixel on B's edge inside A's chip is chip red or white text, never B's green
    for y in range(int(cy0) + 2, int(cy1) - 1):
        r, g, bl = img[y, bx]
        is_chip = r > 170 and g < 120 and bl < 120  # #E53935
        is_text = min(r, g, bl) > 120  # white glyph / antialiasing towards white
        assert is_chip or is_text, (y, (r, g, bl))
        assert not (g > 140 and r < 100), f"B's green outline visible in A's chip at y={y}"
