/* Контроль кассовой дисциплины — UI logic. Vanilla JS, no dependencies.
 * All untrusted text is inserted via textContent / attributes, never innerHTML. */
(function () {
  "use strict";

  var API = "/ui-api";
  var MAX_BYTES = 200 * 1024 * 1024;
  var POLL_MS = 1000;
  var HISTORY_LIMIT = 20;
  var ALLOWED_EXT = [".mov", ".mp4"];

  // ---------- DOM helpers ----------
  function $(id) { return document.getElementById(id); }
  var SVG_NS = "http://www.w3.org/2000/svg";

  /** Create an element. attrs: {class, text, on<event>: fn, other attributes}. */
  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) return;
        if (k === "class") node.className = v;
        else if (k === "text") node.textContent = String(v);
        else if (k.indexOf("on") === 0 && typeof v === "function") node.addEventListener(k.slice(2), v);
        else node.setAttribute(k, v === true ? "" : String(v));
      });
    }
    (children || []).forEach(function (c) {
      if (c === null || c === undefined || c === false) return;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return node;
  }

  /** Build a small inline SVG icon from trusted, static path definitions. */
  function icon(viewBox, shapes, cls) {
    var svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", viewBox);
    svg.setAttribute("aria-hidden", "true");
    if (cls) svg.setAttribute("class", cls);
    shapes.forEach(function (s) {
      var n = document.createElementNS(SVG_NS, s[0]);
      Object.keys(s[1]).forEach(function (k) { n.setAttribute(k, s[1][k]); });
      svg.appendChild(n);
    });
    return svg;
  }
  var ICONS = {
    play: function () { return icon("0 0 12 12", [["path", { d: "M3 1.8v8.4L10 6z", fill: "currentColor" }]]); },
    check: function () { return icon("0 0 24 24", [["path", { d: "M5 12.5l4.5 4.5L19 7.5", fill: "none", stroke: "currentColor", "stroke-width": "2.6", "stroke-linecap": "round", "stroke-linejoin": "round" }]]); },
    alert: function () { return icon("0 0 24 24", [["path", { d: "M12 6.5v7", stroke: "currentColor", "stroke-width": "2.6", "stroke-linecap": "round" }], ["circle", { cx: "12", cy: "17.5", r: "1.6", fill: "currentColor" }]]); },
    external: function () { return icon("0 0 14 14", [["path", { d: "M8 2h4v4M12 2L6.5 7.5M10.5 8.5V12H2V3.5h3.5", fill: "none", stroke: "currentColor", "stroke-width": "1.5", "stroke-linecap": "round", "stroke-linejoin": "round" }]]); },
    shield: function () { return icon("0 0 48 48", [["path", { d: "M24 5l15 6v11c0 10-6.5 17.5-15 21-8.5-3.5-15-11-15-21V11z", fill: "none", stroke: "currentColor", "stroke-width": "2.6", "stroke-linejoin": "round" }], ["path", { d: "M16.5 24.5l5 5 10-10.5", fill: "none", stroke: "currentColor", "stroke-width": "2.6", "stroke-linecap": "round", "stroke-linejoin": "round" }]]); }
  };

  // ---------- Formatting ----------
  function num(v) { var n = Number(v); return isFinite(n) ? n : null; }

  function fmtTime(sec) {
    var s = num(sec);
    if (s === null || s < 0) return "—";
    s = Math.floor(s);
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    var mmss = String(m).padStart(2, "0") + ":" + String(r).padStart(2, "0");
    return h ? h + ":" + mmss : mmss;
  }
  function fmtElapsed(sec) {
    sec = Math.max(0, Math.floor(sec));
    return Math.floor(sec / 60) + ":" + String(sec % 60).padStart(2, "0");
  }
  function fmtDuration(sec) {
    var s = num(sec);
    if (s === null) return "—";
    if (s < 60) return s.toFixed(s < 10 ? 1 : 0).replace(".", ",") + " с";
    return Math.floor(s / 60) + " мин " + Math.round(s % 60) + " с";
  }
  function fmtBytes(b) {
    if (!isFinite(b)) return "";
    if (b < 1024) return b + " Б";
    if (b < 1024 * 1024) return (b / 1024).toFixed(1).replace(".", ",") + " КБ";
    return (b / 1024 / 1024).toFixed(1).replace(".", ",") + " МБ";
  }
  function parseDate(iso) {
    if (!iso) return null;
    var d = new Date(iso);
    return isNaN(d.getTime()) ? null : d;
  }
  function fmtDate(iso) {
    var d = parseDate(iso);
    if (!d) return "—";
    return d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }
  function plural(n, one, few, many) {
    var m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
    return many;
  }
  function violationsWord(n) { return plural(n, "нарушение", "нарушения", "нарушений"); }

  function errorMessage(status, body) {
    var detail = body && body.detail;
    if (Array.isArray(detail)) {
      detail = detail.map(function (d) { return d && (d.msg || JSON.stringify(d)); }).join("; ");
    } else if (detail && typeof detail === "object") {
      detail = JSON.stringify(detail);
    }
    if (status === 413) return "Файл слишком большой. Максимальный размер — 200 МБ." + (detail ? " (" + detail + ")" : "");
    if (status === 400 && !detail) return "Файл пустой или имеет неподдерживаемый формат.";
    if (status === 404) return detail || "Проверка не найдена.";
    if (status === 0) return "Нет связи с сервером. Проверьте подключение и повторите попытку.";
    return detail ? String(detail) : "Ошибка сервера (код " + status + ").";
  }

  function fetchJSON(url) {
    return fetch(url, { headers: { Accept: "application/json" }, cache: "no-store" }).then(function (r) {
      return r.text().then(function (t) {
        var body = null;
        try { body = t ? JSON.parse(t) : null; } catch (e) { body = null; }
        if (!r.ok) { var err = new Error(errorMessage(r.status, body)); err.status = r.status; throw err; }
        return body;
      });
    }, function () { var err = new Error(errorMessage(0)); err.status = 0; throw err; });
  }

  // ---------- Toast ----------
  var toastTimer = null;
  function toast(msg) {
    var t = $("toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.hidden = true; }, 2600);
  }

  // ---------- State ----------
  var state = {
    file: null,          // File chosen in the picker
    lastFile: null,      // File of the last submitted upload (for retry)
    lastFileJobId: null, // job_id the server assigned to lastFile; retry re-uploads only for that job
    jobId: null,         // currently displayed job
    job: null,
    pollToken: 0,
    pollTimer: null,
    elapsedTimer: null,
    startedAt: null,     // ms epoch used for elapsed counter
    xhr: null,
    violations: [],
    duration: null,
    lightbox: { frames: [], index: 0 }
  };

  // ---------- Views ----------
  var sections = {
    upload: $("upload-section"),
    progress: $("progress-section"),
    error: $("error-section"),
    result: $("result-section")
  };
  function show(which) {
    sections.upload.hidden = which !== "upload";
    sections.progress.hidden = which !== "progress";
    sections.error.hidden = which !== "error";
    sections.result.hidden = which !== "result";
  }

  // ---------- File selection ----------
  var fileInput = $("file-input"), dropzone = $("dropzone");

  function setUploadError(msg) {
    var e = $("upload-error");
    e.textContent = msg || "";
    e.hidden = !msg;
  }
  function chooseFile(f) {
    setUploadError("");
    if (!f) { state.file = null; renderFile(); return; }
    var name = (f.name || "").toLowerCase();
    var okExt = ALLOWED_EXT.some(function (x) { return name.slice(-x.length) === x; });
    if (!okExt && !/^video\//.test(f.type || "")) {
      setUploadError("Неподдерживаемый формат файла. Выберите видео .mov или .mp4.");
      state.file = null; renderFile(); return;
    }
    if (f.size === 0) { setUploadError("Файл пустой."); state.file = null; renderFile(); return; }
    if (f.size > MAX_BYTES) {
      setUploadError("Файл слишком большой (" + fmtBytes(f.size) + "). Максимальный размер — 200 МБ.");
      state.file = null; renderFile(); return;
    }
    state.file = f;
    renderFile();
  }
  function renderFile() {
    var f = state.file;
    $("file-row").hidden = !f;
    dropzone.hidden = !!f;
    $("submit-btn").disabled = !f;
    if (f) {
      $("file-name").textContent = f.name;
      $("file-name").title = f.name;
      $("file-size").textContent = fmtBytes(f.size) + (f.type ? " · " + f.type : "");
    }
  }
  fileInput.addEventListener("change", function () { chooseFile(fileInput.files && fileInput.files[0]); });
  $("file-clear").addEventListener("click", function () { fileInput.value = ""; chooseFile(null); fileInput.focus(); });
  ["dragenter", "dragover"].forEach(function (ev) {
    dropzone.addEventListener(ev, function (e) { e.preventDefault(); dropzone.classList.add("is-over"); });
  });
  ["dragleave", "dragend", "drop"].forEach(function (ev) {
    dropzone.addEventListener(ev, function (e) { e.preventDefault(); dropzone.classList.remove("is-over"); });
  });
  dropzone.addEventListener("drop", function (e) {
    var files = e.dataTransfer && e.dataTransfer.files;
    if (files && files.length) chooseFile(files[0]);
  });
  // Prevent the browser from navigating away when a file is dropped outside the zone.
  window.addEventListener("dragover", function (e) { e.preventDefault(); });
  window.addEventListener("drop", function (e) {
    e.preventDefault();
    if (!sections.upload.hidden && e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
      chooseFile(e.dataTransfer.files[0]);
    }
  });

  // ---------- Progress UI ----------
  function setSteps(active) {
    var order = ["upload", "analyze", "report"];
    var idx = order.indexOf(active);
    order.forEach(function (s, i) {
      var n = $("step-" + s);
      n.classList.toggle("is-active", i === idx);
      n.classList.toggle("is-done", i < idx);
    });
  }
  function setProgress(title, stage, frac, indeterminate) {
    $("progress-title").textContent = title;
    $("progress-stage").textContent = stage || "";
    var pct = Math.max(0, Math.min(100, Math.round((frac || 0) * 100)));
    $("progress-pct").textContent = indeterminate ? "…" : pct + "%";
    $("progress-fill").style.width = pct + "%";
    var bar = $("progress-bar");
    bar.setAttribute("aria-valuenow", String(pct));
    bar.classList.toggle("is-indeterminate", !!indeterminate);
  }
  function startElapsed(fromMs) {
    state.startedAt = fromMs || Date.now();
    clearInterval(state.elapsedTimer);
    var tick = function () { $("progress-elapsed").textContent = fmtElapsed((Date.now() - state.startedAt) / 1000); };
    tick();
    state.elapsedTimer = setInterval(tick, 500);
  }
  function stopElapsed() { clearInterval(state.elapsedTimer); state.elapsedTimer = null; }

  function stopPolling() {
    state.pollToken++;
    clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }

  // ---------- Upload ----------
  $("upload-form").addEventListener("submit", function (e) {
    e.preventDefault();
    if (state.file) upload(state.file);
  });

  function upload(file) {
    stopPolling();
    if (state.xhr) { try { state.xhr.abort(); } catch (err) { /* ignore */ } }
    state.lastFile = file;
    state.lastFileJobId = null; // set once the server assigns a job_id to this file
    setUploadError("");
    show("progress");
    setSteps("upload");
    setProgress("Загрузка видео…", file.name + " · " + fmtBytes(file.size), 0, false);
    startElapsed();

    var fd = new FormData();
    fd.append("file", file, file.name);
    var xhr = new XMLHttpRequest();
    state.xhr = xhr;
    xhr.open("POST", API + "/analyze");
    xhr.setRequestHeader("Accept", "application/json");
    xhr.upload.addEventListener("progress", function (ev) {
      if (ev.lengthComputable) {
        setProgress("Загрузка видео…", fmtBytes(ev.loaded) + " из " + fmtBytes(ev.total), ev.loaded / ev.total, false);
        if (ev.loaded >= ev.total) setProgress("Видео загружено", "Регистрация задачи…", 1, true);
      }
    });
    xhr.addEventListener("load", function () {
      state.xhr = null;
      var body = null;
      try { body = JSON.parse(xhr.responseText); } catch (err) { body = null; }
      if (xhr.status >= 200 && xhr.status < 300 && body && body.job_id) {
        fileInput.value = "";
        state.file = null;
        renderFile();
        var id = String(body.job_id);
        state.lastFileJobId = id;
        if (location.hash !== "#job=" + encodeURIComponent(id)) {
          location.hash = "job=" + encodeURIComponent(id); // triggers hashchange → openJob
        } else {
          openJob(id);
        }
        refreshHistory();
      } else {
        uploadFailed(errorMessage(xhr.status, body));
      }
    });
    xhr.addEventListener("error", function () { state.xhr = null; uploadFailed(errorMessage(0)); });
    xhr.addEventListener("abort", function () { state.xhr = null; });
    xhr.send(fd);
  }

  function uploadFailed(msg) {
    stopElapsed();
    show("upload");
    renderFile();
    setUploadError(msg);
  }

  // ---------- Job loading / polling ----------
  function openJob(id) {
    stopPolling();
    if (state.xhr) { try { state.xhr.abort(); } catch (err) { /* ignore */ } state.xhr = null; }
    resetPlayer();
    state.jobId = id;
    state.job = null;
    markCurrentHistory();
    var token = state.pollToken;
    show("progress");
    setSteps("analyze");
    setProgress("Загрузка результата…", "", 0, true);
    startElapsed();
    poll(id, token, true);
  }

  function poll(id, token, first) {
    fetchJSON(API + "/jobs/" + encodeURIComponent(id)).then(function (job) {
      if (token !== state.pollToken) return;
      if (!job || typeof job !== "object" || (job.status !== "done" && job.status !== "failed")) {
        // "processing", or an unexpected body (e.g. a non-JSON proxy page answered 200):
        // never render that as a result, keep polling.
        job = (job && typeof job === "object") ? job : {};
        state.job = job;
        if (first && job.status === "processing") {
          var created = parseDate(job.created_at);
          if (created && Date.now() - created.getTime() < 6 * 3600 * 1000) startElapsed(created.getTime());
        }
        if (job.status === "processing") {
          renderProcessing(job);
          state.pollTimer = setTimeout(function () { poll(id, token, false); }, POLL_MS);
        } else {
          $("progress-stage").textContent = "Неожиданный ответ сервера, повторяем запрос…";
          state.pollTimer = setTimeout(function () { poll(id, token, false); }, POLL_MS * 2);
        }
        return;
      }
      state.job = job;
      if (job.status === "failed") {
        stopElapsed();
        renderFailed(job.error || "Неизвестная ошибка анализа.");
        if (!first) refreshHistory();
      } else {
        stopElapsed();
        renderResult(job);
        if (!first) refreshHistory();
      }
    }).catch(function (err) {
      if (token !== state.pollToken) return;
      if (err.status === 404) {
        stopElapsed();
        renderFailed("Проверка с идентификатором «" + id + "» не найдена. Возможно, ссылка устарела.", true);
        return;
      }
      if (err.status === 0 || err.status >= 500) {
        // transient: keep polling, show the issue in the stage line
        $("progress-stage").textContent = "Нет ответа от сервера, повторяем запрос…";
        state.pollTimer = setTimeout(function () { poll(id, token, false); }, POLL_MS * 2);
        return;
      }
      stopElapsed();
      renderFailed(err.message);
    });
  }

  function renderProcessing(job) {
    show("progress");
    setSteps("analyze");
    var p = num(job.progress);
    var stage = job.stage || (p ? "Обработка…" : "Задача в очереди");
    var title = "Анализ видеозаписи" + (job.filename ? " · " + job.filename : "");
    setProgress(title, stage, p || 0, p === null);
  }

  function renderFailed(msg, notFound) {
    show("error");
    $("error-text").textContent = msg;
    $("retry-btn").hidden = !!notFound;
    $("retry-btn").textContent = canReupload() ? "Повторить загрузку" : "Повторить";
  }

  $("retry-btn").addEventListener("click", function () {
    if (canReupload()) { upload(state.lastFile); return; }
    // No local file (e.g. opened from history): re-fetch the job, else go to upload form.
    if (state.job && state.job.status !== "failed" && state.jobId) { openJob(state.jobId); return; }
    goHome();
    setUploadError("Выберите видеофайл повторно, чтобы запустить новую проверку.");
  });
  $("new-check-btn-err").addEventListener("click", goHome);
  $("new-check-btn").addEventListener("click", goHome);
  $("brand-link").addEventListener("click", function (e) { e.preventDefault(); goHome(); });

  function goHome() {
    stopPolling();
    if (state.xhr) { try { state.xhr.abort(); } catch (err) { /* ignore */ } state.xhr = null; }
    stopElapsed();
    resetPlayer();
    state.jobId = null;
    state.job = null;
    markCurrentHistory();
    if (location.hash) history.pushState(null, "", location.pathname + location.search);
    lastRoutedHash = location.hash;
    show("upload");
    renderFile();
    window.scrollTo({ top: 0 });
  }

  $("copy-link-btn").addEventListener("click", function () {
    var url = location.href;
    var done = function () { toast("Ссылка на результат скопирована"); };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(url).then(done, function () { fallbackCopy(url) ? done() : toast(url); });
    } else {
      fallbackCopy(url) ? done() : toast(url);
    }
  });
  function fallbackCopy(text) {
    var ta = el("textarea", { readonly: true, style: "position:fixed;left:-9999px;top:0" });
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    return ok;
  }

  // ---------- Result rendering ----------
  var video = $("video");

  function renderResult(job) {
    show("result");
    var vs = Array.isArray(job.violations) ? job.violations.slice() : [];
    vs.sort(function (a, b) { return (num(a && a.timecode_sec) || 0) - (num(b && b.timecode_sec) || 0); });
    state.violations = vs;
    var n = vs.length;
    var bad = job.verdict === "violation" || (job.verdict == null && n > 0);

    // Verdict banner
    var verdict = $("verdict");
    verdict.className = "verdict " + (bad ? "verdict--bad" : "verdict--ok");
    var vi = $("verdict-icon");
    vi.textContent = "";
    vi.appendChild(bad ? ICONS.alert() : ICONS.check());
    $("verdict-title").textContent = bad ? "Обнаружены нарушения: " + n : "Нарушений не обнаружено";
    var meta = $("verdict-meta");
    meta.textContent = "";
    if (job.filename) meta.appendChild(el("span", { text: "Файл: " + job.filename }));
    if (job.finished_at || job.created_at) meta.appendChild(el("span", { text: "Проверено: " + fmtDate(job.finished_at || job.created_at) }));
    var c = parseDate(job.created_at), f = parseDate(job.finished_at);
    if (c && f && f >= c) meta.appendChild(el("span", { text: "Время анализа: " + fmtElapsed((f - c) / 1000) }));
    if (job.job_id) meta.appendChild(el("span", { text: "ID: " + job.job_id }));
    $("partial-note").hidden = !job.partial;
    renderWarnings(job.warnings);

    // Video
    var src = API + "/jobs/" + encodeURIComponent(job.job_id || state.jobId) + "/video";
    if (video.getAttribute("src") !== src) {
      resetPlayer();
      removeVideoError();
      video.setAttribute("src", src);
      video.load();
    }
    var vm = job.video_meta || {};
    state.duration = num(vm.duration_sec);
    var w = num(vm.width), h = num(vm.height);
    state.aspect = w && h ? w / h : null;  // number (w/h); clips use it until their own metadata loads
    if (state.aspect) video.style.aspectRatio = w + " / " + h;
    else video.style.removeProperty("aspect-ratio");
    renderVideoMeta(vm);
    renderTimeline();

    // Violations
    $("violations-title").textContent = n ? "Выявленные нарушения (" + n + ")" : "Выявленные нарушения";
    var list = $("violations-list");
    if (clipObserver) clipObserver.disconnect();  // the old cards' clips are being replaced
    list.textContent = "";
    if (!n) {
      list.appendChild(el("div", { class: "empty-ok" }, [
        ICONS.shield(),
        el("strong", { text: "Нарушений регламента не выявлено" }),
        el("span", { text: job.partial
          ? "Анализ был прерван по лимиту времени: часть записи могла остаться непроверенной."
          : "Видеозапись проверена полностью: событий, подпадающих под типы нарушений из нормативной базы, не найдено." }),
        el("button", { type: "button", class: "btn btn--secondary btn--sm", text: "Какие нарушения проверяются",
          onclick: function () { $("rules-open").click(); } })
      ]));
    } else {
      vs.forEach(function (v, i) { list.appendChild(renderViolation(v || {}, i)); });
    }
  }

  // Analyzer / service notes (e.g. «анализ выполнен без VLM-проверок…»), shown above the violations.
  function renderWarnings(ws) {
    var items = (Array.isArray(ws) ? ws : []).filter(function (w) { return w !== null && w !== undefined && String(w).trim(); });
    var ul = $("warnings-list");
    ul.textContent = "";
    items.forEach(function (w) { ul.appendChild(el("li", { text: String(w) })); });
    $("warnings-note").hidden = items.length === 0;
  }

  function renderVideoMeta(vm) {
    var dl = $("video-meta");
    dl.textContent = "";
    var rows = [];
    if (num(vm.duration_sec) !== null) rows.push(["Длительность", fmtDuration(vm.duration_sec)]);
    if (num(vm.width) && num(vm.height)) rows.push(["Разрешение", vm.width + " × " + vm.height]);
    if (num(vm.fps)) rows.push(["Частота кадров", (Math.round(vm.fps * 100) / 100 + "").replace(".", ",") + " к/с"]);
    if (vm.codec) rows.push(["Кодек", String(vm.codec)]);
    rows.forEach(function (r) {
      dl.appendChild(el("dt", { text: r[0] }));
      dl.appendChild(el("dd", { text: r[1] }));
    });
    dl.hidden = rows.length === 0;
  }

  // Two-axis assessment: did it happen (fact) / how firmly the cited norm covers it (legal strength).
  // num(null) is 0, so a missing score must be checked explicitly.
  function score(v) { return v === null || v === undefined || v === "" ? null : num(v); }
  function unit(c) { if (c > 1) c = c / 100; return Math.max(0, Math.min(1, c)); }
  function scoreItem(label, hint, value, gauge, muted) {
    return el("div", { class: "score", title: hint }, [
      el("div", { class: "score__label", text: label }),
      el("div", { class: "score__value" + (muted ? " score__value--muted" : "") }, [gauge, value])
    ]);
  }
  function percentScore(conf, label, hint) {
    var c = score(conf);
    if (c === null) return null;
    c = unit(c);
    var meter = el("span", { class: "meter" + (c < 0.5 ? " meter--low" : ""), "aria-hidden": "true" }, [
      el("span", { class: "meter__fill", style: "width:" + Math.round(c * 100) + "%" })
    ]);
    return scoreItem(label, hint, Math.round(c * 100) + "%", meter);
  }
  var STRENGTH_RANK = { low: 1, medium: 2, high: 3 };
  var STRENGTH_HINT = "Насколько однозначно указанная норма относится к такому событию";
  function strengthScore(s) {
    var key = String(s || "").toLowerCase();
    if (!STRENGTH_RANK[key]) return null;
    var segs = el("span", { class: "segs segs--" + key, "aria-hidden": "true" }, [1, 2, 3].map(function (k) {
      return el("i", { class: k <= STRENGTH_RANK[key] ? "on" : null });
    }));
    return scoreItem("Сила правовой нормы", STRENGTH_HINT, LEGAL_CONF_RU[key], segs);
  }
  function renderScores(v, hasRule) {
    var items = [];
    if (score(v.fact_conf) !== null || score(v.legal_conf) !== null || v.legal_strength) {
      items.push(percentScore(v.fact_conf, "Уверенность в факте", "Насколько уверенно система зафиксировала само событие"));
      // legal_conf is an ordinal encoding of the strength (0.9/0.7/0.4), not a probability:
      // show the level when it is known, the percentage only as a fallback.
      items.push(strengthScore(v.legal_strength) ||
        percentScore(v.legal_conf, "Уверенность в норме", "Насколько уверенно событие подпадает под указанную норму") ||
        (hasRule ? null : scoreItem("Сила правовой нормы", "Для этого типа событий норма не приводится", "не определена", null, true)));
    } else {
      items.push(percentScore(v.confidence, "Уверенность модели", "Уверенность модели в обнаруженном событии"));
    }
    items = items.filter(Boolean);
    if (!items.length) return null;
    var box = el("div", { class: "scores" }, items);
    if (items.length === 1) box.style.gridTemplateColumns = "minmax(0, 1fr)";
    return box;
  }

  // Start of a violation interval: start_sec if given, else the timecode.
  function startOf(v) {
    var s = score(v && v.start_sec);
    return s !== null ? s : num(v && v.timecode_sec);
  }

  // Rule id: `type_id` (current schema); old results carried the id in `type` next to `title_ru`.
  function typeIdOf(v) {
    if (v.type_id) return String(v.type_id);
    if (v.type && v.title_ru && v.type !== v.title_ru) return String(v.type);
    return "";
  }

  function renderViolation(v, i) {
    var t = startOf(v);
    var end = num(v.end_sec);
    var hasRange = end !== null && t !== null && end > t;
    var title = v.title_ru || v.type || "Нарушение";
    var typeId = typeIdOf(v);
    var chips = [];
    if (t !== null) {
      chips.push(el("button", {
        type: "button", class: "timecode",
        "aria-label": "Перейти к моменту " + fmtTime(t) + (hasRange ? "–" + fmtTime(end) : ""),
        onclick: function () { seekTo(t, true); }
      }, [ICONS.play(), fmtTime(t) + (hasRange ? " – " + fmtTime(end) : "")]));
    }
    if (hasRange) chips.push(el("span", { class: "chip", text: "Длительность " + fmtDuration(end - t) }));

    var card = el("article", { class: "vcard", id: "violation-" + i, "aria-labelledby": "vtitle-" + i }, [
      el("div", { class: "vcard__top" }, [
        el("span", { class: "vcard__num", "aria-hidden": "true", text: String(i + 1) }),
        el("div", { class: "vcard__heading" }, [
          el("h4", { class: "vcard__title", id: "vtitle-" + i }, [
            typeId ? el("span", { class: "type-id", title: "Код типа нарушения", text: typeId }) : null,
            title
          ]),
          el("div", { class: "vcard__chips" }, chips)
        ])
      ])
    ]);
    var rules = [];
    if (Array.isArray(v.rules) && v.rules.length) rules = v.rules.filter(Boolean);
    else if (v.rule) rules = [v.rule];
    var scores = renderScores(v, rules.length > 0);
    if (scores) card.appendChild(scores);
    if (v.description) card.appendChild(el("p", { class: "vcard__desc", text: v.description }));

    var frames = Array.isArray(v.evidence_frames) ? v.evidence_frames.filter(function (f) { return f && f.image_base64; }) : [];
    var clip = clipUrl(v.clip_url);
    var media = el("div", { class: "vmedia" });
    if (clip) media.appendChild(renderClip(clip, frames[0], title, t));
    if (frames.length) {
      var strip = el("div", { class: "frames", role: "list", "aria-labelledby": "vframes-" + i });
      frames.forEach(function (fr, fi) {
        var ts = num(fr.timestamp_sec);
        var marked = fr.annotated === true;
        var img = el("img", { src: "data:image/jpeg;base64," + fr.image_base64, alt: "Кадр " + fmtTime(ts) + (marked ? " с разметкой" : ""), loading: "lazy" });
        strip.appendChild(el("button", {
          type: "button", class: "frame", role: "listitem",
          "aria-label": "Открыть кадр " + (fi + 1) + " (" + fmtTime(ts) + ")" + (marked ? ", с разметкой" : ""),
          onclick: function () { openLightbox(frames, fi, title); }
        }, [img, marked ? el("span", { class: "frame__badge", text: "Разметка" }) : null,
          el("span", { class: "frame__ts", text: fmtTime(ts) })]));
      });
      media.appendChild(el("div", { class: "vmedia__frames" }, [
        el("div", { class: "vmedia__label", id: "vframes-" + i, text: "Кадры-доказательства" }), strip
      ]));
    }
    if (media.childNodes.length) card.appendChild(media);

    rules.forEach(function (r, ri) { card.appendChild(renderRule(r, ri === 0 ? "Нарушенная норма" : "Также относится")); });
    if (!rules.length) {
      // No citation in the rules base for this type: say so instead of inventing one.
      card.appendChild(el("div", { class: "rule rule--none", role: "note" }, [
        el("div", { class: "rule__label", text: "Нарушенная норма" }),
        el("div", { class: "rule__none", text: "Норма не определена — требуется проверка специалистом" })
      ]));
    }
    return card;
  }

  // Evidence clips: only same-origin service paths are accepted.
  function clipUrl(u) {
    if (!u || typeof u !== "string") return null;
    try {
      var parsed = new URL(u, location.href);
      if (parsed.origin !== location.origin || !/^\/(ui-api|api\/v1)\/jobs\/[^/]+\/clips\/[0-9]+\.mp4$/.test(parsed.pathname)) return null;
      return parsed.pathname;
    } catch (e) { return null; }
  }

  // Muted looping clip that plays only while visible; native controls appear on hover / focus.
  var clipObserver = "IntersectionObserver" in window ? new IntersectionObserver(function (entries) {
    entries.forEach(function (en) {
      var vid = en.target;
      if (en.isIntersecting && en.intersectionRatio >= 0.5) {
        var p = vid.play();
        if (p && p.catch) p.catch(function () {});
      } else if (!vid.paused) {
        vid.pause();
      }
    });
  }, { threshold: [0, 0.5] }) : null;

  function renderClip(src, poster, title, t) {
    var vid = el("video", {
      class: "vclip__video", src: src, muted: true, loop: true, playsinline: true, preload: "metadata",
      tabindex: "0", "aria-label": "Видеофрагмент нарушения: " + title,
      poster: poster && poster.image_base64 ? "data:image/jpeg;base64," + poster.image_base64 : null
    });
    vid.muted = true;  // the attribute alone does not mute in every browser
    if (state.aspect) vid.style.setProperty("--ar", String(state.aspect));
    vid.addEventListener("loadedmetadata", function () {
      if (vid.videoWidth && vid.videoHeight) vid.style.setProperty("--ar", String(vid.videoWidth / vid.videoHeight));
    });
    var showControls = function () { vid.controls = true; };
    var hideControls = function () { if (document.activeElement !== vid && !vid.matches(":hover")) vid.controls = false; };
    vid.addEventListener("mouseenter", showControls);
    vid.addEventListener("focus", showControls);
    vid.addEventListener("mouseleave", hideControls);
    vid.addEventListener("blur", hideControls);
    if (clipObserver) clipObserver.observe(vid);
    var wrap = el("figure", { class: "vclip" }, [
      el("div", { class: "vmedia__label", "aria-hidden": "true", text: "Видеофрагмент" }),
      vid,
      t !== null ? el("figcaption", { class: "vclip__cap" }, [
        el("button", {
          type: "button", class: "vclip__seek", text: "Смотреть с " + fmtTime(t) + " в записи",
          onclick: function () { seekTo(t, true); }
        })
      ]) : null
    ]);
    vid.addEventListener("error", function () {
      // Clip missing or unplayable: drop it; the frame thumbnails below remain.
      if (clipObserver) clipObserver.unobserve(vid);
      if (wrap.parentNode) wrap.parentNode.removeChild(wrap);
    });
    return wrap;
  }

  function safeUrl(u) {
    if (!u || typeof u !== "string") return null;
    try {
      var parsed = new URL(u, location.href);
      return /^https?:$/.test(parsed.protocol) ? parsed.href : null;
    } catch (e) { return null; }
  }

  var LEGAL_CONF_RU = { high: "высокая", medium: "средняя", low: "низкая" };
  function renderRule(r, label) {
    var box = el("div", { class: "rule" }, [el("div", { class: "rule__label", text: label })]);
    if (r.document) box.appendChild(el("div", { class: "rule__doc", text: r.document }));
    if (r.point) box.appendChild(el("div", { class: "rule__point", text: r.point }));
    if (r.quote) box.appendChild(el("blockquote", { class: "rule__quote", text: "«" + r.quote + "»" }));
    var lc = LEGAL_CONF_RU[String(r.legal_conf || "").toLowerCase()];
    if (lc) box.appendChild(el("div", { class: "rule__conf rule__conf--" + String(r.legal_conf).toLowerCase(), text: "Применимость нормы: " + lc }));
    var href = safeUrl(r.url);
    if (href) {
      var host = "";
      try { host = new URL(href).hostname.replace(/^www\./, ""); } catch (e) { host = ""; }
      box.appendChild(el("a", { class: "rule__link", href: href, target: "_blank", rel: "noopener noreferrer", title: href },
        [host ? "Текст документа на " + host : "Открыть документ", ICONS.external()]));
    }
    return box;
  }

  // ---------- Video + timeline ----------
  function effectiveDuration() {
    var d = num(video.duration);
    if (d && isFinite(d) && d > 0) return d;
    return state.duration && state.duration > 0 ? state.duration : null;
  }

  function renderTimeline() {
    var markers = $("timeline-markers");
    markers.textContent = "";
    var d = effectiveDuration();
    $("tl-end").textContent = d ? fmtTime(d) : "—";
    if (!d) return;
    state.violations.forEach(function (v, i) {
      var t = startOf(v);
      if (t === null) return;
      var end = num(v.end_sec);
      var left = Math.max(0, Math.min(100, (t / d) * 100));
      var width = end !== null && end > t ? Math.min(100 - left, ((end - t) / d) * 100) : 0;
      var title = (v.title_ru || v.type || "Нарушение") + " · " + fmtTime(t);
      markers.appendChild(el("button", {
        type: "button", class: "marker", "data-index": i, title: title, "aria-label": "Нарушение " + (i + 1) + ": " + title,
        style: "left:" + left + "%;" + (width ? "width:" + width + "%;transform:none;" : ""),
        onclick: function (e) { e.stopPropagation(); seekTo(t, true, true); highlightViolation(i, true); }
      }));
    });
  }

  $("timeline").addEventListener("click", function (e) {
    var d = effectiveDuration();
    if (!d) return;
    var rect = this.getBoundingClientRect();
    var frac = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    seekTo(frac * d, false, true);
  });

  var pendingSeek = null; // deferred seek waiting for loadedmetadata of the current src
  function cancelPendingSeek() {
    if (pendingSeek) { video.removeEventListener("loadedmetadata", pendingSeek); pendingSeek = null; }
  }
  function seekTo(t, play, noScroll) {
    var go = function () {
      pendingSeek = null;
      try { video.currentTime = Math.max(0, t); } catch (e) { /* ignore */ }
      if (play) { var p = video.play(); if (p && p.catch) p.catch(function () {}); }
    };
    cancelPendingSeek();
    if (video.readyState >= 1) go();
    else { pendingSeek = go; video.addEventListener("loadedmetadata", go, { once: true }); }
    if (!noScroll) {
      var player = document.querySelector(".player");
      var r = player.getBoundingClientRect();
      if (r.top < 0 || r.bottom > window.innerHeight) player.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  var activeIdx = -1;
  /** Stop playback and drop per-job player state (called when switching jobs / sources). */
  function resetPlayer() {
    cancelPendingSeek();
    try { video.pause(); } catch (e) { /* ignore */ }
    activeIdx = -1;
    document.querySelectorAll(".vcard.is-active, .marker.is-active").forEach(function (n) { n.classList.remove("is-active"); });
  }
  function canReupload() {
    return !!state.lastFile && !!state.jobId && state.lastFileJobId === state.jobId;
  }
  function highlightViolation(i, scroll) {
    if (i === activeIdx && !scroll) return;
    activeIdx = i;
    document.querySelectorAll(".vcard.is-active, .marker.is-active").forEach(function (n) { n.classList.remove("is-active"); });
    if (i < 0) return;
    var card = $("violation-" + i);
    if (card) {
      card.classList.add("is-active");
      if (scroll) card.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
    var m = document.querySelector('.marker[data-index="' + i + '"]');
    if (m) m.classList.add("is-active");
  }

  video.addEventListener("loadedmetadata", renderTimeline);
  video.addEventListener("durationchange", renderTimeline);
  video.addEventListener("timeupdate", function () {
    var d = effectiveDuration();
    if (!d) return;
    var pct = Math.max(0, Math.min(100, (video.currentTime / d) * 100));
    $("timeline-played").style.width = pct + "%";
    $("timeline-head").style.left = pct + "%";
    $("tl-start").textContent = fmtTime(video.currentTime);
    var cur = video.currentTime, found = -1;
    state.violations.forEach(function (v, i) {
      var t = startOf(v);
      if (t === null) return;
      var end = num(v.end_sec);
      var stop = end !== null && end > t ? end : t + 2;
      if (cur >= t - 0.25 && cur <= stop) found = i;
    });
    highlightViolation(found, false);
  });
  function removeVideoError() {
    var old = document.querySelector(".player__error");
    if (old) old.remove();
  }
  video.addEventListener("error", function () {
    if (!video.getAttribute("src")) return;
    removeVideoError();
    var msg = el("div", { class: "notice notice--warn player__error", style: "margin-top:10px" }, [
      el("span", { text: "Не удалось воспроизвести видео в браузере. Кадры-доказательства и таймкоды доступны ниже." })
    ]);
    $("video").parentNode.parentNode.insertBefore(msg, document.querySelector(".timeline-wrap"));
  });

  // ---------- Lightbox ----------
  var lightbox = $("lightbox");
  function openLightbox(frames, index, title) {
    state.lightbox = { frames: frames, index: index, title: title };
    renderLightbox();
    if (!lightbox.open) lightbox.showModal();
  }
  function renderLightbox() {
    var lb = state.lightbox, fr = lb.frames[lb.index];
    if (!fr) return;
    var ts = num(fr.timestamp_sec);
    $("lightbox-img").src = "data:image/jpeg;base64," + fr.image_base64;
    $("lightbox-img").alt = (lb.title || "Кадр") + " — " + fmtTime(ts);
    $("lightbox-caption").textContent = (lb.title ? lb.title + " · " : "") + "Кадр " + (lb.index + 1) + " из " + lb.frames.length + " · " + fmtTime(ts);
    var multi = lb.frames.length > 1;
    $("lightbox-prev").hidden = !multi;
    $("lightbox-next").hidden = !multi;
    $("lightbox-seek").hidden = ts === null;
  }
  function stepLightbox(d) {
    var lb = state.lightbox, n = lb.frames.length;
    if (n < 2) return;
    lb.index = (lb.index + d + n) % n;
    renderLightbox();
  }
  $("lightbox-prev").addEventListener("click", function () { stepLightbox(-1); });
  $("lightbox-next").addEventListener("click", function () { stepLightbox(1); });
  $("lightbox-seek").addEventListener("click", function () {
    var fr = state.lightbox.frames[state.lightbox.index];
    lightbox.close();
    if (fr) seekTo(num(fr.timestamp_sec) || 0, false);
  });
  lightbox.addEventListener("keydown", function (e) {
    if (e.key === "ArrowLeft") { e.preventDefault(); stepLightbox(-1); }
    if (e.key === "ArrowRight") { e.preventDefault(); stepLightbox(1); }
  });

  // Generic dialog closing: [data-close] buttons and backdrop clicks.
  document.querySelectorAll("dialog").forEach(function (dlg) {
    dlg.addEventListener("click", function (e) {
      if (e.target === dlg || (e.target.closest && e.target.closest("[data-close]"))) dlg.close();
    });
  });
  // Clicking the empty area around the image in the full-screen lightbox closes it.
  lightbox.querySelector(".lightbox__figure").addEventListener("click", function (e) {
    if (e.target === this) lightbox.close();
  });

  // ---------- Rules modal ----------
  var rulesModal = $("rules-modal"), rulesLoaded = false;
  $("rules-open").addEventListener("click", function () {
    rulesModal.showModal();
    if (!rulesLoaded) loadRules();
  });
  // Readable version of a description template: {t} -> «мм:сс», other placeholders removed,
  // then the same tidy-up the service applies (no double spaces / space before punctuation).
  function templatePreview(tpl) {
    if (!tpl || typeof tpl !== "string") return "";
    var s = tpl.replace(/\{([^{}]*)\}/g, function (_, name) { return name.trim() === "t" ? "мм:сс" : ""; });
    var prev = null;
    while (prev !== s) {
      prev = s;
      s = s.replace(/[ \t ]{2,}/g, " ").replace(/[ \t ]+([.,;:!?])/g, "$1").replace(/,(?=[.,;:!?])/g, "");
    }
    return s.trim();
  }

  function loadRules() {
    var body = $("rules-body");
    fetchJSON(API + "/rules").then(function (data) {
      rulesLoaded = true;
      body.textContent = "";
      var rules = (data && data.rules) || data || {};
      var entries = Array.isArray(rules)
        ? rules.map(function (r) { return [r.id || r.type || "", r]; })
        : Object.keys(rules).map(function (k) { return [k, rules[k]]; });
      if (!entries.length) { body.appendChild(el("p", { class: "empty", text: "Правила не загружены." })); return; }
      var anyPlaceholder = entries.some(function (e) { return e[1] && e[1].placeholder; });
      if (anyPlaceholder) {
        body.appendChild(el("div", { class: "notice notice--warn", style: "margin-top:14px" }, [
          el("span", { text: "Часть правил содержит временные (демонстрационные) формулировки и будет заменена ссылками на утверждённые нормативные документы." })
        ]));
      }
      body.appendChild(el("p", { class: "card__hint", style: "margin-top:12px", text: "Типы нарушений, которые выявляет система (" + entries.length + "), и нормы, на которые ссылаются отчёты: документ, пункт, цитата и ссылка на официальный текст." }));
      body.appendChild(el("nav", { class: "rule-index", "aria-label": "Быстрый переход к типу" }, entries.map(function (e) {
        return e[0] ? el("button", { type: "button", class: "type-id", title: (e[1] && e[1].title_ru) || e[0], text: e[0],
          onclick: function () { var n = $("rule-" + e[0]); if (n) n.scrollIntoView({ behavior: "smooth", block: "start" }); } }) : null;
      })));
      entries.forEach(function (e) {
        var id = e[0], r = e[1] || {};
        var titleRow = el("div", { class: "rule-entry__title" }, [
          id ? el("span", { class: "type-id", text: id }) : null, r.title_ru || id
        ]);
        if (r.placeholder) titleRow.appendChild(el("span", { class: "badge-placeholder", text: "черновик" }));
        var entry = el("section", { class: "rule-entry", id: id ? "rule-" + id : null }, [
          titleRow,
          r.title_en ? el("div", { class: "rule-entry__en", text: r.title_en }) : null
        ]);
        var tpl = templatePreview(r.description_template);
        if (tpl) entry.appendChild(el("p", { class: "rule-entry__tpl", text: tpl }));
        var cits = (Array.isArray(r.citations) ? r.citations : []).filter(Boolean);
        cits.forEach(function (c, ci) { entry.appendChild(renderRule(c, ci === 0 ? "Основание" : "Дополнительно")); });
        if (!cits.length) {
          entry.appendChild(el("div", { class: "rule rule--none" }, [
            el("div", { class: "rule__label", text: "Основание" }),
            el("div", { class: "rule__none", text: "Ссылка на норму не приводится — событие проверяет специалист." })
          ]));
        }
        body.appendChild(entry);
      });
    }).catch(function (err) {
      body.textContent = "";
      body.appendChild(el("p", { class: "form-error", text: "Не удалось загрузить нормативную базу: " + err.message }));
    });
  }

  // ---------- History ----------
  var historyTimer = null;
  function statusBadge(job) {
    if (job.status === "processing") {
      var p = num(job.progress);
      return el("span", { class: "status status--processing" }, [el("span", { class: "status__dot" }), "В обработке" + (p ? " · " + Math.round(p * 100) + "%" : "")]);
    }
    if (job.status === "failed") return el("span", { class: "status status--failed" }, [el("span", { class: "status__dot" }), "Ошибка"]);
    return el("span", { class: "status status--done" }, [el("span", { class: "status__dot" }), "Завершена"]);
  }
  function verdictBadge(job) {
    if (job.status !== "done") {
      // Desktop shows the status in its own column; on phones that column is hidden.
      return el("span", null, [el("span", { class: "muted hide-sm", text: "—" }), el("span", { class: "show-sm" }, [statusBadge(job)])]);
    }
    var n = Array.isArray(job.violations) ? job.violations.length : num(job.violations_count);
    var bad = job.verdict === "violation";
    var items = [el("span", { class: "status " + (bad ? "status--violation" : "status--ok") }, [el("span", { class: "status__dot" }), bad ? "Нарушения" : "Без нарушений"])];
    if (job.partial) items.push(el("span", { class: "muted partial-flag", title: "Анализ прерван по лимиту времени", text: "неполный" }));
    return el("span", null, items);
  }
  function refreshHistory() {
    clearTimeout(historyTimer);
    return fetchJSON(API + "/jobs").then(function (data) {
      var jobs = (data && Array.isArray(data.jobs)) ? data.jobs : (Array.isArray(data) ? data : []);
      renderHistory(jobs.slice(0, HISTORY_LIMIT));
      if (jobs.some(function (j) { return j && j.status === "processing"; })) {
        historyTimer = setTimeout(refreshHistory, 4000);
      }
    }).catch(function () {
      var tbody = $("history-body");
      tbody.textContent = "";
      $("history-empty").hidden = false;
      $("history-empty").textContent = "Не удалось загрузить историю проверок.";
    });
  }
  function renderHistory(jobs) {
    var tbody = $("history-body");
    tbody.textContent = "";
    $("history-table").hidden = jobs.length === 0;
    $("history-empty").hidden = jobs.length > 0;
    $("history-empty").textContent = "Проверок пока нет. Загрузите первое видео, чтобы начать.";
    jobs.forEach(function (job) {
      if (!job || !job.job_id) return;
      var id = String(job.job_id);
      var n = job.status === "done" ? (Array.isArray(job.violations) ? job.violations.length : (num(job.violations_count) || 0)) : null;
      var open = function () {
        if (location.hash === "#job=" + encodeURIComponent(id)) openJob(id);
        else location.hash = "job=" + encodeURIComponent(id);
        window.scrollTo({ top: 0, behavior: "smooth" });
      };
      var tr = el("tr", {
        tabindex: "0", "data-job": id, "aria-label": "Открыть проверку " + (job.filename || id),
        onclick: open,
        onkeydown: function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } }
      }, [
        el("td", { class: "muted", text: fmtDate(job.created_at) }),
        el("td", { class: "fname", title: job.filename || id, text: job.filename || id }),
        el("td", { class: "col-hide-sm" }, [statusBadge(job)]),
        el("td", null, [verdictBadge(job)]),
        el("td", { class: "num col-hide-sm", text: n === null ? "—" : String(n) })
      ]);
      tbody.appendChild(tr);
    });
    markCurrentHistory();
  }
  function markCurrentHistory() {
    document.querySelectorAll("#history-body tr").forEach(function (tr) {
      tr.classList.toggle("is-current", !!state.jobId && tr.getAttribute("data-job") === state.jobId);
    });
  }
  $("history-refresh").addEventListener("click", function () { refreshHistory().then(function () { toast("История обновлена"); }); });

  // ---------- Routing ----------
  function jobFromHash() {
    var m = /(?:^#|&)job=([^&]+)/.exec(location.hash);
    if (!m) return null;
    try { return decodeURIComponent(m[1]); } catch (e) { return m[1]; }
  }
  var lastRoutedHash = null;
  function route() {
    var hash = location.hash;
    if (hash === lastRoutedHash) return; // hashchange + popstate may both fire
    lastRoutedHash = hash;
    if (hash && hash.indexOf("job=") === -1) return; // in-page anchors are not routes
    var id = jobFromHash();
    if (id) { if (id !== state.jobId || !state.job || sections.upload.hidden === false) openJob(id); }
    else if (state.jobId) goHome();
  }
  window.addEventListener("hashchange", route);
  window.addEventListener("popstate", route);

  // In-page anchors scroll without touching the hash (the hash holds the job route).
  document.querySelectorAll('a[href^="#"]:not(#brand-link)').forEach(function (a) {
    a.addEventListener("click", function (e) {
      var target = document.querySelector(a.getAttribute("href"));
      if (!target) return;
      e.preventDefault();
      target.scrollIntoView({ behavior: "smooth", block: "start" });
      if (target.id === "main") { target.setAttribute("tabindex", "-1"); target.focus({ preventScroll: true }); }
    });
  });

  // ---------- Init ----------
  renderFile();
  show("upload");
  route();
  refreshHistory();
})();
