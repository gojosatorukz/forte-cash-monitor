"""LIVE_REPLAY: settings, server-side <html data-live> injection, live.js pure helpers (via node)."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.branding import apply_brand
from app.config import Settings
from tests.test_api import client_for

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"


def _html_tag(body: str) -> str:
    m = re.search(r"<html\b[^>]*>", body)
    assert m, body[:200]
    return m.group(0)


@pytest.mark.parametrize("raw, expected", [(None, True), ("", True), ("1", True), ("true", True), ("yes", True),
                                           ("0", False), ("false", False), (" FALSE ", False), ("off", False),
                                           ("no", False)])
def test_live_replay_from_env(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("LIVE_REPLAY", raising=False)
    else:
        monkeypatch.setenv("LIVE_REPLAY", raw)
    assert Settings.from_env().live_replay is expected


def test_live_default_on_injected(tmp_path):
    with client_for(tmp_path) as c:
        for path in ("/", "/index.html", "/act.html"):
            tag = _html_tag(c.get(path).text)
            assert 'data-live="1"' in tag and 'data-brand="forte"' in tag, (path, tag)


@pytest.mark.parametrize("brand", ["forte", "neutral"])
def test_live_off_injected(tmp_path, brand):
    with client_for(tmp_path, live_replay=False, brand=brand) as c:
        body = c.get("/").text
        tag = _html_tag(body)
        assert 'data-live="0"' in tag and f'data-brand="{brand}"' in tag, tag
        assert '<script src="live.js"' in body  # module still served; app.js skips the feature
        assert c.get("/live.js").status_code == 200


def test_live_js_gated_like_other_static(tmp_path):
    with client_for(tmp_path, login=False) as c:
        assert c.get("/live.js").status_code == 401


def test_apply_brand_live_attr():
    page = '<html lang="ru"><body></body></html>'
    assert apply_brand(page, "forte", True) == '<html lang="ru" data-brand="forte" data-live="1"><body></body></html>'
    assert apply_brand(page, "neutral", False).startswith('<html lang="ru" data-brand="neutral" data-live="0">')
    assert "data-live" not in apply_brand(page, "forte")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_live_js_helpers_node():
    r = subprocess.run(["node", str(ROOT / "tests" / "js" / "test_live.js")], capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "all assertions passed" in r.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_live_js_syntax():
    r = subprocess.run(["node", "--check", str(STATIC / "live.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
