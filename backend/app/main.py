import asyncio
import importlib
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Security, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles

from backend.app.config import Settings
from backend.app.guard import RequestGuard
from backend.app.reports import render_report
from backend.app.schemas import (
    AnalysisResult,
    CreatedJob,
    JobDetails,
    JobPage,
    JobResponse,
    JobSummary,
)
from backend.app.store import JobStore, QueueFull
from backend.app.worker import Worker
from inference.base import Analyzer

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = PROJECT_ROOT / "frontend"
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
logger = logging.getLogger(__name__)


def load_analyzer(factory_path: str) -> Analyzer | None:
    if not factory_path:
        return None
    module_name, sep, function_name = factory_path.partition(":")
    if not sep:
        raise ValueError("FCM_ANALYZER_FACTORY must have module:function format")
    factory = getattr(importlib.import_module(module_name), function_name)
    analyzer = factory()
    if not callable(getattr(analyzer, "analyze", None)):
        raise TypeError("Analyzer factory must return an object with analyze(request)")
    return analyzer


def job_response(job: dict) -> dict:
    result = {"job_id": job["id"], "status": job["status"]}
    if job["status"] == "done":
        result.update(json.loads(job["payload"]))
    elif job["status"] == "failed":
        result["error"] = json.loads(job["payload"])
    return result


def job_summary(job: dict) -> JobSummary:
    payload = json.loads(job["payload"]) if job.get("payload") else {}
    return JobSummary(
        job_id=job["id"],
        filename=job["filename"],
        status=job["status"],
        stage=job["stage"],
        created_at=job["created_at"],
        started_at=job["started_at"],
        finished_at=job["finished_at"],
        size_bytes=job["size_bytes"],
        verdict=job.get("verdict", payload.get("verdict")),
        event_count=job.get("event_count", len(payload.get("violations", [])))
        if job["status"] == "done"
        else None,
    )


def save_upload(source, part: Path, destination: Path, max_bytes: int) -> None:
    total = 0
    with part.open("xb") as stream:
        while chunk := source.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(413, "Video exceeds the upload limit")
            stream.write(chunk)
    if total == 0:
        raise HTTPException(422, "The video is empty")
    part.replace(destination)


