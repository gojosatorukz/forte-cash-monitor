import asyncio
import importlib
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, File, HTTPException, Security, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles

from backend.app.config import Settings
from backend.app.schemas import CreatedJob, JobResponse
from backend.app.store import JobStore, QueueFull
from backend.app.worker import Worker
from inference.base import Analyzer

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = PROJECT_ROOT / "frontend"
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


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


def create_app(settings: Settings | None = None, analyzer: Analyzer | None = None) -> FastAPI:
    settings = settings or Settings()
    # These flags cover HF Hub only; deployment still needs an outbound-traffic check.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    analyzer = analyzer if analyzer is not None else load_analyzer(settings.analyzer_factory)
    store = JobStore(settings.storage_dir)
    worker = Worker(store, analyzer, settings.job_budget_sec) if analyzer else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store.initialize()
        task = asyncio.create_task(worker.run()) if worker else None
        try:
            yield
        finally:
            if worker and task:
                worker.stop()
                await task

    app = FastAPI(
        title="Forte Cash Monitor",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        description="Service foundation. Real local video analysis is not connected yet.",
    )
    app.state.store = store
    app.state.worker = worker

    async def require_api_key(key: Annotated[str | None, Security(api_key_header)]) -> None:
        if key is None or not secrets.compare_digest(
            key.encode(), settings.api_key.get_secret_value().encode()
        ):
            raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")

    auth = [Depends(require_api_key)]

    @app.get("/healthz", tags=["service"])
    async def health():
        return {"status": "ok", "service": "forte-cash-monitor"}

    @app.get("/readyz", tags=["service"])
    async def readiness():
        return JSONResponse(
            status_code=200 if analyzer else 503,
            content={
                "ready": analyzer is not None,
                "reason": None if analyzer else "analyzer_not_configured",
            },
        )

    @app.post("/api/v1/analyze", status_code=202, response_model=CreatedJob, dependencies=auth)
    async def analyze(file: Annotated[UploadFile, File()]):
        if worker is None:
            raise HTTPException(status_code=503, detail="Local analyzer is not configured yet")
        filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        if Path(filename).suffix.lower() != ".mov":
            raise HTTPException(status_code=415, detail="Expected a .mov video in the file field")
        job_id = uuid4()
        path = store.uploads / f"{job_id}.mov"
        part = path.with_suffix(".part")
        saved = False
        try:
            total = 0
            with part.open("xb") as stream:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > settings.max_upload_mb * 1024 * 1024:
                        raise HTTPException(
                            status_code=413, detail="Video exceeds the upload limit"
                        )
                    stream.write(chunk)
            if total == 0:
                raise HTTPException(status_code=422, detail="The video is empty")
            part.replace(path)
            store.create(job_id, filename[:160], path, settings.max_active_jobs)
            saved = True
        except QueueFull as exc:
            raise HTTPException(
                status_code=429, detail="Analysis queue is full; try again later"
            ) from exc
        finally:
            await file.close()
            part.unlink(missing_ok=True)
            if not saved:
                path.unlink(missing_ok=True)
        worker.wakeup.set()
        return CreatedJob(job_id=job_id)

    @app.get("/api/v1/jobs/{job_id}", response_model=JobResponse, dependencies=auth)
    async def get_job(job_id: UUID):
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        result = {"job_id": job["id"], "status": job["status"]}
        if job["status"] == "done":
            result.update(json.loads(job["payload"]))
        elif job["status"] == "failed":
            result["error"] = json.loads(job["payload"])
        return result

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(FRONTEND_DIR / "index.html")

    @app.get("/docs", include_in_schema=False)
    async def docs():
        # Local HTML instead of Swagger assets from a CDN.
        return FileResponse(FRONTEND_DIR / "api.html")

    app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")
    return app
