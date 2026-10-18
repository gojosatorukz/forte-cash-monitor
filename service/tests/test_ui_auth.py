"""UI password gate (app.ui_auth): pages, static, /ui-api gated; /api/v1 and /healthz untouched."""

from __future__ import annotations

import contextlib
import stat
import time

import pytest
from fastapi.testclient import TestClient

from app import ui_auth
from app.main import create_app
from app.ui_auth import COOKIE, FAIL_LIMIT, LoginLimiter, client_info, load_or_create_secret, make_token, parse_networks, \
    safe_next, signing_key, verify_token
from tests.test_api import HDR, UI_PASS, client_for, make_settings, upload, wait_done


@contextlib.contextmanager
def raw_client(tmp_path, client=("testclient", 50000), **kw):
    app = create_app(make_settings(tmp_path, **kw))
    with TestClient(app, client=client) as c:
        yield c


def login(c, password=UI_PASS, next_path=None, headers=None):
    data = {"password": password}
    if next_path is not None:
        data["next"] = next_path
    return c.post("/login", data=data, headers=headers or {}, follow_redirects=False)


def set_cookie_header(r) -> str:
    return r.headers.get("set-cookie", "")


@pytest.fixture
def done_job(tmp_path, test_video):
    """A finished job with a clip (made through the key API); returns (tmp_path, job_id)."""
    with client_for(tmp_path, stub_debug=True) as c:
        job_id = upload(c, test_video).json()["job_id"]
        body = wait_done(c, job_id)
        assert body["violations"][0]["clip_url"]
    return tmp_path, job_id


def gated_requests(job_id):
    return [
        ("GET", "/ui-api/jobs", {}),
        ("GET", f"/ui-api/jobs/{job_id}", {}),
        ("GET", f"/ui-api/jobs/{job_id}/video", {}),
        ("GET", f"/ui-api/jobs/{job_id}/clips/1.mp4", {}),
        ("GET", "/ui-api/feedback.jsonl", {}),
        ("GET", "/ui-api/rules", {}),
        ("POST", f"/ui-api/jobs/{job_id}/review", {"json": {"decision": "confirmed"}}),
        ("POST", "/ui-api/analyze", {"files": {"file": ("a.mov", b"x", "video/quicktime")}}),
    ]


def bad_cookies(tmp_path):
    key = signing_key(load_or_create_secret(tmp_path / "data"), UI_PASS)
    expired = make_token(key, 1, now=time.time() - 7200)
    good = make_token(key, 1)
    tampered = good[:-1] + ("0" if good[-1] != "0" else "1")
    forged_expiry = good.split(".")
    forged_expiry[2] = str(int(forged_expiry[2]) + 10**6)
    return {"expired": expired, "tampered": tampered, "forged_expiry": ".".join(forged_expiry),
            "garbage": "v1.x.y.z.w"}


# ---------- route classes x (no cookie / valid / bad) ----------

def test_ui_api_needs_session(done_job):
    tmp_path, job_id = done_job
    cookies = bad_cookies(tmp_path)
    with raw_client(tmp_path) as c:
        for method, url, kw in gated_requests(job_id):
            r = c.request(method, url, **kw)
            assert r.status_code == 401, (url, r.status_code)
            assert r.json() == {"detail": "Требуется вход"}
            for name, val in cookies.items():
                c.cookies.set(COOKIE, val)
                r = c.request(method, url, **kw)
                assert r.status_code == 401, (url, name)
                c.cookies.clear()
        assert login(c).status_code == 303
        for method, url, kw in gated_requests(job_id):
            r = c.request(method, url, **kw)
            assert r.status_code in (200, 206), (url, r.status_code, r.text[:200])


