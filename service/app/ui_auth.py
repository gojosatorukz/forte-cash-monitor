"""Password gate for the browser UI (pages, static assets, /ui-api/*).

/api/v1/* (X-API-Key), /healthz, /login and /logout are not gated; everything else needs a
valid signed session cookie. Implemented as a pure ASGI middleware so new routes are gated
by default (allowlist), and streaming/Range responses pass through untouched.

Stdlib only: the cookie is `v1.<issued>.<expiry>.<nonce>.<hmac-sha256 hex>`; the HMAC key
mixes the UI secret with a hash of UI_PASSWORD, so rotating either invalidates all sessions.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import ipaddress
import logging
import math
import os
import secrets
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote

log = logging.getLogger(__name__)

COOKIE = "forte_ui"
TOKEN_VERSION = "v1"
SECRET_FILE = "ui_secret"
DEFAULT_TRUSTED_PROXIES = "127.0.0.1/32,::1/128"
MAX_LOGIN_BODY = 8 * 1024

# Generous enough that a shared venue NAT IP can't easily lock the jury out, still far too slow to
# brute-force a 12+ character password (20 guesses / 5 min per IP).
FAIL_LIMIT = 20
FAIL_WINDOW_SEC = 300.0
MIN_PASSWORD_LEN = 12
PLACEHOLDER_PASSWORDS = {"change-me", "changeme"}
MAX_TRACKED_IPS = 4096

LOGIN_REQUIRED = "Требуется вход"
UI_DISABLED = "Веб-интерфейс отключён: UI_PASSWORD не задан или слишком простой (минимум 12 символов)"
# The login/disabled pages are self-contained except for this one font file (exact path, no prefix match).
LOGIN_FONT_PATH = "/assets/fonts/OpenSans.woff2"
PUBLIC_EXACT = {"/healthz", "/login", "/logout", LOGIN_FONT_PATH}


# ---------- secret / signing ----------

def load_or_create_secret(data_dir: Path) -> bytes:
    """32 random bytes persisted (hex) in DATA_DIR/ui_secret with mode 0600, reused on restart."""
    path = data_dir / SECRET_FILE
    data_dir.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            raw = path.read_text(encoding="ascii").strip()
            secret = bytes.fromhex(raw)
            if len(secret) >= 32:
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
                return secret
            log.warning("%s is too short or malformed; regenerating it (all UI sessions end)", path)
            path.unlink(missing_ok=True)
        except FileNotFoundError:
            pass
        except (ValueError, UnicodeDecodeError):
            log.warning("%s is malformed; regenerating it (all UI sessions end)", path)
            path.unlink(missing_ok=True)
        secret = secrets.token_bytes(32)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue  # another process won the race: read theirs
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(secret.hex() + "\n")
        log.info("Generated UI session secret in %s", path)
        return secret
    raise RuntimeError(f"cannot read or create {path}")


def usable_password(password: str | None) -> bool:
    """A placeholder or short UI_PASSWORD counts as unset (UI disabled)."""
    if not password or not password.strip():
        return False
    return password.strip().lower() not in PLACEHOLDER_PASSWORDS and len(password) >= MIN_PASSWORD_LEN


def signing_key(secret: bytes, password: str) -> bytes:
    pw_hash = hashlib.sha256(password.encode("utf-8")).digest()
    return hmac.new(secret, b"forte-ui-session|" + pw_hash, hashlib.sha256).digest()


def make_token(key: bytes, hours: float, now: float | None = None) -> str:
    issued = int(now if now is not None else time.time())
    expiry = issued + int(hours * 3600)
    payload = f"{TOKEN_VERSION}.{issued}.{expiry}.{secrets.token_hex(16)}"
    sig = hmac.new(key, payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_token(key: bytes, token: str | None, now: float | None = None) -> bool:
    if not token or len(token) > 256:
        return False
    parts = token.split(".")
    if len(parts) != 5 or parts[0] != TOKEN_VERSION:
        return False
    payload, sig = token.rsplit(".", 1)
    try:
        expected = hmac.new(key, payload.encode("ascii"), hashlib.sha256).hexdigest()
    except UnicodeEncodeError:
        return False
    if not hmac.compare_digest(expected.encode("ascii"), sig.encode("ascii", "replace")):
        return False
    try:
        issued, expiry = int(parts[1]), int(parts[2])
    except ValueError:
        return False
    t = now if now is not None else time.time()
    return issued <= t + 300 and t < expiry


# ---------- client IP / proxies ----------

def parse_networks(spec: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    nets = []
    for item in (spec or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            log.warning("TRUSTED_PROXIES: ignoring invalid entry %r", item)
    return nets


def _ip(value: str | None):
    if not value:
        return None
    try:
        addr = ipaddress.ip_address(value.strip().strip("[]"))
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        return addr.ipv4_mapped
    return addr


def _header(scope: dict, name: bytes) -> str | None:
    """Last value of a header (duplicates: the one appended last, i.e. by the nearest proxy)."""
    found = None
    for k, v in scope.get("headers") or []:
        if k.lower() == name:
            found = v.decode("latin-1")
    return found


class ClientInfo:
    __slots__ = ("ip", "https")

    def __init__(self, ip: str, https: bool):
        self.ip = ip
        self.https = https


def client_info(scope: dict, trusted: list) -> ClientInfo:
    """Rate-limit key and https flag.

    Only when the socket peer is a trusted proxy: the RIGHTMOST X-Forwarded-For entry (appended
    by our proxy; entries to its left are client-controlled) and X-Forwarded-Proto are used.
    Otherwise the socket IP is used and both headers are ignored."""
    client = scope.get("client")
    peer_raw = client[0] if client else ""
    peer = _ip(peer_raw)
    ip = str(peer) if peer is not None else (peer_raw or "unknown")
    https = scope.get("scheme") == "https"
    if peer is not None and any(peer in net for net in trusted if net.version == peer.version):
        xff = _header(scope, b"x-forwarded-for")
        if xff:
            last = _ip(xff.split(",")[-1])
            if last is not None:
                ip = str(last)
        xfp = _header(scope, b"x-forwarded-proto")
        if xfp and xfp.split(",")[-1].strip().lower() == "https":
            https = True
    return ClientInfo(ip, https)


# ---------- rate limit ----------

class LoginLimiter:
    """Failed logins per client IP: FAIL_LIMIT within FAIL_WINDOW_SEC -> locked until the window passes."""

    def __init__(self, limit: int = FAIL_LIMIT, window: float = FAIL_WINDOW_SEC, max_ips: int = MAX_TRACKED_IPS):
        self.limit = limit
        self.window = window
        self.max_ips = max_ips
        self._fails: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _trim(self, q: deque[float], now: float) -> None:
        while q and q[0] <= now - self.window:
            q.popleft()

    def retry_after(self, ip: str, now: float | None = None) -> float:
        """Seconds until the next attempt is allowed (0 = allowed now)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._fails.get(ip)
            if not q:
                return 0.0
            self._trim(q, now)
            if not q:
                del self._fails[ip]
                return 0.0
            if len(q) < self.limit:
                return 0.0
            return max(0.0, q[-self.limit] + self.window - now)

    def fail(self, ip: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._fails.get(ip)
            if q is None:
                if len(self._fails) >= self.max_ips:
                    self._prune(now)
                q = self._fails[ip] = deque(maxlen=self.limit)
            self._trim(q, now)
            q.append(now)

    def reset(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)

    def _prune(self, now: float) -> None:
        for k in list(self._fails):
            q = self._fails[k]
            self._trim(q, now)
            if not q:
                del self._fails[k]
        while len(self._fails) >= self.max_ips:  # still full: drop the oldest-tracked IPs
            del self._fails[next(iter(self._fails))]


# ---------- helpers ----------

def safe_next(value: str | None) -> str:
    """Same-site relative path only ("/..." but not "//" or "/\\"), else "/"."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value) or len(value) > 2048:
        return "/"
    if value == "/login" or value.startswith(("/login?", "/login#", "/logout")):
        return "/"
    return value


def _is_public(path: str) -> bool:
    segments = path.split("/")
    if "." in segments or ".." in segments:
        return False  # dot segments could reach the static mount via StaticFiles' normpath
    return path in PUBLIC_EXACT or path == "/api/v1" or path.startswith("/api/v1/")


def _is_page(path: str) -> bool:
    last = path.rsplit("/", 1)[-1]
    return path.endswith("/") or last.endswith((".html", ".htm")) or "." not in last


LOGIN_CSS = """
@font-face{font-family:"Open Sans";src:url("/assets/fonts/OpenSans.woff2") format("woff2");font-weight:300 800;
font-style:normal;font-display:swap}
:root{--brand:#A31551;--brand-h:#891345;--brand-d:#4A0221;--soft:#F8EDF2;--ink:#2C2630;--muted:#736B78;
--line:#E9E5EC;--bg:#F7F7F9;--err:#B3261E}
*{box-sizing:border-box}html,body{margin:0;min-height:100%}
body{font-family:"Open Sans",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
background:radial-gradient(1200px 600px at 100% 0,#F3E3EA 0,rgba(243,227,234,0) 60%),var(--bg);color:var(--ink);
display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px 16px;-webkit-font-smoothing:antialiased}
.card{width:100%;max-width:420px;background:#fff;border:1px solid var(--line);border-radius:14px;
padding:34px 32px 28px;box-shadow:0 1px 2px rgba(44,38,48,.04),0 18px 48px rgba(74,2,33,.08)}
.brand{margin-bottom:26px}.brand svg{display:block;width:118px;height:auto}
.brand b{display:block;margin-top:12px;font-size:15px;font-weight:650;color:var(--brand-d)}
.brand span{display:block;margin-top:4px;font-size:10px;font-weight:600;letter-spacing:1.2px;text-transform:uppercase;
color:var(--muted)}
.eyebrow{font-size:11px;font-weight:650;letter-spacing:1.5px;text-transform:uppercase;color:var(--brand);margin:0 0 6px}
h1{font-size:24px;font-weight:650;letter-spacing:-.5px;line-height:1.25;margin:0 0 20px}
label{display:block;font-size:13px;font-weight:650;margin-bottom:6px;color:#5A5360}
input[type=password]{width:100%;font:inherit;font-size:16px;padding:11px 13px;border:1px solid #D7D2D8;
border-radius:8px;background:#fff;color:var(--ink);transition:border-color .15s,box-shadow .15s}
input[type=password]:focus{outline:0;border-color:var(--brand);box-shadow:0 0 0 3px rgba(163,21,81,.22)}
button{margin-top:16px;width:100%;font:inherit;font-size:15px;font-weight:650;padding:12px;border:0;
border-radius:8px;background:var(--brand);color:#fff;cursor:pointer;box-shadow:0 1px 2px rgba(74,2,33,.25)}
button:hover{background:var(--brand-h)}button:focus-visible{outline:3px solid rgba(163,21,81,.35);outline-offset:2px}
.err{margin:0 0 14px;padding:10px 12px;border-radius:8px;background:#FDF0EF;border:1px solid #F2C9C5;color:var(--err);
font-size:14px}
.note{display:flex;gap:10px;margin:22px 0 0;padding-top:18px;border-top:1px solid var(--line);font-size:13px;
color:var(--muted);line-height:1.5}
.note svg{flex:none;width:18px;height:18px;color:var(--brand);margin-top:1px}
.foot{margin-top:16px;text-align:center;font-size:10px;font-weight:600;letter-spacing:1px;text-transform:uppercase;
color:#9C96A0}
.disabled h1{font-size:19px;line-height:1.35}
@media (max-width:420px){.card{padding:28px 20px 22px}h1{font-size:22px}}
"""

_ASSETS = Path(__file__).resolve().parent / "static" / "assets" / "brand"


def _asset_svg(name: str) -> str:
    """Trusted, bundled brand SVG (official Forte artwork), inlined so /login needs no gated asset."""
    try:
        return (_ASSETS / name).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


# Official Forte logo (assets/brand/forte-magenta.svg), inlined; the alt text is on the wrapper.
BRAND_SVG = _asset_svg("forte-magenta.svg").replace("<svg ", '<svg aria-hidden="true" focusable="false" ', 1)

_FAVICON_SVG = _asset_svg("favicon.svg")
FAVICON = ("data:image/svg+xml," + quote(_FAVICON_SVG, safe=" =:/'\"")) if _FAVICON_SVG else ""

SHIELD_SVG = (
    '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 2.5l6 2.4v4.4c0 4-2.6 7-6 8.2-3.4-1.2-6-4.2-6-8.2V4.9z" '
    'fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M7.3 10l2 2 3.6-3.8" '
    'fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>'
)


def _page(title: str, body: str, cls: str = "") -> str:
    icon = f'<link rel="icon" href="{html.escape(FAVICON, quote=True)}" type="image/svg+xml">' if FAVICON else ""
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta name="robots" content="noindex"><meta name="theme-color" content="#4A0221">'
        f'<title>{html.escape(title)}</title>'
        f'{icon}<style>{LOGIN_CSS}</style></head><body>'
        f'<div><main class="card{(" " + cls) if cls else ""}">{body}</main>'
        '<div class="foot">Forte Cash Monitor · Datathon 26</div></div></body></html>'
    )


def _brand() -> str:
    return ('<div class="brand"><div role="img" aria-label="Forte">' + BRAND_SVG + '</div>'
            '<b>Cash Monitor</b><span>Контроль кассовой дисциплины</span></div>')


def login_page(next_path: str, error: str | None = None) -> str:
    err = f'<p class="err" role="alert">{html.escape(error)}</p>' if error else ""
    body = (
        f'{_brand()}<p class="eyebrow">Рабочее пространство</p><h1>Вход в систему</h1>{err}'
        '<form method="post" action="/login" id="login-form">'
        f'<input type="hidden" name="next" id="next" value="{html.escape(next_path, quote=True)}">'
        '<label for="password">Пароль</label>'
        '<input type="password" id="password" name="password" autocomplete="current-password" required autofocus>'
        '<button type="submit">Войти</button></form>'
        f'<p class="note">{SHIELD_SVG}<span>Доступ только для сотрудников; данные видеозаписей конфиденциальны. '
        'Видео обрабатываются локально.</span></p>'
        # The fragment (#job=...) never reaches the server; keep it across the login round-trip.
        '<script>(function(){var n=document.getElementById("next");'
        'if(location.hash&&n.value.indexOf("#")<0)n.value+=location.hash;})();</script>'
    )
    return _page("Вход — Forte Cash Monitor", body)


def disabled_page() -> str:
    return _page("Веб-интерфейс отключён", f'{_brand()}<h1>{html.escape(UI_DISABLED)}</h1>'
                 '<p class="note">API для внешних систем (/api/v1) работает. Администратору: задайте '
                 'стойкий UI_PASSWORD (не короче 12 символов, не «change-me») в .env и перезапустите '
                 'сервис.</p>', cls='disabled')


# ---------- ASGI plumbing ----------

SEC_HEADERS = [
    (b"cache-control", b"no-store"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"same-origin"),
    (b"x-content-type-options", b"nosniff"),
]


async def _send(send, status: int, body: bytes, content_type: bytes, extra: list | None = None) -> None:
    headers = [(b"content-type", content_type), (b"content-length", str(len(body)).encode())]
    headers += SEC_HEADERS + (extra or [])
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def _json(detail: str) -> bytes:
    return json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")


def _cookies_from(scope: dict) -> list[str]:
    """Every `forte_ui` value in the request (a sibling subdomain could toss in extra ones)."""
    out = []
    for k, v in scope.get("headers") or []:
        if k.lower() != b"cookie":
            continue
        for part in v.decode("latin-1").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE:
                out.append(value.strip().strip('"'))
    return out[:8]


class UiGate:
    """Pure ASGI middleware. Modes: "password" (gate), "open" (UI_OPEN=1, no gate), "disabled" (503)."""

    def __init__(self, app, settings: Any):
        self.app = app
        self.hours = float(settings.ui_session_hours)
        self.trusted = parse_networks(settings.trusted_proxies)
        self.limiter = LoginLimiter()
        password_ok = usable_password(settings.ui_password)
        if settings.ui_password and not password_ok:
            log.error("UI_PASSWORD is a placeholder or shorter than %d characters: treated as UNSET, the web UI "
                      "is disabled (503). Set a strong UI_PASSWORD.", MIN_PASSWORD_LEN)
        if password_ok:
            self.mode = "password"
            if settings.ui_open:
                log.warning("Both UI_PASSWORD and UI_OPEN=1 are set: UI_OPEN is ignored, the password gate is on.")
        elif settings.ui_open:
            self.mode = "open"
            log.warning("!!! UI_OPEN=1: the web UI and /ui-api are served WITHOUT a password. "
                        "Never use this on a public address. !!!")
        else:
            self.mode = "disabled"
            log.warning("Web UI disabled: the web UI and /ui-api answer 503 (the /api/v1 jury API works). "
                        "Set UI_PASSWORD (at least %d characters) to enable it.", MIN_PASSWORD_LEN)
        self.key = b""
        if self.mode == "password":
            if settings.ui_secret:
                secret = settings.ui_secret.encode("utf-8")
                if len(secret) < 32:
                    log.warning("UI_SECRET is shorter than 32 bytes; deriving the key with SHA-256 "
                                "(use a long random value)")
                    secret = hashlib.sha256(b"forte-ui-secret|" + secret).digest()
            else:
                secret = load_or_create_secret(Path(settings.data_dir))
            self.key = signing_key(secret, settings.ui_password)
            self.password = settings.ui_password.encode("utf-8")

    def _valid(self, scope: dict) -> bool:
        return any(verify_token(self.key, v) for v in _cookies_from(scope))

    def _cookie_header(self, value: str, max_age: int, https: bool) -> tuple[bytes, bytes]:
        attrs = f"{COOKIE}={value}; HttpOnly; SameSite=Strict; Path=/; Max-Age={max_age}"
        if https:
            attrs += "; Secure"
        return (b"set-cookie", attrs.encode("ascii"))

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if client_info(scope, self.trusted).https:
            send = _with_hsts(send)
        path = scope.get("path") or "/"
        if path == "/login":
            await self._login(scope, receive, send)
            return
        if path == "/logout":
            await self._logout(scope, send)
            return
        if self.mode == "open" or _is_public(path):
            await self.app(scope, receive, send)
            return
        if self.mode == "disabled":
            if path.startswith("/ui-api/") or path == "/ui-api":
                await _send(send, 503, _json(UI_DISABLED), b"application/json")
            else:
                await _send(send, 503, disabled_page().encode(), b"text/html; charset=utf-8")
            return
        if self._valid(scope):
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET")
        if not path.startswith("/ui-api") and method in ("GET", "HEAD") and _is_page(path):
            qs = scope.get("query_string", b"").decode("latin-1")
            target = path + ("?" + qs if qs else "")
            loc = "/login?next=" + quote(target, safe="/")
            await _send(send, 303, b"", b"text/plain; charset=utf-8", [(b"location", loc.encode("latin-1"))])
            return
        await _send(send, 401, _json(LOGIN_REQUIRED), b"application/json")

    async def _login(self, scope, receive, send):
        method = scope.get("method", "GET")
        if self.mode == "disabled":
            await _send(send, 503, disabled_page().encode(), b"text/html; charset=utf-8")
            return
        qs = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        if method in ("GET", "HEAD"):
            nxt = safe_next((qs.get("next") or [""])[0])
            if self.mode == "open" or self._valid(scope):
                await _send(send, 303, b"", b"text/plain", [(b"location", _location(nxt))])
                return
            await _send(send, 200, login_page(nxt).encode(), b"text/html; charset=utf-8")
            return
        if method != "POST":
            await _send(send, 405, _json("Используйте GET или POST"), b"application/json",
                        [(b"allow", b"GET, HEAD, POST")])
            return
        body = bytearray()
        while True:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                return
            body += msg.get("body", b"")
            if len(body) > MAX_LOGIN_BODY:
                await _send(send, 413, _json("Слишком большой запрос"), b"application/json")
                return
            if not msg.get("more_body"):
                break
        form = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
        nxt = safe_next((form.get("next") or (qs.get("next") or [""]))[0])
        if self.mode == "open":
            await _send(send, 303, b"", b"text/plain", [(b"location", _location(nxt))])
            return
        info = client_info(scope, self.trusted)
        wait = self.limiter.retry_after(info.ip)
        if wait > 0:
            mins = max(1, math.ceil(wait / 60))
            msg = f"Слишком много попыток, попробуйте через {mins} мин"
            await _send(send, 429, login_page(nxt, msg).encode(), b"text/html; charset=utf-8",
                        [(b"retry-after", str(math.ceil(wait)).encode())])
            return
        password = (form.get("password") or [""])[0].encode("utf-8")
        if not hmac.compare_digest(password, self.password):
            self.limiter.fail(info.ip)
            log.warning("UI login failed from %s", info.ip)
            await _send(send, 401, login_page(nxt, "Неверный пароль").encode(), b"text/html; charset=utf-8")
            return
        self.limiter.reset(info.ip)
        token = make_token(self.key, self.hours)
        log.info("UI login from %s", info.ip)
        await _send(send, 303, b"", b"text/plain", [
            (b"location", _location(nxt)),
            self._cookie_header(token, int(self.hours * 3600), info.https),
        ])

    async def _logout(self, scope, send):
        if scope.get("method", "GET") not in ("GET", "HEAD", "POST"):
            await _send(send, 405, _json("Используйте GET или POST"), b"application/json",
                        [(b"allow", b"GET, HEAD, POST")])
            return
        info = client_info(scope, self.trusted)
        await _send(send, 303, b"", b"text/plain", [
            (b"location", b"/login"),
            self._cookie_header("", 0, info.https),
        ])


def _with_hsts(send):
    async def wrapped(message):
        if message["type"] == "http.response.start":
            headers = [h for h in message.get("headers", []) if h[0].lower() != b"strict-transport-security"]
            headers.append((b"strict-transport-security", b"max-age=31536000"))
            message = {**message, "headers": headers}
        await send(message)
    return wrapped


def _location(nxt: str) -> bytes:
    # Non-ASCII in the path/fragment: percent-encode, keep the URL structure.
    return quote(nxt, safe="/?#&=%:+,;@!$'()*~-._").encode("latin-1")
