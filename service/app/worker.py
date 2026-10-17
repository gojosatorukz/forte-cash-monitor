"""Single background worker: FIFO queue, analyzer run in a sub-thread under a hard deadline.

Budget: deadline = submission time + JOB_TIMEOUT_SEC (queue wait counts, because the jury's
limit is wall clock from submit), but every job gets at least MIN_BUDGET_SEC (capped at the
timeout) so a job that waited long is still analysed. An absolute ceiling, submission + JOB_HARD_CAP_SEC,
always wins; a job with less than MIN_RUN_SEC left before it is finalized without analysis. The analyzer is told a slightly earlier
deadline so it can return cleanly; at the hard deadline the worker stops waiting and
finalizes with `PartialSink.snapshot()` (`partial: true`). At finalization the worker sets
`PartialSink.cancelled`; an orphaned analyzer thread that ignores it may keep running, its
return value and progress calls are ignored, and the next job first waits briefly for it
(at most min(ORPHAN_JOIN_MAX_SEC, ORPHAN_JOIN_FRACTION x that job's remaining budget)).

An analyzer that raises after having `partial.add()`-ed violations still yields a `done`
job (`partial: true`, error text in `meta["analyzer_error"]`); only an exception with
nothing found fails the job. The analyzer's own `AnalysisResult.meta` is stored under job
`meta["analyzer"]` when it is JSON-serializable within ANALYZER_META_MAX_BYTES.

Job `warnings` (RU notes for the operator) = `AnalysisResult.warnings` + the analyzer meta's
`notes_ru`, deduped and capped (MAX_WARNINGS x MAX_WARNING_CHARS). `notes_ru` is read from the
raw meta, so it survives even when the meta itself is dropped for size. A `partial: true` job
also gets the service's own note (WARN_PARTIAL_TIMEOUT, or WARN_PARTIAL_ERROR after a crash),
after the analyzer's ones; it always survives the cap.

Evidence work runs after the analyzer, against its own deadline `clip_deadline()` =
submit + JOB_HARD_CAP_SEC - 2 s (normally later than the analyzer's hard deadline, so it never
takes time from the analyzer; for a late-queued job whose hard deadline is already the cap it
is simply cap - 2 s). The first evidence frame with analyzer boxes is drawn (app.evidence)
while the violations are enriched (raw frames for the rest once within DRAW_MARGIN_SEC of that
deadline). Then the job's clip dir is cleared and one MP4 clip per violation is cut
(app.clips) before that deadline; the job stays `processing` until then. A clip that is
skipped/killed for time or fails adds a warning (before the partial note, so that note still
survives the cap). The clip rotation is the analyzer meta's `applied_rotation_deg`, or 0 when
there is no meta (timeout/crash).
"""

from __future__ import annotations

import json
import logging
import math
import queue
import threading
import time
from pathlib import Path
from typing import Any

from app.analyzers import AnalyzerLoadError, load_analyzer
from app.analyzers.base import PartialSink
from app.analyzers.stub import StubAnalyzer
from app.clips import generate_clips, normalize_rotation
from app.config import Settings
from app.evidence import annotate_frames
from app.jobs import JobStore
from app.models import AnalysisResult, Violation, now_iso
from app.rules import RulesKB
from app.video import probe

log = logging.getLogger(__name__)

MIN_BUDGET_SEC = 20.0
ANALYZER_MARGIN_MAX_SEC = 5.0  # analyzer deadline = hard deadline - min(5 s, 10% of budget)
PROGRESS_PERSIST_SEC = 0.5
ORPHAN_JOIN_MAX_SEC = 10.0
ORPHAN_JOIN_FRACTION = 0.25
ANALYZER_META_MAX_BYTES = 8 * 1024
MAX_WARNINGS = 10
MAX_WARNING_CHARS = 300

STAGE_QUEUED = "В очереди"
STAGE_PREPARE = "Подготовка видео"
STAGE_DONE = "Готово"
STAGE_PARTIAL = "Готово: время анализа истекло, результат частичный"
STAGE_FAILED = "Ошибка"
STAGE_CLIPS = "Нарезка видеофрагментов нарушений"
DRAW_MARGIN_SEC = 1.0  # don't draw evidence boxes this close to the post-analysis deadline
CLIP_CAP_MARGIN_SEC = 2.0  # clip deadline = submit + JOB_HARD_CAP_SEC - this


