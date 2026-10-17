"use strict";
const $ = (id) => document.getElementById(id);
const state = {
  key: "",
  service: null,
  ready: false,
  file: null,
  videoUrl: null,
  sourceJobId: null,
  job: null,
  epoch: 0,
  offset: 0,
  uploading: false,
  polling: false,
};
const STAGES = {
  queued: "Ожидает в очереди",
  analyzing: "Анализируется",
  decoding: "Чтение видеозаписи",
  scanning: "Поиск событий",
  verifying: "Проверка условий",
  evidence: "Подготовка доказательств",
  done: "Готово",
  failed: "Ошибка обработки",
};
const STATUS = {
  processing: "Обработка",
  failed: "Не завершена",
  violation: "Признаки нарушения",
  no_violation: "Не обнаружено",
};
let toastTimer;
let historyLastUpdated = 0;
let serviceLastUpdated = 0;
const timecode = (s) =>
  !Number.isFinite(s)
    ? "—"
    : `${String(Math.floor(s / 60)).padStart(2, "0")}:${(s % 60).toFixed(1).padStart(4, "0")}`;
const sizeLabel = (bytes) =>
  bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} КБ`
    : `${(bytes / 1024 / 1024).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} МБ`;
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};
function visualIcon(name, danger = false) {
  const box = el("div", danger ? "empty-symbol danger" : "empty-symbol");
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `/static/icons.svg#${name}`);
  svg.append(use);
  box.append(svg);
  return box;
}
function toast(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    $("toast").hidden = true;
  }, 5000);
}
function errorAt(id, text) {
  $(id).textContent = text;
  $(id).hidden = !text;
}
function friendlyError(status, detail) {
  const messages = {
    401: "Ключ не принят. Проверьте API-ключ и подключитесь снова.",
    404: "Проверка или исходный файл не найдены. Возможно, срок хранения истёк.",
    408: "Загрузка заняла слишком много времени. Попробуйте снова.",
    413: "Видео превышает допустимый размер.",
    415: "Выберите видео в формате MOV.",
    422: "Не удалось принять файл или параметры запроса.",
    429: "Очередь заполнена. Дождитесь завершения текущих проверок.",
    503: "Локальный анализатор сейчас недоступен. Проверьте готовность сервиса.",
  };
  return (
    messages[status] ||
    (typeof detail === "string"
      ? detail
      : "Не удалось выполнить запрос. Попробуйте снова.")
  );
}
async function api(path, options = {}, key = state.key) {
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers: { ...options.headers, "X-API-Key": key },
      cache: "no-store",
      signal: options.signal || AbortSignal.timeout(20000),
    });
  } catch {
    throw new Error(
      "Нет ответа от сервера. Проверьте соединение и повторите запрос.",
    );
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error = new Error(friendlyError(response.status, body.detail));
    error.status = response.status;
    throw error;
  }
  return response;
}
function controls() {
  $("analyze-button").disabled =
    !state.file ||
    !state.key ||
    !state.ready ||
    state.uploading ||
    state.job?.result.status === "processing";
  $("choose-file").disabled = state.uploading;
  $("new-check").disabled = state.uploading;
  $("connection-button").disabled = state.uploading;
  $("analyze-button").textContent = state.uploading
    ? "Передаём видео…"
    : state.job?.result.status === "processing"
      ? "Идёт проверка…"
      : "Начать анализ →";
  $("upload-help").textContent = state.uploading
    ? "Дождитесь подтверждения загрузки."
    : !state.key
      ? "Подключите API-ключ для запуска проверки."
      : !state.ready
        ? "Можно выбрать видео. Запуск станет доступен после подключения анализатора."
        : !state.file
          ? "Выберите MOV-файл для новой проверки."
          : state.job?.result.status === "processing"
            ? "Проверка запущена. Следующая запись — через «Новая проверка»."
            : "Будет создана новая задача локального анализа.";
}
function showReadiness(body, online = true) {
  state.ready = online && body.ready;
  $("server-status").classList.toggle("online", online);
  $("server-status").lastChild.textContent = online
    ? " Сервер работает"
    : " Сервер недоступен";
  $("readiness").classList.toggle("ready", state.ready);
  $("readiness-title").textContent = !online
    ? "Нет соединения с сервером"
    : body.reason === "analyzer_stalled"
      ? "Анализатор превысил время обработки"
      : state.ready
        ? "Готов к проверке"
        : "Локальная модель ещё не подключена";
  $("readiness-text").textContent = !online
    ? "Проверьте запуск сервиса и сетевое соединение."
    : body.reason === "analyzer_stalled"
      ? "Нужен перезапуск обработчика. Незавершённые задачи получат статус ошибки."
      : state.ready
        ? body.analyzer
          ? `Анализатор: ${body.analyzer}. Обработка на устройстве команды.`
          : "Выберите запись и запустите локальный анализ."
        : "Загрузка для анализа станет доступна после подключения модели. Предпросмотр видео уже работает.";
  controls();
}
async function refreshService() {
  serviceLastUpdated = Date.now();
  try {
    if (state.key) {
      const epoch = state.epoch;
      const body = await (await api("/api/v1/service")).json();
      if (epoch !== state.epoch) return;
      state.service = body;
      $("format-hint").textContent =
        `MOV · до ${body.max_upload_mb} МБ · анализ без звука`;
      $("result-footer-text").textContent =
        `Срок хранения проверок — ${body.retention_hours} ч`;
      showReadiness(body);
    } else {
      const response = await fetch("/readyz", {
        cache: "no-store",
        signal: AbortSignal.timeout(10000),
      });
      showReadiness(await response.json());
    }
  } catch (error) {
    showReadiness({ ready: false }, error.status === 401);
    if (error.status === 401) disconnect();
  }
}
function clearVideo() {
  $("video-player").pause();
  $("video-player").removeAttribute("src");
  $("video-player").load();
  $("video-player").hidden = true;
  if (state.videoUrl) URL.revokeObjectURL(state.videoUrl);
  state.videoUrl = null;
  state.sourceJobId = null;
  $("video-warning").hidden = true;
  $("video-area").classList.remove("has-video");
  $("file-duration").textContent = "—";
  $("timeline-section").hidden = true;
}
function preview(blob, jobId = null) {
  clearVideo();
  state.videoUrl = URL.createObjectURL(blob);
  state.sourceJobId = jobId;
  $("video-player").src = state.videoUrl;
  $("video-player").hidden = false;
  $("dropzone").hidden = true;
  $("video-message").hidden = true;
  $("video-area").classList.add("has-video");
}
function resetWorkspace() {
  state.epoch++;
  state.job = null;
  state.file = null;
  clearVideo();
  $("dropzone").hidden = false;
  $("video-message").hidden = true;
  $("video-file").value = "";
  $("file-name").textContent = "Не выбран";
  $("file-size").textContent = "—";
  $("result-body").replaceChildren();
  const box = el("div", "result-empty");
  box.append(
    visualIcon("scan"),
    el("h3", "", "Готовы к новой проверке"),
    el(
      "p",
      "",
      "Выберите видеозапись. Найденные события будут сопровождаться таймкодами и исходными кадрами.",
    ),
  );
  $("result-body").append(box);
  badge("Ожидание", "");
  exportsEnabled(false);
  errorAt("upload-error", "");
  history.replaceState(null, "", "/");
  controls();
}
function selectFile(file) {
  if (state.uploading || !file) return;
  const max = (state.service?.max_upload_mb || 64) * 1024 * 1024;
  if (!file.name.toLowerCase().endsWith(".mov")) {
    errorAt("upload-error", "Выберите файл с расширением .mov.");
    return;
  }
  if (!file.size) {
    errorAt("upload-error", "Этот файл пуст. Выберите другую запись.");
    return;
  }
  if (file.size > max) {
    errorAt("upload-error", `Размер файла больше ${max / 1024 / 1024} МБ.`);
    return;
  }
  resetWorkspace();
  state.file = file;
  preview(file);
  $("file-name").textContent = file.name;
  $("file-name").title = file.name;
  $("file-size").textContent = sizeLabel(file.size);
  controls();
}
function badge(text, variant) {
  $("result-badge").textContent = text;
  $("result-badge").className = `status-tag ${variant}`;
}
function exportsEnabled(enabled) {
  $("export-json").disabled = !enabled;
  $("export-html").disabled = !enabled;
}
function linkDescription(text) {
  const p = el("p", "event-description");
  // Links are explicit sources from the model's description. Never infer a legal match by title.
  for (const part of text.split(
    /(https:\/\/(?:old\.)?adilet\.zan\.kz\/[^\s<>"']+)/g,
  )) {
    if (/^https:\/\/(?:old\.)?adilet\.zan\.kz\//.test(part)) {
      const a = el("a", "", part);
      a.href = part;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      p.append(a);
    } else p.append(document.createTextNode(part));
  }
  return p;
}
function seek(seconds) {
  const player = $("video-player");
  if (!state.videoUrl || !Number.isFinite(player.duration)) {
    toast("Сначала откройте исходное видео для перехода к таймкоду.");
    return;
  }
  player.currentTime = Math.min(seconds, player.duration);
  player.focus();
}
function showFrame(frame, type) {
  $("frame-caption").textContent = `${timecode(frame.timestamp_sec)} · ${type}`;
  const src = `data:image/jpeg;base64,${frame.image_base64}`;
  $("frame-image").src = src;
  $("frame-image").alt =
    `Исходный кадр события, ${timecode(frame.timestamp_sec)}`;
  $("frame-download").href = src;
  $("frame-download").download =
    `evidence-${state.job.summary.job_id}-${frame.timestamp_sec}.jpg`;
  $("frame-dialog").showModal();
}
function drawTimeline() {
  const duration = $("video-player").duration;
  const events = state.job?.result?.violations || [];
  $("timeline-section").hidden = !(
    events.length &&
    Number.isFinite(duration) &&
    duration > 0
  );
  $("timeline").replaceChildren();
  if ($("timeline-section").hidden) return;
  $("timeline-duration").textContent = timecode(duration);
  events.forEach((event) => {
    const button = el("button");
    button.style.left = `${Math.min(100, (event.timecode_sec / duration) * 100)}%`;
    button.title = `${timecode(event.timecode_sec)} · ${event.type}`;
    button.setAttribute("aria-label", button.title);
    button.addEventListener("click", () => seek(event.timecode_sec));
    $("timeline").append(button);
  });
}
function renderJob() {
  if (!state.job) return;
  const { summary, result } = state.job;
  $("file-name").textContent = summary.filename;
  $("file-name").title = summary.filename;
  $("file-size").textContent = sizeLabel(summary.size_bytes);
  exportsEnabled(result.status === "done");
  const body = $("result-body");
  body.replaceChildren();
  if (result.status === "processing") {
    badge(STAGES[summary.stage] || "Обработка", "processing");
    const box = el("div", "processing-card");
    const progress = el("progress");
    progress.setAttribute("aria-label", "Обработка продолжается");
    box.append(
      progress,
      el("h3", "", STAGES[summary.stage] || "Анализируем видеозапись"),
      el(
        "p",
        "",
        "Можно перейти к другой проверке. Обработка продолжится на сервере.",
      ),
    );
    const stages = ["decoding", "scanning", "verifying", "evidence"];
    const current = stages.indexOf(summary.stage);
    const list = el("ol", "stage-list");
    stages.forEach((stage, index) => {
      const li = el(
        "li",
        current === index ? "current" : index < current ? "complete" : "",
      );
      li.append(
        el("span", "", index < current ? "✓" : `${index + 1}`),
        document.createTextNode(STAGES[stage]),
      );
      list.append(li);
    });
    box.append(
      list,
      el("p", "elapsed", ""),
      el("span", "job-id", `ID: ${summary.job_id}`),
    );
    body.append(box);
    updateTimer();
  } else if (result.status === "failed") {
    badge("Не завершена", "failed");
    const box = el("div", "result-empty");
    box.append(
      visualIcon("alert", true),
      el("h3", "", "Проверка не завершена"),
      el("p", "", result.error.message),
      el("p", "", "Ошибка обработки не означает, что нарушение отсутствует."),
      el("span", "job-id", `Код: ${result.error.code} · ${summary.job_id}`),
    );
    body.append(box);
  } else {
    badge(STATUS[result.verdict], result.verdict);
    const heading = el("div", "result-summary");
    heading.append(
      el(
        "h3",
        "",
        result.verdict === "violation"
          ? `Найдены признаки нарушения · ${result.violations.length}`
          : "Нарушение не обнаружено",
      ),
      el(
        "p",
        "",
        result.verdict === "violation"
          ? "Проверьте последовательность действий и применимость основания для каждого события."
          : "В этой записи система не обнаружила поддерживаемые признаки. Вывод ограничен видимостью и возможностями модели.",
      ),
    );
    body.append(heading);
    [...result.violations]
      .sort((a, b) => a.timecode_sec - b.timecode_sec)
      .forEach((event, index) => {
        const section = el("article", "event");
        const header = el("div", "event-heading");
        const time = el("button", "time-button", timecode(event.timecode_sec));
        time.title = "Перейти к событию в видео";
        time.addEventListener("click", () => seek(event.timecode_sec));
        header.append(time, el("h4", "", event.type));
        const evidence = el("div", "evidence");
        event.evidence_frames.forEach((frame) => {
          const button = el("button");
          const image = el("img");
          image.src = `data:image/jpeg;base64,${frame.image_base64}`;
          image.alt = `Событие ${index + 1}, кадр ${timecode(frame.timestamp_sec)}`;
          image.loading = "lazy";
          button.append(
            image,
            el("span", "", `${timecode(frame.timestamp_sec)} ↗`),
          );
          button.title = "Увеличить исходный кадр";
          button.addEventListener("click", () => showFrame(frame, event.type));
          evidence.append(button);
        });
        section.append(header, linkDescription(event.description), evidence);
        body.append(section);
      });
  }
  drawTimeline();
}
function updateTimer() {
  const summary = state.job?.summary;
  const label = document.querySelector(".elapsed");
  if (!summary || !label) return;
  const elapsed = Math.max(0, Date.now() / 1000 - summary.created_at);
  label.textContent = `С момента загрузки: ${timecode(elapsed)}. Лимит ТЗ: 05:00.`;
}
async function openJob(jobId) {
  const epoch = ++state.epoch;
  errorAt("upload-error", "");
  try {
    const job = await (
      await api(`/api/v1/jobs/${encodeURIComponent(jobId)}/details`)
    ).json();
    if (epoch !== state.epoch) return;
    state.job = job;
    if (state.sourceJobId !== jobId) {
      state.file = null;
      clearVideo();
      $("dropzone").hidden = true;
      $("video-message").hidden = false;
    }
    history.replaceState(null, "", `/?job=${encodeURIComponent(jobId)}`);
    renderJob();
    controls();
    document
      .querySelectorAll("tr[data-job]")
      .forEach((row) =>
        row.classList.toggle("selected", row.dataset.job === jobId),
      );
  } catch (error) {
    if (epoch === state.epoch) toast(error.message);
  }
}
async function refreshHistory() {
  if (!state.key) return;
  const key = state.key;
  const offset = state.offset;
  try {
    const page = await (
      await api(`/api/v1/jobs?limit=10&offset=${offset}`)
    ).json();
    if (key !== state.key || offset !== state.offset) return;
    if (!page.items.length && offset > 0) {
      state.offset = Math.max(0, offset - 10);
      return refreshHistory();
    }
    historyLastUpdated = Date.now();
    $("history-count").textContent = page.total;
    $("nav-count").textContent = page.total;
    $("history-locked").hidden = Boolean(page.total);
    $("history-locked").lastElementChild.textContent =
      "Проверок пока нет. Начните с загрузки видеозаписи.";
    $("history-table-wrap").hidden = !page.total;
    $("pagination").hidden = !page.total;
    $("prev-page").disabled = offset === 0;
    $("next-page").disabled = offset + 10 >= page.total;
    $("page-label").textContent =
      `${offset + 1}–${Math.min(offset + 10, page.total)} из ${page.total}`;
    $("history-body").replaceChildren();
    for (const job of page.items) {
      const row = el(
        "tr",
        state.job?.summary.job_id === job.job_id ? "selected" : "",
      );
      row.dataset.job = job.job_id;
      const name = el("td", "", job.filename);
      name.title = job.filename;
      const date = el(
        "td",
        "",
        new Date(job.created_at * 1000).toLocaleString("ru-RU", {
          day: "2-digit",
          month: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
        }),
      );
      const status = el("td");
      const variant = job.status === "done" ? job.verdict : job.status;
      status.append(
        el(
          "span",
          `status-tag ${variant}`,
          STATUS[variant] || STAGES[job.stage],
        ),
      );
      const count = el(
        "td",
        "",
        job.event_count === null ? "—" : String(job.event_count),
      );
      const action = el("td");
      const button = el("button", "open-job", "Открыть ↗");
      button.setAttribute("aria-label", `Открыть проверку ${job.filename}`);
      button.addEventListener("click", () => {
        openJob(job.job_id);
        $("workspace").scrollIntoView({ behavior: "smooth", block: "start" });
      });
      action.append(button);
      row.append(name, date, status, count, action);
      $("history-body").append(row);
    }
  } catch (error) {
    toast(error.message);
    if (error.status === 401) disconnect();
  }
}
function disconnect() {
  state.key = "";
  state.service = null;
  state.offset = 0;
  resetWorkspace();
  $("api-key").value = "";
  $("connection-label").textContent = "Подключить ключ";
  $("connection-dot").classList.remove("connected");
  $("lock-button").hidden = true;
  $("history-body").replaceChildren();
  $("history-count").textContent = "—";
  $("nav-count").textContent = "—";
  $("history-table-wrap").hidden = true;
  $("pagination").hidden = true;
  $("history-locked").hidden = false;
  $("history-locked").lastElementChild.textContent =
    "Подключите ключ, чтобы увидеть проверки этого сервиса.";
  $("frame-dialog").close();
  $("frame-image").removeAttribute("src");
  $("frame-download").removeAttribute("href");
  $("rules-content").replaceChildren();
  refreshService();
}
$("connection-button").addEventListener("click", () => {
  errorAt("connection-error", "");
  $("connection-dialog").showModal();
});
$("connection-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const key = $("api-key").value.trim();
  $("connect-submit").disabled = true;
  errorAt("connection-error", "");
  try {
    const body = await (await api("/api/v1/service", {}, key)).json();
    state.key = key;
    state.service = body;
    state.offset = 0;
    $("api-key").value = "";
    $("connection-label").textContent = "Ключ подключён";
    $("connection-dot").classList.add("connected");
    $("lock-button").hidden = false;
    $("connection-dialog").close();
    showReadiness(body);
    await refreshService();
    await refreshHistory();
    const jobId = new URLSearchParams(location.search).get("job");
    if (jobId && /^[0-9a-f-]{36}$/i.test(jobId)) await openJob(jobId);
    toast("Ключ принят. История проверок доступна.");
  } catch (error) {
    errorAt("connection-error", error.message);
  } finally {
    $("connect-submit").disabled = false;
  }
});
$("lock-button").addEventListener("click", () => {
  $("connection-dialog").close();
  disconnect();
  toast("Ключ удалён из памяти вкладки.");
});
document
  .querySelectorAll("[data-close]")
  .forEach((button) =>
    button.addEventListener("click", () => $(button.dataset.close).close()),
  );
$("choose-file").addEventListener("click", () => $("video-file").click());
$("video-file").addEventListener("change", (event) =>
  selectFile(event.target.files[0]),
);
$("new-check").addEventListener("click", () => {
  resetWorkspace();
  $("video-file").click();
});
for (const name of ["dragenter", "dragover"])
  $("dropzone").addEventListener(name, (event) => {
    event.preventDefault();
    $("dropzone").classList.add("dragover");
  });
$("dropzone").addEventListener("dragleave", () =>
  $("dropzone").classList.remove("dragover"),
);
$("dropzone").addEventListener("drop", (event) => {
  event.preventDefault();
  $("dropzone").classList.remove("dragover");
  if (event.dataTransfer.files.length !== 1) {
    errorAt("upload-error", "Выберите одну видеозапись для проверки.");
    return;
  }
  selectFile(event.dataTransfer.files[0]);
});
window.addEventListener("dragover", (event) => event.preventDefault());
window.addEventListener("drop", (event) => event.preventDefault());
$("video-player").addEventListener("loadedmetadata", () => {
  $("file-duration").textContent = timecode($("video-player").duration);
  $("video-warning").hidden = true;
  drawTimeline();
});
$("video-player").addEventListener("error", () => {
  if (state.videoUrl) $("video-warning").hidden = false;
});
$("analyze-button").addEventListener("click", async () => {
  if (!state.file || !state.key || !state.ready || state.uploading) return;
  state.uploading = true;
  controls();
  errorAt("upload-error", "");
  const data = new FormData();
  data.append("file", state.file);
  try {
    const created = await (
      await api("/api/v1/analyze", {
        method: "POST",
        body: data,
        signal: AbortSignal.timeout(90000),
      })
    ).json();
    state.sourceJobId = created.job_id;
    state.offset = 0;
    await openJob(created.job_id);
    await refreshHistory();
    toast("Видео принято. Проверка запущена.");
  } catch (error) {
    errorAt("upload-error", error.message);
    await refreshService();
  } finally {
    state.uploading = false;
    controls();
  }
});
$("load-source").addEventListener("click", async () => {
  if (!state.job) return;
  const epoch = state.epoch;
  const id = state.job.summary.job_id;
  $("load-source").disabled = true;
  $("load-source").textContent = "Получаем видео…";
  try {
    const blob = await (
      await api(`/api/v1/jobs/${id}/video`, {
        signal: AbortSignal.timeout(60000),
      })
    ).blob();
    if (epoch === state.epoch) preview(blob, id);
  } catch (error) {
    toast(error.message);
  } finally {
    $("load-source").disabled = false;
    $("load-source").textContent = "Открыть исходное видео";
  }
});
async function downloadReport(format) {
  if (state.job?.result.status !== "done") return;
  const button = $(`export-${format}`);
  button.disabled = true;
  try {
    const id = state.job.summary.job_id;
    const response = await api(`/api/v1/jobs/${id}/report?format=${format}`, {
      signal: AbortSignal.timeout(60000),
    });
    const url = URL.createObjectURL(await response.blob());
    const a = el("a");
    a.href = url;
    a.download = `forte-report-${id}.${format}`;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    toast(
      format === "html"
        ? "Отчёт сохранён. Откройте HTML; для PDF выберите «Печать → Сохранить как PDF»."
        : "JSON с результатом и кадрами сохранён.",
    );
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = state.job?.result.status !== "done";
  }
}
$("export-json").addEventListener("click", () => downloadReport("json"));
$("export-html").addEventListener("click", () => downloadReport("html"));
$("refresh-service").addEventListener("click", refreshService);
$("refresh-history").addEventListener("click", refreshHistory);
$("prev-page").addEventListener("click", () => {
  state.offset = Math.max(0, state.offset - 10);
  refreshHistory();
});
$("next-page").addEventListener("click", () => {
  state.offset += 10;
  refreshHistory();
});
const ruleLabels = {
  previously_accepted_cash_tracked:
    "Прослежено поступление ранее принятой наличности",
  separate_next_customer_started: "Началось обслуживание следующего клиента",
  previous_cash_not_stored:
    "Наличность предыдущей операции не убрана на хранение",
  two_people_represent_one_customer:
    "Два человека могут участвовать в одной операции",
  object_is_not_cash: "Предмет может не быть наличностью",
  cash_belongs_to_current_operation:
    "Наличность может относиться к текущей операции",
  cashier_departure_confirmed: "Подтверждён уход кассира с рабочего места",
  cash_remains_on_surface:
    "Прослеженная наличность осталась на рабочей поверхности",
  temporary_occlusion_is_not_departure:
    "Краткое перекрытие камеры не подтверждает уход",
  cash_operation_context_unknown:
    "Контекст приёма или выдачи наличности не установлен",
};
$("rules-button").addEventListener("click", async () => {
  $("rules-content").replaceChildren(el("p", "", "Загружаем каталог…"));
  $("rules-dialog").showModal();
  if (!state.key) {
    $("rules-content").replaceChildren(
      el("p", "", "Подключите ключ сервиса для просмотра каталога правил."),
    );
    return;
  }
  try {
    const catalog = await (await api("/api/v1/rules")).json();
    const content = $("rules-content");
    content.replaceChildren();
    content.append(
      el(
        "p",
        "",
        `Редакция каталога: ${catalog.version}. Подготовлено оснований: ${catalog.rules.length}. Каталог для подключения к анализатору.`,
      ),
    );
    for (const rule of catalog.rules) {
      const card = el("article", "rule-card");
      card.append(
        el("h3", "", rule.title || rule.id),
        el("p", "", rule.wording),
      );
      const a = el("a", "", `${rule.document} · пункт ${rule.paragraph} ↗`);
      a.href = rule.url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      card.append(a);
      for (const [heading, items] of [
        ["Что нужно увидеть", rule.visible_conditions],
        ["Что нужно исключить", rule.exceptions_to_check],
      ]) {
        card.append(el("h4", "", heading));
        const list = el("ul");
        items.forEach((item) =>
          list.append(el("li", "", ruleLabels[item] || item)),
        );
        card.append(list);
      }
      content.append(card);
    }
  } catch (error) {
    $("rules-content").replaceChildren(el("p", "form-error", error.message));
  }
});
async function tick() {
  if (!document.hidden && !state.polling) {
    state.polling = true;
    try {
      updateTimer();
      if (Date.now() - serviceLastUpdated > 15000) await refreshService();
      if (state.key && Date.now() - historyLastUpdated > 6000)
        await refreshHistory();
      if (state.key && state.job?.result.status === "processing") {
        const epoch = state.epoch;
        const id = state.job.summary.job_id;
        try {
          const job = await (await api(`/api/v1/jobs/${id}/details`)).json();
          if (epoch === state.epoch) {
            const changed =
              job.result.status !== state.job.result.status ||
              job.summary.stage !== state.job.summary.stage;
            state.job = job;
            if (changed) {
              renderJob();
              controls();
              if (job.result.status !== "processing") await refreshHistory();
            }
          }
        } catch (error) {
          if (epoch === state.epoch) toast(error.message);
        }
      }
    } finally {
      state.polling = false;
    }
  }
  setTimeout(tick, 1500);
}
refreshService();
tick();
