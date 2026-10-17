import asyncio
import logging
import time
from pathlib import Path

from backend.app.schemas import AnalysisResult
from backend.app.store import JobStore
from inference.base import AnalysisRequest, Analyzer

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, store: JobStore, analyzer: Analyzer, budget_sec: float):
        self.store = store
        self.analyzer = analyzer
        self.budget_sec = budget_sec
        self.stopping = asyncio.Event()
        self.wakeup = asyncio.Event()

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
                self.fail(job["id"], "deadline_exceeded", "Истёк бюджет времени задачи.")
                continue
            deadline = time.monotonic() + remaining
            request = AnalysisRequest(Path(job["path"]), deadline)
            try:
                # One GPU job at a time; the HTTP event loop remains responsive.
                raw = await asyncio.to_thread(self.analyzer.analyze, request)
                result = AnalysisResult.model_validate(raw)
                if time.monotonic() > deadline:
                    self.fail(job["id"], "deadline_exceeded", "Истёк бюджет времени задачи.")
                else:
                    self.store.finish(job["id"], "done", result.model_dump(mode="json"))
            except Exception:
                logger.exception("Local analysis failed for job %s", job["id"])
                self.fail(job["id"], "analysis_failed", "Не удалось проанализировать видео.")

    def fail(self, job_id: str, code: str, message: str) -> None:
        self.store.finish(job_id, "failed", {"code": code, "message": message})

    def stop(self) -> None:
        self.stopping.set()
        self.wakeup.set()
