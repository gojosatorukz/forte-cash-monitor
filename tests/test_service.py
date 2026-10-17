import base64
import threading
import time
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.schemas import AnalysisResult
from backend.app.store import JobStore

KEY = "test-key-for-local-contract-checks-only-1234"
AUTH = {"X-API-Key": KEY}


class SuccessfulAnalyzer:
    """Synthetic transport fixture, never enabled through production config."""

    name = "test-fixture"

    def analyze(self, request):
        assert request.video_path.read_bytes() == b"synthetic-container"
        stream = BytesIO()
        Image.new("RGB", (8, 8), color="white").save(stream, format="JPEG")
        return AnalysisResult.model_validate(
            {
                "verdict": "violation",
                "violations": [
                    {
                        "type": "Synthetic test event",
                        "timecode_sec": 1.0,
                        "description": "Transport contract fixture; not a real video finding.",
                        "evidence_frames": [
                            {
                                "timestamp_sec": 1.0,
                                "image_base64": base64.b64encode(stream.getvalue()).decode(),
                            }
                        ],
                    }
                ],
            }
        )


def settings(tmp_path, **kwargs):
    return Settings(_env_file=None, api_key=KEY, storage_dir=tmp_path, **kwargs)


def upload(client, content=b"synthetic-container", filename="video.mov"):
    return client.post(
        "/api/v1/analyze", headers=AUTH, files={"file": (filename, content, "video/quicktime")}
    )


def wait_for_job(client, job_id):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = client.get(f"/api/v1/jobs/{job_id}", headers=AUTH)
        assert result.status_code == 200
        if result.json()["status"] != "processing":
            return result.json()
        time.sleep(0.01)
    pytest.fail("The infrastructure test job did not finish")


def test_unconfigured_model_is_not_reported_as_no_violation(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 503
        response = upload(client)
        assert response.status_code == 503
        assert "verdict" not in response.json()
        assert list((tmp_path / "uploads").iterdir()) == []
        assert "анализатор" in client.get("/").text
        assert client.get("/docs").status_code == 200
        assert client.get("/static/app.js").status_code == 200


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
def test_both_jury_routes_require_key(tmp_path, headers):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert (
            client.post(
                "/api/v1/analyze", headers=headers, files={"file": ("x.mov", b"x")}
            ).status_code
            == 401
        )
        assert client.get(f"/api/v1/jobs/{uuid4()}", headers=headers).status_code == 401


def test_upload_job_and_actual_jpeg_response(tmp_path):
    with TestClient(create_app(settings(tmp_path), SuccessfulAnalyzer())) as client:
        assert client.get("/readyz").status_code == 200
        created = upload(client, filename="../../outside.MOV")
        assert created.status_code == 202
        job = wait_for_job(client, created.json()["job_id"])
        assert job["status"] == "done"
        assert job["verdict"] == "violation"
        frame = job["violations"][0]["evidence_frames"][0]
        with Image.open(BytesIO(base64.b64decode(frame["image_base64"]))) as image:
            assert image.format == "JPEG"
        assert len(list((tmp_path / "uploads").glob("*.mov"))) == 1
        assert not (tmp_path.parent / "outside.MOV").exists()


@pytest.mark.parametrize(
    "filename,content,status",
    [("wrong.mp4", b"x", 415), ("empty.mov", b"", 422), ("big.mov", b"x" * (1024 * 1024 + 1), 413)],
)
def test_rejected_uploads_leave_no_files(tmp_path, filename, content, status):
    with TestClient(
        create_app(settings(tmp_path, max_upload_mb=1), SuccessfulAnalyzer())
    ) as client:
        assert upload(client, content, filename).status_code == status
        assert list((tmp_path / "uploads").iterdir()) == []


def test_unknown_and_invalid_job_ids(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.get(f"/api/v1/jobs/{uuid4()}", headers=AUTH).status_code == 404
        assert client.get("/api/v1/jobs/not-a-uuid", headers=AUTH).status_code == 422


def test_model_exception_becomes_failed_without_leaking_details(tmp_path):
    class Broken:
        name = "broken-test"

        def analyze(self, request):
            raise RuntimeError("private-internal-path-or-key")

    with TestClient(create_app(settings(tmp_path), Broken())) as client:
        job_id = upload(client).json()["job_id"]
        job = wait_for_job(client, job_id)
        assert job["status"] == "failed"
        assert "verdict" not in job
        assert "private-internal" not in str(job)


def test_queue_limit_does_not_create_orphan_uploads(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class Blocked:
        name = "blocked-test"

        def analyze(self, request):
            entered.set()
            release.wait(3)
            return AnalysisResult(verdict="no_violation", violations=[])

    with TestClient(create_app(settings(tmp_path, max_active_jobs=1), Blocked())) as client:
        try:
            assert upload(client).status_code == 202
            assert entered.wait(2)
            assert upload(client).status_code == 429
            assert len(list((tmp_path / "uploads").iterdir())) == 1
        finally:
            release.set()


def test_restart_preserves_done_results_and_fails_interrupted_jobs(tmp_path):
    store = JobStore(tmp_path)
    store.initialize()
    done_id, interrupted_id = uuid4(), uuid4()
    store.create(done_id, "one.mov", tmp_path / "one.mov", 4)
    store.finish(str(done_id), "done", {"verdict": "no_violation", "violations": []})
    store.create(interrupted_id, "two.mov", tmp_path / "two.mov", 4)
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.get(f"/api/v1/jobs/{done_id}", headers=AUTH).json()["status"] == "done"
        job = client.get(f"/api/v1/jobs/{interrupted_id}", headers=AUTH).json()
        assert job["status"] == "failed"
        assert job["error"]["code"] == "service_restarted"


def test_schema_rejects_inconsistent_verdict_and_invalid_evidence():
    with pytest.raises(ValidationError):
        AnalysisResult(verdict="violation", violations=[])
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(
            {
                "verdict": "violation",
                "violations": [
                    {
                        "type": "x",
                        "timecode_sec": 0,
                        "description": "x",
                        "evidence_frames": [{"timestamp_sec": 0, "image_base64": "/9j/2Q=="}],
                    }
                ],
            }
        )


def test_missing_field_and_openapi_contract(tmp_path):
    with TestClient(create_app(settings(tmp_path), SuccessfulAnalyzer())) as client:
        assert client.post("/api/v1/analyze", headers=AUTH).status_code == 422
        schema = client.get("/openapi.json").json()
        assert schema["components"]["securitySchemes"]["APIKeyHeader"]["name"] == "X-API-Key"
        assert "/api/v1/jobs/{job_id}" in schema["paths"]


def test_local_secret_is_required(tmp_path):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_key="replace-with-a-random-local-secret", storage_dir=tmp_path)
