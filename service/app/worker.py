"""Background job workers: JOB_WORKERS threads (default 1) share one FIFO queue; each owns its
own analyzer instance and runs it in a sub-thread under a hard deadline (see `Worker`).

Budget: deadline = submission time + JOB_TIMEOUT_SEC (queue wait counts, because the jury's
limit is wall clock from submit), but every job gets at least MIN_BUDGET_SEC (capped at the
timeout) so a job that waited long is still analysed. An absolute ceiling, submission + JOB_HARD_CAP_SEC,
always wins; a job with less than MIN_RUN_SEC left before it is finalized without analysis. The analyzer is told a slightly earlier
deadline so it can return cleanly; at the hard deadline the worker stops waiting and
finalizes with `PartialSink.snapshot()` (`partial: true`). At finalization the worker sets
`PartialSink.cancelled`; an orphaned analyzer thread that ignores it may keep running, its
return value and progress calls are ignored, and that worker's next job first waits briefly for it
(at most min(ORPHAN_JOIN_MAX_SEC, ORPHAN_JOIN_FRACTION x that job's remaining budget)).

An analyzer that raises after having `partial.add()`-ed violations still yields a `done`
job (`partial: true`, error text in `meta["analyzer_error"]`); only an exception with
nothing found fails the job. The analyzer's own `AnalysisResult.meta` is stored under job
`meta["analyzer"]` when it is JSON-serializable within ANALYZER_META_MAX_BYTES; its
`procedure` (operation steps) is validated and promoted to job `meta["procedure"]` (own cap).

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

Load watchdog: if no slot has its analyzer ready ANALYZER_LOAD_TIMEOUT_SEC after `start()` (a
hung model load never returns, and its thread cannot be killed), a watchdog thread fails queued
jobs fast with «Анализатор недоступен: загрузка модели не завершилась за N с» until some slot
becomes ready; then the ready slots serve the queue as usual.
"""

from __future__ import annotations

import json
import logging
import math
import queue
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from app.analyzers import AnalyzerLoadError, load_analyzer
from app.analyzers.base import PartialSink
from app.analyzers.stub import StubAnalyzer
from app.clips import generate_clips, max_parallel, normalize_rotation
from app.config import DEFAULT_ANALYZER_LOAD_TIMEOUT_SEC, MAX_JOB_WORKERS, Settings, clamp_int
from app.evidence import annotate_frames
from app.jobs import JobStore
from app.models import AnalysisResult, Violation, now_iso
from app.review import CHECKLIST
from app.rules import RulesKB
from app.video import probe

log = logging.getLogger(__name__)

MIN_BUDGET_SEC = 20.0
ANALYZER_MARGIN_MAX_SEC = 5.0  # analyzer deadline = hard deadline - min(5 s, 10% of budget)
PROGRESS_PERSIST_SEC = 0.5
ORPHAN_JOIN_MAX_SEC = 10.0
ORPHAN_JOIN_FRACTION = 0.25
ANALYZER_META_MAX_BYTES = 8 * 1024
PROCEDURE_MAX_BYTES = 32 * 1024
PROCEDURE_MAX_STEPS = 30
PROCEDURE_STATUSES = ("ok", "violation", "risk", "not_checked", "manual")
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


def _short(x: Any, n: int) -> str | None:
    """Whitespace-normalized string capped at n chars, or None for non-strings / empty."""
    if not isinstance(x, str):
        return None
    text = " ".join(x.split())
    if len(text) > n:
        text = text[:n - 1].rstrip() + "…"
    return text or None


# forte-detector `manual` step id -> reviewer checklist item ids (app/checklist.json). The detector
# is frozen; its only manual step, "documents", covers two checklist items. A manual step whose id
# is itself a checklist id maps to that item.
CHECKLIST_STEP_ALIASES: dict[str, list[str]] = {"documents": ["cash_doc_signed", "client_got_receipt"]}
_DOC_SHORT_RE = re.compile(r"(НБРК|АРРФР|АФН|РК)\s*№\s*\d+")


def doc_short(document: str) -> str:
    """«Постановление Правления НБРК № 231 от …» -> «НБРК № 231» (the full title if no match)."""
    m = _DOC_SHORT_RE.search(document or "")
    return m.group(0) if m else (document or "")


