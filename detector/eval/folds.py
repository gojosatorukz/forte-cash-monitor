"""Fixed 2-fold split by video pair for overfitting checks (reproducible, hand-balanced).

Identical clips share a fold (X-4/X-47, X-5/X-48). Each fold has 11 violations and 10 clean clips,
and the camera layouts are mixed across both folds. The only fitted parameter is the verdict threshold:
it is tuned on one fold (max recall, then min FP, then the highest such threshold) and reported on the other.
"""
from __future__ import annotations

FOLDS: dict[str, list[str]] = {
    "A": ["X-4", "X-47", "X-1", "X-11", "X-15", "X-18", "X-22", "X-26", "X-30", "X-34", "X-40",
          "X-5", "X-48", "X-3", "X-10", "X-14", "X-19", "X-23", "X-27", "X-31", "X-37"],
    "B": ["X-2", "X-13", "X-17", "X-20", "X-24", "X-28", "X-32", "X-33", "X-38", "X-44", "X-46",
          "X-8", "X-12", "X-16", "X-21", "X-25", "X-29", "X-35", "X-39", "X-43", "X-45"],
}


def video_score(result: dict) -> float:
    """Max event confidence incl. sub-threshold events (0 for errors / no events)."""
    if result.get("error"):
        return 0.0
    evs = list(result.get("events", [])) + list((result.get("timeline") or {}).get("low_conf_events", []))
    return max((float(e.get("confidence", 0.0)) for e in evs), default=0.0)


def _counts(labels: dict, scores: dict, ids: list[str], thr: float) -> tuple[int, int, int, int]:
    pos = [i for i in ids if labels[i]["violation"]]
    neg = [i for i in ids if not labels[i]["violation"]]
    tp = sum(scores[i] >= thr and scores[i] > 0 for i in pos)
    fp = sum(scores[i] >= thr and scores[i] > 0 for i in neg)
    return tp, len(pos), fp, len(neg)


def tune_threshold(labels: dict, scores: dict, ids: list[str]) -> float:
    cands = sorted({s for i, s in scores.items() if i in ids and s > 0} | {1.01})
    best = None
    for thr in cands:
        tp, _, fp, _ = _counts(labels, scores, ids, thr)
        key = (tp, -fp, thr)
        if best is None or key > best[0]:
            best = (key, thr)
    return best[1]


def cross_validate(labels: dict, results: dict) -> dict | None:
    """2-fold CV of the verdict threshold. None unless both folds are (partly) present."""
    scores = {vid: video_score(r) for vid, r in results.items()}
    folds = {k: [v for v in ids if v in scores] for k, ids in FOLDS.items()}
    if not all(folds.values()):
        return None
    out = {}
    for test, train in (("A", "B"), ("B", "A")):
        thr = tune_threshold(labels, scores, folds[train])
        tp, n_pos, fp, n_neg = _counts(labels, scores, folds[test], thr)
        out[test] = {"tuned_on": train, "threshold": round(thr, 3), "recall": f"{tp}/{n_pos}", "fp": f"{fp}/{n_neg}",
                     "tp": tp, "n_pos": n_pos, "fp_n": fp, "n_neg": n_neg}
    tp = sum(f["tp"] for f in out.values())
    fp = sum(f["fp_n"] for f in out.values())
    out["pooled"] = {"recall": f"{tp}/{sum(f['n_pos'] for f in out.values())}",
                     "fp": f"{fp}/{sum(f['n_neg'] for f in out.values())}"}
    return out
