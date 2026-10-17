"""HTTP contract tests (FastAPI TestClient, real worker thread, generated clips)."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import AnalysisResult, EvidenceFrame, Violation
from app.rules import BUNDLED_RULES_PATH

KEY = "test-key"
HDR = {"X-API-Key": KEY}
UI_PASS = "test-ui-pass"
TINY_JPEG = base64.b64encode(b"\xff\xd8\xff\xe0fake").decode()


def make_settings(tmp_path: Path, **kw) -> Settings:
    # Pinned to the bundled rules so tests don't depend on ../detector or ../docs files.
    base = dict(api_key=KEY, api_key_is_default=False, data_dir=tmp_path / "data",
                job_timeout_sec=30.0, stub_debug=False, rules_path=str(BUNDLED_RULES_PATH),
                ui_password=UI_PASS)
    if "analyzer_path_override" in kw:
        kw["analyzer"] = kw.pop("analyzer_path_override")
    base.update(kw)
    return Settings(**base)


@contextlib.contextmanager
def client_for(tmp_path: Path, analyzer=None, login=True, **kw):
    """TestClient for the app; `login=True` signs in through the UI gate (session cookie kept by the client)."""
    app = create_app(make_settings(tmp_path, **kw), analyzer=analyzer)
    with TestClient(app) as c:
        if login:
            r = c.post("/login", data={"password": UI_PASS}, follow_redirects=False)
            assert r.status_code == 303, r.text
        yield c


def upload(c: TestClient, video: Path, route="/api/v1/analyze", headers=HDR, name=None):
    with open(video, "rb") as f:
        return c.post(route, files={"file": (name or video.name, f, "video/quicktime")}, headers=headers)


def wait_done(c: TestClient, job_id: str, timeout=20.0, route="/ui-api/jobs/") -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        r = c.get(route + job_id, headers=HDR)
        assert r.status_code == 200, r.text
        body = r.json()
        if body["status"] != "processing":
            return body
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} still processing after {timeout}s")


def assert_contract(body: dict) -> None:
    for k in ("job_id", "status", "verdict", "violations"):
        assert k in body
    assert isinstance(body["job_id"], str)
    assert body["status"] in ("processing", "done", "failed")
    assert body["verdict"] in ("violation", "no_violation", None)
    assert isinstance(body["violations"], list)
    if body["status"] != "done":
        assert body["verdict"] is None and body["violations"] == []
    else:
        assert body["verdict"] == ("violation" if body["violations"] else "no_violation")
    if body["status"] == "failed":
        assert isinstance(body["error"], str) and body["error"]
    for v in body["violations"]:
        assert isinstance(v["type"], str)
        assert isinstance(v["timecode_sec"], float)
        assert isinstance(v["description"], str)
        assert isinstance(v["evidence_frames"], list)
        for fr in v["evidence_frames"]:
            assert isinstance(fr["timestamp_sec"], float)
            assert isinstance(fr["image_base64"], str)
            assert not fr["image_base64"].startswith("data:")
            raw = base64.b64decode(fr["image_base64"], validate=True)
            assert raw[:3] == b"\xff\xd8\xff"


# ---------- test analyzers ----------

class RaisingAnalyzer:
    name = "raising"

    def analyze(self, video_path, progress_cb, deadline, partial):
        progress_cb(0.1, "x")
        raise ValueError("boom from analyzer")


class SlowAnalyzer:
    """Adds one partial violation, then ignores the deadline for `sleep` seconds and finally
    returns a *different* result plus late progress calls (must all be ignored)."""

    name = "slow"

    def __init__(self, sleep=3.0):
        self.sleep = sleep
        self.finished = threading.Event()

    def analyze(self, video_path, progress_cb, deadline, partial):
        partial.add(Violation(type="T1", timecode_sec=1.0, confidence=0.7,
                              evidence_frames=[EvidenceFrame(timestamp_sec=1.0, image_base64=TINY_JPEG)]))
        progress_cb(0.3, "slow")
        time.sleep(self.sleep)
        progress_cb(0.9, "late progress from orphan")
        self.finished.set()
        return AnalysisResult(violations=[Violation(type="T2", timecode_sec=2.0)] * 3)


class MessyAnalyzer:
    """Returns a mix of valid and malformed violations (dicts and models)."""

    name = "messy"

    def analyze(self, video_path, progress_cb, deadline, partial):
        return AnalysisResult.model_construct(
            verdict="no_violation",  # wrong on purpose: the service recomputes it
            meta={},
            violations=[
                {"type": "T3", "timecode_sec": 1, "confidence": 7,
                 "rule": {"document": "FAKE", "point": "1", "quote": "q", "url": "http://evil"},
                 "evidence_frames": [{"timestamp_sec": 1, "image_base64": "data:image/jpeg;base64," + TINY_JPEG}]},
                {"type": "made_up_type", "timecode_sec": 2.5, "description": "своё описание"},
                {"timecode_sec": 3.0},  # no type -> dropped
                {"type": "T1", "timecode_sec": float("nan")},  # non-finite -> dropped
                "garbage",  # not a violation at all -> dropped
            ],
        )


class PartialThenRaiseAnalyzer:
    """Confirms one violation, then crashes."""

    name = "partial-raise"

    def analyze(self, video_path, progress_cb, deadline, partial):
        partial.add(Violation(type="T3", timecode_sec=1.5, confidence=0.6,
                              evidence_frames=[EvidenceFrame(timestamp_sec=1.5, image_base64=TINY_JPEG)]))
        raise RuntimeError("cuda out of memory")


class MetaAnalyzer:
    name = "meta"

    def __init__(self, meta):
        self.meta = meta

    def analyze(self, video_path, progress_cb, deadline, partial):
        return AnalysisResult(violations=[], meta=self.meta)


class CooperativeSlowAnalyzer:
    """Ignores the deadline but honours `partial.cancelled` (polls it)."""

    name = "coop"

    def __init__(self):
        self.saw_cancel = threading.Event()

    def analyze(self, video_path, progress_cb, deadline, partial):
        end = time.monotonic() + 10
        while time.monotonic() < end:
            if partial.cancelled:
                self.saw_cancel.set()
                return AnalysisResult()
            time.sleep(0.01)
        return AnalysisResult()


class NoneAnalyzer:
    name = "none"

    def analyze(self, video_path, progress_cb, deadline, partial):
        return None


# ---------- health / auth ----------

def test_healthz(tmp_path):
    with client_for(tmp_path) as c:
        assert c.app.state.worker.ready.wait(5)
        r = c.get("/healthz")
        assert r.status_code == 200
        body = r.json()
        rules = c.app.state.rules
        assert body == {"ok": True, "analyzer_ready": True, "analyzer_error": None,
                        "public_url": None, "public_url_source": None,
                        "rules_path": "app/rules.json", "rules_count": len(rules.rules),
                        "vlm_ok": None, "vlm_url": None}
        assert body["rules_count"] >= 1


def test_healthz_public_url_read_per_request(tmp_path):
    with client_for(tmp_path) as c:
        assert c.get("/healthz").json()["public_url"] is None
        url_file = c.app.state.settings.public_url_path
        assert url_file == tmp_path / "data" / "public_url.txt"
        url_file.write_text("https://abc-def.trycloudflare.com\n", encoding="utf-8")
        body = c.get("/healthz").json()
        assert body["public_url"] == "https://abc-def.trycloudflare.com"
        assert body["public_url_source"] == "quick-tunnel"
        url_file.write_text("https://new-one.trycloudflare.com\n", encoding="utf-8")  # tunnel restarted
        assert c.get("/healthz").json()["public_url"] == "https://new-one.trycloudflare.com"


def test_healthz_public_hostname_env_wins(tmp_path):
    with client_for(tmp_path, public_hostname="forte.example.kz") as c:
        c.app.state.settings.public_url_path.write_text("https://stale.trycloudflare.com\n", encoding="utf-8")
        body = c.get("/healthz").json()
        assert body["public_url"] == "https://forte.example.kz"
        assert body["public_url_source"] == "env"


def test_public_url_file_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLIC_URL_FILE", str(tmp_path / "url.txt"))
    assert Settings.from_env().public_url_path == tmp_path / "url.txt"
    monkeypatch.delenv("PUBLIC_URL_FILE")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "d"))
    assert Settings.from_env().public_url_path == tmp_path / "d" / "public_url.txt"
    assert Settings.from_env().public_hostname == ""
    monkeypatch.setenv("PUBLIC_HOSTNAME", " forte.example.kz ")
    assert Settings.from_env().public_hostname == "forte.example.kz"


def test_healthz_reports_analyzer_load_error(tmp_path):
    with client_for(tmp_path, analyzer_path_override="no.such.module:Nope") as c:
        assert c.app.state.worker.ready.wait(5)
        body = c.get("/healthz").json()
        assert body["ok"] is True and body["analyzer_ready"] is False
        assert "no.such.module" in body["analyzer_error"]


def test_head_supported_on_get_routes(tmp_path, test_video):
    with client_for(tmp_path) as c:
        job_id = upload(c, test_video).json()["job_id"]
        wait_done(c, job_id)
        assert c.head("/healthz").status_code == 200
        assert c.head(f"/api/v1/jobs/{job_id}", headers=HDR).status_code == 200
        assert c.head(f"/api/v1/jobs/{job_id}").status_code == 401  # auth still enforced
        assert c.head("/api/v1/jobs/nope", headers=HDR).status_code == 404
        assert c.head(f"/ui-api/jobs/{job_id}").status_code == 200
        r = c.head(f"/ui-api/jobs/{job_id}/video")
        assert r.status_code == 200 and r.headers["content-type"] == "video/quicktime"
        assert c.head("/ui-api/jobs/nope/video").status_code == 404


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}, {"X-API-Key": ""}])
def test_public_routes_require_key(tmp_path, test_video, headers):
    with client_for(tmp_path) as c:
        r = upload(c, test_video, headers=headers)
        assert r.status_code == 401
        assert r.headers["content-type"].startswith("application/json")
        assert "detail" in r.json()
        r = c.get("/api/v1/jobs/whatever", headers=headers)
        assert r.status_code == 401 and "detail" in r.json()


def test_unknown_job_404(tmp_path):
    with client_for(tmp_path) as c:
        r = c.get("/api/v1/jobs/does-not-exist", headers=HDR)
        assert r.status_code == 404 and "detail" in r.json()
        assert c.get("/ui-api/jobs/does-not-exist").status_code == 404
        assert c.get("/ui-api/jobs/does-not-exist/video").status_code == 404


# ---------- happy paths ----------

def test_analyze_and_poll_until_done_no_violation(tmp_path, test_video):
    with client_for(tmp_path) as c:
        r = upload(c, test_video)
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        assert isinstance(job_id, str) and job_id

        first = c.get(f"/api/v1/jobs/{job_id}", headers=HDR).json()
        assert_contract(first)

        body = wait_done(c, job_id, route="/api/v1/jobs/")
        assert_contract(body)
        assert body["status"] == "done" and body["verdict"] == "no_violation"
        assert body["violations"] == [] and body["partial"] is False
        assert body["error"] is None
        assert body["progress"] == 1.0
        assert body["video_meta"]["width"] == 320 and body["video_meta"]["height"] == 240
        assert body["filename"] == "clip.mov"
        assert body["created_at"] and body["finished_at"]
        assert "upload_path" not in body
        # result is persisted to disk as JSON
        saved = json.loads((tmp_path / "data" / "jobs" / f"{job_id}.json").read_text("utf-8"))
        assert saved["status"] == "done"


def test_stub_debug_violation_with_rule_from_kb(tmp_path, cyrillic_video):
    with client_for(tmp_path, stub_debug=True) as c:
        rules = c.app.state.rules
        r = upload(c, cyrillic_video)
        assert r.status_code == 200
        body = wait_done(c, r.json()["job_id"], route="/api/v1/jobs/")
        assert_contract(body)
        assert body["verdict"] == "violation"
        assert len(body["violations"]) == 1
        v = body["violations"][0]
        assert v["type_id"] == "T99"
        assert v["type"] == v["title_ru"] == rules.rules["T99"].title_ru
        assert v["rules"] == [c.model_dump() for c in rules.rules["T99"].citations]
        assert v["rule"] == (v["rules"][0] if v["rules"] else None)  # T99: no citation, never invented
        assert v["start_sec"] == v["timecode_sec"] and v["end_sec"] > v["timecode_sec"]
        assert v["fact_conf"] == 0.5
        # T99 template wins; the demo label survives through obs, the raw text in analyzer_description
        assert v["description"].startswith("Наблюдение: на ") and "ДЕМО / ТЕСТ" in v["description"]
        assert "{" not in v["description"]
        assert "ДЕМО / ТЕСТ" in v["analyzer_description"]
        # 2 stub frames, the first boxed -> [annotated, same frame raw, second frame]
        assert [f["annotated"] for f in v["evidence_frames"]] == [True, False, False]
        assert v["clip_url"] == f"/api/v1/jobs/{body['job_id']}/clips/1.mp4"
        assert 0.0 <= v["confidence"] <= 1.0
        assert body["filename"] == "Х-тест видео.mov"
        stored = list((tmp_path / "data" / "uploads" / body["job_id"]).iterdir())
        assert [p.name for p in stored] == ["Х-тест видео.mov"]


def test_messy_analyzer_output_is_sanitized_not_failed(tmp_path, test_video):
    with client_for(tmp_path, analyzer=MessyAnalyzer()) as c:
        body = wait_done(c, upload(c, test_video).json()["job_id"])
        assert body["status"] == "done", body
        assert_contract(body)
        assert body["verdict"] == "violation"
        types = [v["type_id"] for v in body["violations"]]
        assert types == ["T3", "made_up_type"]
        first, second = body["violations"]
        assert first["type"] == c.app.state.rules.rules["T3"].title_ru
        assert first["confidence"] == 1.0
        assert first["rule"]["document"] != "FAKE"  # citations only from rules.json
        assert first["evidence_frames"][0]["image_base64"] == TINY_JPEG
        assert second["description"] == "своё описание"
        assert second["type"] == "made_up_type"
        assert second["rule"] is None and second["rules"] == []  # unknown id: passed through, no citation


def test_analyzer_returning_none_is_done_without_violations(tmp_path, test_video):
    with client_for(tmp_path, analyzer=NoneAnalyzer()) as c:
        body = wait_done(c, upload(c, test_video).json()["job_id"])
        assert body["status"] == "done" and body["verdict"] == "no_violation"


# ---------- failures ----------

def test_analyzer_exception_marks_failed(tmp_path, test_video):
    with client_for(tmp_path, analyzer=RaisingAnalyzer()) as c:
        job_id = upload(c, test_video).json()["job_id"]
        body = wait_done(c, job_id, route="/api/v1/jobs/")
        assert_contract(body)
        assert body["status"] == "failed" and body["verdict"] is None
        assert "boom from analyzer" in body["error"]


def test_analyzer_exception_after_partial_finding_is_done_partial(tmp_path, test_video):
    with client_for(tmp_path, analyzer=PartialThenRaiseAnalyzer()) as c:
        body = wait_done(c, upload(c, test_video).json()["job_id"], route="/api/v1/jobs/")
        assert_contract(body)
        assert body["status"] == "done" and body["partial"] is True and body["error"] is None
        assert body["verdict"] == "violation"
        assert [v["type_id"] for v in body["violations"]] == ["T3"]
        assert body["violations"][0]["rule"]["document"]  # enriched from rules.json
        assert "cuda out of memory" in body["meta"]["analyzer_error"]
        assert body["warnings"] == [WARN_CRASH]


def test_analyzer_meta_is_stored_json_safe(tmp_path, test_video):
    meta = {"model": "yolo", "score": float("nan"), "path": Path("/x"), "n": 3}
    with client_for(tmp_path, analyzer=MetaAnalyzer(meta)) as c:
        body = wait_done(c, upload(c, test_video).json()["job_id"])
        assert body["status"] == "done"
        assert body["meta"]["analyzer"] == {"model": "yolo", "score": None, "path": "/x", "n": 3}


def test_oversized_analyzer_meta_is_dropped(tmp_path, test_video):
    with client_for(tmp_path, analyzer=MetaAnalyzer({"blob": "x" * 20000})) as c:
        body = wait_done(c, upload(c, test_video).json()["job_id"])
        assert body["status"] == "done" and "analyzer" not in body["meta"]
        assert body["warnings"] == []


# ---------- warnings ----------

NO_VLM = "анализ выполнен без VLM-проверок, результат может быть неполным"
WARN_TIMEOUT = "анализ прерван по времени, результат может быть неполным"
WARN_CLIPS_TIME_1 = "не хватило времени на нарезку клипов № 1"
WARN_CRASH = "анализ прерван из-за ошибки, результат может быть неполным"


class WarningAnalyzer:
    name = "warn"

    def __init__(self, result):
        self.result = result

    def analyze(self, video_path, progress_cb, deadline, partial):
        return self.result


def test_warnings_from_result_and_notes_ru(tmp_path, test_video):
    res = AnalysisResult(warnings=["из результата", NO_VLM], meta={"notes_ru": [NO_VLM, "вторая заметка"]})
    with client_for(tmp_path, analyzer=WarningAnalyzer(res)) as c:
        job_id = upload(c, test_video).json()["job_id"]
        body = wait_done(c, job_id, route="/api/v1/jobs/")
        assert body["status"] == "done"
        assert body["warnings"] == ["из результата", NO_VLM, "вторая заметка"]  # deduped, order kept
        assert c.get("/ui-api/jobs/" + job_id).json()["warnings"] == body["warnings"]
        assert c.get("/ui-api/jobs").json()["jobs"][0]["warnings"] == body["warnings"]


@pytest.mark.parametrize("flag", ["stopped_early", "partial"])
def test_partial_meta_adds_service_warning_after_analyzer_ones(tmp_path, test_video, flag):
    res = AnalysisResult(warnings=[NO_VLM], meta={flag: True, "notes_ru": [WARN_TIMEOUT, NO_VLM]})
    with client_for(tmp_path, analyzer=WarningAnalyzer(res)) as c:
        body = wait_done(c, upload(c, test_video).json()["job_id"])
        assert body["partial"] is True
        assert body["warnings"] == [NO_VLM, WARN_TIMEOUT]  # analyzer's first, service note deduped


def test_complete_run_has_no_service_warning(tmp_path, test_video):
    with client_for(tmp_path, analyzer=WarningAnalyzer(AnalysisResult(meta={"stopped_early": False}))) as c:
        body = wait_done(c, upload(c, test_video).json()["job_id"])
        assert body["partial"] is False and body["warnings"] == []


def test_partial_warning_kept_when_analyzer_fills_cap(tmp_path, test_video):
    meta = {"stopped_early": True, "notes_ru": [f"заметка {i}" for i in range(50)]}
    with client_for(tmp_path, analyzer=MetaAnalyzer(meta)) as c:
        w = wait_done(c, upload(c, test_video).json()["job_id"])["warnings"]
        assert len(w) == 10 and w[-1] == WARN_TIMEOUT


def test_notes_ru_survive_oversized_meta(tmp_path, test_video):
    meta = {"notes_ru": [NO_VLM], "blob": "x" * 20000}
    with client_for(tmp_path, analyzer=MetaAnalyzer(meta)) as c:
        body = wait_done(c, upload(c, test_video).json()["job_id"])
        assert "analyzer" not in body["meta"]  # meta itself still dropped
        assert body["warnings"] == [NO_VLM]


def test_warnings_from_dict_result_coerced_and_capped(tmp_path, test_video):
    res = {"violations": [], "warnings": [1, "x" * 1000, None],
           "meta": {"notes_ru": "одиночная строка"}}
    with client_for(tmp_path, analyzer=WarningAnalyzer(res)) as c:
        body = wait_done(c, upload(c, test_video).json()["job_id"])
        w = body["warnings"]
        assert w[0] == "1" and len(w[1]) <= 300 and w[1].startswith("xxx") and w[2] == "одиночная строка"
        assert len(w) == 3


def test_warnings_capped_in_count(tmp_path, test_video):
    meta = {"notes_ru": [f"заметка {i}" for i in range(50)]}
    with client_for(tmp_path, analyzer=MetaAnalyzer(meta)) as c:
        body = wait_done(c, upload(c, test_video).json()["job_id"])
        assert body["warnings"] == [f"заметка {i}" for i in range(10)]


def test_warnings_empty_for_failed_and_processing(tmp_path, test_video):
    with client_for(tmp_path, analyzer=RaisingAnalyzer()) as c:
        body = wait_done(c, upload(c, test_video).json()["job_id"], route="/api/v1/jobs/")
        assert body["status"] == "failed" and body["warnings"] == []
    slow = SlowAnalyzer(sleep=1.0)
    with client_for(tmp_path / "b", analyzer=slow) as c:
        body = c.get("/api/v1/jobs/" + upload(c, test_video).json()["job_id"], headers=HDR).json()
        assert body["status"] == "processing" and body["warnings"] == []
        slow.finished.wait(5)


def test_collect_warnings_unit():
    from app.worker import collect_warnings

    assert collect_warnings(None, [], {"a": 1}) == []
    assert collect_warnings(["a", " a ", "b"], ("c", "a"), "d") == ["a", "b", "c", "d"]
    assert collect_warnings([float("nan"), ["nested"]]) == ["nan", "['nested']"]


def test_timed_out_analyzer_is_cancelled(tmp_path, test_video):
    coop = CooperativeSlowAnalyzer()
    with client_for(tmp_path, analyzer=coop, job_timeout_sec=1.5) as c:
        body = wait_done(c, upload(c, test_video).json()["job_id"], timeout=10)
        assert body["status"] == "done" and body["partial"] is True
        assert coop.saw_cancel.wait(2)


def test_budget_counts_from_before_upload(tmp_path, test_video, monkeypatch):
    """The submit time handed to the worker is taken before the body is read."""
    import app.main as main_mod

    real = main_mod.receive_upload
    seen = {}

    async def slow_receive(request, settings):
        seen["before"] = time.monotonic()
        await asyncio.sleep(0.3)
        return await real(request, settings)

    monkeypatch.setattr(main_mod, "receive_upload", slow_receive)
    with client_for(tmp_path) as c:
        worker = c.app.state.worker
        orig_submit = worker.submit
        monkeypatch.setattr(worker, "submit",
                            lambda job_id, submitted=None: (seen.setdefault("submitted", submitted),
                                                            orig_submit(job_id, submitted))[1])
        job_id = upload(c, test_video).json()["job_id"]
        assert seen["submitted"] <= seen["before"]
        wait_done(c, job_id)


def test_non_video_upload_fails_job_with_error(tmp_path):
    junk = tmp_path / "junk.mov"
    junk.write_bytes(b"this is not a video" * 100)
    with client_for(tmp_path) as c:
        r = upload(c, junk)
        assert r.status_code == 200
        body = wait_done(c, r.json()["job_id"])
        assert body["status"] == "failed" and body["error"]


def test_bad_analyzer_path_fails_jobs_but_app_runs(tmp_path, test_video):
    with client_for(tmp_path, analyzer_path_override="no.such.module:Nope") as c:
        assert c.get("/healthz").status_code == 200
        body = wait_done(c, upload(c, test_video).json()["job_id"])
        assert body["status"] == "failed" and "no.such.module" in body["error"]


# ---------- timeout ----------

def test_timeout_finalizes_partial_and_ignores_orphan(tmp_path, test_video):
    slow = SlowAnalyzer(sleep=2.5)
    timeout = 1.0
    with client_for(tmp_path, analyzer=slow, job_timeout_sec=timeout) as c:
        t0 = time.monotonic()
        job_id = upload(c, test_video).json()["job_id"]
        body = wait_done(c, job_id, route="/api/v1/jobs/", timeout=10)
        elapsed = time.monotonic() - t0
        assert elapsed < timeout + 3.0, elapsed  # + the clip phase (own deadline, cap - 2 s)
        assert_contract(body)
        assert body["status"] == "done" and body["partial"] is True
        assert body["verdict"] == "violation"
        assert [v["type_id"] for v in body["violations"]] == ["T1"]
        assert body["violations"][0]["rule"]["document"]
        # the clip phase has its own deadline (cap - 2 s), so the overrun still gets a clip
        assert body["warnings"] == [WARN_TIMEOUT]
        assert body["violations"][0]["clip_url"] == f"/api/v1/jobs/{job_id}/clips/1.mp4"

        # a second job can start while the orphan is still running
        job2 = upload(c, test_video).json()["job_id"]

        # the orphan's late return/progress must not overwrite the finalized job
        assert slow.finished.wait(5)
        time.sleep(0.2)
        again = c.get(f"/api/v1/jobs/{job_id}", headers=HDR).json()
        assert again == body
        body2 = wait_done(c, job2, timeout=10)
        assert body2["status"] == "done"


# ---------- uploads ----------

def test_oversize_upload_413_and_cleanup(tmp_path):
    big = tmp_path / "big.mov"
    big.write_bytes(b"\0" * 5000)
    with client_for(tmp_path, max_upload_bytes=1000) as c:
        r = upload(c, big)
        assert r.status_code == 413 and "detail" in r.json()
        r = upload(c, big, route="/ui-api/analyze", headers={})
        assert r.status_code == 413
        uploads = tmp_path / "data" / "uploads"
        assert not uploads.exists() or not any(uploads.iterdir())
        assert c.get("/ui-api/jobs").json()["jobs"] == []


def test_empty_upload_400(tmp_path):
    empty = tmp_path / "empty.mov"
    empty.write_bytes(b"")
    with client_for(tmp_path) as c:
        r = upload(c, empty)
        assert r.status_code == 400 and "detail" in r.json()


def test_missing_file_field_400(tmp_path):
    with client_for(tmp_path) as c:
        r = c.post("/api/v1/analyze", data={"x": "y"}, headers=HDR)
        assert r.status_code == 400 and "detail" in r.json()
        r = c.post("/api/v1/analyze", content=b"raw", headers={**HDR, "content-type": "video/quicktime"})
        assert r.status_code == 400 and "detail" in r.json()


def test_other_file_field_name_is_accepted(tmp_path, test_video):
    with client_for(tmp_path) as c:
        with open(test_video, "rb") as f:
            r = c.post("/api/v1/analyze", files={"video": ("c.mp4", f, "video/mp4")}, headers=HDR)
        assert r.status_code == 200
        assert wait_done(c, r.json()["job_id"])["filename"] == "c.mp4"


def test_hostile_filename_is_sanitized(tmp_path, test_video):
    with client_for(tmp_path) as c:
        r = upload(c, test_video, name="../../../evil/x.mov")
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        body = wait_done(c, job_id)
        assert body["filename"] == "x.mov"
        assert (tmp_path / "data" / "uploads" / job_id / "x.mov").exists()
        assert not (tmp_path / "evil").exists()


# ---------- restart ----------

def test_restart_persistence_and_recovery(tmp_path, test_video):
    with client_for(tmp_path) as c:
        job_id = upload(c, test_video).json()["job_id"]
        done = wait_done(c, job_id)
    with client_for(tmp_path) as c2:
        r = c2.get(f"/api/v1/jobs/{job_id}", headers=HDR)
        assert r.status_code == 200 and r.json() == done

    # simulate a crash mid-processing: one job with its upload, one without
    jobs_dir = tmp_path / "data" / "jobs"
    rec = json.loads((jobs_dir / f"{job_id}.json").read_text("utf-8"))
    alive = dict(rec, job_id="alive1", status="processing", verdict=None, violations=[], progress=0.4)
    lost = dict(rec, job_id="lost1", status="processing", verdict=None, violations=[],
                upload_path=str(tmp_path / "gone.mov"))
    (jobs_dir / "alive1.json").write_text(json.dumps(alive), "utf-8")
    (jobs_dir / "lost1.json").write_text(json.dumps(lost), "utf-8")
    with client_for(tmp_path) as c3:
        assert wait_done(c3, "alive1")["status"] == "done"
        lost_body = c3.get("/ui-api/jobs/lost1").json()
        assert lost_body["status"] == "failed" and "restart" in lost_body["error"]


# ---------- UI API ----------

def test_ui_routes_work_without_key(tmp_path, test_video):
    with client_for(tmp_path, stub_debug=True) as c:
        r = upload(c, test_video, route="/ui-api/analyze", headers={})
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        body = wait_done(c, job_id)
        assert_contract(body)
        # same payload as the public API, except that clip links point at the key-less route
        api = c.get(f"/api/v1/jobs/{job_id}", headers=HDR).json()
        assert api["violations"][0]["clip_url"] == f"/api/v1/jobs/{job_id}/clips/1.mp4"
        assert body["violations"][0]["clip_url"] == f"/ui-api/jobs/{job_id}/clips/1.mp4"
        api["violations"][0]["clip_url"] = body["violations"][0]["clip_url"]
        assert api == body

        jobs = c.get("/ui-api/jobs").json()["jobs"]
        assert [j["job_id"] for j in jobs] == [job_id]
        assert jobs[0]["status"] == "done"
        for v in jobs[0]["violations"]:
            for fr in v["evidence_frames"]:
                assert "image_base64" not in fr  # list stays light

        rules = c.get("/ui-api/rules").json()["rules"]
        assert "T99" in rules and rules["T1"]["title_ru"]
        assert rules["T1"]["citations"][0]["legal_conf"]  # extra citation keys passed through


def test_ui_jobs_list_newest_first_max_20(tmp_path, test_video):
    with client_for(tmp_path, analyzer=NoneAnalyzer()) as c:
        ids = [upload(c, test_video, route="/ui-api/analyze", headers={}).json()["job_id"] for _ in range(22)]
        jobs = c.get("/ui-api/jobs").json()["jobs"]
        assert [j["job_id"] for j in jobs] == list(reversed(ids))[:20]


def test_video_range_requests(tmp_path, test_video):
    size = test_video.stat().st_size
    data = test_video.read_bytes()
    with client_for(tmp_path) as c:
        job_id = upload(c, test_video).json()["job_id"]
        url = f"/ui-api/jobs/{job_id}/video"

        r = c.get(url)
        assert r.status_code == 200 and r.content == data
        assert r.headers["content-type"] == "video/quicktime"
        assert r.headers["accept-ranges"] == "bytes"

        r = c.get(url, headers={"Range": "bytes=0-99"})
        assert r.status_code == 206 and r.content == data[:100]
        assert r.headers["content-range"] == f"bytes 0-99/{size}"
        assert r.headers["content-length"] == "100"
        assert r.headers["content-type"] == "video/quicktime"

        r = c.get(url, headers={"Range": "bytes=100-"})
        assert r.status_code == 206 and r.content == data[100:]
        assert r.headers["content-range"] == f"bytes 100-{size - 1}/{size}"

        r = c.get(url, headers={"Range": "bytes=-10"})
        assert r.status_code == 206 and r.content == data[-10:]

        r = c.get(url, headers={"Range": f"bytes={size + 10}-"})
        assert r.status_code == 416

        job_mp4 = upload(c, test_video, name="v.mp4").json()["job_id"]
        r = c.get(f"/ui-api/jobs/{job_mp4}/video", headers={"Range": "bytes=0-1"})
        assert r.status_code == 206 and r.headers["content-type"] == "video/mp4"


def test_static_mount_does_not_shadow_api(tmp_path):
    with client_for(tmp_path) as c:
        assert c.get("/healthz").json()["ok"] is True
        assert c.get("/ui-api/rules").status_code == 200
        for route in ("/api/v1/analyze", "/ui-api/analyze"):
            r = c.get(route, headers=HDR)
            assert r.status_code == 405 and "detail" in r.json()


def test_queued_jobs_never_exceed_hard_cap(tmp_path, test_video):
    """Several jobs queued behind an analyzer that ignores its deadline: each one must be
    finished no later than its submission + JOB_HARD_CAP_SEC (+ slack). With timeout=1 and
    cap=2.5: job 1 is cut at 1 s, job 2 runs on the minimum budget until ~2 s, job 3 has
    <1 s left before its cap and is finalized immediately without running the analyzer."""
    cap = 2.5
    slow = SlowAnalyzer(sleep=4.0)
    with client_for(tmp_path, analyzer=slow, job_timeout_sec=1.0, job_hard_cap_sec=cap) as c:
        submitted = {}
        for _ in range(3):
            jid = upload(c, test_video).json()["job_id"]
            submitted[jid] = time.monotonic()
        finished = {}
        end = time.monotonic() + 10
        while len(finished) < 3 and time.monotonic() < end:
            for jid in submitted:
                if jid not in finished:
                    b = c.get(f"/api/v1/jobs/{jid}", headers=HDR).json()
                    if b["status"] != "processing":
                        finished[jid] = (time.monotonic(), b)
            time.sleep(0.02)
        assert len(finished) == 3
        ids = list(submitted)
        for jid in ids:
            t, b = finished[jid]
            assert t - submitted[jid] <= cap + 1.5, (jid, t - submitted[jid])
            assert_contract(b)
            assert b["status"] == "done" and b["partial"] is True
        # the first two ran the analyzer (partial violation kept); the last never started
        assert finished[ids[0]][1]["verdict"] == "violation"
        assert finished[ids[1]][1]["verdict"] == "violation"
        last = finished[ids[2]][1]
        assert last["verdict"] == "no_violation" and last["violations"] == []
        assert last["error"] is None
        assert last["meta"]["note"] == "queue wait exhausted time budget"
        for jid in ids[:2]:
            # cap 2.5 s -> clip deadline submit + 0.5 s, already gone: skipped for time
            assert finished[jid][1]["warnings"] == [WARN_CLIPS_TIME_1, WARN_TIMEOUT]
        assert last["warnings"] == [WARN_TIMEOUT]


# ---------- rules file selection ----------

def test_rules_path_setting_is_used_and_reported(tmp_path, test_video):
    custom = tmp_path / "custom_rules.json"
    custom.write_text(json.dumps({"rules": [{"id": "Q1", "title_ru": "Своё правило", "extra": 1,
                                             "citations": [{"document": "Документ", "point": "п. 1"}]}]},
                                 ensure_ascii=False), encoding="utf-8")

    class Q1Analyzer:
        name = "q1"

        def analyze(self, video_path, progress_cb, deadline, partial):
            return AnalysisResult(violations=[Violation(type="Q1", timecode_sec=1.0)])

    with client_for(tmp_path, analyzer=Q1Analyzer(), rules_path=str(custom)) as c:
        h = c.get("/healthz").json()
        assert h["rules_count"] == 1
        assert h["rules_path"] == "custom_rules.json"  # outside the service tree: file name only
        assert str(tmp_path) not in h["rules_path"]
        assert c.get("/ui-api/rules").json()["rules"]["Q1"]["extra"] == 1
        body = wait_done(c, upload(c, test_video).json()["job_id"])
        v = body["violations"][0]
        assert v["type"] == "Своё правило" and v["type_id"] == "Q1"
        assert v["rule"] == {"document": "Документ", "point": "п. 1", "quote": "", "url": ""}


@pytest.mark.parametrize("content", [None, "{broken", '{"x": {"title_en": "no title_ru"}}'])
def test_invalid_rules_path_falls_back_to_bundled(tmp_path, monkeypatch, content):
    from app import rules as rules_mod
    monkeypatch.setattr(rules_mod, "DETECTOR_RULES_PATH", tmp_path / "none1.json")
    monkeypatch.setattr(rules_mod, "SPEC_RULES_PATH", tmp_path / "none2.json")
    custom = tmp_path / "custom_rules.json"
    if content is not None:
        custom.write_text(content, encoding="utf-8")
    with client_for(tmp_path, rules_path=str(custom)) as c:
        h = c.get("/healthz").json()
        assert h["rules_path"] == "app/rules.json"  # relative to the service root, no home path
        assert h["rules_count"] == len(c.app.state.rules.rules) >= 1
        assert c.app.state.rules.path == BUNDLED_RULES_PATH


def test_ui_static_has_warnings_block_and_no_inner_html():
    static = Path(__file__).resolve().parent.parent / "app" / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    js = (static / "app.js").read_text(encoding="utf-8")
    assert 'id="warnings-note"' in html and "Предупреждения" in html
    assert "renderWarnings(job.warnings)" in js and "Сила правовой нормы" in js
    assert "innerHTML" not in js.replace("never innerHTML", "")