def derive_step_rule(step_id: str | None, status: str, type_ids: list[str], rules: Any,
                     checklist: list[dict[str, Any]] | None
                     ) -> tuple[str | None, str | None, str | None, list[str]]:
    """(rule, rule_url, rule_source, checklist_ids) for a procedure step, from verified sources only:
    the first rules-KB citation of the first type_id found in the KB; else, for a `manual`
    step, the basis of the matching reviewer checklist item; else nothing. The detector's own
    free-text `rule` is never used for display."""
    kb = getattr(rules, "rules", None) or {}
    for tid in type_ids:
        entry = kb.get(tid)
        cits = list(getattr(entry, "citations", None) or []) if entry is not None else []
        if cits:
            c = cits[0]
            text = ", ".join(x for x in (doc_short(c.document), c.point) if x)
            return text or None, (c.url or None), "rules", []
    if status == "manual" and step_id and checklist:
        by_id = {i.get("id"): i for i in checklist}
        cids = [step_id] if step_id in by_id else CHECKLIST_STEP_ALIASES.get(step_id, [])
        items = [by_id[c] for c in cids if c in by_id]
        if items:
            # «НБРК № 231, п. 19 пп. 5 / п. 31 пп. 5; п. 19 пп. 6 / п. 31 пп. 6»: the items' own bases, verbatim.
            doc = items[0].get("document_short") or items[0].get("document") or ""
            bases = "; ".join(str(i.get("basis")) for i in items if i.get("basis"))
            text = ", ".join(x for x in (doc, bases) if x)
            return text or None, (items[0].get("url") or None), "checklist", [str(i["id"]) for i in items]
    return None, None, None, []


