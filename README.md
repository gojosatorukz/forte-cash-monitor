# Forte: мониторинг кассовой дисциплины

Снимок монорепозитория (2026-10-17): веб-сервис, детектор нарушений и документация.

## Структура

| Папка | Что внутри |
|---|---|
| `service/` | Веб-сервис (FastAPI + UI): загрузка видео, очередь задач, отчёт с кадрами-доказательствами. Подробности: [`service/README.md`](service/README.md) |
| `detector/` | Детектор нарушений `forte_detector`: пробы по кадрам, локальная VLM, движок правил (`taxonomy/rules.json`), адаптер для сервиса |
| `docs/` | Бриф кейса, находки, нормативка, решения, статусы. Итоговые материалы: [`docs/deliverables/`](docs/deliverables/) (инструкция запуска, архитектура, типы нарушений, ограничения, питч, исходник презентации) |
| `spike/` | Минимальный спайк VLM: запуск `llama-server` и тестовый клиент (без бинарников и весов) |

## Как это связано

1. Пользователь загружает видео в **service** (UI или API с ключом `API_KEY`).
2. Воркер сервиса вызывает **detector** через `forte_detector.service_adapter`.
3. Детектор извлекает кадры, гоняет пробы и обращается к **локальной VLM** через `llama-server` (llama.cpp, OpenAI-совместимый API; модель в формате GGUF, в репозиторий не входит).
4. Движок правил сопоставляет наблюдения с нормативной таксономией и возвращает нарушения с таймкодами и кадрами.

Живой сервис: **https://forte.incode.kz** (ключ API в репозитории отсутствует; получить у команды).

Запуск локально: см. [`service/README.md`](service/README.md) и [`docs/deliverables/01_instrukciya_zapusk.md`](docs/deliverables/01_instrukciya_zapusk.md). Скопируйте `service/.env.example` в `service/.env` и задайте свой `API_KEY`.

Данные хакатона (видео, кадры, модели) в репозиторий намеренно не включены.

---

## English

Snapshot of the Forte cash-discipline monitor: `service/` (FastAPI web service + UI, see `service/README.md`), `detector/` (frame probes + local VLM + rules engine), `docs/` (case brief, findings, regulations, decisions; final deliverables in `docs/deliverables/`), and `spike/` (llama-server VLM spike scripts). The service calls the detector, which queries a local VLM served by llama.cpp's `llama-server`. Live instance: https://forte.incode.kz. No API keys, videos, frames or model weights are included.
