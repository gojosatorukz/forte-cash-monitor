import base64
import binascii
from io import BytesIO
from typing import Annotated, Literal, Self
from uuid import UUID

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class EvidenceFrame(StrictModel):
    timestamp_sec: float = Field(ge=0)
    image_base64: str

    @field_validator("image_base64")
    @classmethod
    def require_jpeg_envelope(cls, value: str) -> str:
        try:
            data = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError(
                "Evidence must be plain base64 JPEG, without a data URL prefix"
            ) from exc
        if not (data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9")):
            raise ValueError("Evidence must have a JPEG envelope")
        if len(data) > 8 * 1024 * 1024:
            raise ValueError("Each evidence JPEG must be at most 8 MiB")
        try:
            with Image.open(BytesIO(data)) as image:
                if image.format != "JPEG" or image.width * image.height > 20_000_000:
                    raise ValueError("Expected a JPEG of at most 20 million pixels")
                image.load()
        except (UnidentifiedImageError, OSError) as exc:
            raise ValueError("Evidence JPEG cannot be decoded") from exc
        return value


class Violation(StrictModel):
    type: str = Field(min_length=1, max_length=180)
    timecode_sec: float = Field(ge=0)
    description: str = Field(min_length=1, max_length=5000)
    evidence_frames: list[EvidenceFrame] = Field(min_length=1, max_length=3)


class AnalysisResult(StrictModel):
    verdict: Literal["violation", "no_violation"]
    violations: list[Violation]

    @model_validator(mode="after")
    def require_consistent_verdict(self) -> Self:
        if (self.verdict == "violation") != bool(self.violations):
            raise ValueError("The verdict must agree with the violations list")
        return self


class CreatedJob(StrictModel):
    job_id: UUID


class ProcessingJob(CreatedJob):
    status: Literal["processing"] = "processing"


class DoneJob(AnalysisResult, CreatedJob):
    status: Literal["done"] = "done"


class JobError(StrictModel):
    code: str
    message: str


class FailedJob(CreatedJob):
    status: Literal["failed"] = "failed"
    error: JobError


JobResponse = Annotated[ProcessingJob | DoneJob | FailedJob, Field(discriminator="status")]
