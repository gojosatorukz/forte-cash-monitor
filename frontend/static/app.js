"use strict";

const form = document.getElementById("upload-form");
const submit = document.getElementById("submit-button");
const readiness = document.getElementById("readiness");
const title = document.getElementById("result-title");
const description = document.getElementById("result-description");
const events = document.getElementById("events");
let ready = false;

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const timecode = (sec) => `${Math.floor(sec / 60).toString().padStart(2, "0")}:${(sec % 60).toFixed(1).padStart(4, "0")}`;

async function checkReadiness() {
  try {
    const response = await fetch("/readyz", { cache: "no-store" });
    const body = await response.json();
    ready = response.ok && body.ready;
    readiness.textContent = ready
      ? "Сервис готов к локальному анализу видео."
      : "Сервер работает. Локальный анализатор ещё не подключён, поэтому анализ пока недоступен. Это не результат «нарушений нет».";
  } catch {
    ready = false;
    readiness.textContent = "Сервер недоступен. Проверьте, запущено ли приложение.";
  }
  readiness.classList.toggle("warning", !ready);
  submit.disabled = !ready;
}

function showResult(job) {
  if (job.status === "failed") {
    title.textContent = "Проверка не завершена";
    description.textContent = job.error.message;
    return;
  }
  title.textContent = job.verdict === "violation"
    ? "Обнаружены признаки возможных нарушений"
    : "Нарушение не обнаружено";
  description.textContent = job.verdict === "violation"
    ? "Проверьте исходные кадры и обстоятельства каждого события."
    : "В обработанном видео система не обнаружила поддерживаемые признаки.";
  for (const event of job.violations) {
    const section = document.createElement("section");
    section.className = "event";
    const heading = document.createElement("h3");
    heading.textContent = `${timecode(event.timecode_sec)} · ${event.type}`;
    const text = document.createElement("p");
    text.textContent = event.description;
    const images = document.createElement("div");
    images.className = "evidence";
    for (const frame of event.evidence_frames) {
      const figure = document.createElement("figure");
      const image = document.createElement("img");
      image.src = `data:image/jpeg;base64,${frame.image_base64}`;
      image.alt = `Исходный кадр ${timecode(frame.timestamp_sec)}`;
      const caption = document.createElement("figcaption");
      caption.textContent = timecode(frame.timestamp_sec);
      figure.append(image, caption);
      images.append(figure);
    }
    section.append(heading, text, images);
    events.append(section);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!ready) return;
  const key = document.getElementById("api-key").value;
  const file = document.getElementById("video-file").files[0];
  if (!file) return;
  submit.disabled = true;
  events.replaceChildren();
  title.textContent = "Загружаем видео…";
  description.textContent = "";
  const headers = { "X-API-Key": key };
  const data = new FormData();
  data.append("file", file);
  try {
    const response = await fetch("/api/v1/analyze", { method: "POST", headers, body: data });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Не удалось создать задачу");
    title.textContent = "Идёт анализ…";
    description.textContent = `Задача ${body.job_id}`;
    const deadline = Date.now() + 300_000;
    while (Date.now() < deadline) {
      await delay(1200);
      const state = await fetch(`/api/v1/jobs/${encodeURIComponent(body.job_id)}`, { headers });
      const job = await state.json();
      if (!state.ok) throw new Error(job.detail || "Не удалось получить статус");
      if (job.status !== "processing") {
        showResult(job);
        return;
      }
    }
    throw new Error("За пять минут результат не получен. Это не означает отсутствие нарушения.");
  } catch (error) {
    title.textContent = "Проверка не завершена";
    description.textContent = error.message;
  } finally {
    submit.disabled = !ready;
  }
});

checkReadiness();
