from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Protocol, Callable, Any

@dataclass
class ProbeRequest:
    cue: str                      # one of CUES
    t: float                      # video seconds of the keyframe
    roi: tuple[float, float, float, float] | None = None  # normalized x0,y0,x1,y1 crop, or None = full frame
    extra_ts: list[float] = field(default_factory=list)   # optional extra keyframes (max 2 more)
    context: dict = field(default_factory=dict)            # free hints (e.g. {"zone": "desk"})

@dataclass
class ProbeResult:
    cue: str
    t: float
    value: bool | None            # None = probe unavailable / undecidable
    conf: float                   # 0..1 confidence in `value`
    evidence_ts: list[float] = field(default_factory=list)
    source: str = ""              # "vlm" | "cv:<name>" | "coco"
    raw: dict = field(default_factory=dict)
    def to_dict(self) -> dict: return asdict(self)

CUES = ("cash_on_desk", "bag_on_desk", "stamps_or_journal_on_desk", "safe_open", "key_in_safe",
        "screen_on_unlocked", "door_open", "cup_or_food", "handbag_or_coat_on_desk",
        "phone_visible", "phone_held", "phone_on_desk", "camera_blocked", "counting_machine_used", "id_document_shown",
        "person_count", "cash_in_hand_to_body", "banknotes_in_hand")

class ProbeRunner(Protocol):
    def __call__(self, video_path: str, requests: list[ProbeRequest], *,
                 timeline: dict, transform: Callable[[Any], Any] | None = None) -> list[ProbeResult]: ...