def clip_deadline(submitted: float, hard_cap: float) -> float:
    """Monotonic deadline for post-analysis evidence work (box drawing, clips): just under the
    absolute cap, independent of (usually later than) the analyzer's hard deadline."""
    return submitted + hard_cap - CLIP_CAP_MARGIN_SEC


def clip_url(job_id: str, n: int, prefix: str = "/api/v1") -> str:
    return f"{prefix}/jobs/{job_id}/clips/{n}.mp4"


MIN_RUN_SEC = 1.0  # less than this left before the hard cap -> don't start the analyzer
NOTE_BUDGET_EXHAUSTED = "queue wait exhausted time budget"
WARN_PARTIAL_TIMEOUT = "анализ прерван по времени, результат может быть неполным"
WARN_PARTIAL_ERROR = "анализ прерван из-за ошибки, результат может быть неполным"


def compute_deadline(submitted: float, now: float, timeout: float, min_budget: float = MIN_BUDGET_SEC,
                     hard_cap: float | None = None) -> float:
    """Monotonic hard deadline for a job submitted at `submitted` and started at `now`:
    timeout from submission, at least min(min_budget, timeout) of run time, but never later
    than submitted + hard_cap (absolute wall-clock ceiling from submit)."""
    deadline = max(submitted + timeout, now + min(min_budget, timeout))
    if hard_cap is not None:
        deadline = min(deadline, submitted + hard_cap)
    return deadline


