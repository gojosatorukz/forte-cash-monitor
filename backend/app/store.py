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
                started_at REAL, payload TEXT)"""
            )
            # A restarted process cannot pretend an interrupted GPU task completed.
            payload = json.dumps(
                {
                    "code": "service_restarted",
                    "message": "Задача прервана перезапуском сервера. Загрузите видео повторно.",
                }
            )
            con.execute(
                "UPDATE jobs SET status='failed', payload=? WHERE status='processing'",
                (payload,),
            )

    def create(self, job_id: UUID, filename: str, path: Path, max_active: int) -> None:
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            count = con.execute("SELECT count(*) FROM jobs WHERE status='processing'").fetchone()[0]
            if count >= max_active:
                raise QueueFull
            con.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, 'processing', ?, NULL, NULL)",
                (str(job_id), filename, str(path), time.time()),
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
            con.execute("UPDATE jobs SET started_at=? WHERE id=?", (time.time(), row["id"]))
            return dict(row)

    def finish(self, job_id: str, status: str, payload: dict) -> None:
        with self.connection() as con:
            con.execute(
                "UPDATE jobs SET status=?, payload=? WHERE id=? AND status='processing'",
                (status, json.dumps(payload, ensure_ascii=False), job_id),
            )

    def get(self, job_id: UUID) -> dict | None:
        with self.connection() as con:
            row = con.execute("SELECT * FROM jobs WHERE id=?", (str(job_id),)).fetchone()
            return dict(row) if row else None
