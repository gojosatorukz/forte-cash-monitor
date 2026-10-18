import json

import pytest

from app.config import DEV_API_KEY, Settings
from app.models import JobRecord, Violation


def test_job_record_processing_public():
    job = JobRecord(job_id="abc", upload_path="/secret/x.mov", filename="x.mov")
    job.violations = [{"type": "other_suspicious"}]  # e.g. partial results, not exposed yet
    pub = job.to_public()
    assert pub["job_id"] == "abc"
    assert pub["status"] == "processing"
    assert pub["verdict"] is None
    assert pub["violations"] == []
    assert "upload_path" not in pub
    assert pub["created_at"]
    json.dumps(pub)


def test_job_record_done_recomputes_verdict():
    job = JobRecord(job_id="a", status="done", verdict="no_violation", violations=[{"type": "x"}])
    assert job.to_public()["verdict"] == "violation"
    job = JobRecord(job_id="a", status="done", verdict="violation", violations=[])
    assert job.to_public()["verdict"] == "no_violation"


def test_job_record_failed():
    job = JobRecord(job_id="a", status="failed", error="boom", verdict="violation")
    pub = job.to_public()
    assert pub["verdict"] is None and pub["violations"] == [] and pub["error"] == "boom"


def test_job_record_roundtrip():
    job = JobRecord(job_id="a", partial=True, video_meta={"fps": 30.0})
    again = JobRecord.model_validate_json(job.model_dump_json())
    assert again == job


def test_violation_confidence_is_clamped_not_rejected():
    # Ruling: an out-of-range confidence must never fail a job -> clamp to [0, 1].
    assert Violation(type="x", timecode_sec=0, confidence=1.5).confidence == 1.0
    assert Violation(type="x", timecode_sec=0, confidence=-0.2).confidence == 0.0
    assert Violation(type="x", timecode_sec=0, confidence=0.3).confidence == 0.3
    assert Violation(type="x", timecode_sec=0, confidence=float("nan")).confidence is None
    assert Violation(type="x", timecode_sec=0, confidence=None).confidence is None
    # Task 6: non-numeric scores become None instead of dropping the whole violation
    # (e.g. a citation-style legal_conf "high" leaking into the violation level).
    assert Violation(type="x", timecode_sec=0, confidence="high").confidence is None
    assert Violation(type="x", timecode_sec=0, confidence="0.4").confidence == 0.4


def test_settings_max_upload_mb_env(monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "5")
    assert Settings.from_env().max_upload_bytes == 5 * 1024 * 1024
    monkeypatch.setenv("MAX_UPLOAD_MB", "junk")
    assert Settings.from_env().max_upload_bytes == 200 * 1024 * 1024


def test_settings_from_env(monkeypatch, tmp_path):
    for k in ("API_KEY", "API_KEY_DEV_DEFAULT", "DATA_DIR", "JOB_TIMEOUT_SEC", "ANALYZER", "STUB_DEBUG"):
        monkeypatch.delenv(k, raising=False)
    s = Settings.from_env()
    assert s.api_key == "" and not s.api_key_configured and not s.api_key_is_dev_default
    assert s.job_timeout_sec == 270.0
    assert s.analyzer == "app.analyzers.stub:StubAnalyzer"
    monkeypatch.setenv("API_KEY", "  real-key-0123456789  ")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JOB_TIMEOUT_SEC", "2.5")
    monkeypatch.setenv("STUB_DEBUG", "1")
    s = Settings.from_env()
    assert s.api_key == "real-key-0123456789" and s.api_key_configured and not s.api_key_is_dev_default
    assert s.data_dir == tmp_path and s.jobs_dir == tmp_path / "jobs"
    assert s.job_timeout_sec == 2.5 and s.stub_debug
    assert s.max_upload_bytes == 200 * 1024 * 1024


def test_settings_job_hard_cap_env(monkeypatch):
    monkeypatch.delenv("JOB_HARD_CAP_SEC", raising=False)
    monkeypatch.delenv("JOB_TIMEOUT_SEC", raising=False)
    assert Settings.from_env().job_hard_cap_sec == 280.0
    monkeypatch.setenv("JOB_HARD_CAP_SEC", "12.5")
    assert Settings.from_env().job_hard_cap_sec == 12.5