def _finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def json_safe(obj: Any) -> Any:
    """Replace non-finite floats (not representable in JSON) with None, recursively."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    return obj


def analyzer_meta(meta: Any, job_id: str = "?") -> dict[str, Any] | None:
    """JSON-safe copy of the analyzer's meta dict, or None if empty/unserializable/too big."""
    if not isinstance(meta, dict) or not meta:
        return None
    try:
        text = json.dumps(json_safe(meta), default=str, allow_nan=False, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        log.warning("Job %s: analyzer meta is not JSON-serializable, dropped: %s", job_id, e)
        return None
    if len(text.encode("utf-8")) > ANALYZER_META_MAX_BYTES:
        log.warning("Job %s: analyzer meta is %d bytes (> %d), dropped", job_id, len(text.encode("utf-8")),
                    ANALYZER_META_MAX_BYTES)
        return None
    out = json.loads(text)
    return out if isinstance(out, dict) else None


def collect_warnings(*sources: Any) -> list[str]:
    """Merge warning sources (each a string or a list/tuple of anything) into a deduped, ordered,
    capped list of strings. Other source types (None, dicts, ...) are ignored."""
    out: list[str] = []
    for src in sources:
        if isinstance(src, str):
            src = [src]
        if not isinstance(src, (list, tuple)):
            continue
        for item in src:
            if item is None:
                continue
            try:
                text = " ".join(str(item).split())
            except Exception:  # noqa: BLE001 - a weird object must not break the job
                continue
            if len(text) > MAX_WARNING_CHARS:
                text = text[:MAX_WARNING_CHARS - 1].rstrip() + "…"
            if text and text not in out:
                out.append(text)
                if len(out) >= MAX_WARNINGS:
                    return out
    return out


def add_service_warning(warnings: list[str], note: str) -> list[str]:
    """`warnings` + `note` at the end (deduped); the note replaces the last item if the list is full."""
    if note in warnings:
        return list(warnings)
    return list(warnings[:MAX_WARNINGS - 1]) + [note]


class _Run:
    """Per-job state shared with the analyzer thread's callbacks."""

    def __init__(self, job_id: str):
        self.job_id = job_id
        self.lock = threading.Lock()
        self.finalized = False
        self.progress = 0.0
        self.last_persist = 0.0


class Worker:
    def __init__(self, store: JobStore, rules: RulesKB, settings: Settings, analyzer: Any = None):
        self.store = store
        self.rules = rules
        self.settings = settings
        self.analyzer = analyzer
        self.load_error: str | None = None
        self._queue: queue.Queue[tuple[str, float] | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._orphans: list[threading.Thread] = []
        self.ready = threading.Event()

    # ---------- lifecycle ----------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="job-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._queue.put(None)
        if self._thread is not None:
            self._thread.join(timeout)

    def submit(self, job_id: str, submitted: float | None = None) -> None:
        self._queue.put((job_id, time.monotonic() if submitted is None else submitted))

    def _load_analyzer(self) -> None:
        if self.analyzer is None:
            try:
                self.analyzer = load_analyzer(self.settings.analyzer)
            except AnalyzerLoadError as e:
                self.load_error = str(e)
                log.error("Analyzer failed to load: %s", e)
                return
            except Exception as e:  # noqa: BLE001
                self.load_error = f"{type(e).__name__}: {e}"
                log.exception("Analyzer failed to load")
                return
        if isinstance(self.analyzer, StubAnalyzer):
            self.analyzer.debug = bool(self.settings.stub_debug)
        log.info("Analyzer ready: %s", getattr(self.analyzer, "name", type(self.analyzer).__name__))

    def _loop(self) -> None:
        self._load_analyzer()
        self.ready.set()
        while True:
            item = self._queue.get()
            if item is None:
                return
            job_id, submitted = item
            try:
                self._process(job_id, submitted)
            except Exception as e:  # noqa: BLE001 - the worker must survive anything
                log.exception("Unexpected worker error on job %s", job_id)
                self._fail(job_id, f"Внутренняя ошибка сервиса: {type(e).__name__}: {e}")

    # ---------- job processing ----------

    def _fail(self, job_id: str, message: str, warnings: list[str] | None = None) -> None:
        self.store.update(job_id, status="failed", error=message, stage=STAGE_FAILED,
                          verdict=None, violations=[], finished_at=now_iso(), warnings=list(warnings or []))

    def _process(self, job_id: str, submitted: float) -> None:
        job = self.store.get(job_id)
        if job is None or job.status != "processing":
            return
        start = time.monotonic()
        hard_deadline = compute_deadline(submitted, start, self.settings.job_timeout_sec,
                                         hard_cap=self.settings.job_hard_cap_sec)
        if hard_deadline - start < MIN_RUN_SEC:
            log.warning("Job %s waited %.1f s in the queue; less than %.1f s left before the %.0f s cap, "
                        "finalizing without analysis", job_id, start - submitted, MIN_RUN_SEC,
                        self.settings.job_hard_cap_sec)
            self.store.update(job_id, status="done", violations=[], verdict="no_violation", progress=1.0,
                              stage=STAGE_PARTIAL, partial=True, error=None, finished_at=now_iso(),
                              meta={"note": NOTE_BUDGET_EXHAUSTED}, warnings=[WARN_PARTIAL_TIMEOUT])
            return

        self._orphans = [t for t in self._orphans if t.is_alive()]
        if self._orphans:
            # Already signalled via PartialSink.cancel(); give them a short chance to exit.
            wait = min(ORPHAN_JOIN_MAX_SEC, ORPHAN_JOIN_FRACTION * max(0.0, hard_deadline - time.monotonic()))
            join_end = time.monotonic() + wait
            for t in self._orphans:
                t.join(max(0.0, join_end - time.monotonic()))
            self._orphans = [t for t in self._orphans if t.is_alive()]
            if self._orphans:
                log.warning("%d cancelled analyzer thread(s) still running after %.1f s; job %s may compete "
                            "for CPU/GPU", len(self._orphans), wait, job_id)

        if self.analyzer is None:
            self._fail(job_id, f"Анализатор недоступен: {self.load_error or 'not loaded'}")
            return
        path = Path(job.upload_path or "")
        if not job.upload_path or not path.is_file():
            self._fail(job_id, "Файл видео не найден")
            return

        self.store.update(job_id, stage=STAGE_PREPARE, progress=0.0)
        try:
            meta = probe(path, timeout=max(1.0, min(60.0, hard_deadline - time.monotonic())))
        except Exception as e:  # noqa: BLE001
            self._fail(job_id, f"Не удалось прочитать видео (файл повреждён или не является видео): {e}")
            return
        self.store.update(job_id, video_meta=json_safe(meta.model_dump()))

        run = _Run(job_id)
        partial = PartialSink()
        box: dict[str, Any] = {}
        budget = max(0.0, hard_deadline - time.monotonic())
        analyzer_deadline = hard_deadline - min(ANALYZER_MARGIN_MAX_SEC, 0.1 * budget)

        def progress_cb(fraction: float, stage: str = "") -> None:
            try:
                frac = float(fraction)
                frac = 0.0 if not math.isfinite(frac) else min(1.0, max(0.0, frac))
                with run.lock:
                    if run.finalized:
                        return
                    run.progress = max(run.progress, frac)
                    now = time.monotonic()
                    persist = now - run.last_persist >= PROGRESS_PERSIST_SEC
                    if persist:
                        run.last_persist = now
                    # progress < 1.0 while running: 1.0 means "finished"
                    self.store.update(job_id, persist=persist, progress=min(run.progress, 0.99),
                                      stage=str(stage)[:200] if stage else STAGE_PREPARE)
            except Exception:  # noqa: BLE001 - a progress hiccup must never break analysis
                log.debug("progress_cb failed", exc_info=True)

        analyzer = self.analyzer

        def target() -> None:
            try:
                box["result"] = analyzer.analyze(path, progress_cb, analyzer_deadline, partial)
            except BaseException as e:  # noqa: BLE001
                box["error"] = e

        thread = threading.Thread(target=target, name=f"analyzer-{job_id[:8]}", daemon=True)
        thread.start()
        thread.join(max(0.0, hard_deadline - time.monotonic()))

        with run.lock:
            run.finalized = True  # from here on, late progress calls are ignored
        partial.cancel()  # tell a still-running analyzer to stop

        meta_out: dict[str, Any] = {}
        job_meta: dict[str, Any] = {}
        warnings: list[str] = []
        partial_note = WARN_PARTIAL_TIMEOUT  # overrun / stopped_early; a crash overrides it below
        if thread.is_alive():
            log.warning("Job %s: analyzer overran the %.1f s budget; finalizing with %d partial violation(s). "
                        "The analyzer thread keeps running in the background and its result will be ignored.",
                        job_id, hard_deadline - start, len(partial))
            self._orphans.append(thread)
            items: list[Any] = partial.snapshot()
            is_partial = True
        elif "error" in box:
            e = box["error"]
            err_text = f"{type(e).__name__}: {e}"
            items = partial.snapshot()
            log.error("Job %s: analyzer raised %s (%d partial violation(s))", job_id, err_text, len(items))
            if not items:
                self._fail(job_id, f"Ошибка анализатора: {err_text}")
                return
            job_meta["analyzer_error"] = err_text[:2000]
            is_partial = True
            partial_note = WARN_PARTIAL_ERROR
        else:
            items, meta_out, result_warnings = self._extract(box.get("result"), partial)
            is_partial = bool(meta_out.get("stopped_early") or meta_out.get("partial"))
            # Before the meta size check below: an oversized meta is dropped, its notes are not.
            warnings = collect_warnings(result_warnings, meta_out.get("notes_ru"))
            am = analyzer_meta(meta_out, job_id)
            if am is not None:
                job_meta["analyzer"] = am

        # Post-analysis evidence work (drawing, clips) has its own deadline just under the
        # absolute cap: it never takes time from the analyzer and never pushes the job past
        # submit + JOB_HARD_CAP_SEC.
        post_deadline = clip_deadline(submitted, self.settings.job_hard_cap_sec)
        violations = self._enrich_all(job_id, items, post_deadline)
        if "analyzer_error" in job_meta and not violations:
            self._fail(job_id, f"Ошибка анализатора: {job_meta['analyzer_error']}", warnings)
            return
        for note in self._clips(job_id, path, violations, post_deadline, meta.duration_sec,
                                normalize_rotation(meta_out.get("applied_rotation_deg"))):
            warnings = add_service_warning(warnings, note)
        if is_partial:
            warnings = add_service_warning(warnings, partial_note)
        self.store.update(
            job_id,
            status="done",
            violations=violations,
            verdict="violation" if violations else "no_violation",
            progress=1.0,
            stage=STAGE_PARTIAL if is_partial else STAGE_DONE,
            partial=is_partial,
            error=None,
            finished_at=now_iso(),
            meta=job_meta,
            warnings=warnings,
        )
        log.info("Job %s done in %.1f s: %d violation(s)%s", job_id, time.monotonic() - start,
                 len(violations), " (partial)" if is_partial else "")

    def _clips(self, job_id: str, path: Path, violations: list[dict[str, Any]], deadline: float,
               duration_sec: float | None, rotation_deg: int) -> list[str]:
        """Clear the job's clip dir, cut evidence clips before `deadline` and set `clip_url` on
        the violations that got one. Returns RU warnings ([] without violations)."""
        try:
            if violations:
                self.store.update(job_id, stage=STAGE_CLIPS, progress=0.99)
            made, notes = generate_clips(path, violations, self.settings.clips_dir / job_id, deadline,
                                         duration_sec=duration_sec, rotation_deg=rotation_deg, job_id=job_id)
            for n in made:
                violations[n - 1]["clip_url"] = clip_url(job_id, n)
            return notes if violations else []
        except Exception as e:  # noqa: BLE001 - clips never fail the job
            log.warning("Job %s: evidence clips failed: %s", job_id, e)
            return []

    @staticmethod
    def _extract(result: Any, partial: PartialSink) -> tuple[list[Any], dict[str, Any], Any]:
        """(violations, meta, raw warnings) from whatever the analyzer returned."""
        warnings: Any = None
        if result is None:
            log.warning("Analyzer returned None; using its partial results")
            return partial.snapshot(), {}, None
        if isinstance(result, AnalysisResult):
            # getattr: model_construct() may skip the field
            items, meta, warnings = result.violations, result.meta, getattr(result, "warnings", None)
        elif isinstance(result, dict):
            items, meta, warnings = result.get("violations"), result.get("meta"), result.get("warnings")
        elif isinstance(result, (list, tuple)):
            items, meta = result, {}
        else:
            log.warning("Analyzer returned unsupported %s; using its partial results", type(result).__name__)
            return partial.snapshot(), {}, None
        if not isinstance(items, (list, tuple)):
            items = []
        return list(items), meta if isinstance(meta, dict) else {}, warnings

    def _enrich_all(self, job_id: str, items: list[Any], deadline: float | None = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        draw_logged = False
        for i, item in enumerate(items):
            try:
                v = item if isinstance(item, Violation) else Violation.model_validate(item)
                if not _finite(v.timecode_sec):
                    raise ValueError(f"non-finite timecode_sec {v.timecode_sec!r}")
                frames = []
                for fr in v.evidence_frames:
                    b64 = fr.image_base64.strip()
                    if b64.startswith("data:"):
                        b64 = b64.partition(",")[2]
                    if b64 and _finite(fr.timestamp_sec):
                        frames.append(fr.model_copy(update={"image_base64": b64}))
                v = v.model_copy(update={
                    "evidence_frames": frames,
                    "start_sec": v.start_sec if _finite(v.start_sec) else None,
                    "end_sec": v.end_sec if _finite(v.end_sec) else None,
                })
                d = json_safe(self.rules.enrich(v))
                draw = deadline is None or time.monotonic() < deadline - DRAW_MARGIN_SEC
                if not draw and not draw_logged:
                    draw_logged = True
                    log.warning("Job %s: too close to the deadline, evidence boxes not drawn for the remaining violations", job_id)
                d["evidence_frames"] = annotate_frames(d["evidence_frames"], job_id, draw=draw)
                out.append(d)
            except Exception as e:  # noqa: BLE001 - drop one bad violation, keep the job
                log.warning("Job %s: dropping malformed violation #%d from analyzer: %s", job_id, i, e)
        out.sort(key=lambda d: d["timecode_sec"])
        return out