def test_pages_redirect_and_static_401(tmp_path):
    cookies = bad_cookies(tmp_path)
    with raw_client(tmp_path) as c:
        for page, nxt in [("/", "/"), ("/index.html", "/index.html"), ("/act.html", "/act.html"),
                          ("/act.html?x=1", "/act.html%3Fx%3D1")]:
            r = c.get(page, follow_redirects=False)
            assert r.status_code == 303 and r.headers["location"] == f"/login?next={nxt}", (page, r.headers)
        for asset in ("/app.js", "/app.css", "/act.js", "/act.css", "/favicon.ico"):
            assert c.get(asset).status_code == 401, asset
        for val in cookies.values():
            c.cookies.set(COOKIE, val)
            assert c.get("/", follow_redirects=False).status_code == 303
            assert c.get("/app.js").status_code == 401
            c.cookies.clear()
        # dot segments must not sneak past the /api/v1 allowlist into the static mount (StaticFiles
        # normalises them); httpx normalises client-side, so check the classifier and a raw ASGI call
        assert not ui_auth._is_public("/api/v1/../app.js") and not ui_auth._is_public("/api/v1/./x")
        assert ui_auth._is_public("/api/v1/jobs/x") and not ui_auth._is_public("/api/v1x")
        assert c.get("/docs", follow_redirects=False).status_code == 303
        login(c)
        assert c.get("/").status_code == 200 and "Выйти" in c.get("/").text
        assert c.get("/app.js").status_code == 200
        assert c.get("/act.html").status_code == 200


# ---------- /api/v1 and /healthz untouched ----------

def test_api_v1_is_key_only(tmp_path, test_video):
    with raw_client(tmp_path) as c:
        r = upload(c, test_video)  # key, no cookie
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        assert c.get(f"/api/v1/jobs/{job_id}", headers=HDR).status_code == 200
        assert c.get("/api/v1/jobs/nope", headers=HDR).status_code == 404
        assert c.get("/api/v1/feedback.jsonl", headers=HDR).status_code == 200
        login(c)
        assert COOKIE in c.cookies
        r = c.get(f"/api/v1/jobs/{job_id}")  # cookie, no key
        assert r.status_code == 401 and "X-API-Key" in r.json()["detail"]
        assert upload(c, test_video, headers={}).status_code == 401
        assert c.get("/api/v1/feedback.jsonl").status_code == 401


def test_healthz_public_without_job_data(tmp_path, test_video):
    with raw_client(tmp_path) as c:
        job_id = upload(c, test_video).json()["job_id"]
        r = c.get("/healthz")
        assert r.status_code == 200 and r.json()["ok"] is True
        assert job_id not in r.text and "clip.mov" not in r.text
        assert not any("job" in k for k in r.json())


# ---------- login / logout ----------

def test_login_page_brand_and_only_the_font_is_public(tmp_path):
    font = "/assets/fonts/OpenSans.woff2"
    with raw_client(tmp_path) as c:
        page = c.get("/login").text
        assert "<svg" in page and 'aria-label="Forte"' in page and "data:image/svg+xml," in page
        # the only external reference is the one public font file
        assert page.count("assets/") == 1 and f'url("{font}")' in page and 'src="' not in page
        assert ui_auth.PUBLIC_EXACT == {"/healthz", "/login", "/logout", font}
        r = c.get(font)
        assert r.status_code == 200 and r.headers["content-type"] == "font/woff2" and r.content[:4] == b"wOF2"
        assert c.head(font).status_code == 200
        for path in ("/assets/fonts/OFL.txt", "/assets/brand/forte-magenta.svg", "/assets/brand/forte-white.svg",
                     "/assets/brand/favicon.svg", "/assets/fonts/", "/assets/fonts", "/assets/", "/assets/fonts/x.woff2",
                     font + "x", font + "/", "/app.js", "/index.html", "/act.html", "/"):
            r = c.get(path, follow_redirects=False)
            assert r.status_code in (401, 303, 404) and r.status_code != 200, (path, r.status_code)
            if path != "/assets/fonts/x.woff2":
                assert r.status_code in (401, 303), (path, r.status_code)
        # dot segments never count as the public font path (StaticFiles would normalise them)
        for path in ("/assets/fonts/../../app.js", "/assets/fonts/./OpenSans.woff2", "/assets/fonts/../fonts/OpenSans.woff2",
                     "/x/../assets/fonts/OpenSans.woff2", "/assets/fonts/OpenSans.woff2/../../../index.html"):
            assert not ui_auth._is_public(path), path
        assert ui_auth._is_public(font) and not ui_auth._is_public(font.upper())
        disabled = ui_auth.disabled_page()
        assert "<svg" in disabled and "UI_PASSWORD" in disabled and font in disabled
        login(c)
        assert c.get("/assets/brand/forte-magenta.svg").status_code == 200
        assert c.get("/assets/fonts/OFL.txt").status_code == 200


def test_login_font_public_in_disabled_mode(tmp_path):
    with raw_client(tmp_path, ui_password="") as c:
        assert c.get("/assets/fonts/OpenSans.woff2").status_code == 200
        assert c.get("/assets/brand/favicon.svg").status_code == 503
        assert c.get("/app.js").status_code == 503


