"""Stub analyzer: exercises the full pipeline without any ML.

Probes the video, decodes it at 1 fps (honouring the deadline, reporting progress) and
returns `no_violation`. With STUB_DEBUG=1 it emits one clearly-labelled demo violation
at the video midpoint with 2 real evidence frames; the first carries one demo
`region` box labelled «Зона кассы (демо)».
"""

from __future__ import annotations

import os
from pathlib import Path

from app.analyzers.base import PartialSink, ProgressCb, deadline_passed
from app.models import AnalysisResult, Box, EvidenceFrame, Violation
from app.video import frame_at, iter_frames, jpeg_b64, probe

SAMPLE_FPS = 1.0
DEMO_BOX_LABEL = "Зона кассы (демо)"


class StubAnalyzer:
    name = "stub"

    def __init__(self, debug: bool | None = None) -> None:
        if debug is None:
            debug = os.environ.get("STUB_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
        self.debug = debug

    def _demo_violation(self, video_path: Path, meta) -> Violation:
        mid = meta.duration_sec / 2.0
        step = min(0.5, meta.duration_sec / 4.0) if meta.duration_sec > 0 else 0.0
        frames = [
            EvidenceFrame(timestamp_sec=round(ts, 3), image_base64=jpeg_b64(frame_at(video_path, ts, meta=meta)))
            for ts in (mid, mid + step)
        ]
        # Demo box on the first frame, so the annotation path is exercised end to end.
        frames[0].boxes = [Box(x=0.25, y=0.3, w=0.5, h=0.4, kind="region", label=DEMO_BOX_LABEL)]
        return Violation(
            type="T99",
            timecode_sec=round(mid, 3),
            end_sec=round(mid + step, 3),
            fact_conf=0.5,
            # The T99 template wins over `description`, so the demo label must also be in obs.
            obs="ДЕМО / ТЕСТ — фиктивное нарушение тестового анализатора (STUB_DEBUG=1), не является реальным результатом анализа",
            description=(
                "ДЕМО / ТЕСТ: фиктивное нарушение, сгенерированное тестовым анализатором "
                "(STUB_DEBUG=1). Не является реальным результатом анализа."
            ),
            evidence_frames=frames,
        )

    def analyze(
        self, video_path: Path, progress_cb: ProgressCb, deadline: float, partial: PartialSink
    ) -> AnalysisResult:
        video_path = Path(video_path)
        progress_cb(0.0, "Чтение метаданных видео")
        meta = probe(video_path)

        violations: list[Violation] = []
        if self.debug:
            v = self._demo_violation(video_path, meta)
            partial.add(v)
            violations.append(v)

        progress_cb(0.05, "Покадровый просмотр видео")
        frames = 0
        stopped_early = False
        duration = meta.duration_sec or 1.0
        last_frac = 0.05
        for t, _frame in iter_frames(video_path, fps=SAMPLE_FPS, max_side=480, meta=meta):
            if deadline_passed(deadline) or partial.cancelled:
                stopped_early = True
                break
            frames += 1
            frac = 0.05 + 0.9 * min(1.0, (t + 1.0 / SAMPLE_FPS) / duration)
            if frac > last_frac:
                last_frac = frac
                progress_cb(frac, f"Анализ кадров: {frames}")
        progress_cb(1.0, "Формирование результата")

        return AnalysisResult(
            verdict="violation" if violations else "no_violation",
            violations=violations,
            meta={
                "analyzer": self.name,
                "frames_decoded": frames,
                "sample_fps": SAMPLE_FPS,
                "stopped_early": stopped_early,
                "debug": self.debug,
                "video": meta.model_dump(),
            },
        )
