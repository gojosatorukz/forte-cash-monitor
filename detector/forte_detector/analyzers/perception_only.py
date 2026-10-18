"""Interim analyzer (Task 2): perception timeline + a naive verdict. Replaced by analyzers/full.py.

Naive rules: T1 at the first room_empty_start that follows a cashier exit (or any room-empty period
after someone was present); T6 when >= 3 persons are in the room at once.
"""
from __future__ import annotations

import time
from pathlib import Path

from forte_detector.perception.pipeline import perceive
from forte_detector.schema import VERDICT_CLEAN, VERDICT_VIOLATION, AnalysisResult, Event, ProgressCb, Transform


T6_HOLD_S = 2.0


def _variant_name(transform: Transform | None) -> str | None:
    """Name of a known eval transform (so cached timelines can be reused), None if unknown."""
    if transform is None:
        return "orig"
    try:
        from eval.variants import VARIANTS
    except ImportError:
        return None
    return next((k for k, v in VARIANTS.items() if v is transform), None)


def naive_events(tl: dict) -> list[Event]:
    events: list[Event] = []
    cps = tl["checkpoints"]
    for c in cps:
        if c["type"] == "room_empty_start" and c["t"] > 0:
            prev = [p for p in cps if p["type"] == "cashier_exits_room" and p["t"] <= c["t"]]
            t = prev[-1]["t"] if prev else c["t"]
            events.append(Event(
                type="T1", timecode_sec=t, confidence=0.5,
                description="Кассир покинул помещение, касса осталась без присмотра (наивное правило по трекам).",
                evidence_ts=[t, c["t"]], source="perception_only:room_empty",
            ))
            break
    s, run, start = tl["series"], 0, None
    for t, n in zip(s["t"], s["n_persons"]):  # >= 3 persons sustained for T6_HOLD_S
        if n >= 3:
            start = t if run == 0 else start
            run += 1
            if run * tl["step"] >= T6_HOLD_S - 1e-6:
                events.append(Event(
                    type="T6", timecode_sec=start, confidence=0.4,
                    description=f"В кассовом помещении одновременно {n} человек (наивное правило по трекам).",
                    evidence_ts=[start], source="perception_only:n_persons",
                ))
                break
        else:
            run = 0
    return events


def analyze(
    video_path: str,
    progress_cb: ProgressCb | None = None,
    *,
    transform: Transform | None = None,
) -> AnalysisResult:
    t0 = time.perf_counter()
    tl = perceive(video_path, transform=transform, variant=_variant_name(transform), progress_cb=progress_cb)
    events = naive_events(tl)
    slim = {k: v for k, v in tl.items() if k not in ("series", "tracks")}
    slim["series_len"] = len(tl["series"]["t"])
    return AnalysisResult(
        video=Path(video_path).name,
        verdict=VERDICT_VIOLATION if events else VERDICT_CLEAN,
        events=events,
        timeline=slim,
        runtime_sec=round(time.perf_counter() - t0, 3),
        stage_times={"perception": tl["meta"]["stage_times"]["total"], "cached": float(tl["meta"]["cached"])},
    )