def test_login_page_and_cookie_attributes(tmp_path):
    with raw_client(tmp_path) as c:
        r = c.get("/login?next=/act.html")
        assert r.status_code == 200
        assert "Войти" in r.text and "конфиденциальны" in r.text and 'value="/act.html"' in r.text
        assert "<link rel=\"stylesheet\"" not in r.text and "<script src" not in r.text  # self-contained
        r = login(c, "wrong")
        assert r.status_code == 401 and "Неверный пароль" in r.text and COOKIE not in set_cookie_header(r)
        r = login(c, next_path="/act.html#job=abc")
        assert r.status_code == 303 and r.headers["location"] == "/act.html#job=abc"
        sc = set_cookie_header(r)
        assert sc.startswith(f"{COOKIE}=v1.")
        for attr in ("HttpOnly", "SameSite=Strict", "Path=/", f"Max-Age={12 * 3600}"):
            assert attr in sc, attr
        assert "Secure" not in sc
        # already logged in -> /login redirects
        assert c.get("/login?next=/act.html", follow_redirects=False).headers["location"] == "/act.html"


def test_secure_cookie_behind_trusted_proxy_only(tmp_path):
    with raw_client(tmp_path, client=("127.0.0.1", 40000)) as c:
        r = login(c, headers={"X-Forwarded-Proto": "https", "X-Forwarded-For": "9.9.9.9"})
        assert "; Secure" in set_cookie_header(r)
    with raw_client(tmp_path / "u", client=("8.8.8.8", 40000)) as c:
        r = login(c, headers={"X-Forwarded-Proto": "https"})
        assert r.status_code == 303 and "Secure" not in set_cookie_header(r)


@pytest.mark.parametrize("value,expected", [
    ("//evil.com", "/"), ("https://evil.com", "/"), ("/\\evil.com", "/"), ("evil", "/"), ("", "/"),
    ("/act.html#job=1", "/act.html#job=1"), ("/?a=b", "/?a=b"), ("/login", "/"), ("/logout", "/"),
    ("/\nx", "/"),
])
def test_safe_next(value, expected):
    assert safe_next(value) == expected


def test_login_next_sanitised(tmp_path):
    with raw_client(tmp_path) as c:
        for bad in ("//evil.com", "https://evil.com"):
            r = login(c, next_path=bad)
            assert r.status_code == 303 and r.headers["location"] == "/"


