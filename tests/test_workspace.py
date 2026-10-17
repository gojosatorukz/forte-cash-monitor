import asyncio
import json
import sqlite3
import threading
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_service import AUTH, KEY, SuccessfulAnalyzer, settings, upload, wait_for_job

from backend.app.guard import RequestGuard
from backend.app.main import create_app
from backend.app.schemas import AnalysisResult
from backend.app.store import JobStore
from inference.base import AnalysisFailure


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/service",
        "/api/v1/jobs",
        "/api/v1/rules",
        "/api/v1/jobs/abc/video",
        "/api/v1/jobs/abc/details",
        "/api/v1/jobs/abc/report",
    ],
)
def test_all_workspace_routes_require_key(tmp_path, path):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.get(path).status_code == 401


def test_history_source_and_exports_keep_jury_schema(tmp_path):
    with TestClient(create_app(settings(tmp_path), SuccessfulAnalyzer())) as client:
        job_id = upload(client, filename="<script>alert(1)</script>.mov").json()["job_id"]
        result = wait_for_job(client, job_id)
        page = client.get("/api/v1/jobs?limit=1&offset=0", headers=AUTH).json()
        assert page["total"] == 1
        assert page["items"][0]["event_count"] == 1
        assert "image_base64" not in json.dumps(page)
        assert client.get("/api/v1/jobs?limit=0", headers=AUTH).status_code == 422
        assert client.get("/api/v1/jobs?offset=-1", headers=AUTH).status_code == 422
        details = client.get(f"/api/v1/jobs/{job_id}/details", headers=AUTH).json()
        assert details["result"] == result
        assert details["summary"]["finished_at"] >= details["summary"]["created_at"]
        video = client.get(f"/api/v1/jobs/{job_id}/video", headers=AUTH)
        assert video.content == b"synthetic-container"
        assert video.headers["cache-control"] == "no-store"
        partial = client.get(f"/api/v1/jobs/{job_id}/video", headers={**AUTH, "Range": "bytes=0-8"})
        assert partial.status_code == 206
        assert partial.content == b"synthetic"
        report = client.get(f"/api/v1/jobs/{job_id}/report", headers=AUTH)
        assert report.json() == result
        html = client.get(f"/api/v1/jobs/{job_id}/report?format=html", headers=AUTH)
        assert html.status_code == 200
        assert "attachment; filename=" in html.headers["content-disposition"]
        assert "<script>" not in html.text
        assert "data:image/jpeg;base64," in html.text
        assert (
            client.get(f"/api/v1/jobs/{job_id}/report?format=pdf", headers=AUTH).status_code == 422
        )
        rules = client.get("/api/v1/rules", headers=AUTH).json()
        assert rules["jurisdiction"] == "KZ"


