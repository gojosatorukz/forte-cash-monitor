"""Job store: in-memory index + atomic JSON persistence in DATA_DIR/jobs, restart recovery."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Callable

from app.models import JobRecord, now_iso

log = logging.getLogger(__name__)

RESTART_ERROR = "Обработка прервана перезапуском сервиса, файл видео не найден (interrupted by restart)"
_MAX_NAME_LEN = 150
_ALLOWED_PUNCT = set(" ._-()[]+,")


def new_job_id() -> str:
    return uuid.uuid4().hex


def sanitize_filename(name: str | None, default: str = "video") -> str:
    """Basename only, letters/digits of any script (Cyrillic kept), a few safe punctuation
    characters, everything else -> '_'. Keeps the extension and caps the length."""
    if not name:
        return default
    name = unicodedata.normalize("NFC", str(name))
    name = re.split(r"[\\/]", name)[-1]
    cleaned = "".join(ch if (ch.isalnum() or ch in _ALLOWED_PUNCT) else "_" for ch in name)
    cleaned = re.sub(r"_+", "_", cleaned).strip(" ")
    stem, dot, ext = cleaned.rpartition(".")
    if not dot:
        stem, ext = cleaned, ""
    stem = stem.strip(" .")
    ext = "".join(ch for ch in ext if ch.isalnum())[:16]
    room = _MAX_NAME_LEN - (len(ext) + 1 if ext else 0)
    stem = stem[:room].rstrip(" .") or default
    return f"{stem}.{ext}" if ext else stem


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class JobStore:
    """Thread-safe. Records are kept in memory (insertion order = creation order) and
    persisted as DATA_DIR/jobs/<job_id>.json on every persisting update."""

    def __init__(self, jobs_dir: Path):
        self.jobs_dir = Path(jobs_dir)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._jobs: dict[str, JobRecord] = {}
        self._seq: dict[str, int] = {}
        self._next_seq = 1
        self._load()

    # ---------- persistence ----------

    def _path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    def _load(self) -> None:
        loaded: list[tuple[str, int, JobRecord]] = []
        for p in self.jobs_dir.glob("*.json"):
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                rec = JobRecord.model_validate(raw)
                seq = int(raw.get("seq", 0))
            except Exception as e:  # noqa: BLE001 - never let one bad file break startup
                log.warning("Skipping unreadable job file %s: %s", p, e)
                continue
            loaded.append((rec.created_at, seq, rec))
        # created_at has second precision; the persisted sequence number breaks ties
        loaded.sort(key=lambda x: (x[0], x[1]))
        for _, seq, rec in loaded:
            self._jobs[rec.job_id] = rec
            self._seq[rec.job_id] = seq
            self._next_seq = max(self._next_seq, seq + 1)

    def _save(self, rec: JobRecord) -> None:
        data = rec.model_dump()
        data["seq"] = self._seq.get(rec.job_id, 0)
        atomic_write_json(self._path(rec.job_id), data)

    # ---------- API ----------

    def create(self, filename: str | None, upload_path: str | None, job_id: str | None = None) -> JobRecord:
        rec = JobRecord(job_id=job_id or new_job_id(), filename=filename, upload_path=upload_path,
                        stage="В очереди")
        with self._lock:
            self._jobs[rec.job_id] = rec
            self._seq[rec.job_id] = self._next_seq
            self._next_seq += 1
            self._save(rec)
        return rec.model_copy()

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            rec = self._jobs.get(job_id)
            return rec.model_copy(deep=True) if rec is not None else None

    def list(self, limit: int | None = None) -> list[JobRecord]:
        with self._lock:
            recs = list(self._jobs.values())[::-1]
            if limit is not None:
                recs = recs[:limit]
            return [r.model_copy(deep=True) for r in recs]

    def update(self, job_id: str, persist: bool = True, **fields: Any) -> JobRecord | None:
        with self._lock:
            rec = self._jobs.get(job_id)
            if rec is None:
                return None
            rec = rec.model_copy(update=fields)
            self._jobs[job_id] = rec
            if persist:
                self._save(rec)
            return rec.model_copy(deep=True)

    def mutate(self, job_id: str, fn: Callable[[JobRecord], dict[str, Any]]) -> JobRecord | None:
        """Read-modify-write under the lock: `fn(copy of the record)` returns the fields to set
        (it may raise to abort; nothing is written then). None if the job does not exist."""
        with self._lock:
            rec = self._jobs.get(job_id)
            if rec is None:
                return None
            fields = fn(rec.model_copy(deep=True))
            rec = rec.model_copy(update=fields)
            self._save(rec)  # disk first: a failed write leaves the in-memory record unchanged
            self._jobs[job_id] = rec
            return rec.model_copy(deep=True)

    def persist(self, job_id: str) -> None:
        with self._lock:
            rec = self._jobs.get(job_id)
            if rec is not None:
                self._save(rec)

    def recover(self) -> list[str]:
        """After a restart: jobs still `processing` are re-queued (oldest first) if their
        upload exists, otherwise marked failed. Returns job ids to enqueue."""
        requeue: list[str] = []
        for rec in self.list()[::-1]:
            if rec.status != "processing":
                continue
            if rec.upload_path and Path(rec.upload_path).is_file():
                self.update(rec.job_id, progress=0.0, stage="В очереди (после перезапуска)")
                requeue.append(rec.job_id)
            else:
                self.update(rec.job_id, status="failed", error=RESTART_ERROR, stage="Ошибка",
                            finished_at=now_iso())
        return requeue