def test_logout_clears_cookie(tmp_path):
    with raw_client(tmp_path) as c:
        login(c)
        assert c.get("/ui-api/jobs").status_code == 200
        r = c.post("/logout", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"
        sc = set_cookie_header(r)
        assert sc.startswith(f"{COOKIE}=;") and "Max-Age=0" in sc
        assert c.get("/ui-api/jobs").status_code == 401
        login(c)
        r = c.get("/logout", follow_redirects=False)
        assert r.status_code == 303 and "Max-Age=0" in set_cookie_header(r)


# ---------- rate limit / client IP ----------

def test_rate_limit_after_fail_limit(tmp_path):
    assert FAIL_LIMIT == 20 and ui_auth.FAIL_WINDOW_SEC == 300
    with raw_client(tmp_path) as c:
        for _ in range(FAIL_LIMIT):
            assert login(c, "bad").status_code == 401
        r = login(c, "bad")
        assert r.status_code == 429 and "Слишком много попыток, попробуйте через 5 мин" in r.text
        assert login(c).status_code == 429  # even the right password while locked
        assert int(r.headers["retry-after"]) > 0


def test_rate_limit_per_forwarded_client(tmp_path):
    with raw_client(tmp_path, client=("127.0.0.1", 40000)) as c:
        attacker = {"X-Forwarded-For": "6.6.6.6, 1.2.3.4"}  # leftmost is spoofed; Caddy appended 1.2.3.4
        for _ in range(FAIL_LIMIT):
            assert login(c, "bad", headers=attacker).status_code == 401
        assert login(c, "bad", headers=attacker).status_code == 429
        # changing the spoofable leftmost part doesn't help
        assert login(c, "bad", headers={"X-Forwarded-For": "7.7.7.7, 1.2.3.4"}).status_code == 429
        r = login(c, headers={"X-Forwarded-For": "5.6.7.8"})
        assert r.status_code == 303


def test_client_info_rules():
    assert ui_auth.DEFAULT_TRUSTED_PROXIES == "127.0.0.1/32,::1/128"
    i = client_info({"client": ("172.17.0.1", 1), "scheme": "http",
                     "headers": [(b"x-forwarded-for", b"1.2.3.4")]}, parse_networks(ui_auth.DEFAULT_TRUSTED_PROXIES))
    assert i.ip == "172.17.0.1"  # docker bridge not trusted by default
    trusted = parse_networks("127.0.0.1/32,::1/128,172.30.57.1/32")

    def scope(peer, **headers):
        return {"client": (peer, 1), "scheme": "http",
                "headers": [(k.replace("_", "-").encode(), v.encode()) for k, v in headers.items()]}

    i = client_info(scope("127.0.0.1", x_forwarded_for="6.6.6.6, 1.2.3.4", x_forwarded_proto="https"), trusted)
    assert (i.ip, i.https) == ("1.2.3.4", True)
    i = client_info(scope("172.30.57.1", x_forwarded_for="1.2.3.4"), trusted)  # compose network gateway
    assert (i.ip, i.https) == ("1.2.3.4", False)
    i = client_info(scope("8.8.8.8", x_forwarded_for="1.2.3.4", x_forwarded_proto="https"), trusted)
    assert (i.ip, i.https) == ("8.8.8.8", False)
    i = client_info(scope("127.0.0.1", x_forwarded_for="garbage"), trusted)
    assert i.ip == "127.0.0.1"
    i = client_info(scope("::ffff:127.0.0.1", x_forwarded_for="1.2.3.4"), trusted)
    assert i.ip == "1.2.3.4"


def test_limiter_window_and_bounds():
    lim = LoginLimiter(limit=5, window=600, max_ips=3)
    for t in range(5):
        lim.fail("a", now=100.0 + t)
    assert lim.retry_after("a", now=105.0) > 0
    assert lim.retry_after("b", now=105.0) == 0
    assert lim.retry_after("a", now=701.0) == 0  # oldest failure left the window
    for ip in ("b", "c", "d", "e"):
        lim.fail(ip, now=800.0)
    assert len(lim._fails) <= 3


# ---------- modes / secret ----------

def test_ui_disabled_without_password(tmp_path, test_video):
    with raw_client(tmp_path, ui_password="") as c:
        r = c.get("/")
        assert r.status_code == 503 and "UI_PASSWORD не задан или слишком простой (минимум 12 символов)" in r.text
        r = c.get("/ui-api/jobs")
        assert r.status_code == 503 and "UI_PASSWORD" in r.json()["detail"]
        assert c.get("/app.js").status_code == 503
        assert c.get("/login").status_code == 503
        assert login(c, "").status_code == 503
        r = upload(c, test_video)
        assert r.status_code == 200
        assert c.get(f"/api/v1/jobs/{r.json()['job_id']}", headers=HDR).status_code == 200
        assert c.get("/healthz").status_code == 200


def test_ui_open_has_no_gate(tmp_path):
    with raw_client(tmp_path, ui_password="", ui_open=True) as c:
        assert c.get("/ui-api/jobs").status_code == 200
        assert c.get("/").status_code == 200
        assert c.get("/app.js").status_code == 200


def test_secret_file_mode_and_stable(tmp_path):
    data = tmp_path / "data"
    s1 = load_or_create_secret(data)
    path = data / ui_auth.SECRET_FILE
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert len(s1) == 32 and load_or_create_secret(data) == s1
    # cookie survives an app restart with the persisted secret
    with raw_client(tmp_path) as c:
        login(c)
        cookie = c.cookies[COOKIE]
    with raw_client(tmp_path) as c:
        c.cookies.set(COOKIE, cookie)
        assert c.get("/ui-api/jobs").status_code == 200


def test_explicit_secret_ignores_file(tmp_path):
    with raw_client(tmp_path, ui_secret="explicit-secret-value") as c:
        login(c)
        cookie = c.cookies[COOKIE]
    assert not (tmp_path / "data" / ui_auth.SECRET_FILE).exists()
    with raw_client(tmp_path) as c:  # file-based secret now: old cookie invalid
        c.cookies.set(COOKIE, cookie)
        assert c.get("/ui-api/jobs").status_code == 401


def test_password_change_invalidates_sessions(tmp_path):
    with raw_client(tmp_path) as c:
        login(c)
        cookie = c.cookies[COOKIE]
    with raw_client(tmp_path, ui_password="another-pass") as c:
        c.cookies.set(COOKIE, cookie)
        assert c.get("/ui-api/jobs").status_code == 401
        assert login(c, "another-pass").status_code == 303


def test_dot_segment_bypass_raw_asgi(tmp_path):
    import asyncio

    app = create_app(make_settings(tmp_path))
    sent = []

    async def run():
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(msg):
            sent.append(msg)

        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
                 "scheme": "http", "path": "/api/v1/../app.js", "raw_path": b"/api/v1/../app.js",
                 "query_string": b"", "root_path": "", "headers": [], "client": ("1.1.1.1", 1),
                 "server": ("test", 80)}
        await app(scope, receive, send)

    asyncio.run(run())
    assert sent[0]["status"] == 401


