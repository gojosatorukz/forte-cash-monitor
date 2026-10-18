"""Per-cue accuracy + latency of the state probes on hand-picked cases (forte_detector/data/probe_cases.json).

Usage:
    uv run python scripts/probe_eval.py [--cues door_open,cash_on_desk] [--videos X-26,X-32]
                                        [--no-perception] [--out runs/probe_eval_<ts>]

Per video: perception timeline (cached, Task 2) -> open-vocab calibration -> one run_probes call per
case (so the per-call latency is measured). Cases with a "transform" (camera_blocked synthetic
positives) read frames through that transform. Writes results.jsonl, calibration.json, report.md.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forte_detector import config  # noqa: E402
from forte_detector.labels import load_labels  # noqa: E402
from forte_detector.probes import questions, runner  # noqa: E402
from forte_detector.probes.base import ProbeRequest  # noqa: E402

CASES = config.PACKAGE_DIR / "data" / "probe_cases.json"


def _black(img):
    return np.zeros_like(img)


def _cover85(img):
    """A hand / cloth over the lens: 85% of the frame a blurry, slightly shaded skin-like blob."""
    out = img.copy()
    h, w = img.shape[:2]
    y1 = int(0.85 * h)
    yy = np.linspace(0.8, 1.0, y1)[:, None, None]
    out[:y1] = (np.array([150, 170, 205], np.float32) * yy).astype(np.uint8)
    return cv2.GaussianBlur(out, (0, 0), 3)


class _AfterFirst:
    """Camera covered AFTER the start: the first frame read (the runner's t=0 reference for
    camera_blocked) stays clean, every later read gets the cover. One instance per case."""

    def __init__(self, fn):
        self.fn, self.n = fn, 0

    def __call__(self, img):
        self.n += 1
        return img if self.n == 1 else self.fn(img)


TRANSFORMS = {"black": lambda: _AfterFirst(_black), "cover85": lambda: _AfterFirst(_cover85)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cues", default="")
    ap.add_argument("--videos", default="")
    ap.add_argument("--no-perception", action="store_true")
    ap.add_argument("--any-cache", action="store_true",
                    help="use the newest cached timeline of any config hash (CPU-only runs without the GPU lock)")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    cases = json.loads(CASES.read_text())
    if args.cues:
        cases = [c for c in cases if c["cue"] in args.cues.split(",")]
    if args.videos:
        cases = [c for c in cases if c["vid"] in args.videos.split(",")]
    labels = load_labels()
    out = Path(args.out or f"runs/probe_eval_{time.strftime('%Y%m%d_%H%M%S')}")
    out.mkdir(parents=True, exist_ok=True)

    client = runner.get_client()
    print(f"VLM {client.url}: available={client.available()} model={client.model}", flush=True)

    by_vid = defaultdict(list)
    for c in cases:
        by_vid[c["vid"]].append(c)
    rows, cals = [], {}
    fh = open(out / "results.jsonl", "w")
    for vid, vcases in by_vid.items():
        path = str(config.videos_dir() / labels[vid]["file"])
        t0 = time.perf_counter()
        cached = sorted((config.CACHE_DIR / "perception").glob(f"{vid}__orig__*.json"),
                        key=lambda p: p.stat().st_mtime) if args.any_cache else []
        if args.no_perception:
            tl = {"series": {}, "zones": {}, "duration": labels[vid]["duration_sec"]}
        elif cached:  # newest cached timeline, any config hash (no RT-DETR run, no GPU)
            tl = json.loads(cached[-1].read_text())
        else:
            from forte_detector.perception.pipeline import perceive

            tl = perceive(path)
        t_perc = time.perf_counter() - t0
        t0 = time.perf_counter()
        needs_cal = any(c["cue"] == "door_open" or (c["cue"] in questions.SPECS and questions.SPECS[c["cue"]].roi)
                        for c in vcases)
        cal = runner.calibrate(path, tl) if needs_cal else {}
        t_cal = time.perf_counter() - t0
        if needs_cal:
            cals[vid] = cal
        print(f"{vid}: perception {t_perc:.1f}s calibration {t_cal:.1f}s "
              f"{ {k: cal.get(k) for k in ('safe', 'door', 'laptop')} if cal else ''}", flush=True)
        for c in vcases:
            req = ProbeRequest(c["cue"], c["t"], roi=c.get("roi"), extra_ts=c.get("extra_ts", []))
            tr = TRANSFORMS[c["transform"]]() if c.get("transform") else None
            tc = time.perf_counter()
            (res,) = runner.run_probes(path, [req], timeline=tl, transform=tr, calibration=cal or None)
            dt = time.perf_counter() - tc
            ok = None if res.value is None else (res.value == c["expected"])
            row = {**c, "value": res.value, "conf": res.conf, "source": res.source, "ok": ok,
                   "latency_s": round(dt, 3), "vlm_wall_s": res.raw.get("wall_s"),
                   "count": res.raw.get("count"), "raw": res.raw}
            rows.append(row)
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            fh.flush()
            mark = {True: "OK ", False: "BAD", None: "N/A"}[ok]
            extra = f" n={res.raw.get('count')}" if c["cue"] == "person_count" else ""
            extra += f" chg={res.raw['frames'][0].get('change_frac')}" if c["cue"] == "door_open" and res.raw.get("frames") else ""
            print(f"  {mark} {c['cue']:26s} t={c['t']:5.1f} exp={c['expected']!s:5} got={res.value!s:5} "
                  f"conf={res.conf:.2f} {res.source:12s} {dt:5.2f}s{extra}  | {c['note']}", flush=True)
    fh.close()
    (out / "calibration.json").write_text(json.dumps(cals, indent=1, default=str))

    # ---- report
    lines = ["# Probe eval", "", f"VLM: {client.url} model={client.model}", "",
             "| cue | n | pos/neg | correct | wrong | undecided | acc (decided) | acc (all) | median s | max s |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    per = defaultdict(list)
    for r in rows:
        per[r["cue"]].append(r)
    tot_ok = tot_n = 0
    for cue, rs in per.items():
        n = len(rs)
        pos = sum(r["expected"] for r in rs)
        ok = sum(r["ok"] is True for r in rs)
        bad = sum(r["ok"] is False for r in rs)
        und = sum(r["ok"] is None for r in rs)
        lat = [r["latency_s"] for r in rs]
        tot_ok += ok
        tot_n += n
        accd = f"{ok / (ok + bad):.2f}" if ok + bad else "-"
        lines.append(f"| {cue} | {n} | {pos}/{n - pos} | {ok} | {bad} | {und} | {accd} | {ok / n:.2f} | "
                     f"{statistics.median(lat):.2f} | {max(lat):.2f} |")
    lines.append(f"| **all** | {tot_n} | | {tot_ok} | | | | {tot_ok / max(tot_n, 1):.2f} | | |")
    pc = [r for r in rows if r["cue"] == "person_count" and "count" in r and r["raw"].get("count") is not None]
    if pc:
        exact = sum(r["raw"]["count"] == r["count"] for r in pc)
        empty = [r for r in pc if r["count"] == 0]
        empty_ok = sum(r["raw"]["count"] == 0 for r in empty)
        lines += ["", f"person_count exact count: {exact}/{len(pc)}; room-empty (count==0) confirmed: "
                  f"{empty_ok}/{len(empty)} (incl. people visible behind the glass)"]
    vl = [r["vlm_wall_s"] for r in rows if r.get("vlm_wall_s")]
    if vl:
        lines += ["", f"VLM wall per call: median {statistics.median(vl):.2f}s, mean {statistics.mean(vl):.2f}s, "
                  f"max {max(vl):.2f}s over {len(vl)} calls"]
    lines += ["", "## Wrong / undecided", ""]
    for r in rows:
        if r["ok"] is not True:
            lines.append(f"- {r['vid']} {r['cue']} t={r['t']} exp={r['expected']} got={r['value']} "
                         f"conf={r['conf']} ({r['source']}) - {r['note']} {r['raw'].get('error', '')}")
    (out / "report.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
