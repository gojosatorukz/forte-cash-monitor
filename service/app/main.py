"""FastAPI app: public API (/api/v1, X-API-Key), UI API (/ui-api, password session), health, static UI at /."""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import secrets
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from python_multipart.multipart import MultipartParseError, MultipartParser, parse_options_header
from starlette.requests import ClientDisconnect

from app.branding import BRANDED_PAGES, BrandedPages
from app.clips import configure_parallel as configure_clip_parallel
from app.config import API_KEY_NOT_CONFIGURED, MIN_API_KEY_LEN, Settings
from app.health import VlmProbe
from app.jobs import JobStore, new_job_id, sanitize_filename
from app.review import CHECKLIST, MAX_BODY_BYTES, ReviewError, feedback_ndjson, merge_review
from app.rules import load_rules_auto
from app.ui_auth import UiGate
from app.worker import Worker, clip_url

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
UI_JOBS_LIMIT = 20
WRITE_BUFFER = 1024 * 1024  # uploads are written to disk in 1 MB chunks
NOT_FOUND = "Задача не найдена (job not found)"
CLIP_NOT_FOUND = "Видеофрагмент не найден (clip not found)"
REVIEW_NOT_DONE = "Проверка доступна только для завершённой задачи (review allowed only when status is done)"
CLIP_NAME_RE = re.compile(r"([1-9][0-9]{0,5})\.mp4")  # "<n>.mp4", n a positive int

VIDEO_TYPES = {
    ".mov": "video/quicktime",
    ".qt": "video/quicktime",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
}


def video_media_type(path: Path) -> str:
    ext = path.suffix.lower()
    return VIDEO_TYPES.get(ext) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


# ---------- streaming multipart upload ----------

class _UploadSink:
    """python-multipart callbacks: writes the chosen file part straight to disk with a size cap.

    The part named `file` is preferred; if the client used another field name, the first
    file part is accepted instead."""

    def __init__(self, job_dir: Path, cap: int):
        self.job_dir = job_dir
        self.cap = cap
        self.path: Path | None = None
        self.filename: str | None = None
        self.field: str | None = None
        self.size = 0
        self.too_big = False
        self._fh = None
        self._writing = False
        self._headers: dict[bytes, bytes] = {}
        self._hf = b""
        self._hv = b""

    # header callbacks
    def on_part_begin(self) -> None:
        self._headers = {}
        self._hf = self._hv = b""

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._hf += data[start:end]

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._hv += data[start:end]

    def on_header_end(self) -> None:
        self._headers[self._hf.strip().lower()] = self._hv.strip()
        self._hf = self._hv = b""

    def on_headers_finished(self) -> None:
        raw = self._headers.get(b"content-disposition", b"")
        _, opts = parse_options_header(raw)
        filename = _decode_header(opts.get(b"filename"))
        m = re.search(rb"filename\*\s*=\s*([^']*)'[^']*'([^;]+)", raw, re.I)
        if m:
            filename = unquote(m.group(2).decode("latin-1").strip().strip('"'),
                               encoding=(m.group(1).decode("latin-1") or "utf-8"), errors="replace")
        if filename is None:
            return  # a plain form field: ignored
        name = _decode_header(opts.get(b"name")) or ""
        if self.path is not None and (self.field == "file" or name != "file"):
            return  # already have a file (the preferred one, or this one isn't preferred)
        self._discard()
        self.too_big = False
        self.size = 0
        self.field = name
        self.filename = filename
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.job_dir / sanitize_filename(filename)
        self._fh = open(self.path, "wb", buffering=WRITE_BUFFER)
        self._writing = True

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if not self._writing:
            return
        self.size += end - start
        if self.size > self.cap:
            self.too_big = True
            self._writing = False
            self._discard(keep_state=True)
            return
        self._fh.write(data[start:end])

    def on_part_end(self) -> None:
        if self._writing:
            self._writing = False
            self._fh.close()
            self._fh = None

    def _discard(self, keep_state: bool = False) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        if self.path is not None:
            self.path.unlink(missing_ok=True)
            if not keep_state:
                self.path = None

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def callbacks(self) -> dict[str, Any]:
        return {
            "on_part_begin": self.on_part_begin,
            "on_header_field": self.on_header_field,
            "on_header_value": self.on_header_value,
            "on_header_end": self.on_header_end,
            "on_headers_finished": self.on_headers_finished,
            "on_part_data": self.on_part_data,
            "on_part_end": self.on_part_end,
        }


