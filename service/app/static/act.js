/* Акт проверки — printable inspection act built from /ui-api/jobs/{id}.
 * Vanilla JS; every server string goes through textContent / attributes, never innerHTML. */
(function () {
  "use strict";

  var DISCLAIMER = "Результат автоматического анализа является подсказкой для проверки человеком и не является окончательным юридическим или дисциплинарным заключением";
  var STRENGTH_RU = { high: "высокая", medium: "средняя", low: "низкая" };
  var DECISION_RU = {
    confirmed: "Нарушение подтверждено",
    rejected: "Нарушений нет",
    needs_followup: "Требуется дополнительная проверка"
  };
  var DECISION_TEXT = {
    confirmed: "Факт нарушения кассовой дисциплины подтверждён проверяющим.",
    rejected: "Нарушений кассовой дисциплины не выявлено.",
    needs_followup: "Для окончательного вывода требуется дополнительная проверка."
  };

  function $(id) { return document.getElementById(id); }
  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) return;
        if (k === "class") node.className = v;
        else if (k === "text") node.textContent = String(v);
        else node.setAttribute(k, v === true ? "" : String(v));
      });
    }
    (children || []).forEach(function (c) {
      if (c === null || c === undefined || c === false) return;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return node;
  }

  function num(v) { if (v === null || v === undefined || v === "") return null; var n = Number(v); return isFinite(n) ? n : null; }
  function mmss(sec) {
    var s = num(sec);
    if (s === null || s < 0) return "—";
    s = Math.floor(s);
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    var t = String(m).padStart(2, "0") + ":" + String(r).padStart(2, "0");
    return h ? h + ":" + t : t;
  }
  function pad(n) { return String(n).padStart(2, "0"); }
  function fmtDate(d) {
    if (!d || isNaN(d.getTime())) return "—";
    return pad(d.getDate()) + "." + pad(d.getMonth() + 1) + "." + d.getFullYear() + " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }
  function fmtIso(iso) { return iso ? fmtDate(new Date(iso)) : "—"; }
  function fmtDuration(sec) {
    var s = num(sec);
    if (s === null) return "—";
    if (s < 60) return s.toFixed(1).replace(".", ",") + " с";
    return Math.floor(s / 60) + " мин " + Math.round(s % 60) + " с";
  }
  function safeUrl(u) {
    if (!u || typeof u !== "string") return null;
    try { var p = new URL(u, location.href); return /^https?:$/.test(p.protocol) ? p.href : null; } catch (e) { return null; }
  }
  function startOf(v) { var s = num(v.start_sec); return s !== null ? s : num(v.timecode_sec); }
  function interval(v) {
    var t = startOf(v), end = num(v.end_sec);
    return mmss(t) + (end !== null && t !== null && end > t ? " – " + mmss(end) : "");
  }
  function typeIdOf(v) {
    if (v.type_id) return String(v.type_id);
    if (v.type && v.title_ru && v.type !== v.title_ru) return String(v.type);
    return "";
  }
  function titleOf(v) { return v.title_ru || v.type || "Нарушение"; }
  function rulesOf(v) {
    if (Array.isArray(v.rules) && v.rules.length) return v.rules.filter(Boolean);
    return v.rule ? [v.rule] : [];
  }
  function plural(n, one, few, many) {
    var m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
    return many;
  }

  function showState(msg, isError) {
    var sheet = $("sheet");
    sheet.textContent = "";
    sheet.appendChild(el("p", { class: "state" + (isError ? " state--error" : ""), text: msg }));
  }

  function jobFromHash() {
    var m = /(?:^#|&)job=([^&]+)/.exec(location.hash);
    if (!m) return null;
    try { return decodeURIComponent(m[1]); } catch (e) { return m[1]; }
  }

  // ---------- blocks ----------
  function kvTable(rows, cls) {
    return el("table", { class: "kv" + (cls ? " " + cls : "") }, [el("tbody", null, rows.filter(Boolean).map(function (r) {
      return el("tr", null, [el("th", { scope: "row", text: r[0] }), el("td", null, [r[1]])]);
    }))]);
  }

  /** Two label/value pairs per row; a row with one pair spans the full width. */
  function kvGrid(rows) {
    return el("table", { class: "kv kv--grid" }, [el("tbody", null, rows.map(function (pairs) {
      var cells = [];
      pairs.forEach(function (p) {
        cells.push(el("th", { scope: "row", text: p[0] }));
        cells.push(el("td", { colspan: pairs.length === 1 ? "3" : null }, [p[1]]));
      });
      return el("tr", null, cells);
    }))]);
  }

  function section(no, title, children) {
    return el("section", { class: "act-section" }, [el("h2", { class: "act-h2", text: no + ". " + title })].concat(children));
  }

  function normBlock(rules) {
    if (!rules.length) return el("p", { class: "muted", text: "Норма в справочнике не указана — квалификацию определяет проверяющий." });
    var wrap = el("div", { class: "norms" });
    var first = rules[0] || {};
    rules.forEach(function (r, i) {
      var box = el("div", { class: "norm" + (i ? " norm--also" : "") });
      // A further point of the same document: only the point, its quote and (if different) the link.
      var sameDoc = i > 0 && r.document && r.document === first.document;
      if (i > 0) box.appendChild(el("div", { class: "norm__also", text: sameDoc ? "Также, тот же документ:" : "Также относится:" }));
      var head = sameDoc ? (r.point || "") : [r.document, r.point].filter(Boolean).join(", ");
      if (head) box.appendChild(el("div", { class: "norm__doc", text: head }));
      if (r.quote) box.appendChild(el("blockquote", { class: "norm__quote", text: "«" + r.quote + "»" }));
      var href = safeUrl(r.url);
      if (href && !(i > 0 && href === safeUrl(first.url))) {
        box.appendChild(el("div", { class: "norm__url" }, ["Источник: ", el("a", { href: href, text: href })]));
      }
      wrap.appendChild(box);
    });
    return wrap;
  }

  function strengthText(v, rules) {
    var s = String(v.legal_strength || (rules[0] && rules[0].legal_conf) || "").toLowerCase();
    return STRENGTH_RU[s] || "не определена";
  }

  function pickFrame(v) {
    var frames = Array.isArray(v.evidence_frames) ? v.evidence_frames.filter(function (f) { return f && f.image_base64; }) : [];
    return frames[0] || null;
  }

  function confirmedBlock(item, idx, sectionNo) {
    var v = item.v, rules = rulesOf(v), tid = typeIdOf(v);
    var fr = pickFrame(v);
    var figure = null;
    if (fr) {
      figure = el("figure", { class: "viol__frame" }, [
        el("img", { src: "data:image/jpeg;base64," + fr.image_base64, alt: "Кадр-доказательство " + mmss(fr.timestamp_sec) }),
        el("figcaption", { text: "Кадр " + mmss(fr.timestamp_sec) + (fr.annotated ? " · разметка системы" : "") })
      ]);
    }
    var rows = [
      (v.description_text || v.description) ? ["Описание", el("span", { class: "pre", text: v.description_text || v.description })] : null,
      ["Нарушенная норма", normBlock(rules)],
      ["Сила правовой нормы", el("span", { text: strengthText(v, rules) })],
      ["Комментарий проверяющего", el("span", { class: "pre" + (item.comment ? "" : " muted"), text: item.comment || "—" })]
    ];
    return el("article", { class: "viol" }, [
      el("h3", { class: "viol__title" }, [
        el("span", { class: "viol__no", text: sectionNo + "." + (idx + 1) + "." }),
        el("span", null, [titleOf(v), tid ? el("span", { class: "viol__code", text: " (" + tid + ")" }) : null]),
        el("span", { class: "viol__ref" }, [
          el("span", { class: "viol__time", title: "Время на записи", text: interval(v) }),
          el("span", { text: " · срабатывание № " + item.n })
        ])
      ]),
      el("div", { class: "viol__grid" + (figure ? "" : " viol__grid--noimg") }, [figure, kvTable(rows, "kv--viol")])
    ]);
  }

  function compactTable(items, withReason) {
    var head = ["№", "Тип срабатывания", "Время"].concat(withReason ? ["Причина отклонения"] : []);
    return el("table", { class: "grid" }, [
      el("thead", null, [el("tr", null, head.map(function (h) { return el("th", { scope: "col", text: h }); }))]),
      el("tbody", null, items.map(function (it) {
        var tid = typeIdOf(it.v);
        var cells = [
          el("td", { class: "c-no", text: String(it.n) }),
          el("td", { text: titleOf(it.v) + (tid ? " (" + tid + ")" : "") }),
          el("td", { class: "nowrap", text: interval(it.v) })
        ];
        if (withReason) cells.push(el("td", { class: "pre" + (it.comment ? "" : " muted"), text: it.comment || "не указана" }));
        return el("tr", null, cells);
      }))
    ]);
  }

  /** Reviewer checklist: steps the camera cannot verify (definitions from /ui-api/rules `checklist`).
   *  Grouped by `group` under subheadings; per group, answered items are listed in full and the
   *  unanswered ones are folded into one «Не проверялось: …» line so the act stays short. */
  var CL_GROUP_ORDER = ["Общие", "Выдача", "Инкассация", "Сомнительные купюры"];
  function checklistBlock(defs, values) {
    var by = {}, names = CL_GROUP_ORDER.slice();
    defs.forEach(function (d) {
      if (!d || !d.id) return;
      var g = typeof d.group === "string" && d.group ? d.group : "Общие";
      if (names.indexOf(g) < 0) names.push(g);
      (by[g] = by[g] || []).push(d);
    });
    var out = [], n = 0;
    names.filter(function (g) { return by[g]; }).forEach(function (g) {
      var done = by[g].filter(function (d) { return values[d.id] === true || values[d.id] === false; });
      var unset = by[g].filter(function (d) { return values[d.id] !== true && values[d.id] !== false; });
      out.push(el("h3", { class: "cl-group", text: g }));
      if (done.length) {
        var list = el("ol", { class: "cl", start: String(n + 1) });
        done.forEach(function (d) {
          n++;
          var v = values[d.id];
          var st = v === true ? ["Да", "cl__st--yes"] : ["Нет", "cl__st--no"];
          var basis = [d.document || d.document_short, d.basis].filter(Boolean).join(", ");
          var href = safeUrl(d.url);
          list.appendChild(el("li", { class: "cl__item" + (v === false ? " cl__item--no" : "") }, [
            el("div", { class: "cl__row" }, [
              el("span", { class: "cl__label", text: d.label_ru || d.id }),
              el("span", { class: "cl__st " + st[1], text: st[0] })
            ]),
            basis ? el("div", { class: "cl__basis" }, ["Основание: " + basis,
              href ? el("span", null, [" · ", el("a", { href: href, text: href })]) : null]) : null,
            d.quote ? el("div", { class: "cl__quote", text: String(d.quote) }) : null
          ]));
        });
        out.push(list);
      }
      if (unset.length) {
        out.push(el("p", { class: "cl-unset" }, [el("span", { class: "cl__st cl__st--unset", text: "Не проверялось: " }),
          unset.map(function (d) {
            return (d.label_ru || d.id) + (d.basis ? " (" + [d.document_short, d.basis].filter(Boolean).join(", ") + ")" : "");
          }).join("; ") + "."]));
      }
    });
    var nNo = defs.filter(function (d) { return d && values[d.id] === false; }).length;
    return [
      el("p", { class: "note", text: "Документальные шаги, которые не видны на видеозаписи; отмечены проверяющим вручную." }),
      nNo ? el("p", { class: "cl-alert", role: "note", text: "Внимание: не выполнено пунктов — " + nNo + "." }) : null
    ].concat(out);
  }

  // Operation steps (`job.meta.procedure`, promoted and validated by the worker; re-checked here).
  var PROC_ST = {
    ok: ["Соблюдено", "pst--ok"], violation: ["Нарушение", "pst--bad"], risk: ["Риск", "pst--risk"],
    not_checked: ["Не проверялось", "pst--na"], manual: ["Проверяется вручную", "pst--manual"]
  };
  function procedureOf(job) {
    var p = job && job.meta && job.meta.procedure;
    if (!p || typeof p !== "object" || !Array.isArray(p.steps)) return null;
    var steps = p.steps.slice(0, 30).filter(function (s) { return s && typeof s === "object" && s.title_ru; });
    return steps.length ? { p: p, steps: steps } : null;
  }
  function procedureBlock(pr) {
    var p = pr.p;
    var title = typeof p.title_ru === "string" ? p.title_ru.trim() : "";  // never mapped from `type`
    var head = "Операция: " + (title || "не определена");
    var conf = num(p.confidence);
    if (conf !== null && conf >= 0 && conf <= 1) head += " (уверенность " + Math.round(conf * 100) + " %)";
    return [
      el("p", { class: "proc-op", text: head }),
      p.basis_ru ? el("p", { class: "note", text: "Основание определения: " + String(p.basis_ru) }) : null,
      el("table", { class: "grid proc-grid" }, [
        el("thead", null, [el("tr", null, ["№", "Этап", "Статус", "Время", "Примечание"].map(function (h) {
          return el("th", { scope: "col", text: h });
        }))]),
        el("tbody", null, pr.steps.map(function (s, i) {
          var st = PROC_ST[s.status] || PROC_ST.not_checked;
          var t = num(s.t_sec);
          var noteText = s.note_ru ? String(s.note_ru).trim().replace(/[.\s]+$/, "") : "";
          // Only a norm derived by the worker from the rules KB / checklist; never the detector's text.
          var rule = (s.rule_source === "rules" || s.rule_source === "checklist") && s.rule ? String(s.rule) : "";
          var note = [noteText, rule ? "Норма: " + rule : null].filter(Boolean).join(". ");
          return el("tr", null, [
            el("td", { class: "c-no", text: String(i + 1) }),
            el("td", { text: String(s.title_ru) }),
            el("td", { class: "nowrap" }, [el("span", { class: "pst " + st[1], text: st[0] })]),
            el("td", { class: "nowrap", text: t !== null && t >= 0 ? mmss(t) : "—" }),
            el("td", { class: note ? "" : "muted", text: note || "—" })
          ]);
        }))
      ])
    ];
  }

  // ---------- render ----------
  function render(job, checklistDefs) {
    var review = job.review && typeof job.review === "object" ? job.review : {};
    var rvs = review.violations || {};
    var items = (Array.isArray(job.violations) ? job.violations : []).map(function (v, i) {
      v = v && typeof v === "object" ? v : {};
      var e = rvs[String(i + 1)] || {};
      var st = e.status === "confirmed" || e.status === "rejected" ? e.status : "pending";
      return { v: v, n: i + 1, status: st, comment: (e.comment || "").trim() };
    });
    // Same order as the result page (interval start, timecode, number); references use the server
    // number n, as the result page cards and clip_url do.
    items.sort(function (a, b) {
      return ((startOf(a.v) || 0) - (startOf(b.v) || 0)) ||
        ((num(a.v.timecode_sec) || 0) - (num(b.v.timecode_sec) || 0)) || (a.n - b.n);
    });
    var confirmed = items.filter(function (it) { return it.status === "confirmed"; });
    var rejected = items.filter(function (it) { return it.status === "rejected"; });
    var pending = items.filter(function (it) { return it.status === "pending"; });
    var decision = DECISION_RU[review.decision] ? review.decision : null;
    var draft = pending.length > 0 || !decision;

    var actNo = String(job.job_id || "").slice(0, 8).toUpperCase();
    var now = new Date();
    document.title = "Акт № " + actNo + (job.filename ? " — " + job.filename : "");
    var sheet = $("sheet");
    sheet.textContent = "";
    sheet.classList.toggle("is-draft", draft);
    if (draft) {
      document.body.appendChild(el("div", { class: "watermark", "aria-hidden": "true" }, [el("span", { text: "ЧЕРНОВИК" })]));
    }

    // Header
    sheet.appendChild(el("header", { class: "act-head" }, [
      el("div", { class: "act-org" }, [
        // BRAND=neutral (data-brand set server-side on <html>): Cash Monitor wordmark instead of the Forte logo.
        document.documentElement.getAttribute("data-brand") === "neutral"
          ? el("img", { class: "act-org__logo act-org__logo--neutral", src: "assets/brand/cash-monitor-wordmark.svg", alt: "Cash Monitor", width: "220", height: "44" })
          : el("img", { class: "act-org__logo", src: "assets/brand/forte-magenta.svg", alt: "ForteBank", width: "92", height: "27" }),
        el("span", { class: "act-org__unit", text: "Служба внутреннего контроля" })
      ]),
      el("div", { class: "act-head__right", text: "Для служебного пользования" })
    ]));
    sheet.appendChild(el("h1", { class: "act-title", text: "Акт проверки соблюдения кассовой дисциплины" }));
    sheet.appendChild(el("p", { class: "act-no", text: "№ " + actNo }));
    if (draft) {
      sheet.appendChild(el("p", { class: "act-draft-note", text: "Черновик: " + (pending.length
        ? "не все срабатывания рассмотрены проверяющим"
        : "итоговое решение не принято") + ". Документ не является окончательным." }));
    }
    sheet.appendChild(el("div", { class: "act-dates" }, [
      el("div", null, [el("span", { class: "muted", text: "Дата и время формирования акта: " }), fmtDate(now)]),
      el("div", null, [el("span", { class: "muted", text: "Дата и время анализа: " }), fmtIso(job.finished_at || job.created_at)])
    ]));

    var no = 0;
    // 1. Object
    var vm = job.video_meta || {};
    var res = num(vm.width) && num(vm.height) ? vm.width + " × " + vm.height + " пикс." : "—";
    var fps = num(vm.fps) ? ", " + String(Math.round(vm.fps * 100) / 100).replace(".", ",") + " к/с" : "";
    sheet.appendChild(section(++no, "Объект проверки", [kvGrid([
      [["Файл видеозаписи", el("span", { class: "break", text: job.filename || "—" })],
        ["Длительность", fmtDuration(vm.duration_sec)]],
      [["Дата и время анализа", fmtIso(job.finished_at || job.created_at)], ["Разрешение", res + (res !== "—" ? fps : "")]],
      [["Способ", "автоматический видеоанализ с последующей проверкой специалистом"]],
      [["Результат анализа", items.length
        ? "выявлено " + items.length + " " + plural(items.length, "срабатывание", "срабатывания", "срабатываний")
        : "нарушений не выявлено"]],
      [["Идентификатор проверки", el("span", { class: "mono", text: job.job_id || "—" })]]
    ]), job.partial ? el("p", { class: "note", text: "Анализ остановлен по лимиту времени: запись могла быть проверена не полностью." }) : null]));

    // Operation steps (hidden when the analyzer gave none)
    var pr = procedureOf(job);
    if (pr) sheet.appendChild(section(++no, "Ход операции", procedureBlock(pr)));

    // 2. Confirmed
    var secNo = ++no;
    sheet.appendChild(section(secNo, "Подтверждённые нарушения", confirmed.length
      ? confirmed.map(function (it, i) { return confirmedBlock(it, i, secNo); })
      : [el("p", { class: "none", text: "Нарушений не подтверждено." })]));

    // 3. Rejected
    if (rejected.length) sheet.appendChild(section(++no, "Отклонённые срабатывания", [compactTable(rejected, true)]));
    // 4. Not reviewed
    if (pending.length) {
      sheet.appendChild(section(++no, "Не рассмотренные срабатывания", [
        el("p", { class: "note", text: "Срабатывания ниже проверяющим не рассмотрены; до их рассмотрения акт остаётся черновиком." }),
        compactTable(pending, false)
      ]));
    }

    // Manual checklist (not visible on video)
    var clValues = review.checklist && typeof review.checklist === "object" ? review.checklist : {};
    var clDefs = Array.isArray(checklistDefs) ? checklistDefs : [];
    if (!clDefs.length) clDefs = Object.keys(clValues).map(function (k) { return { id: k }; });
    if (clDefs.length) sheet.appendChild(section(++no, "Проверено вручную (не видно на видео)", checklistBlock(clDefs, clValues)));

    // 5. Conclusion
    var stats = items.length
      ? "По результатам автоматического анализа выявлено " + items.length + " " +
        plural(items.length, "срабатывание", "срабатывания", "срабатываний") + ": подтверждено проверяющим — " +
        confirmed.length + ", отклонено — " + rejected.length + ", не рассмотрено — " + pending.length + "."
      : "Автоматический анализ нарушений не выявил.";
    var mismatch = (decision === "rejected" && confirmed.length > 0) || (decision === "confirmed" && confirmed.length === 0);
    // Conclusion and signatures stay together on one page.
    var closing = el("div", { class: "act-closing" });
    sheet.appendChild(closing);
    closing.appendChild(section(++no, "Заключение", [
      el("p", { class: "concl-stats", text: stats }),
      el("p", { class: "concl-decision" }, [
        el("span", { class: "muted", text: "Решение проверяющего: " }),
        el("strong", { text: decision ? DECISION_RU[decision] : "не принято" }),
        decision ? ". " + DECISION_TEXT[decision] : ""
      ]),
      mismatch ? el("p", { class: "concl-warn", role: "note", text: "Внимание: заключение не согласуется с подтверждёнными нарушениями (" +
        (decision === "rejected" ? "подтверждено срабатываний: " + confirmed.length : "ни одно срабатывание не подтверждено") + ")." }) : null,
      review.decision_comment ? el("p", { class: "concl-comment pre" }, [
        el("span", { class: "muted", text: "Комментарий: " }), review.decision_comment
      ]) : null
    ]));

    // Signatures
    var who = [review.reviewer_name, review.reviewer_position].filter(function (s) { return s && String(s).trim(); }).join(", ");
    closing.appendChild(el("footer", { class: "act-foot" }, [
      el("p", { class: "sign-who" }, [el("span", { class: "muted", text: "Проверяющий: " }),
        who ? who : el("span", { class: "blank blank--wide" })]),
      el("div", { class: "sign-row" }, [
        el("div", { text: "Подпись ____________" }),
        el("div", { text: "Дата «__» ________ 20__ г." })
      ]),
      el("div", { class: "disclaimer", role: "note", text: DISCLAIMER }),
      el("p", { class: "smallprint", text: "Сформировано локальной системой видеоаналитики; распознавание лиц не применялось." })
    ]));

    var back = $("back-link");
    if (back && job.job_id) back.setAttribute("href", "./#job=" + encodeURIComponent(job.job_id));
    $("print-btn").disabled = false;
    document.documentElement.setAttribute("data-ready", "1");
  }

  $("print-btn").addEventListener("click", function () { window.print(); });

  var id = jobFromHash();
  if (!id) { showState("Не указана проверка: откройте акт со страницы результата.", true); return; }
  // Checklist definitions are optional for the act: without them the stored ids are listed.
  var checklistP = fetch("/ui-api/rules", { headers: { Accept: "application/json" }, cache: "no-store" })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) { return d && Array.isArray(d.checklist) ? d.checklist : []; }, function () { return []; });
  fetch("/ui-api/jobs/" + encodeURIComponent(id), { headers: { Accept: "application/json" }, cache: "no-store" })
    .then(function (r) {
      if (r.status === 401) {
        location.replace("/login?next=" + encodeURIComponent(location.pathname + location.search + location.hash));
        throw new Error("Требуется вход…");
      }
      if (r.status === 404) throw new Error("Проверка не найдена. Возможно, ссылка устарела.");
      if (!r.ok) throw new Error("Ошибка сервера (код " + r.status + ").");
      return r.json();
    }, function () { throw new Error("Нет связи с сервером. Обновите страницу."); })
    .then(function (job) {
      if (!job || job.status !== "done") {
        showState(job && job.status === "processing"
          ? "Анализ ещё выполняется. Акт можно сформировать после завершения проверки."
          : "Акт можно сформировать только для успешно завершённой проверки.", true);
        return;
      }
      return checklistP.then(function (defs) { render(job, defs); });
    })
    .catch(function (err) { showState(err.message || String(err), true); });
})();