def test_token_verify_unit():
    key = signing_key(b"s" * 32, "pw")
    tok = make_token(key, 1, now=1000.0)
    assert verify_token(key, tok, now=1001.0)
    assert not verify_token(key, tok, now=1000.0 + 3600)
    assert not verify_token(signing_key(b"s" * 32, "pw2"), tok, now=1001.0)
    assert not verify_token(key, None) and not verify_token(key, "") and not verify_token(key, "v1.a.b.c")
    assert not verify_token(key, "v1.1.2.3.ЖЖ")


@pytest.mark.parametrize("pw", ["change-me", "CHANGE-ME", "Change-Me", "short-pass", "  ", "x" * 11])
def test_placeholder_or_short_password_disables_ui(tmp_path, pw, caplog):
    with caplog.at_level("ERROR"):
        with raw_client(tmp_path, ui_password=pw) as c:
            r = c.get("/")
            assert r.status_code == 503 and "слишком простой" in r.text
            assert c.get("/ui-api/jobs").status_code == 503
            assert login(c, pw).status_code == 503
            assert c.get("/api/v1/jobs/nope", headers=HDR).status_code == 404
    if pw.strip():
        assert any("treated as UNSET" in rec.message for rec in caplog.records)


def test_usable_password_unit():
    assert ui_auth.usable_password("x" * 12) and not ui_auth.usable_password("x" * 11)
    assert not ui_auth.usable_password("change-me") and not ui_auth.usable_password(None)


def test_password_wins_over_ui_open(tmp_path, caplog):
    with caplog.at_level("WARNING"):
        with raw_client(tmp_path, ui_open=True) as c:
            assert c.get("/ui-api/jobs").status_code == 401
            assert login(c).status_code == 303
            assert c.get("/ui-api/jobs").status_code == 200
    assert any("UI_OPEN is ignored" in rec.message for rec in caplog.records)


def test_any_valid_cookie_value_is_accepted(tmp_path):
    with raw_client(tmp_path) as c:
        login(c)
        good = c.cookies[COOKIE]
        c.cookies.clear()
        for header in (f"{COOKIE}=v1.1.2.junk.sig; {COOKIE}={good}", f"{COOKIE}={good}; {COOKIE}=garbage"):
            assert c.get("/ui-api/jobs", headers={"Cookie": header}).status_code == 200, header
        assert c.get("/ui-api/jobs", headers={"Cookie": f"{COOKIE}=a; {COOKIE}=b"}).status_code == 401


def test_short_ui_secret_still_works(tmp_path, caplog):
    with caplog.at_level("WARNING"):
        with raw_client(tmp_path, ui_secret="short") as c:
            assert login(c).status_code == 303
            assert c.get("/ui-api/jobs").status_code == 200
    assert any("UI_SECRET is shorter than 32 bytes" in rec.message for rec in caplog.records)


def test_hsts_only_over_trusted_https(tmp_path, test_video):
    with raw_client(tmp_path, client=("127.0.0.1", 40000)) as c:
        https = {"X-Forwarded-Proto": "https"}
        for r in (c.get("/healthz", headers=https), c.get("/", headers=https, follow_redirects=False),
                  c.get("/api/v1/jobs/nope", headers={**https, **HDR}), login(c, headers=https)):
            assert r.headers.get("strict-transport-security") == "max-age=31536000", r.url
        assert "strict-transport-security" not in c.get("/healthz").headers
    with raw_client(tmp_path / "u", client=("8.8.8.8", 40000)) as c:
        assert "strict-transport-security" not in c.get("/healthz", headers={"X-Forwarded-Proto": "https"}).headers
