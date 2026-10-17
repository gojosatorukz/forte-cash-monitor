"""Standalone, escaped HTML evidence report; no scripts or external resources."""

from datetime import UTC, datetime
from html import escape

from backend.app.schemas import AnalysisResult


def timecode(seconds: float) -> str:
    minutes, seconds = divmod(seconds, 60)
    return f"{int(minutes):02d}:{seconds:04.1f}"


def render_report(job: dict, result: AnalysisResult) -> str:
    title = (
        "Найдены признаки нарушения" if result.verdict == "violation" else "Нарушение не обнаружено"
    )
    events = []
    for index, event in enumerate(sorted(result.violations, key=lambda v: v.timecode_sec), 1):
        frames = "".join(
            f'<figure><img src="data:image/jpeg;base64,{frame.image_base64}" '
            f'alt="Кадр {timecode(frame.timestamp_sec)}"><figcaption>'
            f"{timecode(frame.timestamp_sec)}</figcaption></figure>"
            for frame in event.evidence_frames
        )
        events.append(
            f"<section><h2>{index:02d} · {timecode(event.timecode_sec)} · "
            f'{escape(event.type)}</h2><p class="description">{escape(event.description)}</p>'
            f'<div class="frames">{frames}</div></section>'
        )
    created = datetime.fromtimestamp(job["created_at"], UTC).isoformat(timespec="seconds")
    return f"""<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Отчёт · {escape(job["filename"])}</title><style>
*{{box-sizing:border-box}}body{{font:15px/1.6 -apple-system,BlinkMacSystemFont,
"Segoe UI",sans-serif;
color:#202321;max-width:1000px;margin:40px auto;padding:0 24px}}header{{border-bottom:2px solid
#ef695a;padding-bottom:24px}}small{{color:#69716b}}h1{{font-size:30px;line-height:1.2}}
section{{border-top:1px solid #ddd;padding:24px 0;break-inside:avoid}}h2{{font-size:18px}}
.description{{white-space:pre-wrap;overflow-wrap:anywhere}}.frames{{display:flex;gap:14px}}
figure{{margin:0;flex:1;min-width:0}}img{{width:100%;max-height:410px;object-fit:contain;
background:#f2f3ef}}figcaption{{font-size:12px;text-align:center}}footer{{font-size:12px;
color:#69716b;border-top:1px solid #ddd;padding-top:18px}}@media print{{body{{margin:0}}
section{{page-break-inside:avoid}}}}@media(max-width:600px){{.frames{{flex-wrap:wrap}}
figure{{flex-basis:100%}}}}</style><body>
<header><small>FORTE CASH MONITOR · DATATHON 26</small><h1>{title}</h1>
<p>{escape(job["filename"])} · событий: {len(result.violations)}</p>
<small>Задача: {escape(job["id"])}<br>Загружено (UTC): {created}</small></header>
{"".join(events) or "<p>В обработанном видео система не обнаружила поддерживаемые признаки.</p>"}
<footer>Автоматическая подсказка для проверки человеком. Вывод относится к наблюдаемым
действиям в этой записи и не устанавливает юридическую ответственность или наказание.
Кадры и описания содержат данные проверки; используйте отчёт только в рамках хакатона.
Для PDF откройте этот файл и выберите «Печать → Сохранить как PDF».</footer></body></html>"""