@pytest.mark.parametrize("raw", ["0", "-5", "nan", "inf", "-inf", "junk"])
def test_settings_invalid_time_budgets_fall_back(monkeypatch, raw):
    monkeypatch.setenv("JOB_TIMEOUT_SEC", raw)
    monkeypatch.setenv("JOB_HARD_CAP_SEC", raw)
    s = Settings.from_env()
    assert s.job_timeout_sec == 270.0 and s.job_hard_cap_sec == 280.0


def test_settings_timeout_above_cap_warns(monkeypatch, caplog):
    monkeypatch.setenv("JOB_TIMEOUT_SEC", "500")
    monkeypatch.setenv("JOB_HARD_CAP_SEC", "100")
    with caplog.at_level("WARNING", logger="app.config"):
        s = Settings.from_env()
    assert s.job_timeout_sec == 500.0 and s.job_hard_cap_sec == 100.0  # the cap wins in compute_deadline
    assert any("hard cap wins" in r.getMessage() for r in caplog.records)


# ---------- warnings / legal_strength ----------

def test_job_record_warnings_public_for_every_status():
    for status in ("processing", "done", "failed"):
        pub = JobRecord(job_id="w", status=status, error="x" if status == "failed" else None).to_public()
        assert pub["warnings"] == []
    pub = JobRecord(job_id="w", status="done", warnings=["анализ без VLM"]).to_public()
    assert pub["warnings"] == ["анализ без VLM"]


def test_job_record_without_warnings_key_loads():
    # job files written before the field existed
    assert JobRecord.model_validate({"job_id": "old", "status": "done"}).warnings == []


def test_analysis_result_warnings_lenient():
    from app.models import AnalysisResult

    assert AnalysisResult().warnings == []
    assert AnalysisResult(warnings=["a", 3, None, " ", "b "]).warnings == ["a", "3", "b"]
    assert AnalysisResult(warnings="одна строка").warnings == ["одна строка"]
    assert AnalysisResult(warnings={"x": 1}).warnings == []


@pytest.mark.parametrize("raw,expected", [
    ("high", "high"), ("Medium", "medium"), (" LOW ", "low"),
    ("very", None), ("", None), (None, None), (0.9, None), (True, None),
])
def test_violation_legal_strength(raw, expected):
    assert Violation(type="T1", timecode_sec=0, legal_strength=raw).legal_strength == expected


@pytest.mark.parametrize("value", [None, "", "   ", "short-key", " 15-chars-key-x "])
def test_settings_api_key_unset_or_short_is_not_configured(monkeypatch, value):
    monkeypatch.delenv("API_KEY_DEV_DEFAULT", raising=False)
    if value is None:
        monkeypatch.delenv("API_KEY", raising=False)
    else:
        monkeypatch.setenv("API_KEY", value)
    s = Settings.from_env()
    assert not s.api_key_configured and s.api_key == "" and not s.api_key_is_dev_default


def test_settings_api_key_min_length_boundary():
    assert not Settings(api_key="x" * 15).api_key_configured
    assert Settings(api_key="x" * 16).api_key_configured
    assert not Settings(api_key="  " + "x" * 15 + "  ").api_key_configured
    assert not Settings().api_key_configured  # no default key in code


@pytest.mark.parametrize("value", [None, "", "short"])
def test_settings_dev_default_flag(monkeypatch, caplog, value):
    if value is None:
        monkeypatch.delenv("API_KEY", raising=False)
    else:
        monkeypatch.setenv("API_KEY", value)
    monkeypatch.setenv("API_KEY_DEV_DEFAULT", "1")
    with caplog.at_level("WARNING", logger="app.config"):
        s = Settings.from_env()
    assert s.api_key == DEV_API_KEY and s.api_key_configured and s.api_key_is_dev_default
    assert any(r.levelname == "WARNING" and "API_KEY_DEV_DEFAULT" in r.getMessage() for r in caplog.records)


def test_settings_real_key_beats_dev_flag(monkeypatch):
    monkeypatch.setenv("API_KEY", "real-key-0123456789")
    monkeypatch.setenv("API_KEY_DEV_DEFAULT", "1")
    s = Settings.from_env()
    assert s.api_key == "real-key-0123456789" and not s.api_key_is_dev_default
