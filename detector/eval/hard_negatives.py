"""Hard negatives: clean clips that contain the cue a naive detector fires on (teammate list,
ROOT/docs/02_teammate_comparison.md §4.3, mapped to our clean videos via ROOT/docs/videos/batch_*.md).

Each cue fires only in sequence with context; an FP on one of these clips means a rule lost its context.
"""
from __future__ import annotations

# category -> {video id: what happens there}
HARD_NEGATIVES: dict[str, dict[str, str]] = {
    "bright screen (lock screen / wallpaper)": {
        "X-23": "PC locked (Windows lock screen) before leaving",
        "X-29": "Win+L -> lock-screen wallpaper visible after exit",
    },
    "second person in the room": {
        "X-16": "collector with bag, stays and sits (ID checked)",
        "X-19": "two clients, strictly sequential",
        "X-35": "client seated during the whole cycle",
        "X-39": "two clients, desk cleared between them",
        "X-5": "next client enters after the desk is empty",
        "X-48": "duplicate of X-5",
    },
    "open safe during access": {
        "X-5": "safe open ~4 s while seated", "X-8": "safe open while alone", "X-14": "safe open ~6-8 s",
        "X-19": "safe open with client seated", "X-21": "safe open 2-3 s, cashier at it",
        "X-25": "safe open ~10 s while moving items in", "X-27": "cash into safe, locked",
        "X-35": "safe open ~6 s with client", "X-39": "safe opened twice with clients",
    },
    "green collection bag": {
        "X-14": "bags opened and emptied in view", "X-16": "collector hands over bag after ID check",
    },
    "mixed note colours / several piles": {
        "X-10": "two bundles, banding", "X-43": "separate brown pile + detector", "X-45": "separate pile + detector",
    },
    "cashier briefly hidden / hands at body": {
        "X-3": "back hides hands briefly; hand count near body", "X-8": "bends below desk edge ~2 s",
        "X-10": "hands at chest while banding", "X-14": "bends at safe base",
    },
    "cashier leaves the room (state secured)": {
        "X-12": "laptop closed, machine off, lights off, exit", "X-23": "PC locked, key taken, exit",
        "X-25": "items into safe, exit", "X-27": "cash into safe, exit", "X-29": "PC locked, exit",
    },
    "door open while passing through": {
        "X-31": "door closed right after entering",
    },
}


def hard_negative_ids() -> list[str]:
    seen: list[str] = []
    for vids in HARD_NEGATIVES.values():
        seen += [v for v in vids if v not in seen]
    return seen


def hard_negative_report(per_video: dict) -> dict:
    """FPs on hard-negative clips, per category. per_video = compute_metrics()['per_video']."""
    cats = {}
    for cat, vids in HARD_NEGATIVES.items():
        rows = []
        for vid, what in vids.items():
            v = per_video.get(vid)
            if v is None:
                continue
            rows.append({"video": vid, "what": what, "outcome": v["outcome"], "pred_types": v["pred_types"]})
        cats[cat] = {"n": len(rows), "fp": sum(r["outcome"] in ("FP", "ERR") for r in rows), "rows": rows}
    ids = [v for v in hard_negative_ids() if v in per_video]
    return {"n_videos": len(ids), "n_fp": sum(per_video[v]["outcome"] in ("FP", "ERR") for v in ids),
            "categories": cats}
