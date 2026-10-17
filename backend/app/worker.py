import asyncio
import logging
import time
from pathlib import Path

from backend.app.schemas import AnalysisResult
from backend.app.store import JobStore
from inference.base import AnalysisFailure, AnalysisRequest, Analyzer

logger = logging.getLogger(__name__)
FAILURE_MESSAGES = {
    "invalid_video": "Видео повреждено или не содержит доступной видеодорожки.",
    "unsupported_codec": "Локальный декодер не поддерживает кодек этого видео.",
    "deadline_exceeded": "Истёк бюджет времени задачи.",
}


class Worker:
    def __init__(self, store: JobStore, analyzer: Analyzer, budget_sec: float):
        self.store = store
        self.analyzer = analyzer
        self.budget_sec = budget_sec
        self.stopping = asyncio.Event()
        self.wakeup = asyncio.Event()
        self.active_id: str | None = None
        self.deadline: float | None = None

    @property
    def stalled(self) -> bool:
        return self.deadline is not None and time.monotonic() > self.deadline

    async def run(self) -> None:
        while not self.stopping.is_set():
            self.wakeup.clear()
            job = self.store.claim_next()
            if job is None:
                try:
                    await asyncio.wait_for(self.wakeup.wait(), timeout=0.5)
                except TimeoutError:
                    pass
                continue
            remaining = self.budget_sec - (time.time() - job["created_at"])
            if remaining <= 0:
                self.fail(job["id"], "deadline_exceeded", FAILURE_MESSAGES["deadline_exceeded"])
                continue
            self.active_id = job["id"]
            self.deadline = time.monotonic() + remaining
            request = AnalysisRequest(
                Path(job["path"]),
                self.deadline,
                report_progress=lambda stage, job_id=job["id"]: self.store.progress(job_id, stage),
            )
            try:
                raw = await asyncio.to_thread(self.analyzer.analyze, request)
                result = AnalysisResult.model_validate(raw)
                if time.monotonic() > self.deadline:
                    self.fail(job["id"], "deadline_exceeded", FAILURE_MESSAGES["deadline_exceeded"])
                else:
                    self.store.finish(job["id"], "done", result.model_dump(mode="json"))
            except AnalysisFailure as exc:
                code = exc.code if exc.code in FAILURE_MESSAGES else "analysis_failed"
                self.fail(job["id"], code, FAILURE_MESSAGES.get(code, "Ошибка анализа видео."))
            except Exception:
                logger.exception("Local analysis failed for job %s", job["id"])
                self.fail(job["id"], "analysis_failed", "Не удалось проанализировать видео.")
            finally:
                self.active_id = None
                self.deadline = None

    def fail(self, job_id: str, code: str, message: str) -> None:
        self.store.finish(job_id, "failed", {"code": code, "message": message})

    def stop(self) -> None:
        self.stopping.set()
        self.wakeup.set()
