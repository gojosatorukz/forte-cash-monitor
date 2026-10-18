"""BRAND=forte|neutral: server-side branded UI pages, login/disabled pages, /healthz, gate still applies."""

from __future__ import annotations

import html
import re

import pytest

from app import ui_auth
from app.branding import apply_brand
from app.config import Settings
from tests.test_api import client_for

FORTE_ART = ("forte-magenta.svg", "forte-white.svg")
PAGES = ("/", "/index.html", "/act.html")


def _html_attr(body: str) -> str:
    m = re.search(r"<html\b[^>]*>", body)
    assert m, body[:200]
    return m.group(0)


def _favicon(body: str) -> str:
    m = re.search(r'<link rel="icon" href="([^"]+)"', body)
    assert m, body[:400]
    return m.group(1)


# ---------- settings ----------

@pytest.mark.parametrize("raw, expected", [(None, "forte"), ("", "forte"), ("forte", "forte"),
                                           ("neutral", "neutral"), (" Neutral ", "neutral"),
                                           ("acme", "forte")])
def test_brand_from_env(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("BRAND", raising=False)
    else:
        monkeypatch.setenv("BRAND", raw)
    assert Settings.from_env().brand == expected


def test_invalid_brand_warns(monkeypatch, caplog):
    monkeypatch.setenv("BRAND", "acme")
    with caplog.at_level("WARNING", logger="app.config"):
        assert Settings.from_env().brand == "forte"
    assert "BRAND" in caplog.text


# ---------- served pages ----------

def test_forte_default_pages(tmp_path):
    with client_for(tmp_path) as c:
        assert c.get("/healthz").json()["brand"] == "forte"
        for path in PAGES:
            r = c.get(path)
            assert r.status_code == 200, path
            assert r.headers["content-type"].startswith("text/html")
            assert 'data-brand="forte"' in _html_attr(r.text), path
            assert _favicon(r.text) == "assets/brand/favicon.svg", path
            assert "<!--brand:" not in r.text
            assert "cash-monitor-wordmark.svg" not in r.text, path
        index = c.get("/").text
        assert "forte-magenta.svg" in index and "Forte Cash Monitor" in index
        assert "forte-magenta.svg" in c.get("/act.html").text
        assert c.head("/").status_code == 200


def test_neutral_pages(tmp_path):
    with client_for(tmp_path, brand="neutral") as c:
        assert c.get("/healthz").json()["brand"] == "neutral"
        for path in PAGES:
            r = c.get(path)
            assert r.status_code == 200, path
            assert 'data-brand="neutral"' in _html_attr(r.text), path
            assert _favicon(r.text) == "assets/brand/favicon-neutral.svg", path
            assert "cash-monitor-wordmark.svg" in r.text, path
            for art in FORTE_ART:
                assert art not in r.text, (path, art)
            # Only «для ForteBank» may mention the bank.
            assert "Forte" not in r.text.replace("для ForteBank", ""), path
        assert "Командный прототип для ForteBank" in c.get("/").text
        for asset in ("cash-monitor-wordmark.svg", "favicon-neutral.svg"):
            r = c.get(f"/assets/brand/{asset}")
            assert r.status_code == 200 and r.content.startswith(b"<svg")


def test_invalid_brand_value_falls_back_to_forte(tmp_path):
    with client_for(tmp_path, brand="acme") as c:
        assert 'data-brand="forte"' in _html_attr(c.get("/").text)


def test_apply_brand_keeps_forte_markup_unchanged():
    page = ('<html lang="ru"><head><link rel="icon" href="assets/brand/favicon.svg"></head><body>'
            '<!--brand:forte--><img src="assets/brand/forte-magenta.svg"><!--/brand:forte-->'
            '<!--brand:neutral--><img src="assets/brand/cash-monitor-wordmark.svg"><!--/brand:neutral-->'
            '</body></html>')
    assert apply_brand(page, "forte") == ('<html lang="ru" data-brand="forte"><head>'
                                          '<link rel="icon" href="assets/brand/favicon.svg"></head><body>'
                                          '<img src="assets/brand/forte-magenta.svg"></body></html>')
    neutral = apply_brand(page, "neutral")
    assert "forte-magenta" not in neutral and "favicon-neutral.svg" in neutral


# ---------- gate ----------

@pytest.mark.parametrize("brand", ["forte", "neutral"])
def test_gate_still_applies_to_branded_pages(tmp_path, brand):
    with client_for(tmp_path, login=False, brand=brand) as c:
        for path in PAGES:
            r = c.get(path, follow_redirects=False)
            assert r.status_code == 303, (path, r.status_code)
            assert r.headers["location"].startswith("/login?next=")
            assert c.head(path, follow_redirects=False).status_code == 303


def test_login_and_disabled_pages_by_brand(tmp_path):
    with client_for(tmp_path, login=False, brand="neutral") as c:
        login = c.get("/login").text
        assert "Cash Monitor" in login and "Forte" not in login
        assert ui_auth.NEUTRAL_FAVICON and html.escape(ui_auth.NEUTRAL_FAVICON, quote=True) in login
    with client_for(tmp_path, login=False) as c:
        login = c.get("/login").text
        assert "Forte Cash Monitor" in login and html.escape(ui_auth.FAVICON, quote=True) in login
    with client_for(tmp_path, login=False, brand="neutral", ui_password="") as c:
        r = c.get("/")
        assert r.status_code == 503 and "Forte" not in r.text and "Cash Monitor" in r.text
    forte_disabled = ui_auth.disabled_page()
    assert "Forte Cash Monitor" in forte_disabled
    for art in FORTE_ART:
        assert art not in ui_auth.login_page("/", brand="neutral")
