"""Debug the perception stage on one video: dump the timeline + a contact sheet to cache/debug/.

    uv run python scripts/perceive.py X-15 [--variant hflip] [--every 1.0] [--no-cache] [--device cpu]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.variants import VARIANTS  # noqa: E402
from forte_detector import config  # noqa: E402
from forte_detector.labels import load_labels  # noqa: E402
from forte_detector.perception.pipeline import effective_transform, perceive  # noqa: E402
from forte_detector.video_io import iter_frames  # noqa: E402

COLORS = {"cashier": (0, 200, 0), "visitor": (0, 0, 255)}


def _px(box, w, h):
    return int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)


def draw(frame, tl, i, label_secs):
    h, w = frame.shape[:2]
    img = frame.copy()
    s = tl["series"]
    z = tl["zones"]
    if z["seat"]:
        x1, y1, x2, y2 = _px(z["seat"], w, h)
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 255), 1)
    if z["door"]:
        x1, y1, x2, y2 = _px(z["door"], w, h)
        cv2.rectangle(img, (x1, y1), (x2, y2), (255, 128, 0), 1)
    for d in s["detections"][i]:  # raw detections, thin grey
        x1, y1, x2, y2 = _px(d, w, h)
        cv2.rectangle(img, (x1, y1), (x2, y2), (160, 160, 160), 1)
        cv2.putText(img, f"{d[4]:.2f}", (x1 + 2, y2 - 4), 0, 0.4, (160, 160, 160), 1)
    for tid, role, box, *rest in s["persons"][i]:
        outside = bool(rest and rest[0])
        col = (0, 140, 255) if outside else COLORS.get(role, (255, 0, 255))  # orange = behind the glass
        x1, y1, x2, y2 = _px(box, w, h)
        cv2.rectangle(img, (x1, y1), (x2, y2), col, 1 if outside else 3)
        cv2.putText(img, f"#{tid}{role[0]}{'-out' if outside else ''}", (x1 + 3, y1 + 22), 0, 0.8, col, 2)
    for o in s["objects"][i]:
        x1, y1, x2, y2 = _px(o[1:5], w, h)
        cv2.rectangle(img, (x1, y1), (x2, y2), (255, 0, 255), 1)
        cv2.putText(img, f"{o[0]} {o[5]:.2f}", (x1, max(10, y1 - 2)), 0, 0.4, (255, 0, 255), 1)
    t = s["t"][i]
    near_label = any(abs(t - ls) < 0.13 for ls in label_secs)
    cps = [c["type"] for c in tl["checkpoints"] if abs(c["t"] - t) < 1e-3]
    lines = [f"t={t:.2f} n={s['n_persons'][i]}({s['n_persons_raw'][i]}) seat={int(s['cashier_at_seat'][i])}"
             f" pres={int(s['cashier_present'][i])}"] + cps
    y = 20
    cv2.rectangle(img, (0, 0), (w, 18 * len(lines) + 8), (0, 0, 0), -1)
    for ln in lines:
        cv2.putText(img, ln, (4, y), 0, 0.55, (0, 0, 255) if near_label else (255, 255, 255), 1)
        y += 18
    if near_label:
        cv2.rectangle(img, (0, 0), (w - 1, h - 1), (0, 0, 255), 6)
    return img


def contact_sheet(video, transform, tl, every, label_secs, thumb_h=424, cols=8):
    s = tl["series"]
    want = set()
    step = tl["step"]
    for i, t in enumerate(s["t"]):
        if abs(t / every - round(t / every)) < step / every / 2 + 1e-6:
            want.add(i)
    idx_of = {t: i for i, t in enumerate(s["t"])}
    for c in tl["checkpoints"]:
        if c["t"] in idx_of:
            want.add(idx_of[c["t"]])
    for ls in label_secs:
        want.add(min(range(len(s["t"])), key=lambda i: abs(s["t"][i] - ls)))
    thumbs = []
    transform = effective_transform(tl, transform)  # boxes live in the oriented frame
    for i, (t, frame) in enumerate(iter_frames(video, 1.0 / step, max_side=960, transform=transform)):
        if i >= len(s["t"]):
            break
        if i in want:
            img = draw(frame, tl, i, label_secs)
            sc = thumb_h / img.shape[0]
            thumbs.append(cv2.resize(img, (int(img.shape[1] * sc), thumb_h)))
    if not thumbs:
        return None
    tw = max(t.shape[1] for t in thumbs)
    thumbs = [cv2.copyMakeBorder(t, 0, 0, 0, tw - t.shape[1], cv2.BORDER_CONSTANT) for t in thumbs]
    rows = []
    for r in range(0, len(thumbs), cols):
        row = thumbs[r : r + cols]
        row += [np.zeros_like(thumbs[0])] * (cols - len(row))
        rows.append(np.hstack(row))
    return np.vstack(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", help="id like X-15")
    ap.add_argument("--variant", default="orig", choices=list(VARIANTS))
    ap.add_argument("--every", type=float, default=1.0, help="sheet sampling period, s")
    ap.add_argument("--device", default=None)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--cols", type=int, default=8)
    args = ap.parse_args()

    labels = load_labels()
    lab = labels[args.video]
    video = str(config.videos_dir() / lab["file"])
    tf = VARIANTS[args.variant]
    tl = perceive(video, transform=tf, variant=args.variant, device=args.device, use_cache=not args.no_cache)
    out = config.cache_dir() / "debug"
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{args.video}__{args.variant}"
    (out / f"{stem}.json").write_text(json.dumps(tl, ensure_ascii=False, indent=1))
    sheet = contact_sheet(video, tf, tl, args.every, lab["label_sec"], cols=args.cols)
    if sheet is not None:
        cv2.imwrite(str(out / f"{stem}.jpg"), sheet, [cv2.IMWRITE_JPEG_QUALITY, 80])
    m = tl["meta"]
    print(f"{args.video} {args.variant}: label={lab['label_sec']} viol={lab['violation']} "
          f"device={m['device']} total={m['stage_times']['total']}s vram={m['vram_peak_mb']}MB "
          f"raw_tracks={m['n_raw_tracks']} merged={m['n_merged_tracks']} cached={m['cached']} "
          f"orientation={tl.get('orientation')}")
    print("roles:", tl["roles"], "zones:", tl["zones"])
    for c in tl["checkpoints"]:
        extra = f" {c['from']}->{c['to']}" if "from" in c else ""
        print(f"  {c['t']:7.2f}  {c['type']}{extra}  {c['track_ids']}")
    print(f"sheet: {out / (stem + '.jpg')}")


if __name__ == "__main__":
    main()
