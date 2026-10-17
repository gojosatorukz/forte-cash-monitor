# Task 12: UI access gate (URGENT, security)

**Problem.** On the public URL, `/ui-api/*` and the UI pages need no credentials. Anyone can list jobs, view evidence frames, clips and the uploaded videos (that is, the jury's test videos, which the case forbids publishing), read the review and feedback export, and post reviews.

**Goal.** A minimal, safe password gate for the browser UI. `/api/v1/*` stays API-key only and is unchanged (the jury uses it).

## Config
- **`UI_PASSWORD`** (env): required in production.
  - If unset and `UI_OPEN` is not `1`, the UI is disabled. Every gated route answers with a clear Russian message. Pages get a 503 HTML page «Веб-интерфейс отключён: не задан UI_PASSWORD»; `/ui-api/*` gets 503 JSON. Log a startup warning.
  - The `/api/v1` jury API keeps working.
- **`UI_OPEN=1`**: explicit dev/test escape hatch with no gate. Log a loud warning. Never set it in `.env.example`.
- **`UI_SECRET`** (env): the cookie signing key.
  - If unset, generate 32 random bytes once and persist them to `DATA_DIR/ui_secret` with mode 0600; reuse that file on restart.
  - With an explicit `UI_SECRET`, the file is ignored.
- **`UI_SESSION_HOURS`**: default 12.
- Update the `Settings`, README env table and `.env.example`, using a placeholder such as `UI_PASSWORD=change-me`.

## Session
- `GET /login`: a Russian login page in our style (inline CSS or app.css; no external assets). It has one password field and the «Войти» button, plus a short note «Доступ только для сотрудников; данные видеозаписей конфиденциальны».
- `POST /login`: form-encoded `password` and an optional `next`.
  - The password check uses `hmac.compare_digest` on UTF-8 bytes.
  - On success: set the cookie and 303 redirect to `next`. `next` must be a same-site relative path starting with `/` and not `//`; otherwise use `/`.
  - On failure: re-render with «Неверный пароль» and HTTP 401.
- The cookie `forte_ui`:
  - Value: an HMAC-SHA256-signed token `v1.<issued_ts>.<expiry_ts>.<nonce>.<sig>` (stdlib hmac, no new dependency), verified in constant time with expiry enforced.
  - Attributes: `HttpOnly; SameSite=Strict; Path=/; Max-Age=<hours*3600>`, plus `Secure` when the request is https.
  - Treat the request as https if `request.url.scheme == "https"` or `X-Forwarded-Proto: https` from a trusted proxy (see the rate-limit section). Production relay = Caddy on the team server (sets X-Forwarded-For/Proto); do not edit deploy/relay files.
  - Rotating the secret or changing the password invalidates all sessions: mix a hash of the password into the HMAC key.
- `POST /logout` (plus a `GET /logout` convenience): clear the cookie and redirect to `/login`. The UI header gets a «Выйти» link or button.
- **Rate limit on failed logins.**
  - Keyed by client IP, determined as follows (amended per orchestrator). Behind Caddy → SSH tunnel, every request's socket peer is loopback (bare-metal) or the Docker bridge gateway (compose: host sshd → docker-proxy → the container sees e.g. 172.17.0.1). Env `TRUSTED_PROXIES` (CIDR list, default `127.0.0.1/32,::1/128,172.16.0.0/12`). ONLY when the socket peer is in TRUSTED_PROXIES: use the RIGHTMOST entry of `X-Forwarded-For` (the one Caddy appended; leftmost entries are client-controlled and spoofable) and trust `X-Forwarded-Proto` for Secure. Otherwise use the socket IP and ignore both headers.
  - Tests: peer 127.0.0.1 + XFF "6.6.6.6, 1.2.3.4" → client 1.2.3.4; two different XFF clients via loopback are rate-limited independently (one locked out, the other still allowed); untrusted peer + XFF → socket IP used and XFP ignored.
  - After 5 failures within 10 minutes, return 429 «Слишком много попыток, попробуйте через N мин» until the window passes.
  - Keep it in memory with a lock and a bounded dict (prune old entries).
  - Successful logins don't reset other IPs.

## What is gated
- **Gated pages:** `/`, `/index.html`, `/act.html`, and any other HTML under the static mount. Without a valid cookie → 303 to `/login?next=<path>`.
- **Gated static assets:** the JS/CSS/assets under the static mount. Without a cookie → 401 (or a redirect; either is fine). Exempt only the assets the login page itself needs; better still, keep the login page self-contained.
- **Gated API:** all `/ui-api/*`, including the video and clip routes, `review`, `feedback.jsonl`, `rules` and `jobs`. Without a valid cookie → 401 JSON `{"detail": "Требуется вход"}`.
- **Not gated:**
  - `/api/v1/*`: unchanged, API key only, and the cookie has no effect there.
  - `/healthz`: stays public. It must not leak job data. Review what it returns: drop `public_url_source` if it's sensitive? (Probably fine.) It must never include job ids, filenames or counts per job. `rules_path`/`rules_count` are OK.
  - `/login`, `/logout`.
- **Implementation:** an ASGI middleware or a dependency applied centrally, so new routes are gated by default. Prefer an allowlist: anything not in {`/api/v1/*`, `/healthz`, `/login`, `/logout`} needs the cookie.
- **CSRF:** SameSite=Strict, plus the existing JSON-only check on review POSTs.
- **Frontend:** `app.js` must handle a 401 from `/ui-api` by redirecting to `/login?next=<current path + hash>` (the hash is kept client-side via `next`, or after login via sessionStorage). Fetch is same-origin, so the cookie is sent automatically.

## Tests
For each route class, check three cases (no cookie, a valid cookie, an expired or tampered cookie):
- pages → redirect;
- static JS → 401;
- `/ui-api/jobs` → 401;
- video, clip, review POST, feedback → 401.

Also:
- The API key path is unaffected: `/api/v1/analyze` and `/api/v1/jobs` work with the key and no cookie, and a cookie without the key still gets 401.
- `/healthz` is public and contains no job data.
- Login success sets the cookie with HttpOnly and SameSite=Strict; Secure appears when `X-Forwarded-Proto: https` is set.
- A wrong password → 401. Six failures → 429.
- `next` sanitising (`//evil.com`, `https://evil.com` → `/`).
- Logout clears the cookie.
- With `UI_PASSWORD` unset and no `UI_OPEN` → 503 on gated routes, while `/api/v1` still works.
- With `UI_OPEN=1` → no gate.
- The secret file is generated with mode 0600 and is stable across restarts.
- A password change invalidates old cookies.

Existing tests that hit `/ui-api` need either `UI_OPEN=1` in their settings or a logged-in client fixture. Prefer a fixture that logs in, so the gate itself is exercised.

## Deploy docs
README «День защиты» checklist: set `UI_PASSWORD` (a strong one, shared only within the team) and verify from outside:
- `curl -I https://forte.incode.kz/` → 303 to /login;
- `curl https://forte.incode.kz/ui-api/jobs` → 401;
- `/api/v1` works with the key.
