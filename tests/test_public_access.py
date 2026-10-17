import os
import stat

import pytest
from fastapi.testclient import TestClient
from test_service import KEY, settings

from backend.app.main import create_app
from scripts.public_link import ROOT, export_access, single_instance


@pytest.mark.parametrize(
    "path", ["/.env", "/.git/config", "/.runtime/jobs.sqlite3", "/data/video.mov"]
)
def test_public_server_does_not_serve_source_secrets_or_runtime(tmp_path, path):
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.get(path)
        assert response.status_code == 404
        assert KEY not in response.text
        assert "noindex" in response.headers["x-robots-tag"]
        assert client.get("/robots.txt").text == "User-agent: *\nDisallow: /\n"


def test_access_handoff_is_private_and_separate_from_source(tmp_path):
    base = "https://synthetic-test-only.trycloudflare.com"
    target = export_access(base, KEY, tmp_path, False)
    content = target.read_text()
    assert f"X-API-Key: {KEY}" in content
    assert f"POST {base}/api/v1/analyze" in content
    assert "503" in content
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
    with pytest.raises(ValueError):
        export_access(base, KEY, ROOT / "docs", False)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.trycloudflare.com",
        "https://example.trycloudflare.com/path",
        "https://user:pass@example.trycloudflare.com",
        "https://example.trycloudflare.com\nsecret",
    ],
)
def test_handoff_rejects_non_origin_or_injected_urls(tmp_path, url):
    with pytest.raises(ValueError):
        export_access(url, KEY, tmp_path, False)
    assert not list(tmp_path.iterdir())


def test_duplicate_tunnel_launcher_is_rejected(tmp_path):
    with single_instance(tmp_path / "tunnel.lock"), pytest.raises(SystemExit):
        with single_instance(tmp_path / "tunnel.lock"):
            pytest.fail("A second connector could replace the active URL")
