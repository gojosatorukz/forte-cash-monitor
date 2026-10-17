"""VLM reachability probe reported by /healthz (network always mocked)."""

from __future__ import annotations

import io
import urllib.error

import pytest

from app import health
from app.config import Settings
from app.health import VlmProbe, vlm_health_url_from_env


@pytest.mark.parametrize("env,expected", [
    ({}, "http://127.0.0.1:8090/health"),
    ({"FORTE_VLM_URL": "http://10.0.0.5:9000/v1/chat/completions"}, "http://10.0.0.5:9000/health"),
    ({"FORTE_VLM_URL": "https://vlm.example.kz/v1/chat/completions"}, "https://vlm.example.kz/health"),
    ({"FORTE_VLM_URL": "not a url"}, None),
    ({"VLM_HEALTH_URL": "http://h:1/healthz"}, "http://h:1/healthz"),
    ({"VLM_HEALTH_URL": "http://h:1/healthz", "FORTE_VLM_URL": "http://x:2/v1"}, "http://h:1/healthz"),
    ({"VLM_HEALTH_URL": ""}, None),
    ({"VLM_HEALTH_URL": " OFF "}, None),
])
def test_vlm_health_url_from_env(monkeypatch, env, expected):
    monkeypatch.delenv("FORTE_VLM_URL", raising=False)
    monkeypatch.delenv("VLM_HEALTH_URL", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert vlm_health_url_from_env() == expected
    assert Settings.from_env().vlm_health_url == expected


class FakeResp(io.BytesIO):
    def __init__(self, body: bytes, status: int = 200):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def fake_open(calls, body=b'{"status":"ok"}', status=200, exc=None):
    def _open(url, timeout):
        calls.append((url, timeout))
        if exc is not None:
            raise exc
        return FakeResp(body, status)
    return _open


def test_probe_ok_and_cached(monkeypatch):
    calls = []
    clock = [100.0]
    probe = VlmProbe("http://127.0.0.1:8090/health", opener=fake_open(calls), clock=lambda: clock[0])
    assert probe.check() is True
    assert probe.check() is True
    assert len(calls) == 1 and calls[0][1] <= 1.5
    clock[0] += health.VLM_CACHE_SEC + 0.1
    assert probe.check() is True
    assert len(calls) == 2


@pytest.mark.parametrize("kw", [
    dict(body=b"loading model", status=200),
    dict(body=b'{"status":"ok"}', status=503),
    dict(exc=urllib.error.URLError("refused")),
    dict(exc=TimeoutError("timed out")),
    dict(exc=RuntimeError("anything")),
])
def test_probe_not_ok(kw):
    probe = VlmProbe("http://127.0.0.1:8090/health", opener=fake_open([], **kw))
    assert probe.check() is False


def test_probe_not_configured():
    calls = []
    probe = VlmProbe(None, opener=fake_open(calls))
    assert probe.check() is None and calls == []


def client_for(tmp_path, **kw):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.rules import BUNDLED_RULES_PATH

    settings = Settings(api_key="k", api_key_is_default=False, data_dir=tmp_path / "data",
                        rules_path=str(BUNDLED_RULES_PATH), **kw)
    return TestClient(create_app(settings))


def test_healthz_reports_vlm(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(health, "_default_open", fake_open(calls))
    with client_for(tmp_path, vlm_health_url="http://127.0.0.1:8090/health") as c:
        body = c.get("/healthz").json()
        assert body["vlm_ok"] is True and body["vlm_url"] == "http://127.0.0.1:8090/health"
        assert body["ok"] is True
        c.get("/healthz")
        assert len(calls) == 1  # cached

    monkeypatch.setattr(health, "_default_open", fake_open([], exc=urllib.error.URLError("down")))
    with client_for(tmp_path / "b", vlm_health_url="http://127.0.0.1:8090/health") as c:
        body = c.get("/healthz").json()
        assert body["vlm_ok"] is False and body["ok"] is True  # ok never depends on the VLM
