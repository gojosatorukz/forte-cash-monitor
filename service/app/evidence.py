"""Evidence-frame annotation: draws analyzer boxes (with optional label chips) with Pillow.

The analyzer sends upright evidence JPEGs plus boxes normalized to them. The service draws
once and reorders the frames (see `annotate_frames`). The public frame keys stay
`{timestamp_sec, image_base64}`; `annotated` and `boxes` are extra. No labels are invented
and no faces are identified: only what the analyzer boxed and labelled is drawn.
"""

from __future__ import annotations

import base64
import binascii
import io
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

FONT_PATH = Path(__file__).resolve().parent / "assets" / "fonts" / "DejaVuSans.ttf"
OUTLINE_PX = 3
COLOR_PERSON = (0xE5, 0x39, 0x35)  # #E53935
COLOR_OBJECT = (0x1D, 0xB9, 0x54)  # #1DB954 (object / region)
TEXT_COLOR = (255, 255, 255)
FONT_FRACTION = 0.022  # of image height
FONT_MIN_PX = 12
LABEL_MAX_CHARS = 32
MAX_FRAMES = 3
JPEG_QUALITY = 88


@lru_cache(maxsize=16)
def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_PATH), size)


def _truncate(label: str, limit: int = LABEL_MAX_CHARS) -> str:
    label = " ".join(label.split())
    return label if len(label) <= limit else label[:limit - 1].rstrip() + "…"


def _decode(b64: str) -> Image.Image:
    try:
        raw = base64.b64decode(b64, validate=False)
    except (binascii.Error, ValueError) as e:
        raise ValueError(f"bad base64: {e}") from e
    img = Image.open(io.BytesIO(raw))
    img.load()
    return img.convert("RGB")


def _layout(boxes: list[dict[str, Any]], W: int, H: int, draw: ImageDraw.ImageDraw,
            font: ImageFont.FreeTypeFont) -> list[dict[str, Any]]:
    """Pixel geometry per box: color, outline rect, and (if labelled) chip rect + text."""
    pad = max(2, font.size // 4)
    out = []
    for b in boxes:
        color = COLOR_PERSON if b.get("kind") == "person" else COLOR_OBJECT
        x0, y0 = b["x"] * W, b["y"] * H
        x1, y1 = (b["x"] + b["w"]) * W, (b["y"] + b["h"]) * H
        x0, y0 = max(0.0, min(W - 1.0, x0)), max(0.0, min(H - 1.0, y0))
        x1, y1 = max(x0, min(W - 1.0, x1)), max(y0, min(H - 1.0, y1))
        item: dict[str, Any] = {"color": color, "rect": (x0, y0, x1, y1), "chip": None}
        label = b.get("label")
        if label:  # no label -> no chip
            text = _truncate(str(label))
            tl, tt, tr, tb = draw.textbbox((0, 0), text, font=font)
            cw, ch = (tr - tl) + 2 * pad, (tb - tt) + 2 * pad
            cx = max(0.0, min(W - cw, x0))
            cy = y0 - ch if y0 - ch >= 0 else y0  # above the box, or inside its top edge
            cy = max(0.0, min(H - ch, cy))
            item["chip"] = (cx, cy, cx + cw, cy + ch)
            item["text"] = text
            item["text_xy"] = (cx + pad - tl, cy + pad - tt)
        out.append(item)
    return out


def _font_for(H: int) -> ImageFont.FreeTypeFont:
    return _font(max(FONT_MIN_PX, round(FONT_FRACTION * H)))


def chip_rects(boxes: list[dict[str, Any]], size: tuple[int, int]) -> list[tuple[float, float, float, float] | None]:
    """Label-chip rectangles (pixels) `draw_boxes` would use on an image of `size` (W, H)."""
    W, H = size
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    return [it["chip"] for it in _layout(boxes, W, H, draw, _font_for(H))]


def draw_boxes(image_b64: str, boxes: list[dict[str, Any]]) -> str:
    """JPEG base64 with `boxes` drawn on it (same dimensions). Raises on undecodable input.

    Two passes: all outlines first, then all label chips on top, so a later box's outline
    never cuts through an earlier box's label."""
    img = _decode(image_b64)
    W, H = img.size
    draw = ImageDraw.Draw(img)
    font = _font_for(H)
    items = _layout(boxes, W, H, draw, font)
    for it in items:
        draw.rectangle(list(it["rect"]), outline=it["color"], width=OUTLINE_PX)
    for it in items:
        if it["chip"] is None:
            continue
        draw.rectangle(list(it["chip"]), fill=it["color"])
        draw.text(it["text_xy"], it["text"], font=font, fill=TEXT_COLOR)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _plain(fr: dict[str, Any]) -> dict[str, Any]:
    return {**fr, "annotated": False, "boxes": list(fr.get("boxes") or [])}


def _try_draw(fr: dict[str, Any], job_id: str) -> dict[str, Any] | None:
    """Annotated copy of `fr`, or None when drawing fails (logged)."""
    try:
        drawn = draw_boxes(fr["image_base64"], fr["boxes"])
    except Exception as e:  # noqa: BLE001 - evidence polish must never fail the job
        log.warning("Job %s: drawing evidence boxes failed at %ss, keeping the raw frame: %s",
                    job_id, fr.get("timestamp_sec"), e)
        return None
    return {**fr, "image_base64": drawn, "annotated": True}


def annotate_frames(frames: list[dict[str, Any]], job_id: str = "?", draw: bool = True) -> list[dict[str, Any]]:
    """Public evidence frames for one violation.

    The first frame with boxes yields `[annotated, raw same frame, ...the other frames]`,
    capped at MAX_FRAMES; each of the other frames that has boxes is drawn too
    (`annotated: true`), frames without boxes pass through. Without boxes (or with
    `draw=False`) the frames pass through unchanged (plus `annotated: false`), also capped
    at MAX_FRAMES. A drawing failure is logged and only that frame is returned raw (if the
    first boxed frame fails, there is no raw duplicate and the original order is kept)."""
    plain = [_plain(fr) for fr in frames]
    idx = next((i for i, fr in enumerate(plain) if fr["boxes"]), None)
    if idx is None or not draw:
        return plain[:MAX_FRAMES]
    first = plain[idx]
    annotated = _try_draw(first, job_id)
    if annotated is None:
        head, rest = [], plain
    else:
        head, rest = [annotated, first], [fr for i, fr in enumerate(plain) if i != idx]
    out = list(head)
    for fr in rest:
        if len(out) >= MAX_FRAMES:
            break
        if fr is not first and fr["boxes"]:
            fr = _try_draw(fr, job_id) or fr
        out.append(fr)
    return out
