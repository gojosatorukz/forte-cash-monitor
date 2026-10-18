# Task 10: Human review step + printable inspection act

This is a team request; it makes the pitch stronger. **Do not change the jury-facing schema**: only extra fields may be added. The case brief §7 says the automatic result is a hint for a human, not a final verdict. This task makes that human step visible.

## Data model (server)
- New optional field `review` on `JobRecord`. It is persisted with the job JSON and returned as an extra field by `GET /api/v1/jobs/{id}` and `/ui-api/jobs/{id}`:
  ```json
  {"reviewer_name": "…", "reviewer_position": "…",
   "decision": "confirmed" | "rejected" | "needs_followup" | null,   // job-level final decision
   "decision_comment": "…",
   "violations": {"1": {"status": "confirmed" | "rejected" | "pending", "comment": "…", "updated_at": "ISO"}},
   "created_at": "ISO", "updated_at": "ISO", "decided_at": "ISO" | null}
  ```
  Violation keys are 1-based, matching `clip_url` numbering.
- `POST /api/v1/jobs/{id}/review` requires the API key. `POST /ui-api/jobs/{id}/review` needs no key.
  - The body is a partial update that is merged: any subset of `reviewer_name`, `reviewer_position`, `decision`, `decision_comment`, and `violations: {n: {status?, comment?}}`.
  - Validation:
    - Allowed only when the job status is `done` (409 otherwise). Unknown job → 404.
    - `n` must be in 1..len(violations) (422 otherwise).
    - Enum values are checked.
    - Strings are trimmed and capped: name/position 200 chars, comments 2000.
    - JSON body ≤ 64 KB.
  - Timestamps are set by the server.
  - Merging and the write happen under the JobStore lock (atomic write, as for jobs).
  - Returns the full public job.
- Setting `decision` sets `decided_at`. Clearing it (null) clears `decided_at`.
- No auth or identity checks; the FAQ says auth isn't required. The reviewer fields are free text.
- `GET /api/v1/feedback.jsonl` requires the API key; content type `application/x-ndjson`.
  - One line per reviewed violation (status ≠ pending), across all jobs, ordered by job created_at.
  - Fields: `{"kind":"violation", job_id, filename, video_meta, n, type_id, type, timecode_sec, start_sec, end_sec, fact_conf, legal_conf, legal_strength?, analyzer_description?, description, status, comment, reviewer_name, reviewer_position, updated_at}`.
  - Plus one `{"kind":"job", job_id, filename, verdict, decision, decision_comment, reviewer_name, reviewer_position, decided_at, n_violations, n_confirmed, n_rejected}` line per job that has a decision.
  - No images.
  - Also add `GET /ui-api/feedback.jsonl` without a key, so the UI can link to it.
  - README: document it as «данные для дообучения» (labelled feedback for retraining and threshold tuning).

## UI (result view, app/static)
- **Reviewer block.** A «Проверка специалистом» panel above or beside the violations list.
  - Inputs «ФИО проверяющего» and «Должность». They are remembered in localStorage (try/catch) and prefilled; saving sends them with the review.
- **Per violation card.** A review row:
  - segmented buttons «Подтвердить» / «Отклонить» (toggle; clicking again returns to pending);
  - a comment textarea, which shows a placeholder «Причина отклонения» when rejected;
  - status chips (Подтверждено, green; Отклонено, grey; Не проверено, neutral).
  - Saving is automatic (debounced ~600 ms for comments) through POST /ui-api/jobs/{id}/review, with an unobtrusive «Сохранено ✓ hh:mm» indicator. On error, show an inline message and a retry button.
- **Job decision.**
  - Buttons «Нарушение подтверждено» / «Нарушений нет» / «Требуется дополнительная проверка», plus a comment.
  - A summary line: «Проверено N из M».
- **Clean job (no violations).** The reviewer block still appears with the job decision, so a reviewer can still confirm «Нарушений нет».
- **«Сформировать акт».** A primary button that opens `/act.html#job=<id>` in a new tab. It is enabled once at least the reviewer name is filled in. Hint when disabled: «Укажите ФИО проверяющего».
- All text goes through textContent; no innerHTML with server strings. Polling must not overwrite inputs the user is editing: review data renders once per job open, and later state comes from POST responses.

