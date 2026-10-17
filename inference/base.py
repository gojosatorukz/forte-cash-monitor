from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from backend.app.schemas import AnalysisResult


@dataclass(frozen=True)
class AnalysisRequest:
    video_path: Path
    # The backend must stop its own work by this deadline (time.monotonic()).
    deadline_monotonic: float


class Analyzer(Protocol):
    name: str

    def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        """Read local media; return real, validated observations and evidence.

        Honor the deadline cooperatively. Never call external AI APIs, infer a
        label from a filename, or turn a decode/model error into no_violation.
        Validate the media, duration, timestamps, JPEG content, and rule mapping.
        """
        ...
