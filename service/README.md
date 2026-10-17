# Контроль кассовой дисциплины — локальный сервис анализа видео

Сервис для ForteBank Datathon 2026. Он принимает видеозапись из кассового узла (`.mov` / `.mp4`), ищет нарушения регламента работы с наличностью и возвращает вердикт, список нарушений с таймкодами, кадры-доказательства (JPEG base64) и ссылку на пункт регламента.

- Работает полностью локально: никаких облачных AI API, данные не покидают машину.
- Распознавания лиц нет.
- Стек: FastAPI + uvicorn, ffmpeg/ffprobe для видео, веб-интерфейс на русском (чистый HTML/CSS/JS, работает офлайн).
- Детектор подключается как плагин. По умолчанию стоит **заглушка** (`StubAnalyzer`): она честно прочитывает видео и возвращает `no_violation`. Реальный анализатор подключается переменной `ANALYZER`, см. ниже.

## Быстрый старт

Нужно: Linux, [`uv`](https://docs.astral.sh/uv/) и `ffmpeg` / `ffprobe` в `PATH`. Python 3.12 `uv` поставит сам.

```bash
API_KEY=my-secret ./run.sh             # http://localhost:8000/
PORT=9000 STUB_DEBUG=1 ./run.sh        # демо-режим: заглушка выдаёт одно фиктивное нарушение
```

`run.sh` делает следующее:
1. Выполняет `uv sync --frozen --inexact` (если не получилось, то `uv sync --inexact`). `--inexact` не удаляет из venv пакеты, которых нет в `uv.lock`, но зависимости анализатора всё равно лучше добавлять через `uv add` (см. ниже).
2. Подхватывает `.env`, если он есть (пример в `.env.example`; переменные, уже заданные в окружении, имеют приоритет).
3. Предупреждает, если `API_KEY` не задан.
4. Запускает `uvicorn app.main:app` в **одном процессе**.

Запускайте ровно один процесс: очередь задач и фоновый воркер живут внутри процесса, поэтому `--workers N` или несколько реплик на одном `DATA_DIR` не поддерживаются.

Воркер один, очередь FIFO: видео анализируются строго по очереди, а загрузки из веб-интерфейса стоят в той же очереди, что и задачи API. Время ожидания в очереди входит в бюджет задачи, поэтому если жюри отправит N видео одновременно, анализ одного видео должен занимать заметно меньше 280/N секунд, иначе последние в очереди получат частичный или пустой результат.

Веб-интерфейс открывается на `/`. В нём можно загрузить видео, следить за прогрессом и смотреть вердикт и нарушения. Клик по нарушению перематывает плеер на нужный момент. Интерфейс ходит во внутренние маршруты `/ui-api/*`, которым ключ не нужен.

## Переменные окружения

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `API_KEY` | `forte-datathon-2026` (с предупреждением в лог) | Ключ для заголовка `X-API-Key` на `/api/v1/*` |
| `HOST` | `0.0.0.0` | Адрес прослушивания (только `run.sh`) |
| `PORT` | `8000` | Порт (`run.sh` и `scripts/tunnel.sh`; в Docker — порт на хосте) |
| `BIND_ADDR` | `127.0.0.1` | Только Docker: на каком адресе хоста публикуется порт. По умолчанию только loopback — Docker обходит `ufw`, и `0.0.0.0` открыл бы сервис всей локальной сети. Для демо по LAN: `BIND_ADDR=0.0.0.0` и `docker compose up -d` |
| `DATA_DIR` | `./data` | Хранилище: `uploads/<job_id>/<файл>` и `jobs/<job_id>.json` |
| `JOB_TIMEOUT_SEC` | `270` | Целевой бюджет задачи от момента отправки (загрузка и ожидание в очереди входят). Задача, долго ждавшая в очереди, всё равно получает не меньше 20 с, но не дольше `JOB_HARD_CAP_SEC` |
| `JOB_HARD_CAP_SEC` | `280` | Абсолютный потолок от момента отправки (лимит жюри — 300 с). Итоговый дедлайн: `min(max(отправка + JOB_TIMEOUT_SEC, старт + 20 с), отправка + JOB_HARD_CAP_SEC)`. По дедлайну отдаётся частичный результат (`partial: true`). Неположительные и нечисловые значения заменяются значениями по умолчанию с предупреждением |
| `MAX_UPLOAD_MB` | `200` | Максимальный размер загрузки. Файл больше лимита получает `413` |
| `ANALYZER` | `app.analyzers.stub:StubAnalyzer` | Класс анализатора в формате `module:Class` |
| `STUB_DEBUG` | `0` | `1` / `true`: заглушка выдаёт одно демо-нарушение (`T99`, помечено «ДЕМО / ТЕСТ») с двумя реальными кадрами |
| `RULES_PATH` | — | Файл справочника правил (см. «Справочник правил»). Если не задан или непригоден: `../detector/taxonomy/rules.json` → `../docs/deliverables/rules_spec.json` → встроенный `app/rules.json`. Какой файл загружен, видно в логе и в `/healthz` (`rules_path`, `rules_count`) |
| `FORTE_VLM_URL` | `http://127.0.0.1:8090/v1/chat/completions` | Адрес VLM-сервера для анализатора `forte_detector`. `/healthz` проверяет `scheme://host:port/health` этого адреса (таймаут 1,5 с, кэш 5 с) и отдаёт `vlm_ok` / `vlm_url`; на `ok` это не влияет |
| `VLM_HEALTH_URL` | — | Явный адрес проверки VLM для `/healthz`; пустая строка или `off` — не проверять (`vlm_ok: null`) |
| `CLOUDFLARED` | `cloudflared` | Путь к бинарнику cloudflared (только `scripts/tunnel.sh`) |
| `MODELS_DIR`, `HF_HOME`, `TORCH_HOME` | в Docker: `/models`, `/models/hf`, `/models/torch` | Кэш весов моделей для будущего анализатора (в Docker смонтирован из `./models`) |
| `PUBLIC_HOSTNAME` | — | Постоянный публичный хост: `forte.incode.kz` (релей), свой домен (Caddy) или Tailscale Funnel. `/healthz` отдаёт `public_url: https://$PUBLIC_HOSTNAME`, `public_url_source: "env"`; Caddy выпускает сертификат на этот хост |
| `PUBLIC_URL_FILE` | `$DATA_DIR/public_url.txt` | Файл с адресом quick tunnel (пишет `scripts/public_url.sh`); используется, если `PUBLIC_HOSTNAME` пуст (`public_url_source: "quick-tunnel"`) |
| `TUNNEL_METRICS` | `127.0.0.1:20241` | Адрес сервера метрик cloudflared (`scripts/tunnel.sh`, `scripts/public_url.sh`) |

## API

Все маршруты `/api/v1/*` требуют заголовок `X-API-Key`. Без него или с неверным ключом сервис отвечает `401`.

```bash
KEY=my-secret
# 1. Отправить видео → {"job_id": "...", "status": "processing"}
curl -s -H "X-API-Key: $KEY" -F "file=@video.mov" http://localhost:8000/api/v1/analyze

# 2. Опрашивать до status != "processing"
curl -s -H "X-API-Key: $KEY" http://localhost:8000/api/v1/jobs/<job_id>
```

Пример ответа: реальный прогон `Х-10.mov` с `STUB_DEBUG=1` на текущем коде, base64 сокращён. Описание взято из шаблона правила `T99` (текст заглушки вставлен как наблюдение `obs`), исходный текст анализатора сохранён в `analyzer_description`.

```json
{
  "job_id": "8d0dc705a91a4024bb3500294b57de23",
  "status": "done",
  "verdict": "violation",
  "violations": [
    {
      "type": "Иное подозрительное действие",
      "type_id": "T99",
      "title_ru": "Иное подозрительное действие",
      "timecode_sec": 7.783,
      "start_sec": 7.783,
      "end_sec": 8.283,
      "confidence": 0.5,
      "fact_conf": 0.5,
      "legal_conf": null,
      "legal_strength": null,
      "description": "Наблюдение: на 00:07 ДЕМО / ТЕСТ — фиктивное нарушение тестового анализатора (STUB_DEBUG=1), не является реальным результатом анализа. Тип нарушения не определён автоматически, требуется проверка сотрудником. Ссылка на норму не приводится, если событие не совпадает ни с одним правилом справочника.",
      "rule": null,
      "rules": [],
      "evidence_frames": [
        {"timestamp_sec": 7.783, "image_base64": "/9j/4AAQSkZJRgAB…", "annotated": true,
         "boxes": [{"x": 0.25, "y": 0.3, "w": 0.5, "h": 0.4, "label": "Зона кассы (демо)", "kind": "region"}]},
        {"timestamp_sec": 7.783, "image_base64": "/9j/4AAQSkZJRgAB…", "annotated": false,
         "boxes": [{"x": 0.25, "y": 0.3, "w": 0.5, "h": 0.4, "label": "Зона кассы (демо)", "kind": "region"}]},
        {"timestamp_sec": 8.283, "image_base64": "/9j/4AAQSkZJRgAB…", "annotated": false, "boxes": []}
      ],
      "clip_url": "/api/v1/jobs/<job_id>/clips/1.mp4",
      "analyzer_description": "ДЕМО / ТЕСТ: фиктивное нарушение, сгенерированное тестовым анализатором (STUB_DEBUG=1). Не является реальным результатом анализа."
    }
  ],
  "progress": 1.0,
  "stage": "Готово",
  "error": null,
  "created_at": "2026-10-17T10:05:17+00:00",
  "finished_at": "2026-10-17T10:05:18+00:00",
  "video_meta": {
    "duration_sec": 15.566667,
    "fps": 30.0,
    "width": 464,
    "height": 848,
    "rotation": 0,
    "codec": "h264",
    "frame_count": 467
  },
  "filename": "Х-10.mov",
  "partial": false,
  "meta": {
    "analyzer": {
      "analyzer": "stub",
      "frames_decoded": 16,
      "sample_fps": 1.0,
      "stopped_early": false,
      "debug": true,
      "video": "…"
    }
  },
  "warnings": []
}
```

- Статусы: `processing` (тогда `verdict: null`, `violations: []`), `done` и `failed` (у `failed` есть поле `error`, `verdict: null`).
- Неизвестный `job_id` даёт `404`.
- `image_base64` — это JPEG без префикса `data:`. Дополнительные поля кадра (`annotated`, `boxes`) и нарушения (`clip_url`) описаны в разделе «Доказательства: разметка и видеофрагменты».
- `warnings` — список предупреждений для оператора на русском (есть у задачи в любом статусе, по умолчанию `[]`): `AnalysisResult.warnings` анализатора плюс `meta["notes_ru"]` (например, «анализ выполнен без VLM-проверок, результат может быть неполным»). Без повторов, не больше 10 строк по 300 символов; `notes_ru` сохраняются, даже если сама `meta` отброшена по размеру. При `partial: true` сервис добавляет в конец своё предупреждение: «анализ прерван по времени, результат может быть неполным» (дедлайн, `stopped_early` / `partial` в meta, бюджет съеден очередью) или «анализ прерван из-за ошибки, результат может быть неполным» (анализатор упал, но успел что-то найти). Веб-интерфейс показывает их блоком «Предупреждения» над списком нарушений.
- `legal_strength` у нарушения — сила указанной нормы: `"high"` | `"medium"` | `"low"` | `null`. `legal_conf` от `forte_detector` — порядковая кодировка этой метки (0,9 / 0,7 / 0,4), а не вероятность; интерфейс показывает «Сила правовой нормы: высокая/средняя/низкая», если метка есть.
- `meta` — служебные заметки: `meta.analyzer` (то, что анализатор вернул в `AnalysisResult.meta`, если это сериализуется в JSON и занимает не больше ~8 КБ), `meta.analyzer_error` (анализатор упал, но успел что-то найти), `meta.note` (бюджет исчерпан ожиданием в очереди). Без заметок — `{}`.
- Прочие маршруты:
  - `GET /healthz` → `{"ok": true, "analyzer_ready": true, "analyzer_error": null, "public_url": null, "public_url_source": null, "rules_path": "/…/app/rules.json", "rules_count": 17, "vlm_ok": true, "vlm_url": "http://127.0.0.1:8090/health"}`. `rules_path` / `rules_count` — какой справочник правил загружен и сколько в нём записей. `analyzer_ready` становится `true`, когда анализатор загружен; `analyzer_error` — текст ошибки загрузки; `public_url` / `public_url_source` (`"env"` | `"quick-tunnel"` | `null`) — текущий публичный адрес, см. «Публичный адрес»; `vlm_ok` — ответил ли VLM-сервер на `vlm_url` (HTTP 200 и `ok` в теле; `null`, если проверка отключена). `ok` от VLM не зависит.
  - `HEAD` поддерживается на `/healthz`, `/api/v1/jobs/{id}` (с ключом) и GET-маршрутах `/ui-api/*`.
  - `GET|HEAD /api/v1/jobs/{id}/clips/{n}.mp4` (с ключом, Range) — видеофрагмент нарушения `n` (нумерация с 1; `/clips/0.mp4` → 404 — так и задумано). Клиентам следует брать ссылку из поля `clip_url` нарушения как есть, а не собирать её самим.
  - `/ui-api/analyze`, `/ui-api/jobs`, `/ui-api/jobs/{id}`, `/ui-api/jobs/{id}/video` и `/ui-api/jobs/{id}/clips/{n}.mp4` (с поддержкой Range), `/ui-api/rules` — для веб-интерфейса, без ключа. В ответах `/ui-api` ссылка `clip_url` указывает на `/ui-api/...`.

## Доказательства: разметка и видеофрагменты

**Рамки на кадрах.** Анализатор может приложить к `EvidenceFrame` поле `boxes: [{x, y, w, h, label?, kind?}]`. Координаты нормированы 0..1 относительно присланного JPEG (он уже повёрнут как надо). `kind` — `"person"` | `"object"` | `"region"`, без него — `"object"`. Рамки с нечисловыми/бесконечными координатами или `w`/`h` ≤ 0 отбрасываются, координаты обрезаются до [0, 1]. Сервис рисует рамки один раз (Pillow): толщина 3 px, зелёная `#1DB954` для object/region, красная `#E53935` для person; подпись — плашка цвета рамки с белым текстом (шрифт DejaVu Sans из `app/assets/fonts`, около 2,2 % высоты кадра, не меньше 12 px, до 32 символов). Нет `label` — нет плашки: сервис подписей не придумывает, лиц не распознаёт.

Порядок кадров: первый кадр с рамками даёт `evidence_frames[0]` — размеченную копию (`annotated: true`), `[1]` — тот же кадр без разметки, дальше остальные кадры анализатора; всего не больше 3 (ограничение действует всегда). Остальные кадры с рамками тоже размечаются (`annotated: true`), кадры без рамок проходят без изменений. У каждого кадра есть `annotated` и `boxes` (нормализованные рамки). Ошибка рисования не валит задачу: в лог, и этот кадр отдаётся как есть (остальные размечаются). Ближе 1 с к дедлайну доказательств (см. ниже) рамки для оставшихся нарушений не рисуются.

**Видеофрагменты.** После анализа (полного или частичного) на каждое нарушение режется H.264 MP4 без звука: окно `[start_sec − 2, end_sec + 2]` (`start_sec` по умолчанию = `timecode_sec`, `end_sec` = `start_sec`), длина 3–8 с (длиннее — центрируется на `timecode_sec`), в пределах видео; высота 480 (без увеличения), `-preset veryfast -crf 28 -movflags +faststart`. Файлы: `DATA_DIR/clips/<job_id>/<n>.mp4`, `n` — номер нарушения в итоговом списке (с 1). Поле `clip_url` есть только у нарушений, для которых фрагмент создан; маршруты отдают только такие `n` текущей задачи. Каталог фрагментов задачи очищается перед каждой нарезкой. Готовый фрагмент проверяется ffprobe (хотя бы один кадр), иначе он удаляется и считается неудавшимся.

- Поворот: ffmpeg поворачивает по тегу контейнера; дополнительный поворот по часовой стрелке анализатор сообщает в `AnalysisResult.meta["applied_rotation_deg"]` (0/90/180/270), и сервис применяет его (`transpose=1` / `transpose=2` / `hflip,vflip`), чтобы фрагмент совпадал с кадрами. Если meta нет (анализ прерван по времени или упал), поворот считается 0.
- Бюджет: у нарезки свой дедлайн — `submit + JOB_HARD_CAP_SEC − 2 с` (обычно 278 с против ~270 с у анализатора), так что время у анализатора не отнимается, а задача не выходит за `submit + JOB_HARD_CAP_SEC`. Для задачи, долго ждавшей в очереди (её дедлайн уже равен потолку), это тоже потолок − 2 с. Фрагменты режутся параллельно (до 4 ffmpeg по `-threads 2`), сначала самые важные: по убыванию `fact_conf` (иначе `confidence`), затем по времени; `n` остаётся номером в итоговом списке. Таймаут каждого ffmpeg = время до дедлайна нарезки − 1 с, по таймауту процесс убивается; если таймаут вышел бы меньше 1,5 с, фрагмент не запускается; если до дедлайна меньше 2 с, не режется ничего. Задача остаётся `processing`, пока фрагменты не готовы или не пропущены.
- Предупреждения: фрагменты, пропущенные или прерванные из-за времени, — «не хватило времени на нарезку клипов № …»; настоящие ошибки — «не удалось создать клипы № …». Задачу они не валят. У задачи без нарушений таких предупреждений нет.
- Интерфейс: карточка нарушения с `clip_url` показывает фрагмент (без звука, по кругу, играет, только пока карточка на экране; постер — первый кадр-доказательство, элементы управления — при наведении или фокусе). Под ним остаются миниатюры кадров; размеченные помечены «Разметка». Без фрагмента — только миниатюры.

## Справочник правил

Нарушения и нормы берутся из одного JSON-файла. Порядок поиска (побеждает первый файл, в котором есть хотя бы одна корректная запись):

1. `RULES_PATH`;
2. `../detector/taxonomy/rules.json` (относительно корня сервиса; канонический файл детектора);
3. `../docs/deliverables/rules_spec.json`;
4. встроенный `app/rules.json` — дословная копия `rules_spec.json` (id `T1`, `T1b`, `T2`…`T15`, `T99`).

Схема записи (принимаются формы `{id: {...}}`, `{"rules": {id: {...}}}` и список объектов с полем `"id"`):

```json
{
  "T1": {
    "title_ru": "Наличные/ценности оставлены без присмотра",
    "title_en": "Cash/valuables left unattended",
    "description_template": "Наблюдение: на {t} кассир покинул рабочее место…{obs}. Норма: …",
    "citations": [
      {"document": "Правила … (Постановление Правления НБРК № 231 от 29.11.2019)",
       "point": "п. 22", "quote": "…", "url": "https://adilet.zan.kz/rus/docs/V1900019680",
       "legal_conf": "medium"}
    ]
  }
}
```

Обязательно только `title_ru`; `title_en`, `description_template`, `citations` (в каждой цитате обязателен `document`, `point`/`quote`/`url` необязательны) можно опустить. Неизвестные ключи сохраняются и отдаются в `/ui-api/rules`, у цитат — в `rule`/`rules` (например строковый `legal_conf` уровня цитаты; его не путать с числовым `legal_conf` нарушения). Некорректные записи пропускаются с предупреждением в логе. Пустой список `citations` (как у `T99`) означает, что сервис не приводит ссылку на норму.

## Подключение реального анализатора

Интерфейс описан в `app/analyzers/base.py`, пример реализации — `app/analyzers/stub.py`.

```python
from pathlib import Path
from app.analyzers.base import PartialSink, ProgressCb, deadline_passed
from app.models import AnalysisResult, EvidenceFrame, Violation
from app.video import probe, iter_frames, frame_at, jpeg_b64

class MyAnalyzer:
    name = "my-detector"

    def __init__(self):            # вызывается один раз при старте воркера: здесь грузим модели
        ...                        # (torch.inference_mode() здесь НЕ действует на analyze: другой поток)

    def analyze(self, video_path: Path, progress_cb: ProgressCb,
                deadline: float, partial: PartialSink) -> AnalysisResult:
        meta = probe(video_path)
        found = []
        for t, frame in iter_frames(video_path, fps=2, max_side=960, meta=meta):
            if deadline_passed(deadline) or partial.cancelled:   # deadline — значение time.monotonic()
                break
            progress_cb(min(1.0, t / max(meta.duration_sec, 1e-6)), "Анализ кадров")   # доля 0..1, текст по-русски
            if ...:  # нашли нарушение
                v = Violation(type="cash_unattended", timecode_sec=t, end_sec=t + 3, confidence=0.8,
                              description="Наличные оставлены без присмотра",
                              evidence_frames=[EvidenceFrame(timestamp_sec=t, image_base64=jpeg_b64(frame))])
                partial.add(v)                     # сразу отдаём в частичный результат
                found.append(v)
        return AnalysisResult(verdict=None, violations=found, meta={"stopped_early": deadline_passed(deadline)})
```

Запуск: `ANALYZER=mypkg.detector:MyAnalyzer ./run.sh`. Модуль должен импортироваться из корня проекта или из venv. Класс создаётся без аргументов один раз за процесс.

Наш детектор уже подключён как зависимость (`forte-detector`, editable из `../detector`, вместе с torch cu128): `ANALYZER=forte_detector.service_adapter:ForteAnalyzer RULES_PATH=../detector/taxonomy/rules.json FORTE_VLM_URL=http://127.0.0.1:8090/v1/chat/completions ./run.sh`. Доступность VLM видна в `/healthz` (`vlm_ok`). Если VLM недоступен, анализ идёт без VLM-проверок, и задача получает предупреждение в `warnings`. Docker-образ этот путь пока не собирает: `../detector` не входит в контекст сборки.

Зависимости пайплайна (например, torch) добавляйте через `uv add <пакет>`, чтобы они попали в `pyproject.toml` и `uv.lock`; `pip install` в venv переживёт `run.sh` только благодаря `--inexact` и не воспроизведётся на другой машине.

Модель загружается в `__init__` в потоке воркера, а каждый вызов `analyze()` идёт в **новом** потоке. Поэтому `torch.inference_mode()` / `torch.no_grad()` (они действуют на поток) включайте внутри `analyze`, а не в `__init__`.

Загрузка анализатора идёт в фоне после старта сервера. Пока `GET /healthz` не вернул `"analyzer_ready": true`, задачи ждут в очереди, и время загрузки тяжёлой модели съедает бюджет первых задач. Открывайте туннель и отдавайте URL жюри только после `analyzer_ready: true`.

Как сервис обращается с результатом анализатора:
- **Дедлайн.** Анализатор работает в отдельном потоке и получает `deadline` с небольшим запасом (до 5 с) до жёсткого дедлайна задачи `min(max(отправка + JOB_TIMEOUT_SEC, старт + 20 с), отправка + JOB_HARD_CAP_SEC)`. Если он не вернулся к жёсткому дедлайну, воркер перестаёт ждать и завершает задачу тем, что уже лежит в `partial` (`partial: true`, статус `done`), и выставляет `partial.cancelled = True`. Поток Python нельзя убить, поэтому опаздывающий анализатор продолжит есть CPU/GPU; следующая задача ждёт его не больше min(10 с, 25% своего оставшегося бюджета) и затем стартует параллельно с ним. В каждом цикле проверяйте `deadline_passed(deadline) or partial.cancelled` и сразу выходите.
- **Частичность.** Если анализатор сам остановился по дедлайну, пусть вернёт `meta={"stopped_early": True}` (или `"partial": True`), и задача будет помечена `partial: true`.
- **Типы нарушений.** Анализатор пишет в `Violation.type` id правила из справочника (`T1`, `T1b`, `T2`…`T15`, `T99`). Дополнительно можно передать `start_sec`, `end_sec`, `fact_conf` и `legal_conf` (0..1; нечисловые значения → `null`), `obs` (короткое наблюдение для шаблона описания) и `title_ru` (используется, только если id нет в справочнике). В ответе API `type` — это русское название правила, а id лежит в `type_id`. Неизвестный id проходит как есть: `type` = `title_ru` анализатора или сам id, `rule: null`, `rules: []`.
- **Цитаты регламента.** `title_ru`, `rule` и `rules` сервис подставляет **только из справочника правил**. Любые цитаты или ссылки, которые вернул анализатор, отбрасываются; если для типа в справочнике нет нормы, ссылки не будет (интерфейс пишет «Норма не определена — требуется проверка специалистом»).
- **Описание.** Если id есть в справочнике и у правила есть `description_template`, в ответ идёт заполненный шаблон (даже если анализатор прислал свой `description`; тогда его текст сохраняется в поле `analyzer_description`); иначе — `description` анализатора; если и он пуст (или шаблон дал пустую строку) — `"{название}: {obs}"`, когда передан `obs`, иначе просто название типа. Шаблон заполняется так: `{t}` → таймкод «мм:сс» (с часа — «ч:мм:сс»), `{obs}` → наблюдение (с обрезанными пробелами и точкой в конце) вставляется так: после непробельного символа — `", " + obs`, но если `obs` начинается с `,` `;` `:` `.` — как есть, а если с открывающего знака (`(`, `«`, `"` и т. п.) — `" " + obs`; после пробела — `obs` как есть; в самом начале шаблона — `obs` с заглавной буквы. Без `obs` плейсхолдер удаляется; прочие `{...}` удаляются, двойные пробелы и пробелы перед знаками препинания убираются.
- **Вердикт.** Сервис пересчитывает его сам: `violation` тогда и только тогда, когда есть хотя бы одно нарушение.
- **Исключения.** Если анализатор бросил исключение, ничего не добавив в `partial`, задача переходит в `failed` с текстом ошибки. Если до исключения в `partial` уже были нарушения, задача завершается как `done` с `partial: true` и этими нарушениями, а текст ошибки пишется в `meta.analyzer_error`. Отдельные некорректные нарушения (NaN-таймкод, нет `type`) отбрасываются, остальные сохраняются. `confidence`, `fact_conf`, `legal_conf` обрезаются до диапазона 0..1; если `confidence` нет, он берётся из `fact_conf`.
- **Мета.** `AnalysisResult.meta` сохраняется в `meta.analyzer` задачи (NaN → `null`, неизвестные типы → строка); если в сериализованном виде больше ~8 КБ, мета отбрасывается.
- **Видео.** Для работы с видео используйте `app/video.py` (`probe`, `iter_frames`, `frame_at`, `jpeg_b64`). Эти функции учитывают поворот и работают с кириллицей в путях через ffmpeg. `cv2.VideoCapture` на путях не используйте. Если ffmpeg не смог декодировать ни одного кадра, `iter_frames` бросает `VideoError` (а не молча отдаёт пустой поток).

## Развёртывание на целевой машине (Ubuntu 24.04, RTX 5080)

Целевая машина: Ubuntu 24.04 без WSL, RTX 5080 16 ГБ (Blackwell, sm_120), 64 ГБ RAM, драйвер NVIDIA ≥ 570. Blackwell работает только с открытыми модулями ядра (`nvidia-driver-570-open` или новее) и требует CUDA ≥ 12.8: образ собран на `nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04`, а для torch в `pyproject.toml` уже прописан индекс колёс `cu128` (см. «torch для RTX 50xx»).

Основной путь — Docker Compose. Запасной — bare-metal с systemd. Как сделать сервис доступным из интернета, описано в разделе «Публичный адрес».

### 1. Docker Compose (основной путь)

**Docker Engine** (если ещё не стоит) и автозапуск после перезагрузки:

```bash
curl -fsSL https://get.docker.com | sudo sh        # официальный скрипт Docker (apt-репозиторий download.docker.com)
sudo usermod -aG docker $USER                      # затем перелогиньтесь
sudo systemctl enable docker                       # docker стартует при загрузке, контейнеры поднимутся сами
```

**NVIDIA Container Toolkit** (официальные шаги для Ubuntu):

```bash
nvidia-smi                                         # драйвер на хосте должен работать
sudo apt-get update && sudo apt-get install -y --no-install-recommends curl gnupg2
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
# проверка: должна напечататься таблица nvidia-smi с RTX 5080
docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi
```

**Запуск сервиса:**

```bash
cp .env.example .env && nano .env      # обязательно свой API_KEY; PORT — порт на хосте (по умолчанию 8000)
mkdir -p data models                   # иначе Docker создаст их от root, а контейнер работает от uid 1000
docker compose up -d --build
docker compose ps                      # app должен стать healthy
curl -s localhost:8000/healthz         # {"ok":true,"analyzer_ready":true,...}  (порт открыт только на 127.0.0.1)
docker compose logs -f app
```

- Образ: CUDA 12.8.1 + cuDNN, ffmpeg, Python 3.12 через `uv` (зависимости строго по `uv.lock`, без dev), пользователь `app` (uid 1000), `HEALTHCHECK` на `/healthz`, один процесс uvicorn на порту 8000 внутри контейнера.
- Тома: `./data` → `/app/data` (загрузки и задачи, переживают пересоздание контейнера), `./models` → `/models` (кэш весов: `MODELS_DIR=/models`, `HF_HOME=/models/hf`, `TORCH_HOME=/models/torch`). Если ваш uid на хосте не 1000: `sudo chown -R 1000:1000 data models`.
- Переменные берутся из `.env` (необязателен). `DATA_DIR` внутри контейнера всегда `/app/data`. **После правки `.env` — `docker compose up -d`**: compose пересоздаст контейнеры, у которых изменилось окружение; `docker compose restart` перезапускает контейнер со старыми переменными.
- **Справочник правил и анализатор в Docker.** Внутри контейнера поиск по умолчанию (`../detector/...`, `../docs/...` относительно `/app`) ничего не находит, поэтому используется встроенный `app/rules.json`. Чтобы взять правила детектора, смонтируйте файл и задайте `RULES_PATH` (`/healthz` → `rules_path` покажет, что загружено):
  ```yaml
  # docker-compose.override.yml (compose подхватывает его сам)
  services:
    app:
      volumes:
        - ../detector/taxonomy/rules.json:/rules/rules.json:ro
      environment:
        RULES_PATH: /rules/rules.json
  ```
  Реальный анализатор тоже должен попасть в контейнер: либо код лежит в репозитории сервиса (например, `app/analyzers/...`) и попадает в образ при `docker compose up -d --build`, либо монтируется томом (например, `../detector:/detector:ro` + `PYTHONPATH=/detector`). В обоих случаях задайте `ANALYZER=пакет.модуль:Класс`, а его Python-зависимости добавьте через `uv add` и пересоберите образ.
- Порт публикуется как `${BIND_ADDR:-127.0.0.1}:${PORT:-8000}:8000`, то есть только на loopback хоста. Docker пишет свои правила iptables в обход `ufw`, поэтому «открыто всем» нельзя закрыть файрволом. Релей (autossh → `127.0.0.1:8000`), Tailscale Funnel (`localhost:8000`) и Caddy/quick tunnel (`app:8000` внутри сети compose) так работают. Для демо с другого компьютера в LAN: `BIND_ADDR=0.0.0.0` в `.env` и `docker compose up -d`.
- GPU отдаётся контейнеру через `deploy.resources.reservations.devices` (driver `nvidia`, все GPU).
- `restart: unless-stopped` у всех сервисов: вместе с `systemctl enable docker` сервис (и выбранный способ публикации) поднимаются после перезагрузки.
- Обновление кода: `git pull && docker compose up -d --build`. Compose пересоздаёт только изменённые сервисы; `docker compose restart app` трогает только `app` (но `.env` не перечитывает).

### 2. Bare-metal + systemd (запасной путь)

```bash
scripts/setup.sh --with-cloudflared    # проверяет nvidia-smi и ffmpeg (печатает sudo apt ...), ставит uv в ~/.local/bin,
                                       # делает uv sync --frozen --inexact, создаёт data/ и models/, качает cloudflared
cp .env.example .env && nano .env
./run.sh                               # проверить руками, затем Ctrl+C
```

`setup.sh` идемпотентен и сам не вызывает `sudo`: недостающее (например, `sudo apt install -y ffmpeg`) он печатает. `--with-cloudflared` нужен только для варианта C.

**systemd** — автозапуск после перезагрузки. Юниты лежат в `deploy/systemd/`:
- `forte-service.service` запускает `run.sh` от вашего пользователя, `EnvironmentFile=` указывает на `.env`, `Restart=always`;
- `forte-relay.service` (основной публичный адрес, см. «Релей») — autossh-туннель к серверу команды; нужен и при Docker, и при bare-metal;
- `forte-tunnel.service` (только вариант C) запускает `scripts/tunnel.sh` с `Restart=always`. Он **не** привязан к сервису (`PartOf=`/`BindsTo=` нет), поэтому перезапуск сервиса не меняет URL. `ExecStartPost` до 60 с ждёт адрес и пишет его в `DATA_DIR/public_url.txt`, `ExecStopPost` удаляет файл при остановке (поправьте путь, если `DATA_DIR` другой). Запасной поиск адреса по журналу читает только текущий запуск юнита; пользователю юнита для этого нужна группа `adm` или `systemd-journal`.

```bash
sudo cp deploy/systemd/forte-service.service /etc/systemd/system/
# поправьте User=, Group=, WorkingDirectory=, EnvironmentFile=, ExecStart= и PATH
# (в шаблоне: пользователь forte, проект в /home/forte/forte-service)
sudo nano /etc/systemd/system/forte-service.service
sudo systemctl daemon-reload
sudo systemctl enable --now forte-service
journalctl -u forte-service -f
```

Для варианта C то же самое с `forte-tunnel.service` (`sudo systemctl enable --now forte-tunnel` — только когда пора открывать доступ). Если `cloudflared` поставлен из apt-репозитория Cloudflare, он лежит в `/usr/bin` и уже есть в `PATH` юнита.

## Публичный адрес

Жюри нужен постоянный `https://` адрес. **Выбранный вариант — релей: `https://forte.incode.kz`** через сервер команды (89.126.193.108). Роутер на площадке не трогаем, белый IP не нужен. Остальные варианты — запасные:

```
Релей работает (forte-relay active, curl https://forte.incode.kz/healthz снаружи)  → готово
└─ сервер команды недоступен / нет SSH-доступа к нему:
   curl -4 ifconfig.me   # сравните с WAN IP в веб-интерфейсе роутера
   ├─ совпадают (белый IP)                         → A. A-запись у hoster.kz + проброс 80/443 + Caddy на ПК
   ├─ WAN IP роутера из 100.64.0.0/10, 10.x, 172.16-31.x, 192.168.x
   │  или не совпадает с ifconfig.me (CGNAT)       → B. Tailscale Funnel
   └─ ничего не получилось / нет времени            → C. Cloudflare quick tunnel (URL меняется при перезапуске)
```

`GET /healthz` показывает текущий адрес: `public_url` и `public_url_source`. Если задан `PUBLIC_HOSTNAME`, это `https://$PUBLIC_HOSTNAME` и `"env"` (релей, варианты A и B). Иначе берётся содержимое `PUBLIC_URL_FILE` (по умолчанию `DATA_DIR/public_url.txt`, его пишет `scripts/public_url.sh`) и `"quick-tunnel"`. Если нет ни того, ни другого — `null`. Файл перечитывается на каждый запрос.

### Релей через сервер команды: `forte.incode.kz` (основной вариант)

```
жюри ──https──▶ forte.incode.kz (89.126.193.108: Caddy или nginx, TLS)
                   └─▶ 127.0.0.1:18000 на сервере ══ SSH-туннель (autossh, инициирует ПК) ══▶ 127.0.0.1:8000 на ПК
```

ПК сам подключается к серверу по SSH и держит обратный туннель (`-R 127.0.0.1:18000:127.0.0.1:8000`), поэтому входящие соединения на площадку не нужны. Порт 18000 слушается только на loopback сервера; наружу смотрит только прокси с TLS. Всё, что ставится на сервер, — отдельные новые файлы; существующие сайты incode.kz не меняются (единственное возможное касание — одна строка `import` в Caddyfile, см. ниже).

**1. DNS (hoster.kz):** A-запись `forte.incode.kz` → `89.126.193.108`. Проверка: `dig +short forte.incode.kz`.

**2. ПК: ключ и юнит.**

```bash
sudo apt install -y autossh
install -d -m 700 ~/.ssh
ssh-keygen -t ed25519 -N "" -C forte-relay -f ~/.ssh/forte_relay          # отдельный ключ без пароля
cat ~/.ssh/forte_relay.pub                                                 # → на сервер, шаг 3
ssh-keyscan -t ed25519 89.126.193.108 > ~/.ssh/known_hosts_forte_relay     # закрепить ключ хоста сервера
ssh-keygen -lf ~/.ssh/known_hosts_forte_relay                              # сверить отпечаток с тем, что на сервере
                                                                           # (на сервере: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub)
```

SSH на сервере не на порту 22: `ssh-keyscan -p "$PORT" -t ed25519 89.126.193.108 > ~/.ssh/known_hosts_forte_relay` — запись будет вида `[89.126.193.108]:PORT`, именно её ssh ищет для нестандартного порта; тот же порт укажите в `RELAY_SSH_PORT` юнита.

**3. Сервер: отдельный пользователь, которому разрешён только этот форвард.**

```bash
sudo adduser --disabled-password --gecos "" forte-relay
sudo install -d -m 700 -o forte-relay -g forte-relay /home/forte-relay/.ssh
# одна строка: restrict отключает всё, port-forwarding возвращает форварды, permitlisten разрешает только 127.0.0.1:18000
echo 'restrict,port-forwarding,permitlisten="127.0.0.1:18000" ssh-ed25519 AAAA...ключ-с-ПК... forte-relay' \
  | sudo tee /home/forte-relay/.ssh/authorized_keys
sudo chown forte-relay: /home/forte-relay/.ssh/authorized_keys && sudo chmod 600 /home/forte-relay/.ssh/authorized_keys
# ОБЯЗАТЕЛЬНО: без этого после обрыва связи sshd держит мёртвую сессию и порт 18000 до ~2 часов,
# каждое переподключение autossh падает на ExitOnForwardFailure, и forte.incode.kz всё это время отвечает 502
sudo cp deploy/relay/sshd-forte-relay.conf /etc/ssh/sshd_config.d/forte-relay.conf
sudo sshd -t && sudo systemctl reload ssh
sudo sshd -T -C user=forte-relay,host=x,addr=1.2.3.4 | grep -i clientalive   # ожидается: clientaliveinterval 15, clientalivecountmax 3
```

Drop-in `deploy/relay/sshd-forte-relay.conf` **обязателен**: с ним sshd замечает мёртвую сессию за ~45 с (`ClientAliveInterval 15` × 3) и освобождает `127.0.0.1:18000`, и переподключившийся туннель снова занимает порт. Блок `Match User forte-relay` действует только на этого пользователя.

`permitlisten` требует OpenSSH ≥ 7.8 (в Ubuntu 20.04+ есть). Если в `sshd_config` стоит `AllowUsers`/`AllowGroups`, добавьте туда `forte-relay`.

**4. Сервер: прокси.** Один из двух вариантов — смотря что уже стоит на сервере.

- **Caddy:** `deploy/relay/forte.caddy` → `/etc/caddy/sites/forte.caddy`. Если в основном `/etc/caddy/Caddyfile` ещё нет `import sites/*.caddy`, допишите в конец эту одну строку. Если основной Caddyfile — один сайт **без фигурных скобок** (адрес в первой строке, дальше директивы), сначала оберните этот сайт в `{ }`, иначе строка `import` станет частью того сайта. Перед перезагрузкой всегда проверяйте: `sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile && sudo systemctl reload caddy`. Сертификат Caddy получит сам. Если Caddy на сервере работает в Docker в bridge-сети, `127.0.0.1` для него — сам контейнер: нужен `network_mode: host` или вариант NetBird.
- **nginx:** сначала сертификат, потом сайт (подробности в комментариях файла):
  ```bash
  sudo apt install -y certbot python3-certbot-nginx      # если certbot ещё не стоит
  sudo certbot certonly --nginx -d forte.incode.kz --deploy-hook "systemctl reload nginx"
  sudo cp deploy/relay/forte.incode.kz.nginx /etc/nginx/sites-available/forte.incode.kz
  sudo ln -s /etc/nginx/sites-available/forte.incode.kz /etc/nginx/sites-enabled/forte.incode.kz
  sudo nginx -t && sudo systemctl reload nginx
  sudo certbot renew --dry-run          # продление работает
  ```
  В файле: `client_max_body_size 250m`, `proxy_request_buffering off` (загрузка идёт потоком, без буфера на диске сервера), `proxy_read_timeout`/`proxy_send_timeout 600s`, keep-alive к апстриму с `keepalive_timeout 60s`. Если nginx на сервере работает в Docker в bridge-сети — то же предупреждение про `127.0.0.1`, что и для Caddy.

Оба варианта пропускают тело до 250 МБ (лимит сервиса 200 МБ, поэтому большой файл получит JSON `413` от сервиса), таймауты 10 минут. Простаивающие соединения к сервису прокси закрывает через 60 с, а uvicorn запущен с `--timeout-keep-alive 75` (`run.sh`, `Dockerfile`): соединение всегда закрывает прокси, и запрос не попадает на соединение, которое сервис как раз закрывает (иначе — случайные 502, а загрузку POST повторить нельзя). Пока туннель или ПК недоступны, сервер отвечает `502`.

**5. ПК: запуск туннеля.**

```bash
sudo cp deploy/systemd/forte-relay.service /etc/systemd/system/
sudo nano /etc/systemd/system/forte-relay.service      # User=, Group=, RELAY_KEY, RELAY_KNOWN_HOSTS (и RELAY_USER/RELAY_HOST, если другие)
sudo systemctl daemon-reload
sudo systemctl enable --now forte-relay
journalctl -u forte-relay -f                           # без ошибок = туннель поднят
```

Юнит не зависит ни от Docker, ни от `forte-service`: перезапуск сервиса туннель не рвёт. `Restart=always`, `StartLimitIntervalSec=0`, `AUTOSSH_GATETIME=0`, `AUTOSSH_POLL=30` — переподключается бесконечно, в том числе если при загрузке сети ещё нет. Обрыв связи ssh замечает за ~45 с (`ServerAliveInterval=15` × 3). С Docker приложение слушает `127.0.0.1:8000` на хосте — именно туда идёт туннель.

**6. `.env` на ПК:** `PUBLIC_HOSTNAME=forte.incode.kz`, затем `docker compose up -d` (bare-metal: `sudo systemctl restart forte-service`). `/healthz` покажет `"public_url": "https://forte.incode.kz"`, `"public_url_source": "env"`.

**Проверка:**

```bash
# на сервере: туннель слушает
ss -ltnp | grep 18000                       # 127.0.0.1:18000 ... sshd
curl -s 127.0.0.1:18000/healthz
# снаружи (телефон / ноутбук через мобильный интернет)
curl -s https://forte.incode.kz/healthz
```

**Вариант 2: NetBird вместо SSH-туннеля** (только описание, если SSH-доступа к серверу недостаточно или нужен постоянный mesh). На ПК и на сервере: `curl -fsSL https://pkgs.netbird.io/install.sh | sh`, затем `sudo netbird up --setup-key <ключ из панели NetBird>` (или `sudo netbird up` и вход по ссылке); `netbird status` показывает адрес ПК вида `100.x.y.z`. На сервере прокси смотрит на `<netbird-ip-ПК>:8000`: Caddy — `FORTE_UPSTREAM=<netbird-ip>:8000` в окружении (`sudo systemctl edit caddy`), nginx — строка `server` в блоке `upstream forte_pc`. На ПК приложение должно слушать этот интерфейс: при Docker `BIND_ADDR=<netbird-ip>` (или `0.0.0.0` + разрешить в файрволе только `100.64.0.0/10`), bare-metal `run.sh` и так слушает `0.0.0.0`. `forte-relay.service` в этом варианте не нужен.

### Запасные варианты

### A. Белый IP: Caddy на ПК с автоматическим HTTPS

1. У hoster.kz: A-запись `forte.<домен>` → внешний IP машины (проверка: `dig +short forte.<домен>`).
2. На роутере: проброс TCP 80 и 443 на эту машину (80 нужен Let's Encrypt для выпуска сертификата). В `ufw`, если он включён: `sudo ufw allow 80,443/tcp`.
3. В `.env`: `PUBLIC_HOSTNAME=forte.<домен>` (без него контейнер `caddy` сразу завершится с ошибкой `set PUBLIC_HOSTNAME in .env`).
4. Запуск:
   ```bash
   docker compose --profile caddy up -d caddy
   docker compose logs -f caddy        # ждём "certificate obtained successfully"
   curl -s https://forte.<домен>/healthz
   ```

`deploy/Caddyfile`: `reverse_proxy` на `app:8000`, `request_body max_size 250MB` (лимит самого сервиса 200 МБ, поэтому большой файл получит JSON-ответ `413` от сервиса), таймауты апстрима 10 минут, тело запроса стримится без буферизации. Сертификаты лежат в томе `caddy_data`: не удаляйте его, иначе можно упереться в лимиты Let's Encrypt.

Bare-metal вариант: Caddy из пакета (`sudo apt install caddy` в Ubuntu 24.04 или свежий из официального apt-репозитория Caddy), наш файл и адрес сервиса на хосте:

```bash
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl edit caddy            # добавить:
# [Service]
# Environment=PUBLIC_HOSTNAME=forte.<домен>
# Environment=UPSTREAM=localhost:8000
sudo systemctl enable caddy && sudo systemctl restart caddy
journalctl -u caddy -f
```

### B. Нет белого IP (CGNAT): Tailscale Funnel

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up                    # вход в аккаунт Tailscale по ссылке
sudo tailscale funnel --bg 8000      # https://<имя-пк>.<tailnet>.ts.net → localhost:8000, переживает перезагрузку
tailscale funnel status              # показывает публичный адрес
```

В `.env`: `PUBLIC_HOSTNAME=<имя-пк>.<tailnet>.ts.net`, затем `docker compose up -d` (именно `up -d`: `docker compose restart` не перечитывает `.env`; bare-metal: `sudo systemctl restart forte-service`). При первом запуске Tailscale может попросить включить HTTPS и Funnel для tailnet в админке (ссылка будет в выводе). Funnel работает только на портах 443, 8443 и 10000, адрес постоянный, сертификат выпускает Tailscale; это сервис с ограничениями по пропускной способности, поэтому проверьте загрузку тестового видео заранее.

### C. Последний вариант: Cloudflare quick tunnel

Аккаунт не нужен, но **при каждом перезапуске туннеля (и после перезагрузки машины) адрес `https://<random>.trycloudflare.com` новый**: адрес, отправленный жюри, перестанет работать. `PUBLIC_HOSTNAME` должен быть пустым.

```bash
# Docker: tunnel + tunnel-url (пишет адрес в data/public_url.txt при каждой смене)
docker compose --profile tunnel up -d
cat data/public_url.txt

# Bare-metal: руками или через forte-tunnel.service (см. systemd выше)
scripts/tunnel.sh                    # cloudflared с --metrics 127.0.0.1:20241
scripts/public_url.sh                # ждёт до 60 с, пишет и печатает https://<host>
```

Откуда берётся адрес: `cloudflared` запускается с `--metrics`, и `GET http://127.0.0.1:20241/quicktunnel` возвращает `{"hostname": "..."}`. Если метрики недоступны, `public_url.sh` берёт последнюю строку `https://*.trycloudflare.com` только из **текущего** запуска: журнал текущего `InvocationID` юнита `forte-tunnel` или логи контейнера `tunnel` с момента его последнего старта — адрес от прошлого запуска или прошлой загрузки не попадёт в файл. Вручную: `docker compose logs tunnel | grep trycloudflare` / `journalctl -u forte-tunnel | grep trycloudflare`.

Ограничения quick tunnel: без гарантий доступности; тело запроса не больше 100 МБ (видео ~29 МБ проходят спокойно, файлы больше 100 МБ не дойдут, даже если `MAX_UPLOAD_MB` выше). `scripts/tunnel.sh` сам ничего не устанавливает: `cloudflared` берётся из `PATH` или переменной `CLOUDFLARED` (`scripts/setup.sh --with-cloudflared`, `sudo pacman -S cloudflared` на Arch, или бинарник с GitHub releases, Apache-2.0). `forte-tunnel.service` удаляет `public_url.txt` при остановке. В Docker-варианте после `docker compose stop tunnel` файл остаётся, и `/healthz` покажет устаревший адрес — удалите файл. Образ `cloudflare/cloudflared` закреплён на проверенной версии `2026.10.1`.

### D. Именованный туннель Cloudflare (не используется)

Нужен домен, DNS которого обслуживает Cloudflare; у нас DNS у hoster.kz, поэтому вариант отложен. Для справки: `cloudflared tunnel login`, `cloudflared tunnel create forte`, `cloudflared tunnel route dns forte forte.<домен>`, `cloudflared tunnel run --url http://localhost:8000 forte` (или токен из панели: `cloudflared tunnel --no-autoupdate run --token <TOKEN>`).

## День защиты: чек-лист

Подготовка машины (один раз):

```bash
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target   # без сна

# Без автообновлений. В Ubuntu их запускает не служба unattended-upgrades, а таймеры apt-daily*
# (по настройкам /etc/apt/apt.conf.d/20auto-upgrades), поэтому выключаем таймеры и сами настройки:
sudo systemctl disable --now apt-daily.timer apt-daily-upgrade.timer
printf 'APT::Periodic::Update-Package-Lists "0";\nAPT::Periodic::Unattended-Upgrade "0";\n' \
  | sudo tee /etc/apt/apt.conf.d/20auto-upgrades
# На всякий случай запрещаем обновлять драйвер и Docker даже при ручном apt upgrade:
dpkg -l | grep -E 'nvidia-(driver|utils)'        # точные имена пакетов драйвера, например nvidia-driver-570-open
sudo apt-mark hold nvidia-driver-570-open nvidia-utils-570 \
  docker-ce docker-ce-cli containerd.io nvidia-container-toolkit   # подставьте имена из вывода выше
apt-mark showhold                                 # проверка

sudo systemctl enable docker                     # или forte-service (bare-metal)
sudo systemctl enable forte-relay                # релей (основной публичный адрес)
```

После мероприятия — вернуть как было:

```bash
sudo apt-mark unhold nvidia-driver-570-open nvidia-utils-570 \
  docker-ce docker-ce-cli containerd.io nvidia-container-toolkit   # те же имена, что при hold
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' \
  | sudo tee /etc/apt/apt.conf.d/20auto-upgrades
sudo systemctl enable --now apt-daily.timer apt-daily-upgrade.timer
sudo systemctl unmask sleep.target suspend.target hibernate.target hybrid-sleep.target
```

Перед отправкой адреса жюри:

1. `curl -s localhost:8000/healthz` → `"analyzer_ready": true`, `"analyzer_error": null`, `public_url` — тот адрес, который отправляете (`https://forte.incode.kz`).
2. В `.env` задан свой `API_KEY` (в логе нет предупреждения про ключ по умолчанию).
3. GPU виден сервису: `docker compose exec app nvidia-smi`.
4. Релей: `systemctl is-active forte-relay` → `active`, в `journalctl -u forte-relay -n 20` нет ошибок; на сервере `ss -ltn | grep 18000` показывает `127.0.0.1:18000`.
5. Проверка через публичный адрес **снаружи локальной сети** (с телефона или ноутбука через мобильный интернет, не через Wi-Fi площадки):
   ```bash
   URL=https://forte.incode.kz; KEY=<ваш ключ>
   curl -s $URL/healthz
   curl -s -o /dev/null -w '%{http_code}\n' $URL/api/v1/jobs/x                         # 401 без ключа
   curl -s -H "X-API-Key: $KEY" -F file=@sample.mov $URL/api/v1/analyze                # {"job_id": ...}
   curl -s -H "X-API-Key: $KEY" $URL/api/v1/jobs/<job_id>                               # опрашивать до status=done (< 280 с)
   ```
6. Автозапуск: `systemctl is-enabled docker forte-relay` (или `forte-service forte-relay`) → `enabled`.
7. После отправки адреса **не перезапускайте** без нужды машину, туннель, Funnel или Caddy. Релей после перезагрузки поднимается сам (адрес тот же), но это минута простоя; с quick tunnel перезапуск = новый адрес.

### torch для RTX 50xx

В `pyproject.toml` настроен явный индекс `pytorch-cu128` (`https://download.pytorch.org/whl/cu128`) и `[tool.uv.sources]` для `torch` и `torchvision`. Сами пакеты пока не в зависимостях. Когда реальному анализатору понадобится torch:

```bash
uv add "torch>=2.7" torchvision      # возьмёт колёса +cu128 из этого индекса (проверено: резолвится torch 2.11.0+cu128)
```

Blackwell (sm_120) поддерживается только колёсами torch ≥ 2.7, собранными с CUDA ≥ 12.8; колёса `cu126` и старше на RTX 5080 не работают. Драйвер хоста ≥ 570 нужен и для Docker, и для bare-metal. После `uv add` пересоберите образ (`docker compose up -d --build`).

<sub>**WSL2 (если всё же Windows).** Драйвер NVIDIA ставится только в Windows (libcuda пробрасывается в WSL через `/usr/lib/wsl/lib`), Linux-драйвер внутри WSL не ставить. Docker: Docker Desktop с WSL2-бэкендом или docker-ce + NVIDIA Container Toolkit внутри WSL. Проверка: `nvidia-smi` в WSL и `docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi`. Порты из WSL доступны в Windows через `localhost`.</sub>

## Тесты

```bash
uv run pytest
```

Тесты генерируют крошечные видео через ffmpeg (`testsrc`, включая файл с кириллическим именем и повёрнутый ролик) и от датасета не зависят.

## Лицензии и ограничения

- Все Python-зависимости под пермиссивными лицензиями: FastAPI, Starlette, uvicorn, pydantic, python-multipart, httpx — MIT/BSD; NumPy — BSD; opencv-python-headless — Apache-2.0/MIT; Pillow — MIT-CMU (HPND); pytest — MIT. GPL/AGPL-пакетов нет.
- Шрифт DejaVu Sans (`app/assets/fonts/DejaVuSans.ttf`) — свободная лицензия Bitstream Vera / DejaVu, текст в `app/assets/fonts/LICENSE-DejaVu.txt`.
- ffmpeg вызывается как внешняя программа (системный пакет), а не линкуется.
- cloudflared — Apache-2.0, опционален и не входит в зависимости проекта.
- Никаких облачных AI API. Распознавания и идентификации лиц нет.

---

## English (short)

This is a local service for the ForteBank Datathon 2026. It analyses cash-room videos for procedure violations and returns a verdict, timecoded violations, JPEG evidence frames and a citation of the regulation. It runs fully offline: no cloud or AI APIs and no face recognition. The analyzer is a plug-in; the default `StubAnalyzer` decodes the video and returns `no_violation`, or one labelled demo violation with `STUB_DEBUG=1`.

- **Run:** `API_KEY=secret ./run.sh` starts a single uvicorn process on `0.0.0.0:${PORT:-8000}`. It must stay a single process because the job queue and worker are in-process. The web UI (Russian) is served at `/`. It needs `uv` and `ffmpeg`/`ffprobe`.
- **API:**
  - `POST /api/v1/analyze` with multipart field `file` → `{"job_id"}`.
  - `GET /api/v1/jobs/{id}` → `{job_id, status, verdict, violations[...]}`.
  - `GET|HEAD /api/v1/jobs/{id}/clips/{n}.mp4` → evidence clip of violation `n` (Range supported). `n` is 1-based; `/clips/0.mp4` → 404 by design. Clients should use each violation's `clip_url` as given rather than building URLs.
  - All need `X-API-Key`.
  - Evidence: analyzer frames may carry `boxes: [{x, y, w, h, label?, kind?}]` (normalized to the JPEG; `kind` person/object/region). The first boxed frame is drawn (Pillow, DejaVu Sans) and returned as `evidence_frames[0]` (`annotated: true`), its raw copy as `[1]`, then the rest (each boxed one drawn too, unboxed ones as is), max 3; a drawing failure leaves only that frame raw. Each violation gets a 3–8 s H.264 480p MP4 (`clip_url`) cut most-important-first (fact_conf/confidence, then timecode) before its own deadline `submit + JOB_HARD_CAP_SEC − 2 s` (never taking time from the analyzer, never past the cap); extra clockwise rotation comes from `AnalysisResult.meta["applied_rotation_deg"]` (0 when there is no meta, e.g. after a timeout). Clips skipped/killed for time add «не хватило времени на нарезку клипов № …», real failures «не удалось создать клипы № …»; neither fails the job.
  - See the curl examples and the sample response above.
- **Env vars:** `API_KEY`, `HOST`, `PORT`, `DATA_DIR`, `JOB_TIMEOUT_SEC` (default 270, target budget from submit), `JOB_HARD_CAP_SEC` (default 280, absolute ceiling from submit), `MAX_UPLOAD_MB` (default 200), `ANALYZER` (`module:Class`), `STUB_DEBUG`, `RULES_PATH`. The real per-job deadline is `min(max(submit + JOB_TIMEOUT_SEC, start + 20 s), submit + JOB_HARD_CAP_SEC)`; the partial result is returned at that deadline. Upload time and queue wait count towards it.
- **Queue:** one FIFO worker shared by API and UI uploads. If N videos are submitted at once, per-video analysis must stay well under 280/N seconds.
- **Health:** `GET /healthz` → `{"ok": true, "analyzer_ready": bool, "analyzer_error": str|null, "public_url": str|null, "public_url_source": "env"|"quick-tunnel"|null, "rules_path": str, "rules_count": int}`. The analyzer loads in the background after startup and its load time counts against the first jobs' budget: expose the tunnel only once `analyzer_ready` is true. `HEAD` works on `/healthz`, `/api/v1/jobs/{id}` and the `/ui-api` GET routes.
- **Plug in an analyzer:**
  - Implement `name` plus `analyze(video_path, progress_cb, deadline, partial) -> AnalysisResult` (`app/analyzers/base.py`), then set `ANALYZER=pkg.mod:Class`.
  - Check `deadline_passed(deadline) or partial.cancelled` in every loop and `partial.add()` each confirmed violation. On overrun the worker returns those violations with `partial: true` and sets `partial.cancelled`; a late thread cannot be killed, and the next job waits for it at most min(10 s, 25% of its remaining budget).
  - An exception after something was added to `partial` gives `done`/`partial: true` with those violations and `meta.analyzer_error`; an exception with nothing found gives `failed`. `AnalysisResult.meta` is stored under job `meta.analyzer` (dropped if > ~8 KB as JSON).
  - The model loads in `__init__` on the worker thread, but each `analyze()` runs in a fresh thread: enter `torch.inference_mode()` / `no_grad()` inside `analyze`.
  - Add pipeline dependencies with `uv add <pkg>` (`run.sh` uses `uv sync --frozen --inexact`).
  - Violation `type` is a rule id (`T1`…`T15`, `T1b`, `T99`); optional `start_sec`, `end_sec`, `fact_conf`, `legal_conf`, `obs`, `title_ru`. In the API `type` is the Russian rule title and the id is in `type_id`; an unknown id passes through with `rule: null, rules: []`.
  - Citations (`rule`, `rules`) come only from the rules file (`RULES_PATH` → `../detector/taxonomy/rules.json` → `../docs/deliverables/rules_spec.json` → bundled `app/rules.json`); analyzer citations are dropped. The service recomputes the verdict.
- **Public URL:** primary is the relay `https://forte.incode.kz` (see the deploy bullet below); fallbacks are Caddy / Tailscale Funnel / quick tunnel. `scripts/tunnel.sh` opens a Cloudflare quick tunnel (100 MB body cap; installs nothing; cloudflared via `scripts/setup.sh --with-cloudflared`, `sudo pacman -S cloudflared` or the release binary).
- **Deploy (Ubuntu 24.04, RTX 5080, driver ≥ 570 open modules):**
  - Docker (primary): install Docker Engine + NVIDIA Container Toolkit (`nvidia-ctk runtime configure --runtime=docker`, restart docker), verify with `docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi`, `sudo systemctl enable docker`, then `cp .env.example .env` (set `API_KEY`), `mkdir -p data models`, `docker compose up -d --build`. Image: `nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04`, uv-managed Python 3.12, non-root uid 1000, healthcheck on `/healthz`; volumes `./data` and `./models` (`MODELS_DIR`/`HF_HOME`/`TORCH_HOME`). In Docker the default rules lookup finds only the bundled `app/rules.json`: mount the detector file and set `RULES_PATH` (override example in the RU section); the analyzer code must be baked into the image or mounted (`PYTHONPATH`), with `ANALYZER` set.
  - Bare metal (fallback): `scripts/setup.sh [--with-cloudflared]` (no sudo; prints apt hints), then systemd units from `deploy/systemd/` (edit `User`/`WorkingDirectory`/paths, `systemctl daemon-reload && systemctl enable --now forte-service forte-tunnel`).
  - Docker publishes the app on `${BIND_ADDR:-127.0.0.1}:${PORT:-8000}` (loopback only, since Docker bypasses ufw; `BIND_ADDR=0.0.0.0` for a LAN demo). After editing `.env` run `docker compose up -d` (`restart` does not re-read it).
  - Public URL, primary: **relay** via the team server. `forte.incode.kz` A → 89.126.193.108; the server's Caddy (`deploy/relay/forte.caddy`, imported drop-in) or nginx (`deploy/relay/forte.incode.kz.nginx` + certbot) terminates TLS (250 MB body, 600 s timeouts, streamed uploads) and proxies to its `127.0.0.1:18000`, which `deploy/systemd/forte-relay.service` (autossh `-R 127.0.0.1:18000:127.0.0.1:8000`, dedicated key restricted with `restrict,port-forwarding,permitlisten="127.0.0.1:18000"`) forwards to the PC. **Required** sshd drop-in `deploy/relay/sshd-forte-relay.conf` (`ClientAliveInterval 15`/`CountMax 3` for that user; without it a dead session holds port 18000 for ~2 h and every reconnect fails → 502); verify with `sudo sshd -T -C user=forte-relay,host=x,addr=1.2.3.4 | grep -i clientalive`. Proxies keep idle upstream connections 60 s, uvicorn runs with `--timeout-keep-alive 75`. Alternative private link: NetBird (upstream `<netbird-ip>:8000`). Set `PUBLIC_HOSTNAME=forte.incode.kz`.
  - Public URL fallbacks (DNS at hoster.kz): **A)** public IP → A record + forward 80/443 + `PUBLIC_HOSTNAME=...` + `docker compose --profile caddy up -d caddy` (`deploy/Caddyfile`, automatic HTTPS, 250 MB body, 10 min timeouts); **B)** CGNAT (`curl -4 ifconfig.me` ≠ router WAN IP, or WAN in 100.64.0.0/10) → Tailscale Funnel (`sudo tailscale funnel --bg 8000`, `PUBLIC_HOSTNAME=<pc>.<tailnet>.ts.net`); **C)** last resort: Cloudflare quick tunnel (`docker compose --profile tunnel up -d` or `forte-tunnel.service`, independent of the app). **A quick tunnel gets a new random URL on every tunnel restart/reboot**; `scripts/public_url.sh` reads it from cloudflared's metrics (`/quicktunnel`, fallback: logs of the current unit invocation / container start only) into `data/public_url.txt`; `forte-tunnel.service` deletes the file on stop. cloudflared image pinned to `2026.10.1`. **D)** named Cloudflare tunnel: dormant. `/healthz` reports `public_url` + `public_url_source` (`PUBLIC_HOSTNAME` → `"env"`, else `PUBLIC_URL_FILE` → `"quick-tunnel"`).
  - torch: `pyproject.toml` maps `torch`/`torchvision` to the explicit `pytorch-cu128` index; `uv add "torch>=2.7" torchvision` gets CUDA 12.8 wheels (required for Blackwell sm_120).
  - Jury day: mask sleep targets; stop automatic upgrades (`systemctl disable --now apt-daily.timer apt-daily-upgrade.timer`, `APT::Periodic::* "0"` in `20auto-upgrades`, `apt-mark hold` the NVIDIA driver, docker-ce, containerd.io, nvidia-container-toolkit; undo steps in the RU section); `forte-relay` active and `curl https://forte.incode.kz/healthz` from mobile data; check `analyzer_ready` + `public_url` in `/healthz`, own `API_KEY`; test analyze + poll with curl through the public URL from outside the LAN (mobile data); do not restart the tunnel after sending the URL.
  - WSL2: Windows NVIDIA driver only; Docker Desktop (WSL2 backend) or docker-ce + toolkit inside WSL.
- **Tests:** `uv run pytest`.
- **Licenses:** all dependencies are permissive (MIT/BSD/Apache-2.0); there is no GPL/AGPL code. cloudflared (Apache-2.0) is optional.
