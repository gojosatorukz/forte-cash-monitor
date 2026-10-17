"""Standalone, escaped HTML evidence report; no scripts or external resources."""

import base64
from datetime import UTC, datetime
from functools import lru_cache
from html import escape
from pathlib import Path

from backend.app.schemas import AnalysisResult


@lru_cache(maxsize=1)
def report_brand_assets() -> tuple[str, str, str]:
    """Embed local assets so downloaded reports work without a network or server."""
    static = Path(__file__).resolve().parents[2] / "frontend/static"
    font = base64.b64encode((static / "fonts/OpenSans.ttf").read_bytes()).decode("ascii")
    logo = base64.b64encode((static / "brand/forte-magenta.svg").read_bytes()).decode("ascii")
    license_text = (static / "fonts/OFL.txt").read_text().replace("\\r\\n", "\n")
    return font, logo, license_text


def timecode(seconds: float) -> str:
    minutes, seconds = divmod(seconds, 60)
    return f"{int(minutes):02d}:{seconds:04.1f}"


def render_report(job: dict, result: AnalysisResult) -> str:
    font, logo, font_license = report_brand_assets()
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
@font-face{{font-family:"Open Sans";src:url(data:font/ttf;base64,{font}) format("truetype");
font-weight:300 800;font-display:swap}}
*{{box-sizing:border-box}}body{{font:15px/1.6 "Open Sans",-apple-system,BlinkMacSystemFont,
"Segoe UI",sans-serif;
color:#302631;max-width:1000px;margin:40px auto;padding:0 24px}}header{{border-bottom:2px solid
#a31551;padding-bottom:24px}}small{{color:#827583}}h1{{font-size:30px;line-height:1.2;
font-weight:600;color:#4a0221}}
.brand-row{{display:flex;align-items:center;gap:24px;margin-bottom:24px}}
.brand-row img{{width:140px;height:auto;max-height:none;background:transparent}}
.brand-row span{{font-size:14px;color:#a31551}}
section{{border-top:1px solid #eadfea;padding:24px 0;break-inside:avoid}}h2{{font-size:18px}}
.description{{white-space:pre-wrap;overflow-wrap:anywhere}}.frames{{display:flex;gap:14px}}
figure{{margin:0;flex:1;min-width:0}}img{{width:100%;max-height:410px;object-fit:contain;
background:#f6f2f8}}figcaption{{font-size:12px;text-align:center}}footer{{font-size:12px;
color:#827583;border-top:1px solid #eadfea;padding-top:18px}}@media print{{body{{margin:0}}
section{{page-break-inside:avoid}}}}@media(max-width:600px){{.frames{{flex-wrap:wrap}}
figure{{flex-basis:100%}}}}</style><body>
<header><div class="brand-row"><img src="data:image/svg+xml;base64,{logo}" alt="Forte">
<span>Cash Monitor</span></div><small>DATATHON 26 · КОМАНДНЫЙ ПРОТОТИП</small><h1>{title}</h1>
<p>{escape(job["filename"])} · событий: {len(result.violations)}</p>
<small>Задача: {escape(job["id"])}<br>Загружено (UTC): {created}</small></header>
{"".join(events) or "<p>В обработанном видео система не обнаружила поддерживаемые признаки.</p>"}
<footer>Автоматическая подсказка для проверки человеком. Вывод относится к наблюдаемым
действиям в этой записи и не устанавливает юридическую ответственность или наказание.
Кадры и описания содержат данные проверки; используйте отчёт только в рамках хакатона.
Для PDF откройте этот файл и выберите «Печать → Сохранить как PDF».</footer>
<template id="font-license">{escape(font_license)}</template></body></html>"""