def procedure_meta(proc: Any, job_id: str = "?", rules: Any = None,
                   checklist: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """Validated copy of the analyzer's `meta.procedure` (operation steps), or None.

    Shape: {type, title_ru, confidence, basis_ru, steps: [{id, title_ru, status, t_sec,
    type_ids, rule, rule_url, rule_source, note_ru}]}. Unknown statuses become
    `not_checked`, strings are capped, `t_sec` is a finite non-negative number or None, at most
    PROCEDURE_MAX_STEPS steps, `type_ids` a list of strings. `rule` is DERIVED from the rules KB /
    checklist (see derive_step_rule); the detector's own free-text `rule` is DROPPED (never
    stored, so no unverified citation can reach any API response). Dropped (None) without steps or above PROCEDURE_MAX_BYTES."""
    if not isinstance(proc, dict):
        return None
    steps_in = proc.get("steps")
    if not isinstance(steps_in, (list, tuple)):
        return None
    steps: list[dict[str, Any]] = []
    for i, st in enumerate(steps_in):
        if len(steps) >= PROCEDURE_MAX_STEPS:
            break
        if not isinstance(st, dict):
            continue
        title = _short(st.get("title_ru"), 200)
        if not title:
            continue
        status = st.get("status")
        t = st.get("t_sec")
        ids_in = st.get("type_ids")
        ids = []
        if isinstance(ids_in, (list, tuple)):
            ids = [s for s in (_short(x, 40) for x in ids_in[:10]) if s]
        sid = st.get("id")
        step_id = _short(sid, 60) if isinstance(sid, str) else (str(sid) if _finite(sid) else str(i + 1))
        status = status if status in PROCEDURE_STATUSES else "not_checked"
        rule, rule_url, rule_source, checklist_ids = derive_step_rule(step_id, status, ids, rules, checklist)
        steps.append({
            "id": step_id,
            "title_ru": title,
            "status": status,
            "t_sec": float(t) if _finite(t) and t >= 0 else None,
            "type_ids": ids,
            "rule": rule,
            "rule_url": rule_url,
            "rule_source": rule_source,
            "checklist_ids": checklist_ids,
            "note_ru": _short(st.get("note_ru"), 500),
        })
    if not steps:
        return None
    conf = proc.get("confidence")
    out = {
        "type": _short(proc.get("type"), 60),
        "title_ru": _short(proc.get("title_ru"), 200),
        "confidence": float(conf) if _finite(conf) and 0 <= conf <= 1 else None,
        "basis_ru": _short(proc.get("basis_ru"), 500),
        "steps": steps,
    }
    size = len(json.dumps(out, ensure_ascii=False).encode("utf-8"))
    if size > PROCEDURE_MAX_BYTES:
        log.warning("Job %s: procedure meta is %d bytes (> %d), dropped", job_id, size, PROCEDURE_MAX_BYTES)
        return None
    return out


_UNIX_PATH_RE = re.compile(r"""(?<![\w:/.~-])(?:~)?/(?:[^\s'"<>/]+/)+[^\s'"<>/]*""")
_WIN_PATH_RE = re.compile(r"""(?<![\w])[A-Za-z]:[\\/](?:[^\s'"<>\\/]+[\\/])*[^\s'"<>\\/]*""")


def _basename(path: str) -> str:
    parts = [p for p in re.split(r"[\\/]", path) if p and not p.endswith(":")]
    return parts[-1] if parts else ""


def sanitize_paths(text: Any) -> Any:
    """Absolute server paths (POSIX or Windows) in an error/warning string -> their basename,
    so public job records and /healthz never leak the filesystem layout. Non-str passes through."""
    if not isinstance(text, str):
        return text
    text = _WIN_PATH_RE.sub(lambda m: _basename(m.group(0)), text)
    return _UNIX_PATH_RE.sub(lambda m: _basename(m.group(0)), text)


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
                text = " ".join(sanitize_paths(str(item)).split())
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


class _Slot:
    """One job-worker thread and everything it owns: its analyzer instance (never shared with
    another slot), its load result and its cancelled-but-still-running analyzer threads."""

    def __init__(self, index: int):
        self.index = index
        self.name = f"job-worker-{index}"
        self.thread: threading.Thread | None = None
        self.analyzer: Any = None
        self.load_error: str | None = None
        self.load_done = threading.Event()  # load attempted (success or failure)
        self.ready = False  # analyzer loaded; this slot consumes jobs
        self.busy = False  # processing a job right now
        self.orphans: list[threading.Thread] = []  # touched only by this slot's own thread


class Worker:
    """Pool of JOB_WORKERS job threads sharing one FIFO queue (dequeue order = submit order;
    completion order may differ).

    Each slot loads its own analyzer in its own thread, strictly one after another (slot k
    starts loading once slot k-1 has finished or failed: no VRAM spike). A slot whose analyzer
    failed to load never takes jobs. If every slot failed, the last one to finish loading
    drains the queue and `_process` fails each job fast with «Анализатор недоступен». If no slot
    is ready ANALYZER_LOAD_TIMEOUT_SEC after start (a hung load), the load watchdog fails queued
    jobs fast until one is. On shutdown, a slot that did not get a ready analyzer never drains
    the queue: queued jobs stay `processing` and are re-queued at the next start.

    `analyzer` (tests): a ready instance. It must not be shared between threads, so it forces
    a single worker. `analyzer_factory` (tests): called once per slot, in that slot's thread.
    Otherwise each slot calls `load_analyzer(settings.analyzer)`.
    """

    def __init__(self, store: JobStore, rules: RulesKB, settings: Settings, analyzer: Any = None,
                 analyzer_factory: Callable[[], Any] | None = None):
        self.store = store
        self.rules = rules
        self.settings = settings
        self._analyzer_instance = analyzer
        self._analyzer_factory = analyzer_factory
        n = clamp_int(getattr(settings, "job_workers", 1) or 1, 1, MAX_JOB_WORKERS)
        if analyzer is not None and n > 1:
            log.warning("JOB_WORKERS=%d but a ready analyzer instance was given; it cannot be shared "
                        "between threads, using 1 worker", n)
            n = 1
        self.n_workers = n
        self._queue: queue.Queue[tuple[str, float] | None] = queue.Queue()
        self._slots = [_Slot(i) for i in range(n)]
        self._lock = threading.Lock()  # guards slot analyzer/ready/busy/load_error (read by /healthz)
        self._stopping = threading.Event()
        timeout = getattr(settings, "analyzer_load_timeout_sec", DEFAULT_ANALYZER_LOAD_TIMEOUT_SEC)
        self._load_timeout = float(timeout) if _finite(timeout) and timeout > 0 else DEFAULT_ANALYZER_LOAD_TIMEOUT_SEC
        self._load_timed_out = False  # guarded by _lock; set by the load watchdog
        self._watchdog: threading.Thread | None = None
        # Set once at least one analyzer is ready, every slot has tried to load, or loading timed out.
        self.ready = threading.Event()

    # ---------- status ----------

    @property
    def analyzer(self) -> Any:
        """The first ready slot's analyzer, or None. Status only: a job always uses its own
        slot's instance."""
        with self._lock:
            return next((s.analyzer for s in self._slots if s.ready), None)

    @property
    def load_error(self) -> str | None:
        """The first analyzer load error (slots load in index order); else the load-timeout text
        while loading has timed out and no slot is ready; else None."""
        with self._lock:
            err = sanitize_paths(next((s.load_error for s in self._slots if s.load_error), None))
            if err is None and self._load_timed_out and not any(s.ready for s in self._slots):
                err = self._timeout_text()
            return err

    def _timeout_text(self) -> str:
        return f"загрузка модели не завершилась за {self._load_timeout:g} с"

    def status(self) -> dict[str, int]:
        """/healthz `workers`: {configured, ready, busy}."""
        with self._lock:
            return {"configured": self.n_workers,
                    "ready": sum(1 for s in self._slots if s.ready),
                    "busy": sum(1 for s in self._slots if s.busy)}

    # ---------- lifecycle ----------

    def start(self) -> None:
        log.info("Job workers: %d (JOB_WORKERS=%s); clip ffmpeg limit %d (CLIP_MAX_PARALLEL)",
                 self.n_workers, getattr(self.settings, "job_workers", 1), max_parallel())
        for slot in self._slots:
            slot.thread = threading.Thread(target=self._loop, args=(slot,), name=slot.name, daemon=True)
            slot.thread.start()
        self._watchdog = threading.Thread(target=self._load_watchdog, args=(time.monotonic(),),
                                          name="analyzer-load-watchdog", daemon=True)
        self._watchdog.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stopping.set()
        for _ in self._slots:  # one sentinel per thread, behind any queued jobs
            self._queue.put(None)
        end = time.monotonic() + timeout
        for slot in self._slots:
            if slot.thread is not None:
                slot.thread.join(max(0.0, end - time.monotonic()))
        if self._watchdog is not None:
            self._watchdog.join(max(0.0, end - time.monotonic()))

    def submit(self, job_id: str, submitted: float | None = None) -> None:
        self._queue.put((job_id, time.monotonic() if submitted is None else submitted))

    def _any_ready(self) -> bool:
        with self._lock:
            return any(s.ready for s in self._slots)

    def _requeue_front(self, item: tuple[str, float] | None) -> None:
        """Put a dequeued item (job or stop sentinel) back at the head of the queue (FIFO kept)."""
        q = self._queue
        with q.mutex:
            q.queue.appendleft(item)
            q.not_empty.notify()

    def _load_watchdog(self, started: float) -> None:
        """Fail queued jobs fast while no analyzer became ready within the load timeout."""
        end = started + self._load_timeout
        while True:
            if self._stopping.is_set() or self._any_ready() or all(s.load_done.is_set() for s in self._slots):
                return  # loading finished (all-failed is handled by the last slot) or shutting down
            if time.monotonic() >= end:
                break
            self._stopping.wait(min(0.2, max(0.0, end - time.monotonic())))
        with self._lock:
            self._load_timed_out = True
        message = f"Анализатор недоступен: {self._timeout_text()}"
        log.error("No analyzer ready %.0f s after start (ANALYZER_LOAD_TIMEOUT_SEC); failing queued jobs "
                  "until one becomes ready", self._load_timeout)
        self.ready.set()
        while not self._stopping.is_set():
            if self._any_ready():
                log.info("An analyzer became ready after the load timeout; normal processing resumes")
                return
            if all(s.load_done.is_set() for s in self._slots):
                return  # every load finished and failed: the last slot drains the queue
            try:
                item = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is None or self._stopping.is_set() or self._any_ready():
                self._requeue_front(item)  # a sentinel, shutdown, or a slot can take the job now
                return
            job_id, _ = item
            job = self.store.get(job_id)
            if job is not None and job.status == "processing":
                log.warning("Job %s failed: %s", job_id, message)
                self._fail(job_id, message)

    def _load_analyzer(self, slot: _Slot) -> None:
        analyzer, error = None, None
        try:
            if self._analyzer_instance is not None:
                analyzer = self._analyzer_instance
            elif self._analyzer_factory is not None:
                analyzer = self._analyzer_factory()
            else:
                analyzer = load_analyzer(self.settings.analyzer)
        except AnalyzerLoadError as e:
            error = str(e)
            log.error("%s: analyzer failed to load: %s", slot.name, e)
        except Exception as e:  # noqa: BLE001
            error = f"{type(e).__name__}: {e}"
            log.exception("%s: analyzer failed to load", slot.name)
        if error is None and analyzer is None:
            error = "analyzer factory returned None"
        if error is None:
            if isinstance(analyzer, StubAnalyzer):
                analyzer.debug = bool(self.settings.stub_debug)
            log.info("%s: analyzer ready: %s", slot.name, getattr(analyzer, "name", type(analyzer).__name__))
        with self._lock:
            slot.analyzer = analyzer if error is None else None
            slot.load_error = error
            slot.ready = error is None

    def _loop(self, slot: _Slot) -> None:
        # Sequential loading: wait for the previous slot's attempt (or for shutdown).
        if slot.index > 0:
            prev = self._slots[slot.index - 1]
            while not prev.load_done.wait(0.2):
                if self._stopping.is_set():
                    slot.load_done.set()
                    return
        try:
            if not self._stopping.is_set():
                self._load_analyzer(slot)
        finally:
            slot.load_done.set()
        last = slot.index == self.n_workers - 1
        with self._lock:
            none_ready = not any(s.ready for s in self._slots)
        if slot.ready or last:
            self.ready.set()
        if not slot.ready and (self._stopping.is_set() or not (last and none_ready)):
            # Failed slot: never takes jobs, the healthy ones serve the queue. On shutdown a slot
            # without an analyzer never drains: queued jobs stay `processing` for recovery.
            return
        # A ready slot, or the last slot when every analyzer failed (it drains the queue and
        # _process fails each job fast with «Анализатор недоступен», so nothing hangs).
        while True:
            item = self._queue.get()
            if item is None:
                return
            job_id, submitted = item
            with self._lock:
                slot.busy = True
            try:
                self._process(job_id, submitted, slot)
            except Exception as e:  # noqa: BLE001 - the worker must survive anything
                log.exception("Unexpected worker error on job %s", job_id)
                self._fail(job_id, f"Внутренняя ошибка сервиса: {type(e).__name__}: {e}")
            finally:
                with self._lock:
                    slot.busy = False

    # ---------- job processing ----------

    def _fail(self, job_id: str, message: str, warnings: list[str] | None = None) -> None:
        self.store.update(job_id, status="failed", error=sanitize_paths(message), stage=STAGE_FAILED,
                          verdict=None, violations=[], finished_at=now_iso(),
                          warnings=[sanitize_paths(w) for w in (warnings or [])])

    def _process(self, job_id: str, submitted: float, slot: _Slot) -> None:
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

        # This slot's own orphans only: they hold this slot's analyzer instance.
        slot.orphans = [t for t in slot.orphans if t.is_alive()]
        if slot.orphans:
            # Already signalled via PartialSink.cancel(); give them a short chance to exit.
            wait = min(ORPHAN_JOIN_MAX_SEC, ORPHAN_JOIN_FRACTION * max(0.0, hard_deadline - time.monotonic()))
            join_end = time.monotonic() + wait
            for t in slot.orphans:
                t.join(max(0.0, join_end - time.monotonic()))
            slot.orphans = [t for t in slot.orphans if t.is_alive()]
            if slot.orphans:
                log.warning("%d cancelled analyzer thread(s) still running after %.1f s; job %s may compete "
                            "for CPU/GPU", len(slot.orphans), wait, job_id)

        analyzer = slot.analyzer
        if analyzer is None:
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
            slot.orphans.append(thread)
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
            job_meta["analyzer_error"] = sanitize_paths(err_text)[:2000]
            is_partial = True
            partial_note = WARN_PARTIAL_ERROR
        else:
            items, meta_out, result_warnings = self._extract(box.get("result"), partial)
            is_partial = bool(meta_out.get("stopped_early") or meta_out.get("partial"))
            # Before the meta size check below: an oversized meta is dropped, its notes are not.
            warnings = collect_warnings(result_warnings, meta_out.get("notes_ru"))
            # `procedure` (operation steps) is promoted to job meta with its own validation and
            # a larger cap; it does not count against the analyzer meta's 8 KB.
            proc = procedure_meta(meta_out.get("procedure"), job_id, self.rules, CHECKLIST)
            if proc is not None:
                job_meta["procedure"] = proc
            am = analyzer_meta({k: v for k, v in meta_out.items() if k != "procedure"}, job_id)
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
            warnings = add_service_warning(warnings, sanitize_paths(note))
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