## Act page (app/static/act.html + act.js + act.css)
A static page that renders from `/ui-api/jobs/{id}` (XSS-safe) and looks like an official A4 protocol in Russian.
- **Print CSS.** `@page { size: A4; margin: 18mm 16mm }`; serif body (system Times / PT Serif, no CDN); page-break rules (`break-inside: avoid` on each violation block); a print button and an «Сохранить в PDF» hint that are hidden in print.
- **Header.**
  - The ForteBank-style text mark «ForteBank · Служба внутреннего контроля». Text only, no real logo file.
  - Title «Акт проверки соблюдения кассовой дисциплины».
  - «№ <first 8 chars of job_id, uppercase>», with date and time of generation (local) and of analysis (finished_at).
- **Section 1, «Объект проверки».** File name, duration, resolution, analysis date/time, «Способ: автоматический видеоанализ с последующей проверкой специалистом».
- **Section 2, «Подтверждённые нарушения».** Numbered. For each:
  - type (title_ru + type_id), time interval / timecode (мм:сс);
  - the annotated evidence frame (evidence_frames[0], about 60 mm wide, with a caption timestamp; optionally a second frame);
  - description;
  - «Нарушенная норма»: document, point, quote in «…», adilet link printed as a full URL;
  - «Сила правовой нормы»; the reviewer comment.
  - If there are none: «Нарушений не подтверждено».
- **Section 3, «Отклонённые срабатывания».** A compact table: №, type, timecode, reason (comment). Omitted if empty.
- **Section 4, «Не рассмотренные».** Listed if any are still pending; the act shows a visible «ЧЕРНОВИК» watermark while anything is pending or the job decision is null.
- **Section 5, «Заключение».** The job decision as text, plus the decision comment.
- **Footer.**
  - «Проверяющий: <ФИО>, <должность>».
  - Signature line «Подпись ____________» and «Дата «__» ________ 20__ г.».
  - The disclaimer in a bordered box, verbatim: «Результат автоматического анализа является подсказкой для проверки человеком и не является окончательным юридическим или дисциплинарным заключением».
  - A small line: «Сформировано локальной системой видеоаналитики; распознавание лиц не применялось».
- **Screen view.** The same layout centred on a grey background, like a document preview.

## Tests (pytest)
- Review POST:
  - key required on /api/v1, none on /ui-api;
  - 404 unknown job, 409 when processing, 422 bad n or bad enum;
  - merge semantics (a partial update keeps other fields);
  - caps and trims; timestamps set; decided_at set and cleared;
  - persisted across an app restart;
  - concurrent POSTs don't lose updates (threaded, sequential lock).
- Public job JSON still passes the existing contract checks; `review` is extra.
- feedback.jsonl: key required, NDJSON lines parse, violation and job lines present with the right fields, pending excluded.
- Static: act.html is served; `node --check` on act.js and app.js.

## Verification
- Live smoke on port 18866 with STUB_DEBUG=1 and the crafted three-violation fixture.
  - Tooling: `/home/datamata/.claude/jobs/13d98288/tmp/ux9tools/` (load_fixtures.py with load_public/load_real; the shots/x17shots .mjs scripts; Chromium with CDP).
  - Use the real Х-17 public JSON in `/home/datamata/Projects/datathon2026/share/x17_boxes2/` if available.
- Fill in a review: confirm 2, reject 1 with a reason, set the job decision.
- Screenshots to `/home/datamata/Projects/datathon2026/share/screenshots/`:
  - `ui-review-desktop.png`, `ui-review-mobile-375.png`;
  - `act-screen.png`;
  - `act-print-page1.png` (and page 2 if any);
  - `act-print.pdf`, via Chromium CDP `Page.printToPDF` with A4 and print backgrounds; render page images from the PDF if possible (e.g. `pdftoppm`) for the PNGs.
- Look at every image yourself. Fix the layout until it looks like a credible official document.