def create_app(settings: Settings | None = None, analyzer: Analyzer | None = None) -> FastAPI:
    settings = settings or Settings()
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    analyzer = analyzer if analyzer is not None else load_analyzer(settings.analyzer_factory)
    store = JobStore(settings.storage_dir)
    worker = Worker(store, analyzer, settings.job_budget_sec) if analyzer else None

    async def maintenance():
        last_prune = 0.0
        while True:
            try:
                await asyncio.to_thread(store.expire, settings.job_budget_sec)
                if time.monotonic() - last_prune > 60:
                    await asyncio.to_thread(
                        store.prune, settings.retention_hours, worker.active_id if worker else None
                    )
                    last_prune = time.monotonic()
            except Exception:
                logger.exception("Job maintenance failed")
            await asyncio.sleep(0.5)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store.initialize()
        task = asyncio.create_task(worker.run()) if worker else None
        janitor = asyncio.create_task(maintenance())
        try:
            yield
        finally:
            janitor.cancel()
            with suppress(asyncio.CancelledError):
                await janitor
            if worker and task:
                worker.stop()
                # Does not kill a model thread. A non-cooperative model needs process restart.
                with suppress(TimeoutError):
                    await asyncio.wait_for(task, timeout=5)

    app = FastAPI(
        title="Forte Cash Monitor",
        version="0.2.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        description="Local video review API for Datathon 26. Model readiness: /readyz.",
    )
    app.state.store = store
    app.state.worker = worker
    app.add_middleware(
        RequestGuard,
        api_key=settings.api_key.get_secret_value(),
        max_body_bytes=(settings.max_upload_mb + 2) * 1024 * 1024,
        upload_timeout_sec=settings.upload_timeout_sec,
    )

    async def require_api_key(key: Annotated[str | None, Security(api_key_header)]) -> None:
        if key is None or not secrets.compare_digest(
            key.encode(), settings.api_key.get_secret_value().encode()
        ):
            raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")

    auth = [Depends(require_api_key)]

    def ready_state() -> dict:
        stalled = worker is not None and worker.stalled
        return {
            "ready": analyzer is not None and not stalled,
            "reason": "analyzer_stalled"
            if stalled
            else (None if analyzer else "analyzer_not_configured"),
        }

    def find_job(job_id: UUID) -> dict:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        return job

    @app.get("/healthz", tags=["service"])
    async def health():
        return {"status": "ok", "service": "forte-cash-monitor"}

    @app.get("/readyz", tags=["service"])
    async def readiness():
        state = ready_state()
        return JSONResponse(status_code=200 if state["ready"] else 503, content=state)

    @app.get("/api/v1/service", dependencies=auth, tags=["workspace"])
    async def service():
        return {
            **ready_state(),
            "analyzer": getattr(analyzer, "name", None),
            "max_upload_mb": settings.max_upload_mb,
            "max_active_jobs": settings.max_active_jobs,
            "active_jobs": store.queue_depth(),
            "job_budget_sec": settings.job_budget_sec,
            "retention_hours": settings.retention_hours,
        }

    @app.post("/api/v1/analyze", status_code=202, response_model=CreatedJob, dependencies=auth)
    async def analyze(request: Request, file: Annotated[UploadFile, File()]):
        if not ready_state()["ready"]:
            await file.close()
            raise HTTPException(503, "Local analyzer is not ready")
        filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        filename = "".join(char for char in filename if char.isprintable())[:160]
        if Path(filename).suffix.lower() != ".mov":
            await file.close()
            raise HTTPException(415, "Expected a .mov video in the file field")
        if store.queue_depth() >= settings.max_active_jobs:
            await file.close()
            raise HTTPException(429, "Analysis queue is full; try again later")
        job_id = uuid4()
        path = store.uploads / f"{job_id}.mov"
        part = path.with_suffix(".part")
        saved = False
        try:
            await asyncio.to_thread(
                save_upload, file.file, part, path, settings.max_upload_mb * 1024 * 1024
            )
            store.create(
                job_id,
                filename,
                path,
                settings.max_active_jobs,
                created_at=request.state.upload_started_at,
            )
            saved = True
        except QueueFull as exc:
            raise HTTPException(429, "Analysis queue is full; try again later") from exc
        finally:
            await file.close()
            part.unlink(missing_ok=True)
            if not saved:
                path.unlink(missing_ok=True)
        worker.wakeup.set()
        return CreatedJob(job_id=job_id)

    @app.get("/api/v1/jobs", response_model=JobPage, dependencies=auth, tags=["workspace"])
    async def list_jobs(
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        rows, total = store.list_jobs(limit, offset)
        return JobPage(
            items=[job_summary(row) for row in rows], total=total, limit=limit, offset=offset
        )

    @app.get("/api/v1/jobs/{job_id}", response_model=JobResponse, dependencies=auth)
    async def get_job(job_id: UUID):
        return job_response(find_job(job_id))

    @app.get(
        "/api/v1/jobs/{job_id}/details",
        response_model=JobDetails,
        dependencies=auth,
        tags=["workspace"],
    )
    async def get_details(job_id: UUID):
        job = find_job(job_id)
        return {"summary": job_summary(job), "result": job_response(job)}

    @app.get("/api/v1/jobs/{job_id}/video", dependencies=auth, tags=["workspace"])
    async def get_video(job_id: UUID):
        job = find_job(job_id)
        path = Path(job["path"]).resolve()
        if path.parent != store.uploads or not path.is_file():
            raise HTTPException(404, "Source video is no longer available")
        return FileResponse(path, media_type="video/quicktime")

    @app.get("/api/v1/jobs/{job_id}/report", dependencies=auth, tags=["workspace"])
    async def get_report(
        job_id: UUID, format: Annotated[str, Query(pattern="^(json|html)$")] = "json"
    ):
        job = find_job(job_id)
        if job["status"] != "done":
            raise HTTPException(409, "Report requires a completed analysis")
        headers = {"Content-Disposition": f'attachment; filename="report-{job_id}.{format}"'}
        if format == "json":
            return JSONResponse(job_response(job), headers=headers)
        result = AnalysisResult.model_validate_json(job["payload"])
        report = await asyncio.to_thread(render_report, job, result)
        return HTMLResponse(report, headers=headers)

    @app.get("/api/v1/rules", dependencies=auth, tags=["workspace"])
    async def rules():
        return JSONResponse(json.loads((PROJECT_ROOT / "rules/catalog.json").read_text()))

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(FRONTEND_DIR / "index.html")

    @app.get("/docs", include_in_schema=False)
    async def docs():
        return FileResponse(FRONTEND_DIR / "api.html")

    app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")
    return app