def _decode_header(value: bytes | None) -> str | None:
    if value is None:
        return None
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value.decode("latin-1")


async def receive_upload(request: Request, settings: Settings) -> tuple[str, str, Path]:
    """Stream a multipart upload to DATA_DIR/uploads/<job_id>/<safe name>.

    Returns (job_id, safe filename, path). Raises HTTPException 400/413; never leaves a
    partial file behind."""
    mime, params = parse_options_header(request.headers.get("content-type", ""))
    boundary = params.get(b"boundary")
    if mime != b"multipart/form-data" or not boundary:
        raise HTTPException(400, "Ожидается multipart/form-data с полем 'file' (expected multipart field 'file')")

    cap = settings.max_upload_bytes
    cap_mb = cap / (1024 * 1024)
    too_big_msg = f"Файл больше допустимых {cap_mb:g} МБ (file too large, max {cap_mb:g} MB)"
    # Past the cap the body is drained (so clients still read the 413) up to this bound,
    # beyond which the request is cut off immediately.
    hard_limit = max(4 * cap, cap + 64 * 1024 * 1024)
    try:
        declared = int(request.headers.get("content-length", "") or -1)
    except ValueError:
        declared = -1
    if declared > hard_limit:
        raise HTTPException(413, too_big_msg)

    job_id = new_job_id()
    job_dir = settings.uploads_dir / job_id
    sink = _UploadSink(job_dir, cap)
    parser = MultipartParser(boundary, sink.callbacks())
    ok = False
    try:
        received = 0
        async for chunk in request.stream():
            received += len(chunk)
            if received > hard_limit:
                raise HTTPException(413, too_big_msg)
            if chunk:
                parser.write(chunk)
        parser.finalize()
        sink.close()
        if sink.too_big:
            raise HTTPException(413, too_big_msg)
        if sink.path is None:
            raise HTTPException(400, "В запросе нет файла в поле 'file' (no file in field 'file')")
        if sink.size == 0:
            raise HTTPException(400, "Файл пустой (empty file)")
        ok = True
        return job_id, sink.path.name, sink.path
    except ClientDisconnect:
        raise HTTPException(400, "Загрузка прервана клиентом (client disconnected)") from None
    except MultipartParseError as e:
        raise HTTPException(400, f"Некорректный multipart-запрос (malformed multipart): {e}") from None
    finally:
        sink.close()
        if not ok:
            shutil.rmtree(job_dir, ignore_errors=True)


async def read_json_body(request: Request, cap: int) -> Any:
    """Small JSON body with a hard size cap (413), malformed JSON -> 422."""
    too_big = f"Тело запроса больше {cap // 1024} КБ (body too large)"
    try:
        if int(request.headers.get("content-length", "") or 0) > cap:
            raise HTTPException(413, too_big)
    except ValueError:
        pass
    buf = bytearray()
    try:
        async for chunk in request.stream():
            buf += chunk
            if len(buf) > cap:
                raise HTTPException(413, too_big)
    except ClientDisconnect:
        raise HTTPException(400, "Запрос прерван клиентом (client disconnected)") from None
    try:
        return json.loads(bytes(buf).decode("utf-8"))
    except (ValueError, RecursionError):  # bad UTF-8 / JSON, or nesting deep enough to exhaust the stack
        raise HTTPException(422, "Ожидается JSON-объект (malformed JSON body)") from None


