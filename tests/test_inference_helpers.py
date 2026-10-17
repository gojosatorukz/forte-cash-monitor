import time
from pathlib import Path

import pytest
from PIL import Image

from backend.app.schemas import AnalysisResult
from inference.base import AnalysisFailure, AnalysisRequest
from inference.helpers import check_deadline, jpeg_evidence, validate_timestamps


def test_real_jpeg_and_timestamp_bounds():
    frame = jpeg_evidence(Image.new("RGB", (16, 16), "white"), 2.5)
    result = AnalysisResult.model_validate(
        {
            "verdict": "violation",
            "violations": [
                {
                    "type": "Synthetic helper check",
                    "timecode_sec": 2.5,
                    "description": "Test fixture, not a real finding.",
                    "evidence_frames": [frame],
                }
            ],
        }
    )
    assert validate_timestamps(result, 3) is result
    with pytest.raises(ValueError):
        validate_timestamps(result, 2)
    for invalid in [0, -1, float("nan"), float("inf")]:
        with pytest.raises(AnalysisFailure):
            validate_timestamps(result, invalid)


def test_expired_deadline_is_an_error():
    with pytest.raises(AnalysisFailure) as error:
        check_deadline(AnalysisRequest(Path("unused.mov"), time.monotonic() - 1))
    assert error.value.code == "deadline_exceeded"
