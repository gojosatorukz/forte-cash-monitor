# Модели: кандидаты, а не готовый результат

| Режим | Начальный кандидат | Runtime |
| --- | --- | --- |
| Mac 16 ГБ | Qwen3-VL-4B-Instruct 4-bit | MLX-VLM, нативный Metal |
| Mac 8 ГБ / резерв | Qwen3-VL-2B-Instruct 4-bit | MLX-VLM |
| RTX 5080 | Qwen3-VL-8B-Instruct 4-bit; сравнить с 4B | PyTorch CUDA + Transformers |
| Быстрый детектор | YOLOX-Nano/Tiny | ONNX Runtime / PyTorch |
| Треки без лиц | ByteTrack | Локальный CPU |

Скорость и качество ещё не измерены. Дисковый размер весов не равен памяти runtime.
Проверять короткую запись, самую длинную, новый ракурс и параллельные запросы.
Веса сейчас не скачаны и в репозиторий не включаются.

Для выбранной модели заполнить ID, revision, лицензию, SHA-256 локальных файлов,
версии runtime, используемое разрешение/число кадров, peak RAM/VRAM, время и ошибки.
Не обучать на тестовых записях жюри и не отправлять видео/кадры/описания внешним AI.

Первоисточники:

- https://huggingface.co/mlx-community/Qwen3-VL-4B-Instruct-4bit
- https://huggingface.co/mlx-community/Qwen3-VL-2B-Instruct-4bit
- https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct
- https://github.com/Blaizzy/mlx-vlm
- https://github.com/Megvii-BaseDetection/YOLOX
- https://github.com/FoundationVision/ByteTrack

Лицензии конкретных весов и зависимостей проверяются при выборе; лицензия одной
библиотеки не распространяется автоматически на остальные компоненты.
