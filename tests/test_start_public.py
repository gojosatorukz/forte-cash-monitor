import json
from io import BytesIO
from urllib.error import URLError

import pytest

from scripts import start_public
from scripts.public_link import single_instance


def test_startup_does_not_reuse_an_unrelated_server(monkeypatch):
    monkeypatch.setattr(start_public, "urlopen", lambda *a, **kw: BytesIO(b'{"service":"other"}'))
    with pytest.raises(RuntimeError, match="другим сервисом"):
        start_public.service_running()


def test_startup_distinguishes_a_closed_port_from_running_service(monkeypatch):
    def closed(*args, **kwargs):
        raise URLError("connection refused")

    monkeypatch.setattr(start_public, "urlopen", closed)
    assert start_public.service_running() is False
    monkeypatch.setattr(
        start_public,
        "urlopen",
        lambda *a, **kw: BytesIO(b'{"service":"forte-cash-monitor"}'),
    )
    assert start_public.service_running() is True


def test_stale_active_state_does_not_prevent_a_new_tunnel(tmp_path, monkeypatch):
    runtime = tmp_path / ".runtime"
    runtime.mkdir()
    (runtime / "public-link.json").write_text(
        json.dumps({"active": True, "base_url": "https://expired.trycloudflare.com"})
    )
    monkeypatch.setattr(start_public, "ROOT", tmp_path)
    assert start_public.existing_tunnel() is None


def test_active_tunnel_is_reused_without_creating_a_duplicate(tmp_path, monkeypatch):
    runtime = tmp_path / ".runtime"
    runtime.mkdir()
    url = "https://current.trycloudflare.com"
    (runtime / "public-link.json").write_text(json.dumps({"base_url": url}))
    monkeypatch.setattr(start_public, "ROOT", tmp_path)
    with single_instance(runtime / "public-link.lock"):
        assert start_public.existing_tunnel() == url
