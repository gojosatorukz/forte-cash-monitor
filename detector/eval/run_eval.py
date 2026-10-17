"""Evaluation harness: run an analyzer over the labelled videos (x variants) and score it.

    uv run python -m eval.run_eval [--analyzer module:callable] [--videos all|X-1,X-4]
                                   [--variant orig|hflip|...|all|robust] [--out runs/<ts>/] [--dedupe]
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import statistics
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from forte_detector import config
from forte_detector.labels import load_labels, normalize_id

from eval.folds import cross_validate
from eval.hard_negatives import hard_negative_ids, hard_negative_report
from eval.variants import VARIANTS, resolve_variants

DEFAULT_ANALYZER = "forte_detector.analyzers.stub:analyze"
DUPLICATE_IDS = ("X-47", "X-48")  # identical to X-4 / X-5


# ----------------------------------------------------------------------------- metrics

def _ratio(num: float, den: float) -> float | None:
    return num / den if den else None


def compute_metrics(labels: dict, results: dict) -> dict:
    """Score one variant. `results` maps video id -> AnalysisResult.to_dict() (or {"error": ...}).

    Only ids present in `results` are scored. A result counts as a positive prediction iff
    verdict == "violation". An errored video is always WRONG (outcome "ERR"): it counts as FN on a
    violation clip and as FP on a clean clip, never as a correct negative. Timing: best |dt| = min over events x label
    seconds, computed only for positive videos predicted as violation; hit@k rates are over all
    evaluated positives (a miss counts as no hit). Type-hit = any predicted type in hyp_types.
    """
    conf = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    per_video: dict = {}
    for vid, r in results.items():
        lab = labels[vid]
        gt = bool(lab["violation"])
        # degraded = VLM probes unavailable (CV-only verdict): scored as an error, never TN/FN
        errored = r.get("error") is not None or bool(r.get("degraded"))
        pred = r.get("verdict") == "violation" and not errored
        events = r.get("events", []) if pred else []
        if errored:
            outcome = "ERR"
            conf["fn" if gt else "fp"] += 1
        else:
            outcome = ("TP" if pred else "FN") if gt else ("FP" if pred else "TN")
            conf[outcome.lower()] += 1
        pred_types = sorted({e["type"] for e in events})
        best_dt = None
        if gt and events and lab["label_sec"]:
            best_dt = min(abs(e["timecode_sec"] - s) for e in events for s in lab["label_sec"])
        per_video[vid] = {
            "gt": gt, "pred": pred, "outcome": outcome,
            "label_sec": lab["label_sec"], "best_dt": best_dt,
            "hit3": (best_dt is not None and best_dt <= 3.0) if gt else None,
            "hit5": (best_dt is not None and best_dt <= 5.0) if gt else None,
            "pred_types": pred_types, "hyp_types": lab["hyp_types"],
            "type_hit": bool(set(pred_types) & set(lab["hyp_types"])) if gt else None,
            "primary_hit": (bool(lab["hyp_types"]) and lab["hyp_types"][0] in pred_types) if gt else None,
            "n_events": len(events),
            "runtime_sec": r.get("wall_sec", r.get("runtime_sec", 0.0)),
            "stage_times": r.get("stage_times", {}),
            "duration_sec": lab["duration_sec"],
            "error": r.get("error") or ("degraded: VLM probes unavailable" if r.get("degraded") else None),
            "degraded": bool(r.get("degraded")),
        }

    pos = [v for v in per_video.values() if v["gt"]]
    tps = [v for v in pos if v["pred"]]
    dts = [v["best_dt"] for v in pos if v["best_dt"] is not None]
    n = len(per_video)
    runtimes = {vid: v["runtime_sec"] for vid, v in per_video.items()}
    total_rt = sum(runtimes.values())
    total_dur = sum(v["duration_sec"] for v in per_video.values())
    max_vid = max(runtimes, key=runtimes.get) if runtimes else None
    per_type: dict = {}
    for v in pos:
        for t in v["hyp_types"]:
            d = per_type.setdefault(t, {"n": 0, "detected": 0, "type_hit": 0, "videos": [], "hit3": 0, "hit5": 0})
            d["n"] += 1
            d["detected"] += int(v["pred"])
            d["type_hit"] += int(t in v["pred_types"])
            d["hit3"] += int(bool(v["hit3"]))
            d["hit5"] += int(bool(v["hit5"]))
            d["videos"].append(v_id_of(per_video, v))
    fired: dict = {}
    for v in per_video.values():
        for t in v["pred_types"]:
            fired.setdefault(t, {"tp_videos": 0, "fp_videos": 0})["tp_videos" if v["gt"] else "fp_videos"] += 1
    n_pos, n_neg = len(pos), n - len(pos)
    return {
        "headline": {
            "recall": f"{conf['tp']}/{n_pos}", "fp": f"{conf['fp']}/{n_neg}",
            "n_pos": n_pos, "n_neg": n_neg,
        },
        "per_type": per_type,
        "types_fired": fired,
        "hard_negatives": hard_negative_report(per_video),
        "n_videos": n,
        "n_errors": sum(v["error"] is not None for v in per_video.values()),
        "n_degraded": sum(v["degraded"] for v in per_video.values()),
        "confusion": conf,
        "accuracy": _ratio(conf["tp"] + conf["tn"], n),
        "recall": _ratio(conf["tp"], conf["tp"] + conf["fn"]),
        "precision": _ratio(conf["tp"], conf["tp"] + conf["fp"]),
        "fp_rate": _ratio(conf["fp"], conf["fp"] + conf["tn"]),
        "hit3_rate": _ratio(sum(v["hit3"] for v in pos), len(pos)),
        "hit5_rate": _ratio(sum(v["hit5"] for v in pos), len(pos)),
        "mean_abs_dt": statistics.fmean(dts) if dts else None,
        "median_abs_dt": statistics.median(dts) if dts else None,
        "type_hit_rate": _ratio(sum(v["type_hit"] for v in tps), len(tps)),
        "primary_hit_rate": _ratio(sum(v["primary_hit"] for v in tps), len(tps)),
        "runtime": {
            "total_sec": total_rt,
            "mean_sec": _ratio(total_rt, n),
            "max_sec": runtimes[max_vid] if max_vid else None,
            "max_video": max_vid,
            "sec_per_video_sec": _ratio(total_rt, total_dur),
        },
        "per_video": per_video,
    }


def v_id_of(per_video: dict, v: dict) -> str:
    return next(k for k, x in per_video.items() if x is v)


# ----------------------------------------------------------------------------- running

def load_analyzer(spec: str):
    module, _, attr = spec.partition(":")
    return getattr(importlib.import_module(module), attr or "analyze")


def run_one(analyze, path: Path, transform) -> dict:
    t0 = time.perf_counter()
    try:
        result = analyze(str(path), None, transform=transform).to_dict()
    except Exception as exc:  # keep the sweep going; scored as ERR (always wrong: FN/FP)
        result = {"video": path.name, "error": f"{type(exc).__name__}: {exc}",
                  "traceback": traceback.format_exc()}
    result["wall_sec"] = round(time.perf_counter() - t0, 3)
    return result


def select_videos(labels: dict, spec: str, dedupe: bool) -> list[str]:
    """`spec` = "all" | comma list of ids (Cyrillic "Х-4" / lowercase "x-4" accepted) | "hard"
    (the hard-negative clean clips)."""
    if spec == "all":
        ids = list(labels)
    elif spec == "hard":
        ids = [i for i in hard_negative_ids() if i in labels]
    else:
        ids = [normalize_id(s) for s in spec.split(",") if s.strip()]
    unknown = [i for i in ids if i not in labels]
    if unknown:
        raise SystemExit(f"unknown video id(s): {unknown}")
    return [i for i in ids if not (dedupe and i in DUPLICATE_IDS)]


# ----------------------------------------------------------------------------- report

def _f(x, nd=2, pct=False) -> str:
    if x is None:
        return "—"
    if isinstance(x, bool):
        return "✓" if x else "✗"
    return f"{100 * x:.0f}%" if pct else f"{x:.{nd}f}"


def render_report(summary: dict) -> str:
    cfg, variants = summary["config"], summary["variants"]
    out = ["# Eval report", "", "## Headline (recall on violations, FPs on clean clips)", ""]
    for name, m in variants.items():
        h, hn = m["headline"], m["hard_negatives"]
        out.append(f"- **{name}: recall {h['recall']}, FP {h['fp']}**, hard-negative FPs {hn['n_fp']}/{hn['n_videos']},"
                   f" type-hit {_f(m['type_hit_rate'], pct=True)} (primary {_f(m.get('primary_hit_rate'), pct=True)}),"
                   f" hit@5s {_f(m['hit5_rate'], pct=True)},"
                   f" mean abs Δt {_f(m['mean_abs_dt'])} s, errors {m['n_errors']}")
        cv = m.get("cv")
        if cv:
            out.append(f"  - 2-fold CV of the verdict threshold (eval/folds.py): fold A {cv['A']['recall']} recall,"
                       f" FP {cv['A']['fp']} (thr {cv['A']['threshold']} from B); fold B {cv['B']['recall']} recall,"
                       f" FP {cv['B']['fp']} (thr {cv['B']['threshold']} from A); pooled {cv['pooled']['recall']},"
                       f" FP {cv['pooled']['fp']}")
    out += [
        "",
        "Note: X-4/X-47 and X-5/X-48 are identical clips; any threshold tuning / CV must keep each pair"
        " in the same fold.", "",
        f"- analyzer: `{cfg['analyzer']}`",
        f"- videos: {cfg['n_videos']} ({cfg['videos']}), dedupe={cfg['dedupe']}",
        f"- variants: {', '.join(variants)}",
        f"- started: {cfg['started']}, total wall: {cfg['total_wall_sec']:.1f} s", "",
        "## Variants", "",
        "| variant | n | acc | recall | precision | FP rate | hit@3s | hit@5s | mean abs Δt | median abs Δt"
        " | type-hit (TP) | mean s/video | max s | s per video-s | errors |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, m in variants.items():
        rt = m["runtime"]
        out.append(
            f"| {name} | {m['n_videos']} | {_f(m['accuracy'], pct=True)} | {_f(m['recall'], pct=True)}"
            f" | {_f(m['precision'], pct=True)} | {_f(m['fp_rate'], pct=True)} | {_f(m['hit3_rate'], pct=True)}"
            f" | {_f(m['hit5_rate'], pct=True)} | {_f(m['mean_abs_dt'])} | {_f(m['median_abs_dt'])}"
            f" | {_f(m['type_hit_rate'], pct=True)} | {_f(rt['mean_sec'])} | {_f(rt['max_sec'])} ({rt['max_video']})"
            f" | {_f(rt['sec_per_video_sec'], 3)} | {m['n_errors']} |"
        )
    for name, m in variants.items():
        out += ["", f"## Per type `{name}` (positives by hyp type)", "",
                "| type | n | detected | type-hit | hit@3s | hit@5s | videos |", "|---|---|---|---|---|---|---|"]
        for t, d in sorted(m["per_type"].items()):
            out.append(f"| {t} | {d['n']} | {d['detected']} | {d['type_hit']} | {d.get('hit3', '—')} | {d.get('hit5', '—')}"
                       f" | {', '.join(d['videos'])} |")
        out += ["", "| fired type | on violation clips | on clean clips |", "|---|---|---|"]
        for t, d in sorted(m["types_fired"].items()):
            out.append(f"| {t} | {d['tp_videos']} | {d['fp_videos']} |")
        hn = m["hard_negatives"]
        out += ["", f"## Hard negatives `{name}`: {hn['n_fp']}/{hn['n_videos']} clips with FP", "",
                "| category | FP / n | FP clips (types) |", "|---|---|---|"]
        for cat, d in hn["categories"].items():
            fps = [f"{r['video']} ({', '.join(r['pred_types']) or r['outcome']})" for r in d["rows"]
                   if r["outcome"] in ("FP", "ERR")]
            out.append(f"| {cat} | {d['fp']}/{d['n']} | {'; '.join(fps) or '—'} |")
    for name, m in variants.items():
        c = m["confusion"]
        out += [
            "", f"## Variant `{name}`", "",
            "| | pred violation | pred clean |", "|---|---|---|",
            f"| **GT violation** | TP {c['tp']} | FN {c['fn']} |",
            f"| **GT clean** | FP {c['fp']} | TN {c['tn']} |", "",
            "| video | outcome | label s | best abs Δt | hit@3 | hit@5 | pred types | hyp types | type-hit"
            " | dur s | wall s | stages | error |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for vid, v in m["per_video"].items():
            stages = ", ".join(f"{k}={s:.2f}" for k, s in v["stage_times"].items())
            out.append(
                f"| {vid} | {v['outcome']} | {', '.join(f'{s:g}' for s in v['label_sec']) or '—'}"
                f" | {_f(v['best_dt'], 1)} | {_f(v['hit3'])} | {_f(v['hit5'])}"
                f" | {', '.join(v['pred_types']) or '—'} | {', '.join(v['hyp_types']) or '—'} | {_f(v['type_hit'])}"
                f" | {v['duration_sec']:.1f} | {v['runtime_sec']:.2f} | {stages or '—'} | {v['error'] or ''} |"
            )
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--analyzer", default=DEFAULT_ANALYZER, help="module:callable")
    ap.add_argument("--videos", default="all", help="all | comma-separated ids, e.g. X-1,X-4")
    ap.add_argument("--variant", default="orig", help=f"{'|'.join(VARIANTS)}|all|robust or comma list")
    ap.add_argument("--out", default=None, help="output dir (default runs/<timestamp>/)")
    ap.add_argument("--dedupe", action="store_true", help="skip duplicate clips X-47, X-48")
    args = ap.parse_args(argv)
    os.environ.setdefault("FORTE_EVAL_CACHE", "1")  # perception/probe caches are opt-in; the harness opts in

    labels = load_labels()
    vids = select_videos(labels, args.videos, args.dedupe)
    variants = resolve_variants(args.variant)
    analyze = load_analyzer(args.analyzer)
    started = datetime.now()
    out = Path(args.out or config.REPO_DIR / "runs" / started.strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    per_variant: dict = {}
    for variant in variants:
        results = {}
        for i, vid in enumerate(vids, 1):  # sequential: single GPU
            r = run_one(analyze, config.videos_dir() / labels[vid]["file"], VARIANTS[variant])
            results[vid] = r
            d = out / variant / vid
            d.mkdir(parents=True, exist_ok=True)
            (d / "result.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
            status = r.get("error") or f"{r['verdict']} {len(r['events'])} ev"
            print(f"[{variant} {i}/{len(vids)}] {vid}: {status} ({r['wall_sec']:.2f} s)", file=sys.stderr)
        per_variant[variant] = compute_metrics(labels, results)
        per_variant[variant]["cv"] = cross_validate(labels, results)

    summary = {
        "config": {
            "analyzer": args.analyzer, "videos": args.videos, "n_videos": len(vids), "dedupe": args.dedupe,
            "variants": variants, "started": started.isoformat(timespec="seconds"),
            "total_wall_sec": round(time.perf_counter() - t0, 2),
        },
        "variants": per_variant,
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "report.md").write_text(render_report(summary), encoding="utf-8")
    print(f"wrote {out}/report.md and summary.json (total {summary['config']['total_wall_sec']} s)", file=sys.stderr)
    return summary


if __name__ == "__main__":
    main()
