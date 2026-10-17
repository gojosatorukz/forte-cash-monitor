"""Small optional helpers for the ML adapter; no model or network calls."""

import base64
import math
import time
from io import BytesIO

from PIL import Image

from backend.app.schemas import AnalysisResult, EvidenceFrame
from inference.base import AnalysisFailure, AnalysisRequest


def check_deadline(request: AnalysisRequest) -> None:
    if time.monotonic() >= request.deadline_monotonic:
        raise AnalysisFailure("deadline_exceeded")


def jpeg_evidence(image: Image.Image, timestamp_sec: float) -> EvidenceFrame:
    """Encode a decoded source frame. Caller supplies its actual video PTS."""
    stream = BytesIO()
    image.convert("RGB").save(stream, format="JPEG", quality=88)
    return EvidenceFrame(
        timestamp_sec=timestamp_sec,
        image_base64=base64.b64encode(stream.getvalue()).decode("ascii"),
    )


def validate_timestamps(result: AnalysisResult, duration_sec: float) -> AnalysisResult:
    """Call with decoder-measured duration, never a filename or frontend value."""
    if not math.isfinite(duration_sec) or duration_sec <= 0:
        raise AnalysisFailure("invalid_video")
    for event in result.violations:
        timestamps = [event.timecode_sec, *(frame.timestamp_sec for frame in event.evidence_frames)]
        if any(timestamp > duration_sec for timestamp in timestamps):
            raise ValueError("Result timestamp is outside decoded video duration")
    return result
