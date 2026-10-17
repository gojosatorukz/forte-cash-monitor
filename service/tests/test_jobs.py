"""JobStore persistence/recovery and worker deadline arithmetic (no HTTP)."""

from __future__ import annotations

import json

from app.jobs import JobStore, sanitize_filename
from app.worker import MIN_BUDGET_SEC, compute_deadline


def test_store_create_get_update_persist(tmp_path):
    store = JobStore(tmp_path / "jobs")
    job = store.create(filename="a.mov", upload_path=str(tmp_path / "a.mov"))
    assert store.get(job.job_id).status == "processing"
    path = tmp_path / "jobs" / f"{job.job_id}.json"
    assert json.loads(path.read_text("utf-8"))["filename"] == "a.mov"

    store.update(job.job_id, status="done", progress=1.0)
    assert json.loads(path.read_text("utf-8"))["status"] == "done"
    # in-memory-only update is visible but not persisted
    store.update(job.job_id, persist=False, stage="x")
    assert store.get(job.job_id).stage == "x"
    assert json.loads(path.read_text("utf-8"))["stage"] != "x"
    assert not list((tmp_path / "jobs").glob("*.tmp"))


def test_store_get_unknown_and_hostile_ids(tmp_path):
    store = JobStore(tmp_path / "jobs")
    assert store.get("nope") is None
    assert store.get("../../etc/passwd") is None


def test_store_list_newest_first_and_limit(tmp_path):
    store = JobStore(tmp_path / "jobs")
    ids = [store.create(filename=f"{i}.mov", upload_path="x").job_id for i in range(25)]
    listed = [j.job_id for j in store.list(limit=20)]
    assert listed == list(reversed(ids))[:20]
    # order survives a reload from disk
    store2 = JobStore(tmp_path / "jobs")
    assert [j.job_id for j in store2.list(limit=20)] == listed


def test_store_skips_corrupt_files(tmp_path):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "broken.json").write_text("{not json", "utf-8")
    store = JobStore(jobs)
    assert store.list() == []


def test_store_recovery(tmp_path):
    store = JobStore(tmp_path / "jobs")
    upload = tmp_path / "v.mov"
    upload.write_bytes(b"x")
    alive = store.create(filename="v.mov", upload_path=str(upload))
    lost = store.create(filename="w.mov", upload_path=str(tmp_path / "missing.mov"))
    done = store.create(filename="d.mov", upload_path=str(upload))
    store.update(done.job_id, status="done")

    store2 = JobStore(tmp_path / "jobs")
    requeue = store2.recover()
    assert requeue == [alive.job_id]
    assert store2.get(alive.job_id).status == "processing"
    assert store2.get(alive.job_id).progress == 0.0
    lost2 = store2.get(lost.job_id)
    assert lost2.status == "failed" and "restart" in lost2.error
    assert store2.get(done.job_id).status == "done"


def test_sanitize_filename():
    assert sanitize_filename("Х-10.mov") == "Х-10.mov"
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("C:\\Users\\me\\Видео 1.MOV") == "Видео 1.MOV"
    assert sanitize_filename("a<b>|c?.mp4") == "a_b_c_.mp4"
    assert sanitize_filename("") == "video"
    assert sanitize_filename(None) == "video"
    assert sanitize_filename("...") == "video"
    assert sanitize_filename(".mov") == "video.mov"
    long = sanitize_filename("я" * 500 + ".mov")
    assert long.endswith(".mov") and len(long) <= 150
    assert "\x00" not in sanitize_filename("a\x00b.mov")


def test_compute_deadline():
    # fresh job: full budget from submission
    assert compute_deadline(submitted=100.0, now=100.0, timeout=270.0) == 370.0
    # queue wait counts against the budget
    assert compute_deadline(submitted=100.0, now=200.0, timeout=270.0) == 370.0
    # but a job that waited too long still gets the minimum budget
    assert compute_deadline(submitted=100.0, now=360.0, timeout=270.0) == 360.0 + MIN_BUDGET_SEC
    # tiny test timeouts are not inflated to the minimum
    assert compute_deadline(submitted=0.0, now=5.0, timeout=0.5) == 5.5


def test_compute_deadline_hard_cap():
    # the absolute ceiling from submission always wins
    assert compute_deadline(submitted=100.0, now=360.0, timeout=270.0, hard_cap=290.0) == 380.0
    assert compute_deadline(submitted=100.0, now=380.0, timeout=270.0, hard_cap=290.0) == 390.0
    assert compute_deadline(submitted=100.0, now=100.0, timeout=270.0, hard_cap=290.0) == 370.0
    assert compute_deadline(submitted=0.0, now=2.0, timeout=1.0, hard_cap=2.5) == 2.5
