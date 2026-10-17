"""Human review (POST .../review), feedback.jsonl export and the act page."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from app.models import AnalysisResult, EvidenceFrame, Violation
from tests.test_api import HDR, TINY_JPEG, assert_contract, client_for, upload, wait_done

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


class ThreeViolations:
    name = "three"

    def analyze(self, video_path, progress_cb, deadline, partial):
        fr = [EvidenceFrame(timestamp_sec=1.0, image_base64=TINY_JPEG)]
        return AnalysisResult(violations=[
            Violation(type="T1", timecode_sec=1.0, end_sec=2.0, fact_conf=0.8, legal_conf=0.9,
                      legal_strength="high", evidence_frames=fr),
            Violation(type="T5", timecode_sec=1.5, fact_conf=0.6, evidence_frames=fr),
            Violation(type="T99", timecode_sec=2.5, obs="посторонний предмет", evidence_frames=fr),
        ])


class Blocking:
    name = "blocking"

    def __init__(self):
        self.release = threading.Event()

    def analyze(self, video_path, progress_cb, deadline, partial):
        self.release.wait(20)
        return AnalysisResult()


class NoViolations:
    name = "none"

    def analyze(self, video_path, progress_cb, deadline, partial):
        return AnalysisResult()


def done_job(c, test_video) -> str:
    job_id = upload(c, test_video).json()["job_id"]
    assert wait_done(c, job_id, timeout=60)["status"] == "done"
    return job_id


def post(c, job_id, body, ui=True, headers=None):
    if ui:
        return c.post(f"/ui-api/jobs/{job_id}/review", json=body, headers=headers or {})
    return c.post(f"/api/v1/jobs/{job_id}/review", json=body, headers=HDR if headers is None else headers)


# ---------- auth / errors ----------

def test_review_key_required_on_api_not_on_ui(tmp_path, test_video):
    with client_for(tmp_path, analyzer=ThreeViolations()) as c:
        job_id = done_job(c, test_video)
        assert post(c, job_id, {"reviewer_name": "x"}, ui=False, headers={}).status_code == 401
        assert post(c, job_id, {"reviewer_name": "x"}, ui=False, headers={"X-API-Key": "nope"}).status_code == 401
        r = post(c, job_id, {"reviewer_name": "Иванова А. А."}, ui=False)
        assert r.status_code == 200 and r.json()["review"]["reviewer_name"] == "Иванова А. А."
        r = post(c, job_id, {"reviewer_position": "контролёр"}, ui=True)
        assert r.status_code == 200
        body = r.json()
        assert body["review"]["reviewer_name"] == "Иванова А. А."  # merged, not replaced
        assert body["violations"][0]["clip_url"] is None or body["violations"][0]["clip_url"].startswith("/ui-api/")


def test_review_404_409_422(tmp_path, test_video):
    analyzer = Blocking()
    with client_for(tmp_path, analyzer=analyzer) as c:
        assert post(c, "nope", {"decision": "confirmed"}).status_code == 404
        assert post(c, "nope", {"decision": "confirmed"}, ui=False).status_code == 404
        job_id = upload(c, test_video).json()["job_id"]
        r = post(c, job_id, {"decision": "confirmed"})
        assert r.status_code == 409, r.text
        analyzer.release.set()
        assert wait_done(c, job_id, timeout=60)["status"] == "done"
        # no violations: any n is out of range
        assert post(c, job_id, {"violations": {"1": {"status": "confirmed"}}}).status_code == 422
        assert post(c, job_id, {"decision": "rejected"}).status_code == 200


@pytest.mark.parametrize("body", [
    {"violations": {"0": {"status": "confirmed"}}},
    {"violations": {"4": {"status": "confirmed"}}},
    {"violations": {"x": {"status": "confirmed"}}},
    {"violations": {"1": {"status": "maybe"}}},
    {"violations": {"1": {"status": None}}},
    {"violations": {"1": "confirmed"}},
    {"violations": [1, 2]},
    {"decision": "yes"},
    {"decision": 1},
    {"reviewer_name": 5},
    {"violations": {"1": {"comment": ["a"]}}},
    {"surprise": 1},
    ["not", "an", "object"],
])
def test_review_422(tmp_path, test_video, body):
    with client_for(tmp_path, analyzer=ThreeViolations()) as c:
        job_id = done_job(c, test_video)
        r = post(c, job_id, body)
        assert r.status_code == 422, r.text
        assert c.get(f"/ui-api/jobs/{job_id}").json()["review"] is None  # nothing written


def test_review_malformed_and_oversized_body(tmp_path, test_video):
    with client_for(tmp_path, analyzer=ThreeViolations()) as c:
        job_id = done_job(c, test_video)
        r = c.post(f"/ui-api/jobs/{job_id}/review", content=b"{not json", headers={"Content-Type": "application/json"})
        assert r.status_code == 422
        big = json.dumps({"decision_comment": "я" * 40000}).encode()  # > 64 KB as UTF-8
        r = c.post(f"/ui-api/jobs/{job_id}/review", content=big, headers={"Content-Type": "application/json"})
        assert r.status_code == 413


def test_review_deeply_nested_body_is_422_not_500(tmp_path, test_video):
    with client_for(tmp_path, analyzer=ThreeViolations()) as c:
        job_id = done_job(c, test_video)
        for body in (b"[" * 65000, b"[" * 32000 + b"]" * 32000, b'{"violations":' * 4000):
            r = c.post(f"/ui-api/jobs/{job_id}/review", content=body, headers={"Content-Type": "application/json"})
            assert r.status_code == 422, (r.status_code, r.text[:200])
        assert c.get(f"/ui-api/jobs/{job_id}").json()["review"] is None


def test_review_streamed_body_cap_without_content_length(tmp_path, test_video):
    with client_for(tmp_path, analyzer=ThreeViolations()) as c:
        job_id = done_job(c, test_video)

        def chunks():  # no Content-Length: httpx sends it chunked
            yield b'{"decision_comment": "'
            for _ in range(80):
                yield b"x" * 1024
            yield b'"}'

        r = c.post(f"/ui-api/jobs/{job_id}/review", content=chunks(), headers={"Content-Type": "application/json"})
        assert r.status_code == 413
        assert c.get(f"/ui-api/jobs/{job_id}").json()["review"] is None


@pytest.mark.parametrize("ctype", [None, "text/plain", "application/x-www-form-urlencoded",
                                   "multipart/form-data; boundary=x", "application/jsonx"])
def test_review_requires_json_content_type(tmp_path, test_video, ctype):
    with client_for(tmp_path, analyzer=ThreeViolations()) as c:
        job_id = done_job(c, test_video)
        body = b'{"decision": "confirmed"}'
        headers = {"Content-Type": ctype} if ctype else {}
        assert c.post(f"/ui-api/jobs/{job_id}/review", content=body, headers=headers).status_code == 415
        assert c.post(f"/api/v1/jobs/{job_id}/review", content=body, headers={**HDR, **headers}).status_code == 415
        assert c.get(f"/ui-api/jobs/{job_id}").json()["review"] is None
        ok = c.post(f"/ui-api/jobs/{job_id}/review", content=body,
                    headers={"Content-Type": "Application/JSON; charset=utf-8"})
        assert ok.status_code == 200 and ok.json()["review"]["decision"] == "confirmed"


# ---------- semantics ----------

def test_review_merge_caps_trims_timestamps(tmp_path, test_video):
    with client_for(tmp_path, analyzer=ThreeViolations()) as c:
        job_id = done_job(c, test_video)
        r = post(c, job_id, {"reviewer_name": "  Петров П. П.  ", "reviewer_position": "x" * 500,
                             "violations": {"1": {"status": "confirmed"}, "3": {"status": "rejected",
                                                                                "comment": "  " + "к" * 3000}}})
        assert r.status_code == 200, r.text
        rv = r.json()["review"]
        assert rv["reviewer_name"] == "Петров П. П."
        assert rv["reviewer_position"] == "x" * 200
        assert rv["violations"]["1"] == {"status": "confirmed", "comment": "", "updated_at": rv["updated_at"]}
        assert rv["violations"]["3"]["comment"] == "к" * 2000
        assert rv["decision"] is None and rv["decided_at"] is None
        assert rv["created_at"] and rv["updated_at"]

        # partial update: only the comment of #1 changes, its status and the rest stay
        rv2 = post(c, job_id, {"violations": {"1": {"comment": "вижу пачку"}}}).json()["review"]
        assert rv2["violations"]["1"]["status"] == "confirmed"
        assert rv2["violations"]["1"]["comment"] == "вижу пачку"
        assert rv2["violations"]["3"] == rv["violations"]["3"]
        assert rv2["reviewer_name"] == "Петров П. П." and rv2["created_at"] == rv["created_at"]

        rv3 = post(c, job_id, {"decision": "confirmed", "decision_comment": " ok "}).json()["review"]
        assert rv3["decision"] == "confirmed" and rv3["decided_at"] and rv3["decision_comment"] == "ok"
        rv4 = post(c, job_id, {"decision": None}).json()["review"]
        assert rv4["decision"] is None and rv4["decided_at"] is None
        assert rv4["decision_comment"] == "ok"

        # back to pending via explicit status; null text clears
        rv5 = post(c, job_id, {"violations": {"1": {"status": "pending", "comment": None}}}).json()["review"]
        assert rv5["violations"]["1"]["status"] == "pending" and rv5["violations"]["1"]["comment"] == ""


def test_public_job_contract_with_review(tmp_path, test_video):
    with client_for(tmp_path, analyzer=ThreeViolations()) as c:
        job_id = done_job(c, test_video)
        assert c.get(f"/api/v1/jobs/{job_id}", headers=HDR).json()["review"] is None
        post(c, job_id, {"reviewer_name": "А", "decision": "confirmed", "violations": {"2": {"status": "rejected"}}})
        body = c.get(f"/api/v1/jobs/{job_id}", headers=HDR).json()
        assert_contract(body)
        assert body["verdict"] == "violation" and len(body["violations"]) == 3  # review never changes the verdict
        assert body["review"]["decision"] == "confirmed"
        ui = c.get(f"/ui-api/jobs/{job_id}").json()
        assert ui["review"] == body["review"]


def test_review_persists_across_restart(tmp_path, test_video):
    with client_for(tmp_path, analyzer=ThreeViolations()) as c:
        job_id = done_job(c, test_video)
        rv = post(c, job_id, {"reviewer_name": "Сидоров", "violations": {"2": {"status": "confirmed"}}}).json()["review"]
    with client_for(tmp_path, analyzer=ThreeViolations()) as c2:
        assert c2.get(f"/api/v1/jobs/{job_id}", headers=HDR).json()["review"] == rv
        rv2 = post(c2, job_id, {"violations": {"3": {"status": "rejected"}}}).json()["review"]
        assert rv2["violations"]["2"]["status"] == "confirmed" and rv2["violations"]["3"]["status"] == "rejected"


def test_concurrent_reviews_do_not_lose_updates(tmp_path, test_video):
    with client_for(tmp_path, analyzer=ThreeViolations()) as c:
        job_id = done_job(c, test_video)
        errors = []

        def worker(i):
            n = str(i % 3 + 1)
            body = {"violations": {n: {"comment": f"c{i}"}}} if i >= 3 else {"violations": {n: {"status": "confirmed"}}}
            if i == 7:
                body["reviewer_name"] = "Конкурентный"
            try:
                r = post(c, job_id, body)
                if r.status_code != 200:
                    errors.append(r.text)
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(3, 12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        assert not errors, errors
        rv = c.get(f"/ui-api/jobs/{job_id}").json()["review"]
        assert [rv["violations"][n]["status"] for n in ("1", "2", "3")] == ["confirmed"] * 3
        assert all(rv["violations"][n]["comment"].startswith("c") for n in ("1", "2", "3"))
        assert rv["reviewer_name"] == "Конкурентный"
        # the persisted file agrees with memory
        saved = json.loads((tmp_path / "data" / "jobs" / f"{job_id}.json").read_text("utf-8"))
        assert saved["review"] == rv


def test_concurrent_writes_to_the_same_field(tmp_path, test_video):
    with client_for(tmp_path, analyzer=ThreeViolations()) as c:
        job_id = done_job(c, test_video)
        values = [f"значение-{i}" for i in range(16)]
        errors = []
        start = threading.Barrier(len(values))

        def worker(i):
            try:
                start.wait(30)
                # every thread writes the same comment field; statuses of #1..#3 spread across threads
                vio = {"1": {"comment": values[i]}}
                vio.setdefault(str(i % 3 + 1), {})["status"] = "confirmed"
                r = post(c, job_id, {"violations": vio})
                if r.status_code != 200:
                    errors.append(r.text)
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(values))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        assert not errors, errors
        rv = c.get(f"/ui-api/jobs/{job_id}").json()["review"]
        assert rv["violations"]["1"]["comment"] in values  # last writer wins, never a mix
        assert [rv["violations"][n]["status"] for n in ("1", "2", "3")] == ["confirmed"] * 3  # no lost update
        saved = json.loads((tmp_path / "data" / "jobs" / f"{job_id}.json").read_text("utf-8"))  # valid JSON
        assert saved["review"] == rv
        assert not list((tmp_path / "data" / "jobs").glob(".*.tmp"))  # no stray temp files


# ---------- feedback.jsonl ----------

def test_feedback_jsonl(tmp_path, test_video):
    with client_for(tmp_path, analyzer=ThreeViolations()) as c:
        a = done_job(c, test_video)
        b = done_job(c, test_video)
        done_job(c, test_video)  # never reviewed
        assert c.get("/api/v1/feedback.jsonl").status_code == 401
        r = c.get("/api/v1/feedback.jsonl", headers=HDR)
        assert r.status_code == 200 and r.text == ""
        post(c, b, {"reviewer_name": "Б", "violations": {"1": {"status": "rejected", "comment": "это ручка"}}})
        post(c, a, {"reviewer_name": "А", "reviewer_position": "кассир-контролёр", "decision": "confirmed",
                    "decision_comment": "есть", "violations": {
                        "1": {"status": "confirmed"}, "2": {"status": "rejected", "comment": "нет"},
                        "3": {"comment": "не смотрел"}}})
        for route, headers in (("/api/v1/feedback.jsonl", HDR), ("/ui-api/feedback.jsonl", {})):
            r = c.get(route, headers=headers)
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("application/x-ndjson")
            lines = [json.loads(x) for x in r.text.splitlines()]
            assert [(x["kind"], x["job_id"], x.get("n")) for x in lines] == [
                ("violation", a, 1), ("violation", a, 2), ("job", a, None), ("violation", b, 1)]
            v = lines[0]
            for k in ("filename", "video_meta", "type_id", "type", "timecode_sec", "start_sec", "end_sec",
                      "fact_conf", "legal_conf", "description", "status", "comment", "reviewer_name",
                      "reviewer_position", "updated_at"):
                assert k in v, k
            assert v["type_id"] == "T1" and v["status"] == "confirmed" and v["legal_strength"] == "high"
            assert v["reviewer_position"] == "кассир-контролёр" and v["video_meta"]["width"] == 320
            assert "evidence_frames" not in v and "image_base64" not in r.text
            assert lines[1]["comment"] == "нет"
            job = lines[2]
            assert job == {"kind": "job", "job_id": a, "filename": "clip.mov", "verdict": "violation",
                           "decision": "confirmed", "decision_comment": "есть", "reviewer_name": "А",
                           "reviewer_position": "кассир-контролёр", "decided_at": job["decided_at"],
                           "n_violations": 3, "n_confirmed": 1, "n_rejected": 1}
            assert job["decided_at"]
            assert lines[3]["comment"] == "это ручка"


def test_feedback_clean_job_decision(tmp_path, test_video):
    with client_for(tmp_path, analyzer=NoViolations()) as c:
        job_id = done_job(c, test_video)
        assert post(c, job_id, {"decision": "rejected", "reviewer_name": "В"}).status_code == 200
        lines = [json.loads(x) for x in c.get("/ui-api/feedback.jsonl").text.splitlines()]
        assert len(lines) == 1 and lines[0]["kind"] == "job" and lines[0]["verdict"] == "no_violation"
        assert lines[0]["n_violations"] == 0


# ---------- static ----------

def test_act_page_served(tmp_path):
    with client_for(tmp_path, analyzer=NoViolations()) as c:
        r = c.get("/act.html")
        assert r.status_code == 200 and "act.js" in r.text and "act.css" in r.text
        assert c.get("/act.js").status_code == 200 and c.get("/act.css").status_code == 200
    js = (STATIC / "act.js").read_text("utf-8")
    assert "innerHTML" not in js.replace("never innerHTML", "")
    for text in ("Акт проверки соблюдения кассовой дисциплины", "ЧЕРНОВИК", "Подтверждённые нарушения",
                 "Отклонённые срабатывания", "Заключение",
                 "Результат автоматического анализа является подсказкой для проверки человеком и не является "
                 "окончательным юридическим или дисциплинарным заключением",
                 "распознавание лиц не применялось"):
        assert text in js or text in r.text, text
    app_js = (STATIC / "app.js").read_text("utf-8")
    for text in ("Проверка специалистом", "Сформировать акт", "Укажите ФИО проверяющего", "/review"):
        assert text in app_js or text in (STATIC / "index.html").read_text("utf-8"), text


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("name", ["app.js", "act.js"])
def test_js_syntax(name):
    r = subprocess.run(["node", "--check", str(STATIC / name)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
