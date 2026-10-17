"""Pydantic models shared by analyzers, the rules KB, the job store and the API."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Verdict = Literal["violation", "no_violation"]
JobStatus = Literal["processing", "done", "failed"]


def now_iso() -> str:
    """Current UTC time as ISO-8601 string (second precision)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class VideoMeta(BaseModel):
    duration_sec: float
    fps: float
    width: int  # as displayed (after rotation)
    height: int  # as displayed (after rotation)
    rotation: int = 0  # degrees, from display matrix / rotate tag
    codec: str | None = None
    frame_count: int | None = None


BOX_KINDS = ("person", "object", "region")
MAX_BOX_LABEL_CHARS = 200
MAX_BOXES_PER_FRAME = 50


def _num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def normalize_box(raw: Any) -> dict[str, Any] | None:
    """One analyzer box -> {x, y, w, h, label, kind} or None if unusable.

    Coordinates are normalized 0..1 relative to the evidence JPEG. Non-finite values or
    w/h <= 0 drop the box; x/y are clamped into [0, 1] and w/h so the box stays inside the
    image (a box that ends up with no area is dropped). `kind` is person/object/region,
    anything else (or missing) -> "object". A blank label -> None (no chip is drawn)."""
    if isinstance(raw, BaseModel):
        raw = raw.model_dump()
    if not isinstance(raw, dict):
        return None
    x, y, w, h = (_num(raw.get(k)) for k in ("x", "y", "w", "h"))
    if x is None or y is None or w is None or h is None or w <= 0 or h <= 0:
        return None
    x0, y0 = min(1.0, max(0.0, x)), min(1.0, max(0.0, y))
    x1, y1 = min(1.0, max(0.0, x + w)), min(1.0, max(0.0, y + h))
    if x1 - x0 < 1e-6 or y1 - y0 < 1e-6:
        return None
    label = raw.get("label")
    label = " ".join(str(label).split())[:MAX_BOX_LABEL_CHARS] if label is not None else ""
    kind = raw.get("kind")
    kind = kind.strip().lower() if isinstance(kind, str) else ""
    return {"x": round(x0, 6), "y": round(y0, 6), "w": round(x1 - x0, 6), "h": round(y1 - y0, 6), "label": label or None,
            "kind": kind if kind in BOX_KINDS else "object"}


class Box(BaseModel):
    """Normalized (0..1) box on an evidence JPEG; see `normalize_box`."""

    x: float
    y: float
    w: float
    h: float
    label: str | None = None
    kind: Literal["person", "object", "region"] = "object"


class EvidenceFrame(BaseModel):
    timestamp_sec: float
    image_base64: str  # JPEG base64, no "data:" prefix
    # Optional analyzer boxes (normalized to this JPEG). Bad entries are dropped, never the frame.
    boxes: list[Box] = Field(default_factory=list)

    @field_validator("boxes", mode="before")
    @classmethod
    def _boxes(cls, v: Any) -> list[dict[str, Any]]:
        if not isinstance(v, (list, tuple)):
            return []
        out = [b for b in (normalize_box(x) for x in v) if b is not None]
        return out[:MAX_BOXES_PER_FRAME]


def _to_conf(v: Any) -> float | None:
    """Lenient 0..1 score: numbers are clamped, anything else (None, bool, text, NaN) -> None.
    An odd value from an analyzer must never get its violation dropped."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return min(1.0, max(0.0, f))


LEGAL_STRENGTHS = ("high", "medium", "low")


def _to_strength(v: Any) -> str | None:
    """Citation strength label: "high"/"medium"/"low" (case-insensitive), anything else -> None."""
    if not isinstance(v, str):
        return None
    s = v.strip().lower()
    return s if s in LEGAL_STRENGTHS else None


def _to_str_list(v: Any) -> list[str]:
    """Lenient list of non-blank strings: a single string -> [it], items coerced with str(),
    None/blank items skipped, anything that is not a list/tuple/string -> []."""
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, (list, tuple)):
        return []
    return [s for s in (str(x).strip() for x in v if x is not None) if s]


class Violation(BaseModel):
    """What an analyzer emits. Unknown fields (e.g. analyzer-supplied citations) are dropped.

    `type` is the rule id (e.g. "T1"); the service maps it to the human title and attaches
    citations from the rules file only. `title_ru` is used only when the id is unknown to
    the rules file. `obs` is a short observation inserted into the rule's description
    template when `description` is empty.
    """

    model_config = ConfigDict(extra="ignore")

    type: str
    timecode_sec: float
    start_sec: float | None = None  # defaults to timecode_sec in the public JSON
    end_sec: float | None = None
    confidence: float | None = None  # clamped to [0, 1]; non-finite / non-numeric -> None
    fact_conf: float | None = None  # confidence that the event happened (0..1)
    legal_conf: float | None = None  # confidence that the cited norm applies (0..1)
    # Strength label of the cited norm ("high"/"medium"/"low"). legal_conf from the detector is an
    # ordinal encoding of it (0.9/0.7/0.4), not a probability.
    legal_strength: str | None = None
    title_ru: str | None = None
    obs: str | None = None
    description: str = ""
    evidence_frames: list[EvidenceFrame] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _type_id_alias(cls, data: Any) -> Any:
        # Accept `type_id` from analyzers that already speak the public schema.
        if isinstance(data, dict) and not data.get("type") and isinstance(data.get("type_id"), str):
            data = {**data, "type": data["type_id"]}
        return data

    @field_validator("confidence", "fact_conf", "legal_conf", mode="before")
    @classmethod
    def _clamp_conf(cls, v: Any) -> float | None:
        return _to_conf(v)

    @field_validator("legal_strength", mode="before")
    @classmethod
    def _strength(cls, v: Any) -> str | None:
        return _to_strength(v)

    @model_validator(mode="after")
    def _default_confidence(self) -> "Violation":
        if self.confidence is None and self.fact_conf is not None:
            self.confidence = self.fact_conf
        return self


class AnalysisResult(BaseModel):
    verdict: Verdict | None = None  # advisory: the service recomputes it
    violations: list[Violation] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)  # RU notes for the operator, e.g. degraded run

    @field_validator("warnings", mode="before")
    @classmethod
    def _warnings(cls, v: Any) -> list[str]:
        return _to_str_list(v)


class JobRecord(BaseModel):
    """Persisted job state. `violations` holds already-enriched dicts (see RulesKB.enrich)."""

    job_id: str
    status: JobStatus = "processing"
    verdict: Verdict | None = None
    violations: list[dict[str, Any]] = Field(default_factory=list)
    progress: float = 0.0
    stage: str = ""
    error: str | None = None
    created_at: str = Field(default_factory=now_iso)
    finished_at: str | None = None
    video_meta: dict[str, Any] | None = None
    filename: str | None = None
    partial: bool = False
    meta: dict[str, Any] = Field(default_factory=dict)  # service notes, e.g. budget exhausted
    warnings: list[str] = Field(default_factory=list)  # RU notes shown to the operator (any status)
    upload_path: str | None = None  # internal only, never exposed

    def to_public(self) -> dict[str, Any]:
        """Contract JSON for GET /api/v1/jobs/{id} (and the UI API)."""
        data = self.model_dump(exclude={"upload_path"})
        if self.status != "done":
            data["verdict"] = None
            data["violations"] = []
        else:
            data["verdict"] = "violation" if self.violations else "no_violation"
        return data
