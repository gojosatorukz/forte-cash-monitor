import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID


class QueueFull(Exception):
    pass


class JobStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.uploads = self.root / "uploads"
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "jobs.sqlite3"

    @contextmanager
    def connection(self):
        with sqlite3.connect(self.db_path, timeout=5) as con:
            con.row_factory = sqlite3.Row
            yield con

    def initialize(self) -> None:
        with self.connection() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute(
                """CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, filename TEXT NOT NULL, path TEXT NOT NULL,
                status TEXT NOT NULL, created_at REAL NOT NULL,
                started_at REAL, payload TEXT, finished_at REAL, stage TEXT DEFAULT 'queued',
                size_bytes INTEGER DEFAULT 0)"""
            )
            columns = {row[1] for row in con.execute("PRAGMA table_info(jobs)")}
            for name, declaration in {
                "finished_at": "REAL",
                "stage": "TEXT DEFAULT 'queued'",
                "size_bytes": "INTEGER DEFAULT 0",
            }.items():
                if name not in columns:
                    con.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")
            payload = json.dumps(
                {
                    "code": "service_restarted",
                    "message": "Задача прервана перезапуском сервера. Загрузите видео повторно.",
                }
            )
            con.execute(
                "UPDATE jobs SET status='failed', stage='failed', payload=?, finished_at=? "
                "WHERE status='processing'",
                (payload, time.time()),
            )
            con.execute(
                "UPDATE jobs SET finished_at=created_at, stage=status "
                "WHERE status!='processing' AND finished_at IS NULL"
            )

    def create(
        self,
        job_id: UUID,
        filename: str,
        path: Path,
        max_active: int,
        created_at: float | None = None,
    ) -> None:
        size = path.stat().st_size if path.exists() else 0
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            count = con.execute("SELECT count(*) FROM jobs WHERE status='processing'").fetchone()[0]
            if count >= max_active:
                raise QueueFull
            con.execute(
                "INSERT INTO jobs (id, filename, path, status, created_at, stage, size_bytes) "
                "VALUES (?, ?, ?, 'processing', ?, 'queued', ?)",
                (str(job_id), filename, str(path), created_at or time.time(), size),
            )

    def claim_next(self) -> dict | None:
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT * FROM jobs WHERE status='processing' AND started_at IS NULL "
                "ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            con.execute(
                "UPDATE jobs SET started_at=?, stage='analyzing' WHERE id=?",
                (time.time(), row["id"]),
            )
            return dict(row)

    def progress(self, job_id: str, stage: str) -> None:
        if stage not in {"decoding", "scanning", "verifying", "evidence"}:
            raise ValueError("Unknown analysis stage")
        with self.connection() as con:
            con.execute(
                "UPDATE jobs SET stage=? WHERE id=? AND status='processing'", (stage, job_id)
            )

    def finish(self, job_id: str, status: str, payload: dict) -> None:
        with self.connection() as con:
            con.execute(
                "UPDATE jobs SET status=?, stage=?, payload=?, finished_at=? "
                "WHERE id=? AND status='processing'",
                (status, status, json.dumps(payload, ensure_ascii=False), time.time(), job_id),
            )

    def get(self, job_id: UUID | str) -> dict | None:
        with self.connection() as con:
            row = con.execute("SELECT * FROM jobs WHERE id=?", (str(job_id),)).fetchone()
            return dict(row) if row else None

    def list_jobs(self, limit: int, offset: int) -> tuple[list[dict], int]:
        with self.connection() as con:
            # History never reads base64 images into the HTTP event loop.
            rows = con.execute(
                "SELECT id, filename, status, stage, created_at, started_at, finished_at, "
                "size_bytes, json_extract(payload, '$.verdict') AS verdict, "
                "json_array_length(json_extract(payload, '$.violations')) AS event_count "
                "FROM jobs ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            total = con.execute("SELECT count(*) FROM jobs").fetchone()[0]
            return [dict(row) for row in rows], total

    def queue_depth(self) -> int:
        with self.connection() as con:
            return con.execute("SELECT count(*) FROM jobs WHERE status='processing'").fetchone()[0]

    def expire(self, budget_sec: float) -> None:
        payload = json.dumps(
            {"code": "deadline_exceeded", "message": "Истёк бюджет времени задачи."}
        )
        with self.connection() as con:
            con.execute(
                "UPDATE jobs SET status='failed', stage='failed', finished_at=?, payload=? "
                "WHERE status='processing' AND created_at<=?",
                (time.time(), payload, time.time() - budget_sec),
            )

    def prune(self, retention_hours: int, protected_id: str | None = None) -> None:
        with self.connection() as con:
            rows = con.execute(
                "SELECT id, path FROM jobs WHERE status!='processing' AND finished_at<?",
                (time.time() - retention_hours * 3600,),
            ).fetchall()
            for row in rows:
                if row["id"] == protected_id:
                    continue
                self._remove_upload(row["path"])
                con.execute("DELETE FROM jobs WHERE id=?", (row["id"],))

    def _remove_upload(self, path: str) -> None:
        candidate = Path(path).resolve()
        if candidate.parent != self.uploads:
            # Never unlink an arbitrary path from a corrupt/migrated database.
            return
        candidate.unlink(missing_ok=True)
