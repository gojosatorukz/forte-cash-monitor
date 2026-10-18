# Task 17: Mobile player timeline bug + «Процедура» panel

Orchestrator calls this Task 16. Deadline 05:30. It runs after the reviewer checklist task, because both touch app.js and act.js.

## (A) Bug: timeline and player time on iPhone (result page, mobile)
- **Symptom.** The timeline labels under the player show «00:56 … 00:56»; the start label should be 00:00. The native player shows «00:56 / 00:00».
- **Suspected causes:**
  - the duration and currentTime labels are mixed up;
  - the start label is filled from the wrong field;
  - duration is read before Safari has loaded the metadata (NaN or Infinity);
  - a pending seek or a `#t=` in the src makes the video open at its end.
- **Do this.** Find the root cause in app.js, video element handling and the timeline code:
  - The start label is always 00:00.
  - The end label comes from `video.duration` once it is finite, otherwise from `job.video_meta.duration_sec`.
  - Refresh labels on `loadedmetadata`, `durationchange` and `timeupdate`.
  - Never seek before metadata has loaded; keep the existing deferred-seek logic. Don't set currentTime to the duration.
  - Check the video route serves correct `Content-Length` and `Accept-Ranges`, and that `HEAD` and `Range: bytes=0-1` work (Safari probes with them).
- **Test.**
  - If Playwright with WebKit is installed, test with it.
  - Otherwise use headless Chromium with the 375 px iPhone UA and viewport. Emulate late metadata by delaying the video response through a throttled route.
  - Add a JS-level test, or document the manual check.

## (B) «Процедура» panel
- **Data.** forte-detector adds `procedure` to `AnalysisResult.meta`. Schema:
  ```
  {type, title_ru, confidence, basis_ru,
   steps: [{id, title_ru, status: ok|violation|risk|not_checked|manual, t_sec, type_ids: [..], rule, note_ru}]}
  ```
  - The service currently stores analyzer meta under `job.meta.analyzer`, capped at 8 KB. So the worker must PROMOTE `procedure` to `job.meta.procedure`, with its own validation and a 32 KB cap. That is an extra field; no change to the jury schema.
  - Validation: known statuses only (unknown → `not_checked`); strings capped; `t_sec` finite or null; at most 30 steps; `type_ids` is a list of strings.
  - Also keep tolerating `job.meta.procedure` set directly.
- **UI.** A panel above the violations list, hidden entirely when `meta.procedure` is absent.
  - Header «Операция: <title_ru>», with a confidence chip if present and the basis line `basis_ru`.
  - A vertical list of steps, each with a status icon:

    | status | icon | colour |
    |---|---|---|
    | ok | ✓ | green |
    | violation | ✕ | red |
    | risk | ⚠ | amber |
    | not_checked | — | grey |
    | manual | ✎ | blue |

  - Each step has its title and note.
  - A timecode button (mm:ss) seeks the player.
  - `type_ids` chips scroll to and highlight the matching violation card (by `type_id`); no link if there is no match.
  - A `manual` step links to the reviewer checklist block (scroll and focus).
  - Accessible: icons carry text labels, and the layout works at 375 px. XSS-safe (textContent only).
- **Act.** Section «Ход операции», placed before the violations: the same steps as a table (№, этап, статус, время, примечание), using the status words «Соблюдено / Нарушение / Риск / Не проверялось / Проверяется вручную». Hidden when absent.
- **Fixture.** Until the detector field lands, use a crafted job with `meta.procedure` (4–6 steps covering all statuses), for example a cash intake (приём наличных).
- **Tests.**
  - Worker promotion and validation, including the cap, unknown status and missing field.
  - The UI and act tolerate absence.
  - `node --check`.
  - The full suite is green.
- **Screenshots.** `ui-v2-procedure-desktop.png`, `ui-v2-procedure-mobile-375.png`, `act-procedure.png`, and for the bug fix `ui-v2-player-mobile-375-fixed.png`, all into `share/screenshots/`.
