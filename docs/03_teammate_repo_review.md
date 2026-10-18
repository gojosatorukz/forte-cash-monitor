# 03 — Teammate repo review: gojosatorukz/forte-cash-monitor

Reviewed 2026-10-17 at commit `20f30ad` (shallow clone, code not executed). The clone is in the
session scratchpad, and so are the screenshots `bekzat_ui.png` (1440 px) and `bekzat_ui_390.png`.

## 1. What the repo is

- **Private** repo, Python, no LICENSE file. 56 files, about 11k lines including the lock file.
- **DataMata2 has push access** (`permissions`: pull/triage/push = true, admin/maintain = false).
- **Stack:** FastAPI + uvicorn + pydantic-settings + Pillow + python-multipart. SQLite job store,
  worker thread, deadline watchdog, 24 h retention. Vanilla JS/CSS frontend (`frontend/index.html`,
  `static/app.js` 829 lines, `app.css` 1847 lines) with local Open Sans and the official Forte logo SVGs.
  It has no build step and loads nothing from a CDN.
- **The repo is a full service scaffold, not just a UI. It has no detection.** `inference/base.py`
  defines an `Analyzer` protocol, and `inference/local.py` is left for an ML dev who has not written it.
  `FCM_ANALYZER_FACTORY` is empty, so `/readyz` returns 503 `analyzer_not_configured` and
  `POST /api/v1/analyze` returns 503. The live tunnel confirms this:
  `{"ready":false,"reason":"analyzer_not_configured"}`.
- Endpoints, all requiring `X-API-Key`, including the UI ones:
  - Jury contract: `/api/v1/analyze` and `/api/v1/jobs/{id}`, same shape as ours.
  - Extras: `/api/v1/service`, `/jobs` (paged), `/jobs/{id}/details`, `/jobs/{id}/video`,
    `/jobs/{id}/report?format=json|html`, `/rules`, plus `/healthz` and `/readyz`.
- The browser UI asks the user to paste the API key ("Подключить ключ"), which `app.js` sends as a header.
- Extra scripts: a Cloudflare quick-tunnel launcher (`scripts/install_tunnel.py` downloads cloudflared
  from GitHub releases), a smoke test, and `Запустить_сайт.command` (macOS launcher).
- CI (`.github/workflows/ci.yml`) runs on every push and PR: `ruff check`, `ruff format --check`,
  `node --check`, and `pytest` on synthetic fixtures.

## 2. Compliance checks

| Check | Result |
|---|---|
| Cloud AI APIs (OpenAI/Gemini/Claude/HF Inference/…) | **None in code.** `AGENTS.md` and the `Analyzer` docstring forbid them, and `.env.example` sets `HF_HUB_OFFLINE=1`. `docs/models.md` only *suggests* local Qwen3-VL and YOLOX. |
| AGPL/GPL deps | **None.** All runtime and dev deps are MIT, BSD, Apache or HPND (Pillow). No ultralytics or torch. Font: SIL OFL 1.1 (license included). |
| Committed secrets | **None found.** `.env` is gitignored, and `.env.example` holds only a placeholder. Tests use the literal placeholder `replace-with-a-random-local-secret`. No tokens, keys or tunnel credentials. |
| Other | It uses the official Forte logo and favicon (`docs/design.md` sources them from forte.kz/en/logo). That is fine for a ForteBank case prototype labelled "командный прототип". The repo has no LICENSE file. |

Our side: neither `service/pyproject.toml` nor `detector/pyproject.toml` mentions ultralytics or cloud
AI SDKs.

## 3. Design comparison

Bekzat's UI shows only the empty and waiting state, because no analysis can run. Ours was compared
using `share/screenshots/ui-home-desktop.png`, `ui-x17-result-desktop.png` and
`ui-x17-card-clip-desktop.png`.

**Better in his design**
- It reads as a polished banking product: left sidebar with the real Forte logo, breadcrumbs,
  a hero line ("Каждое событие — с доказательством."), numbered cards ("01 Видеозапись",
  "02 Результат проверки") and a 1-2-3 empty state.
- Brand-accurate palette (#A31551 / #4A0221) and the local Open Sans font.
- A clear readiness banner, a "Сервер работает" status pill and a "Обработка на устройстве" privacy note.
- JSON export and an offline HTML report you can print to PDF. The report is a nice deliverable.
- The 390 px layout is clean: the sidebar collapses into a header and the cards stack.

**Better in ours**
- It shows real results. Each violation card has an annotated evidence frame ("Разметка"), the clip,
  a time range, a confidence bar, the strength of the legal norm, and a cited НБРК №231 clause with
  an adilet link.
- An event bar on the video timeline, video metadata, and a history table with verdict chips and
  "неполный" (incomplete) marks.
- No API key in the browser: `/ui-api/*` routes take no key. For a jury demo, a key prompt is friction.
- It is proven end-to-end on the live https://forte.incode.kz.

His design has no states yet for results with clips, annotated frames, partial/incomplete results,
or confidence and norm strength. All of that would have to be built against our payload.

## 4. Recommendation (least risk before 12:00)

1. **Our service stays the backend and the submission.** Do not change `/api/v1/*` or the
   forte.incode.kz deployment. His backend adds nothing the jury needs, and his strict schema
   (`extra="forbid"`) would reject our `clip_url` and box fields.
2. **Do not push our code onto his `main`.** His CI runs ruff and pytest over the whole repo, so a
   root-level push would turn CI red and mix two FastAPI apps. If the team wants one repo, push to
   a branch such as `datamata/service` (or a `service/` and `detector/` subtree on a branch) and keep
   his `main` untouched. DataMata2 can push, but agree on this with Bekzat first.
3. **Adopt his visual language, not his frontend code.** This is the cheapest win, about 1–2 h for
   one person, and stays inside `service/app/static/`:
   - Copy `brand/forte-*.svg`, `favicon.svg` and `fonts/OpenSans.ttf` + `OFL.txt` into `static/`.
   - Port the CSS tokens (magenta and dark-magenta palette, card and section-header style, sidebar
     or header logo, readiness banner) into our `app.css`.
   - Keep our `app.js` logic and the `/ui-api` calls.
   - Check again at 1440 and 390 px and on X-17.
4. **Swapping in his `app.js` wholesale is not recommended tonight.** It would mean rewriting it
   against our endpoints: `/ui-api/jobs` instead of the paged `/jobs`, our job payload instead of
   `/details`, no key, plus clips, boxes and incomplete states. That is 4–6 h of risky UI work for
   no scoring gain. The HTML report export is the one feature worth porting, and only if time remains.
5. **Freeze rule:** any restyle lands only if `/api/v1` tests pass and a smoke run on X-17 / X-43
   matches the current output. Otherwise keep the current UI. It already works.
