"""Shared data model for all analyzers (see docs/plan_phase1.md, "Shared data model").

Type ids are strings loaded from `taxonomy/rules.json` (see forte_detector.rules.taxonomy); there is
no closed list of event types here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Callable, Protocol

VERDICT_VIOLATION = "violation"
VERDICT_CLEAN = "no_violation"

ProgressCb = Callable[[float, str], None]
Transform = Callable[[Any], Any]  # BGR ndarray -> BGR ndarray


@dataclass
class Event:
    """One detected (suspected) violation.

    `type` is the type id from rules.json ("T1", "T1b", ... "T99"); `to_dict()` also exposes it as
    `type_id`. `confidence` is the combined score used for the verdict; `fact_conf` = how sure we are
    the fact was observed, `legal_conf` = strength of the rule link ("high"/"medium"/"low", from the
    rules.json citations). `obs` = bare Russian observed-fact clause (lowercase start, no trailing
    period, no prefix); `description_ru` = rendered rules.json template (fallback text).
    """

    type: str
    timecode_sec: float
    confidence: float  # 0..1 combined
    description_ru: str = ""
    evidence_ts: list[float] = field(default_factory=list)  # 1-3 timestamps (s)
    source: str = ""  # rule / probe that produced it
    extra: dict = field(default_factory=dict)
    title_ru: str = ""
    start_sec: float | None = None
    end_sec: float | None = None
    fact_conf: float | None = None
    legal_conf: str = ""
    obs: str = ""

    def __init__(self, type: str, timecode_sec: float, confidence: float, description_ru: str = "",
                 evidence_ts: list[float] | None = None, source: str = "", extra: dict | None = None,
                 title_ru: str = "", start_sec: float | None = None, end_sec: float | None = None,
                 fact_conf: float | None = None, legal_conf: str = "", obs: str = "",
                 description: str | None = None, type_id: str | None = None):
        # `description` / `type_id` are accepted for backward compatibility (aliases).
        self.type = type_id or type
        self.timecode_sec = float(timecode_sec)
        self.confidence = float(confidence)
        self.description_ru = description_ru or (description or "")
        self.evidence_ts = list(evidence_ts or [])
        self.source = source
        self.extra = dict(extra or {})
        self.title_ru = title_ru
        self.start_sec = self.timecode_sec if start_sec is None else float(start_sec)
        self.end_sec = self.timecode_sec if end_sec is None else float(end_sec)
        self.fact_conf = self.confidence if fact_conf is None else float(fact_conf)
        self.legal_conf = legal_conf
        self.obs = obs

    @property
    def type_id(self) -> str:
        return self.type

    @property
    def description(self) -> str:
        return self.description_ru

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type_id"] = self.type
        d["description"] = self.description_ru  # backward-compatible alias
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        names = {f.name for f in fields(cls)} | {"description", "type_id"}
        kw = {k: v for k, v in d.items() if k in names}
        if "type_id" in kw and "type" in kw:
            kw.pop("type_id")
        if "description" in kw and kw.get("description_ru"):
            kw.pop("description")
        return cls(**kw)


@dataclass
class AnalysisResult:
    video: str
    verdict: str  # "violation" | "no_violation"
    events: list[Event] = field(default_factory=list)
    timeline: dict = field(default_factory=dict)  # perception summary, JSON-able
    calibration: dict = field(default_factory=dict)
    runtime_sec: float = 0.0
    stage_times: dict = field(default_factory=dict)
    probes: list[dict] = field(default_factory=list)  # probe requests + results (debug / audit)
    degraded: bool = False  # VLM probes were unavailable: verdict is CV-only (eval counts it as an error)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["events"] = [e.to_dict() for e in self.events]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "AnalysisResult":
        d = dict(d)
        d["events"] = [Event.from_dict(e) for e in d.get("events", [])]
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


class Analyzer(Protocol):
    def __call__(
        self,
        video_path: str,
        progress_cb: ProgressCb | None = None,
        *,
        transform: Transform | None = None,
    ) -> AnalysisResult: ...