def require_json_content_type(request: Request) -> None:
    """415 unless Content-Type is application/json (parameters such as charset allowed).

    A cross-site form or `fetch(..., {mode: "no-cors"})` can only send "simple" content types;
    requiring JSON forces a CORS preflight, which this service never answers, so the key-less
    review route cannot be driven from another origin."""
    mime = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if mime != "application/json":
        raise HTTPException(415, "Ожидается Content-Type: application/json (unsupported media type)")


# ---------- app factory ----------

def _setup_logging() -> None:
    """uvicorn only configures its own loggers; make `app.*` INFO logs visible too."""
    app_log = logging.getLogger("app")
    if not app_log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        app_log.addHandler(handler)
        app_log.setLevel(logging.INFO)


def read_public_url(path: Path) -> str | None:
    """Current public tunnel URL, re-read on every request; None if the file is missing/empty/unreadable."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return text.splitlines()[0].strip() if text else None


def public_url_info(settings: Settings) -> tuple[str | None, str | None]:
    """(public_url, source): PUBLIC_HOSTNAME env -> "env"; else the quick-tunnel URL file -> "quick-tunnel"."""
    host = settings.public_hostname.strip()
    if host:
        host = re.sub(r"^https?://", "", host).rstrip("/")
        return f"https://{host}", "env"
    url = read_public_url(settings.public_url_path)
    return (url, "quick-tunnel") if url else (None, None)


def create_app(settings: Settings | None = None, analyzer: Any = None,
               analyzer_factory: Callable[[], Any] | None = None) -> FastAPI:
    """`analyzer`: optional ready instance (tests; forces one job worker, an instance is never
    shared between threads). `analyzer_factory`: optional callable (tests) giving each of the
    JOB_WORKERS workers its own instance. Otherwise every worker loads settings.analyzer in its
    own thread at startup, one after another."""
    _setup_logging()
    settings = settings or Settings.from_env()
    rules = load_rules_auto(settings.rules_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        store = JobStore(settings.jobs_dir)
        configure_clip_parallel(settings.clip_max_parallel)
        worker = Worker(store, rules, settings, analyzer=analyzer, analyzer_factory=analyzer_factory)
        app.state.store = store
        app.state.worker = worker
        worker.start()
        for job_id in store.recover():  # once, into the shared queue
            log.info("Re-queuing job %s left in processing state", job_id)
            worker.submit(job_id)
        yield
        worker.stop()

    app = FastAPI(title="ForteBank cash-room violation detection", version="1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.rules = rules
    app.state.vlm_probe = VlmProbe(settings.vlm_health_url)
    # Browser UI password gate: everything except /api/v1/*, /healthz, /login, /logout needs the
    # session cookie (allowlist, so new routes are gated by default). /api/v1 stays API-key only.
    app.add_middleware(UiGate, settings=settings)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "Внутренняя ошибка сервера (internal server error)"}, status_code=500)

    if not settings.api_key_configured:
        log.error("API_KEY is not set or shorter than %d characters: /api/v1/* answers 503 until it is set "
                  "(UI and /healthz still work)", MIN_API_KEY_LEN)

    def require_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
        # Checked before the key comparison: an unconfigured key must not be probe-able.
        if not settings.api_key_configured:
            raise HTTPException(503, API_KEY_NOT_CONFIGURED)
        if not x_api_key or not secrets.compare_digest(x_api_key.encode(), settings.api_key.strip().encode()):
            raise HTTPException(401, "Неверный или отсутствующий X-API-Key (invalid or missing API key)")

    def store_of(request: Request) -> JobStore:
        return request.app.state.store

    async def analyze(request: Request, submitted: float) -> dict[str, Any]:
        """`submitted`: monotonic time taken before the body was read, so the per-job budget
        (the jury's wall clock starts at submit) includes the upload itself."""
        job_id, filename, path = await receive_upload(request, settings)
        store = store_of(request)
        store.create(filename=filename, upload_path=str(path), job_id=job_id)
        request.app.state.worker.submit(job_id, submitted=submitted)
        log.info("Job %s accepted: %s (%d bytes)", job_id, filename, path.stat().st_size)
        return {"job_id": job_id, "status": "processing"}

    def job_public(request: Request, job_id: str) -> dict[str, Any]:
        job = store_of(request).get(job_id)
        if job is None:
            raise HTTPException(404, NOT_FOUND)
        return job.to_public()

    def ui_urls(pub: dict[str, Any]) -> dict[str, Any]:
        """UI payload: clip links point at the key-less /ui-api route."""
        for n, v in enumerate(pub.get("violations") or [], start=1):
            if v.get("clip_url"):
                v["clip_url"] = clip_url(pub["job_id"], n, prefix="/ui-api")
        return pub

    async def review(request: Request, job_id: str) -> dict[str, Any]:
        """Merge a partial review update into the job (under the store lock, atomic write)."""
        require_json_content_type(request)
        store = store_of(request)
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, NOT_FOUND)
        if job.status != "done":
            raise HTTPException(409, REVIEW_NOT_DONE)
        patch = await read_json_body(request, MAX_BODY_BYTES)

        def apply(rec):
            if rec.status != "done":
                raise HTTPException(409, REVIEW_NOT_DONE)
            try:
                return {"review": merge_review(rec.review, patch, len(rec.violations))}
            except ReviewError as e:
                raise HTTPException(422, str(e)) from None

        rec = await run_in_threadpool(store.mutate, job_id, apply)
        if rec is None:
            raise HTTPException(404, NOT_FOUND)
        return rec.to_public()

    def feedback(request: Request) -> Response:
        jobs = store_of(request).list()[::-1]  # oldest first (creation order)
        return Response(feedback_ndjson(jobs), media_type="application/x-ndjson",
                        headers={"Content-Disposition": 'attachment; filename="feedback.jsonl"'})

    def clip_response(request: Request, job_id: str, name: str) -> FileResponse:
        m = CLIP_NAME_RE.fullmatch(name)
        job = store_of(request).get(job_id)
        if job is None:
            raise HTTPException(404, NOT_FOUND)
        if m is None:
            raise HTTPException(404, CLIP_NOT_FOUND)
        n = int(m.group(1))
        # Only clips the current job record links to (no stale files from an earlier run).
        if job.status != "done" or n > len(job.violations) or not job.violations[n - 1].get("clip_url"):
            raise HTTPException(404, CLIP_NOT_FOUND)
        path = settings.clips_dir / job.job_id / f"{n}.mp4"
        if not path.is_file():
            raise HTTPException(404, CLIP_NOT_FOUND)
        # Same Range handling as the video route (Starlette FileResponse: 206/416, Content-Range).
        return FileResponse(path, media_type="video/mp4")

    # ----- health -----
    # GET routes also answer HEAD (otherwise HEAD falls through to the static mount -> 404).
    @app.api_route("/healthz", methods=["GET", "HEAD"])
    def healthz(request: Request) -> dict[str, Any]:
        worker = request.app.state.worker
        workers = worker.status()
        ready = workers["ready"] > 0  # at least one job worker has its analyzer loaded
        public_url, source = public_url_info(request.app.state.settings)
        probe = request.app.state.vlm_probe
        # vlm_ok is informational: `ok` stays "the service is up" (the analyzer can run without VLM).
        return {"ok": True, "api_key_configured": settings.api_key_configured,
                "analyzer_ready": ready, "analyzer_error": worker.load_error, "workers": workers,
                "public_url": public_url, "public_url_source": source,
                "rules_count": len(rules), "vlm_ok": probe.check(), "brand": settings.brand}

    # ----- public API -----
    @app.post("/api/v1/analyze", dependencies=[Depends(require_key)])
    async def api_analyze(request: Request):
        return await analyze(request, time.monotonic())

    @app.api_route("/api/v1/jobs/{job_id}", methods=["GET", "HEAD"], dependencies=[Depends(require_key)])
    def api_job(job_id: str, request: Request):
        return job_public(request, job_id)

    @app.api_route("/api/v1/jobs/{job_id}/clips/{name}", methods=["GET", "HEAD"],
                   dependencies=[Depends(require_key)])
    def api_job_clip(job_id: str, name: str, request: Request):
        return clip_response(request, job_id, name)

    @app.post("/api/v1/jobs/{job_id}/review", dependencies=[Depends(require_key)])
    async def api_job_review(job_id: str, request: Request):
        return await review(request, job_id)

    @app.api_route("/api/v1/feedback.jsonl", methods=["GET", "HEAD"], dependencies=[Depends(require_key)])
    def api_feedback(request: Request):
        return feedback(request)

    # ----- UI API (no key; gated by the UiGate session cookie) -----
    @app.post("/ui-api/analyze")
    async def ui_analyze(request: Request):
        return await analyze(request, time.monotonic())

    @app.api_route("/ui-api/jobs", methods=["GET", "HEAD"])
    def ui_jobs(request: Request):
        out = []
        for job in store_of(request).list(limit=UI_JOBS_LIMIT):
            pub = job.to_public()
            for v in pub["violations"]:
                v["evidence_frames"] = [{"timestamp_sec": f.get("timestamp_sec"), "annotated": bool(f.get("annotated"))}
                                        for f in v.get("evidence_frames", [])]
            out.append(ui_urls(pub))
        return {"jobs": out}

    @app.api_route("/ui-api/jobs/{job_id}", methods=["GET", "HEAD"])
    def ui_job(job_id: str, request: Request):
        return ui_urls(job_public(request, job_id))

    @app.post("/ui-api/jobs/{job_id}/review")
    async def ui_job_review(job_id: str, request: Request):
        return ui_urls(await review(request, job_id))

    @app.api_route("/ui-api/feedback.jsonl", methods=["GET", "HEAD"])
    def ui_feedback(request: Request):
        return feedback(request)

    @app.api_route("/ui-api/jobs/{job_id}/clips/{name}", methods=["GET", "HEAD"])
    def ui_job_clip(job_id: str, name: str, request: Request):
        return clip_response(request, job_id, name)

    @app.api_route("/ui-api/jobs/{job_id}/video", methods=["GET", "HEAD"])
    def ui_job_video(job_id: str, request: Request):
        job = store_of(request).get(job_id)
        if job is None:
            raise HTTPException(404, NOT_FOUND)
        path = Path(job.upload_path or "")
        if not job.upload_path or not path.is_file():
            raise HTTPException(404, "Файл видео не найден (video file not found)")
        # Starlette's FileResponse implements Range (206/416, Content-Range, Accept-Ranges).
        return FileResponse(path, media_type=video_media_type(path))

    @app.api_route("/ui-api/rules", methods=["GET", "HEAD"])
    def ui_rules():
        # `checklist`: reviewer-only steps the camera cannot verify (app/checklist.json).
        return {"rules": rules.public_dict(), "checklist": CHECKLIST}

    # Without these, other methods on the upload routes would fall through to the static
    # mount and answer 404 instead of 405.
    @app.api_route("/api/v1/analyze", methods=["GET", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    @app.api_route("/ui-api/analyze", methods=["GET", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    def analyze_wrong_method():
        raise HTTPException(405, "Используйте POST (method not allowed, use POST)", headers={"Allow": "POST"})

    # ----- static UI (registered last so it never shadows the API) -----
    # The HTML pages are branded server-side (BRAND, see app.branding) so the logo never flashes;
    # the UiGate middleware still gates them like any other non-public path.
    branded = BrandedPages(STATIC_DIR, settings.brand, settings.live_replay)

    def branded_page(name: str) -> Callable[[], Response]:
        def page() -> Response:
            return Response(branded.get(name), media_type="text/html; charset=utf-8",
                            headers={"Cache-Control": "no-cache"})
        return page

    for url_path, file_name in BRANDED_PAGES.items():
        app.add_api_route(url_path, branded_page(file_name), methods=["GET", "HEAD"], include_in_schema=False)

    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()
