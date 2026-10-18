"""Ground-truth labels: parse open_labels.xlsx -> forte_detector/data/labels.json.

Rebuild:  uv run python -m forte_detector.labels
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from forte_detector import config

# Hypothesized violation types per positive video (01_findings.md; plan "Ground truth" incl. the
# binding "Final hyp_types" revision + human review round 1: X-24 T1b, X-18 T2, X-2 T15 (primary)+T10,
# X-13 T8+T4, X-30/X-33 T5+T6). Order matters: the first type is the primary one.
HYP_TYPES: dict[str, list[str]] = {
    "X-1": ["T10"], "X-2": ["T15", "T10"],
    "X-4": ["T2"], "X-47": ["T2"], "X-38": ["T2"],
    "X-11": ["T4", "T3"], "X-13": ["T8"], "X-28": ["T4"],
    "X-15": ["T1"], "X-24": ["T1b"], "X-26": ["T1"],
    "X-17": ["T9"],
    "X-18": ["T2"],
    "X-20": ["T3"], "X-22": ["T3"],
    "X-30": ["T5", "T6"], "X-32": ["T5"], "X-33": ["T5", "T6"],
    "X-34": ["T7"],
    "X-40": ["T11"],
    "X-44": ["T8"],
    "X-46": ["T12"],
}

# Team labels (human review round 2, team decision 17.10): a missing client ID is a T15 procedure
# violation even where the organizer says clean. Scoring stays on the organizer `violation` labels;
# `team_types` is reported separately.
TEAM_TYPES: dict[str, list[str]] = {
    "X-19": ["T15"], "X-35": ["T15"], "X-37": ["T15"], "X-39": ["T15"],
    "X-2": ["T15", "T10"], "X-11": ["T4", "T3"], "X-13": ["T8"], "X-1": ["T10"],
}
TEAM_T15_CLEAN = ("X-19", "X-35", "X-37", "X-39")  # organizer-clean, team-T15

# Identical files: their label seconds are valid targets for both ids.
DUPLICATES: list[tuple[str, str]] = [("X-4", "X-47"), ("X-5", "X-48")]

_CYR_TO_LAT = str.maketrans({"Х": "X", "х": "X", "x": "X"})
_MIN_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*мин", re.IGNORECASE)
_SEC_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*сек", re.IGNORECASE)
_CLOCK_RE = re.compile(r"^(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)$")
_NUM_RE = re.compile(r"^\d+(?:[.,]\d+)?$")


def normalize_id(name: str) -> str:
    """'Х-15.mov' (Cyrillic) / 'x-15' -> 'X-15'."""
    stem = name.strip()
    if stem.lower().endswith(".mov"):
        stem = stem[:-4]
    return stem.translate(_CYR_TO_LAT)


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def parse_label_seconds(raw) -> list[float]:
    """Parse a 'таймстемп' cell: '22 секунда', '1минута 8 секунда', '0:57', 57 -> [seconds]."""
    if raw is None:
        return []
    if isinstance(raw, (int, float)):
        return [float(raw)]
    s = str(raw).strip()
    if not s:
        return []
    if _NUM_RE.match(s):
        return [_num(s)]
    m = _CLOCK_RE.match(s)
    if m:
        h, mnt, sec = m.groups()
        return [float(h or 0) * 3600 + float(mnt) * 60 + float(sec)]
    mins = _MIN_RE.search(s)
    secs = _SEC_RE.search(s)
    if not mins and not secs:
        raise ValueError(f"cannot parse label time: {raw!r}")
    return [(_num(mins.group(1)) * 60 if mins else 0.0) + (_num(secs.group(1)) if secs else 0.0)]


def merge_duplicate_seconds(labels: dict) -> dict:
    """Give both ids of each duplicate pair the union of their label seconds (in place)."""
    for a, b in DUPLICATES:
        if a in labels and b in labels:
            merged = sorted(set(labels[a]["label_sec"]) | set(labels[b]["label_sec"]))
            labels[a]["label_sec"] = list(merged)
            labels[b]["label_sec"] = list(merged)
    return labels


def video_duration(path: Path) -> float:
    import av

    with av.open(str(path)) as c:
        s = c.streams.video[0]
        if s.duration is not None:
            return round(float(s.duration * s.time_base), 3)
        return round(c.duration / 1_000_000, 3)


def build_labels(xlsx: Path, videos: Path) -> dict:
    import openpyxl

    wb = openpyxl.load_workbook(xlsx, read_only=True)
    ws = wb.worksheets[0]
    files = {normalize_id(p.name): p.name for p in videos.glob("*.mov")}
    labels: dict = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        name, ts, verdict = row[:3]
        if not name:
            continue
        vid = normalize_id(str(name))
        is_violation = str(verdict).strip().lower() == "нарушение"
        labels[vid] = {
            "file": files[vid],
            "violation": is_violation,
            "label_sec": parse_label_seconds(ts),
            "hyp_types": list(HYP_TYPES.get(vid, [])) if is_violation else [],
            "team_types": list(TEAM_TYPES.get(vid, HYP_TYPES.get(vid, []) if is_violation else [])),
            "duration_sec": video_duration(videos / files[vid]),
        }
    return merge_duplicate_seconds(labels)


def load_labels(path: Path = config.LABELS_JSON) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> None:
    labels = build_labels(config.labels_xlsx(), config.videos_dir())
    config.LABELS_JSON.parent.mkdir(parents=True, exist_ok=True)
    lines = [f" {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}" for k, v in labels.items()]
    config.LABELS_JSON.write_text("{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8")
    n_pos = sum(v["violation"] for v in labels.values())
    print(f"wrote {config.LABELS_JSON}: {len(labels)} videos, {n_pos} violations")


if __name__ == "__main__":
    main()
