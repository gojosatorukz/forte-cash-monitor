from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from backend.app.schemas import AnalysisResult

ProgressStage = Literal["decoding", "scanning", "verifying", "evidence"]


def _ignore_progress(stage: ProgressStage) -> None:
    pass


class AnalysisFailure(Exception):
    """Expected failure; only this stable code is exposed, never exception text."""

    def __init__(self, code: Literal["invalid_video", "unsupported_codec", "deadline_exceeded"]):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class AnalysisRequest:
    video_path: Path
    # The backend must stop its own work by this deadline (time.monotonic()).
    deadline_monotonic: float
    report_progress: Callable[[ProgressStage], None] = _ignore_progress


class Analyzer(Protocol):
    name: str

    def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        """Read local media; return real, validated observations and evidence.

        Honor the deadline cooperatively. Never call external AI APIs, infer a
        label from a filename, or turn a decode/model error into no_violation.
        Validate the media, duration, timestamps, JPEG content, and rule mapping.
        """
        ...