def test_progress_and_unfinished_report(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class ProgressAnalyzer:
        name = "progress-fixture"

        def analyze(self, request):
            request.report_progress("scanning")
            entered.set()
            release.wait(3)
            return AnalysisResult(verdict="no_violation", violations=[])

    with TestClient(create_app(settings(tmp_path), ProgressAnalyzer())) as client:
        try:
            job_id = upload(client).json()["job_id"]
            assert entered.wait(2)
            details = client.get(f"/api/v1/jobs/{job_id}/details", headers=AUTH).json()
            assert details["summary"]["stage"] == "scanning"
            assert details["result"] == {"job_id": job_id, "status": "processing"}
            assert client.get(f"/api/v1/jobs/{job_id}/report", headers=AUTH).status_code == 409
            # A blocked model does not prevent health and history responses.
            assert client.get("/healthz").status_code == 200
            assert client.get("/api/v1/service", headers=AUTH).json()["active_jobs"] == 1
        finally:
            release.set()
        assert wait_for_job(client, job_id)["verdict"] == "no_violation"


def test_watchdog_fails_hung_model_and_cannot_be_overwritten(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class Hung:
        name = "deadline-fixture"

        def analyze(self, request):
            entered.set()
            release.wait(3)
            return AnalysisResult(verdict="no_violation", violations=[])

    with TestClient(create_app(settings(tmp_path, job_budget_sec=0.1), Hung())) as client:
        try:
            job_id = upload(client).json()["job_id"]
            assert entered.wait(2)
            job = wait_for_job(client, job_id)
            assert job["status"] == "failed"
            assert job["error"]["code"] == "deadline_exceeded"
            assert "verdict" not in job
            assert client.get("/readyz").json()["reason"] == "analyzer_stalled"
            assert upload(client).status_code == 503
        finally:
            release.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not client.get("/readyz").json()["ready"]:
            time.sleep(0.01)
        assert client.get(f"/api/v1/jobs/{job_id}", headers=AUTH).json() == job
        assert client.get("/readyz").status_code == 200


def test_expected_decode_failure_is_safe(tmp_path):
    class InvalidVideo:
        name = "decode-fixture"

        def analyze(self, request):
            raise AnalysisFailure("invalid_video")

    with TestClient(create_app(settings(tmp_path), InvalidVideo())) as client:
        job = wait_for_job(client, upload(client).json()["job_id"])
        assert job["error"]["code"] == "invalid_video"
        assert "verdict" not in job


def test_retention_removes_expired_finished_data_only(tmp_path):
    store = JobStore(tmp_path)
    store.initialize()
    old, active, protected, recent = [uuid4() for _ in range(4)]
    for job_id in [old, active, protected, recent]:
        path = store.uploads / f"{job_id}.mov"
        path.write_bytes(b"test")
        store.create(job_id, "test.mov", path, 4)
        if job_id != active:
            store.finish(str(job_id), "done", {"verdict": "no_violation", "violations": []})
    with store.connection() as con:
        con.execute(
            "UPDATE jobs SET finished_at=? WHERE id IN (?, ?)",
            (time.time() - 25 * 3600, str(old), str(protected)),
        )
    store.prune(24, str(protected))
    assert store.get(old) is None
    assert not (store.uploads / f"{old}.mov").exists()
    for kept in [active, protected, recent]:
        assert store.get(kept)
        assert (store.uploads / f"{kept}.mov").exists()


def test_legacy_database_migration(tmp_path):
    with sqlite3.connect(tmp_path / "jobs.sqlite3") as con:
        con.execute(
            "CREATE TABLE jobs (id TEXT PRIMARY KEY, filename TEXT, path TEXT, "
            "status TEXT, created_at REAL, started_at REAL, payload TEXT)"
        )
        con.execute(
            "INSERT INTO jobs VALUES (?, 'old.mov', 'old.mov', 'processing', ?, NULL, NULL)",
            (str(uuid4()), time.time()),
        )
    with TestClient(create_app(settings(tmp_path))) as client:
        page = client.get("/api/v1/jobs", headers=AUTH).json()
        assert page["items"][0]["status"] == "failed"
        assert page["items"][0]["stage"] == "failed"


def test_bad_key_is_rejected_before_multipart_parsing(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.post(
            "/api/v1/analyze",
            content=b"broken multipart",
            headers={"Content-Type": "multipart/form-data; boundary=test"},
        )
        assert response.status_code == 401


def test_streamed_body_size_limit(tmp_path):
    def chunks():
        yield b'--test\r\nContent-Disposition: form-data; name="file"; filename="x.mov"\r\n\r\n'
        for _ in range(4):
            yield b"x" * 1024 * 1024
        yield b"\r\n--test--\r\n"

    with TestClient(
        create_app(settings(tmp_path, max_upload_mb=1), SuccessfulAnalyzer())
    ) as client:
        response = client.post(
            "/api/v1/analyze",
            content=chunks(),
            headers={**AUTH, "Content-Type": "multipart/form-data; boundary=test"},
        )
        assert response.status_code == 413
        assert list((tmp_path / "uploads").iterdir()) == []


def test_upload_receive_timeout():
    async def run():
        async def receive():
            await asyncio.sleep(0.1)

        async def app(scope, receive, send):
            await receive()

        guard = RequestGuard(app, KEY, 1024, 0.01)
        with pytest.raises(Exception) as error:
            await guard(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/api/v1/analyze",
                    "headers": [(b"x-api-key", KEY.encode())],
                },
                receive,
                None,
            )
        assert error.value.status_code == 408

    asyncio.run(run())
